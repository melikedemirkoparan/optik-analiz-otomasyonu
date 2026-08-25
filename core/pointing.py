"""
Yönelim hatası ölçümü — decenter / tilt / roll + FOV kapsaması.

AMAÇ
----
Ground truth deseni (merkezinde artı işareti olan eş merkezli çember paterni
gibi) dedektörde nereye ve nasıl düşüyor? Üç hata bileşeni ölçülür:

    decenter  Desen merkezinin sensör merkezinden kaçıklığı (bore-sight
              hatası). Piksel VE açı cinsinden — açıya çevirmek IFOV ile
              yapılır, dolayısıyla lens/dedektör parametrelerine bağlıdır.
    tilt      Desen düzleminin optik eksene göre yatışı (düzlem-dışı).
    roll      Düzlem-içi dönme.

Ayrıca **kapsama**: desenin kaç derecelik kısmı sensöre düşüyor, dört
kenarda hangi açıya kadar görülüyor, deseni tam görmek için ne kadar
pay kalmış.

NEREDEN GELİYOR
---------------
Üçü de `dense_align` zincirinin ürettiği homografiden çıkar — ek ölçüm
yapılmaz. Homografi GT piksellerini dedektör piksellerine götürür:

    decenter : GT'nin merkezi homografiyle taşınır, sensör merkeziyle
               farkı alınır.
    roll/tilt: `optics.decompose_homography` ayrıştırmasından okunur.
    kapsama  : GT'nin dört köşesi taşınır, sensör dikdörtgeniyle kesiştirilir.

AÇIYA ÇEVİRME
-------------
Piksel → açı dönüşümü pinhole modeliyledir ve TAN TABANLIDIR:

    theta = atan( r_px * pitch / f )

Küçük açılarda `r_px * IFOV` ile aynıdır, ama kenarda ayrışır — 21.5°
FOV'lu Hydra'da köşede fark %2'yi geçer. Bu yüzden küçük açı yaklaşımı
KULLANILMAZ.

DİKKAT — ölçek ve kırpma
------------------------
Ground truth farklı çözünürlükte ve kırpılmış olabilir. Homografi bu
bilinmeyen ölçeği soğurur, dolayısıyla decenter DEDEKTÖR pikselinde
ölçülür (GT pikselinde değil) ve dedektörün kendi pitch'i ile açıya
çevrilir. Böylece GT'nin ölçeği sonucu etkilemez.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import SystemConfig
from . import optics


@dataclass
class PointingResult:
    """Yönelim hatası + kapsama raporu."""
    ok: bool = False

    # --- Decenter (bore-sight hatası) ---
    decenter_x_px: float = float("nan")    # + sağa
    decenter_y_px: float = float("nan")    # + aşağı
    decenter_px: float = float("nan")      # büyüklük
    decenter_x_deg: float = float("nan")
    decenter_y_deg: float = float("nan")
    decenter_deg: float = float("nan")     # toplam açısal kaçıklık
    decenter_azimuth_deg: float = float("nan")   # kaçıklığın yönü (0=sağ, 90=aşağı)

    # --- Açısal hatalar ---
    roll_deg: float = float("nan")         # düzlem-içi dönme (±90 katlı, eski)
    roll_full_deg: float = float("nan")    # gerçek yönelim, 0..360
    tilt_deg: float = float("nan")         # düzlem-dışı toplam
    tilt_x_deg: float = float("nan")       # dikey keystone
    tilt_y_deg: float = float("nan")       # yatay keystone

    # --- Kapsama ---
    coverage_frac: float = float("nan")    # desenin sensöre düşen alan oranı
    sensor_fill_frac: float = float("nan") # sensörün desenle dolan oranı
    max_angle_deg: float = float("nan")    # sensörde ulaşılan en büyük açı
    edge_angles_deg: dict = field(default_factory=dict)  # sol/sağ/üst/alt
    margin_px: float = float("nan")        # deseni tam görmek için kalan pay
    margin_deg: float = float("nan")
    pattern_fully_visible: bool = False

    # --- Referans ekran (açısal kaynaksa dolu) ---
    screen_angular_res_deg: float = float("nan")
    screen_implied_focal_mm: float = float("nan")
    pattern_radius_from_fov_px: float = float("nan")
    expected_scale: float = float("nan")   # GT->dedektör beklenen ölçek
    measured_scale: float = float("nan")   # hizalamanın ölçtüğü
    scale_error_pct: float = float("nan")  # ikisinin farkı (%)

    # --- Bağlam ---
    fov_x_deg: float = float("nan")
    fov_y_deg: float = float("nan")
    ifov_urad: float = float("nan")
    detector_shape: tuple = ()
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return "ölçülemedi"
        roll = (self.roll_full_deg if self.roll_full_deg == self.roll_full_deg
                else self.roll_deg)
        return (f"decenter {self.decenter_deg:.3f}° "
                f"({self.decenter_px:.1f} px), "
                f"roll {roll:.3f}°, tilt {self.tilt_deg:.3f}°")

    def coverage_summary(self) -> str:
        if not self.ok or not math.isfinite(self.coverage_frac):
            return "ölçülemedi"
        vis = "tamamı görünüyor" if self.pattern_fully_visible else "kırpılıyor"
        return (f"desenin %{100 * self.coverage_frac:.1f}'i sensörde ({vis}), "
                f"sensörün %{100 * self.sensor_fill_frac:.1f}'i dolu, "
                f"en büyük açı {self.max_angle_deg:.2f}°")


# ---------------------------------------------------------------------------
# Piksel <-> açı
# ---------------------------------------------------------------------------

def px_to_deg(cfg: SystemConfig, r_px: float) -> float:
    """
    Sensör merkezinden r_px uzaklığın optik eksene göre açısı (derece).

    Tan tabanlı pinhole: theta = atan(r * pitch / f). Küçük açı yaklaşımı
    (r * IFOV) kenarda %2'den fazla saparsa hatalı olur, kullanılmaz.
    """
    f = cfg.lens.focal_length_mm
    if f <= 0:
        return float("nan")
    pitch_mm = cfg.detector.pixel_pitch_um / 1000.0
    return math.degrees(math.atan(abs(r_px) * pitch_mm / f))


def deg_to_px(cfg: SystemConfig, theta_deg: float) -> float:
    """Açı → sensör merkezinden piksel uzaklığı."""
    f = cfg.lens.focal_length_mm
    pitch_mm = cfg.detector.pixel_pitch_um / 1000.0
    if pitch_mm <= 0:
        return float("nan")
    return f * math.tan(math.radians(abs(theta_deg))) / pitch_mm


def _signed_angle(cfg: SystemConfig, d_px: float, pitch_um: float) -> float:
    """Tek eksende işaretli açı — işaret kaçıklığın yönünü korur."""
    f = cfg.lens.focal_length_mm
    if f <= 0:
        return float("nan")
    return math.copysign(
        math.degrees(math.atan(abs(d_px) * (pitch_um / 1000.0) / f)), d_px)


# ---------------------------------------------------------------------------
# Ana ölçüm
# ---------------------------------------------------------------------------

def _poly_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _clip_to_rect(poly: np.ndarray, w: float, h: float) -> np.ndarray:
    """
    Dörtgeni sensör dikdörtgenine kırpar (Sutherland–Hodgman).
    Kapsama oranı için gereken kesişim alanını verir.
    """
    def inside(p, edge):
        if edge == 0: return p[0] >= 0.0
        if edge == 1: return p[0] <= w
        if edge == 2: return p[1] >= 0.0
        return p[1] <= h

    def intersect(a, b, edge):
        ax, ay = a
        bx, by = b
        if edge in (0, 1):
            xe = 0.0 if edge == 0 else w
            t = (xe - ax) / (bx - ax) if abs(bx - ax) > 1e-12 else 0.0
            return np.array([xe, ay + t * (by - ay)])
        ye = 0.0 if edge == 2 else h
        t = (ye - ay) / (by - ay) if abs(by - ay) > 1e-12 else 0.0
        return np.array([ax + t * (bx - ax), ye])

    out = [np.asarray(p, dtype=float) for p in poly]
    for edge in range(4):
        if not out:
            break
        src, out = out, []
        for i, cur in enumerate(src):
            prv = src[i - 1]
            cin, pin = inside(cur, edge), inside(prv, edge)
            if cin:
                if not pin:
                    out.append(intersect(prv, cur, edge))
                out.append(cur)
            elif pin:
                out.append(intersect(prv, cur, edge))
    return np.array(out) if out else np.zeros((0, 2))


def measure_pointing(H: np.ndarray,
                     gt_shape: tuple,
                     det_shape: tuple,
                     cfg: SystemConfig,
                     tilt: optics.TiltResult | None = None,
                     pattern_center_px: tuple | None = None,
                     pattern_radius_px: float | None = None) -> PointingResult:
    """
    Homografiden decenter / roll / tilt ve FOV kapsamasını çıkarır.

    H:                 GT -> dedektör homografisi (dense_align'dan)
    gt_shape:          ground truth görüntü boyutu (h, w)
    det_shape:         dedektör görüntü boyutu (h, w)
    cfg:               sistem parametreleri (IFOV/FOV buradan)
    tilt:              varsa hazır ayrıştırma; yoksa H'den çıkarılır
    pattern_center_px: GT'de desenin merkezi. Verilmezse görüntü merkezi
                       kullanılır — eş merkezli çember paterninde artı
                       işareti zaten tam merkezdedir.
    pattern_radius_px: GT'de desenin yarıçapı (en dış halka). Verilirse
                       "desen tamamen görünüyor mu" kararı buna göre verilir.
    """
    res = PointingResult()
    res.detector_shape = tuple(det_shape[:2])

    errs = cfg.validate()
    if errs:
        res.messages.extend(errs)
        return res

    if H is None or not np.all(np.isfinite(H)):
        res.messages.append("Homografi yok — yönelim hatası ölçülemez.")
        return res

    fov = optics.compute_fov(cfg)
    res.fov_x_deg, res.fov_y_deg = fov.fov_x_deg, fov.fov_y_deg
    res.ifov_urad = fov.ifov_x_urad

    dh, dw = float(det_shape[0]), float(det_shape[1])
    gh, gw = float(gt_shape[0]), float(gt_shape[1])
    scx, scy = (dw - 1.0) / 2.0, (dh - 1.0) / 2.0     # sensör merkezi

    # --- 1. Decenter: desen merkezini dedektöre taşı ---
    pc = pattern_center_px if pattern_center_px is not None \
        else ((gw - 1.0) / 2.0, (gh - 1.0) / 2.0)
    src = np.array([[[float(pc[0]), float(pc[1])]]], dtype=np.float64)
    try:
        dst = cv2.perspectiveTransform(src, np.asarray(H, dtype=np.float64))
    except cv2.error as e:                                  # noqa: BLE001
        res.messages.append(f"Merkez taşınamadı: {e}")
        return res
    cxd, cyd = float(dst[0, 0, 0]), float(dst[0, 0, 1])

    res.decenter_x_px = cxd - scx
    res.decenter_y_px = cyd - scy
    res.decenter_px = math.hypot(res.decenter_x_px, res.decenter_y_px)

    det = cfg.detector
    res.decenter_x_deg = _signed_angle(cfg, res.decenter_x_px, det.pixel_pitch_um)
    res.decenter_y_deg = _signed_angle(cfg, res.decenter_y_px, det.pixel_pitch_y_um)
    res.decenter_deg = px_to_deg(cfg, res.decenter_px)
    res.decenter_azimuth_deg = math.degrees(
        math.atan2(res.decenter_y_px, res.decenter_x_px))

    # --- 2. Roll ve tilt ---
    t = tilt if tilt is not None else optics.decompose_homography(
        H, image_shape=det_shape)
    if t is not None:
        res.roll_deg = t.in_plane_rotation_deg
        # Yönelim raporunda GERÇEK açı kullanılır. ±90'a katlı değer 224°'yi
        # 44°'ye düşürür ve kullanıcı tamamen farklı bir yönelim okur.
        # İŞARET: `optics` açıyı matematiksel yönde (saat tersi pozitif)
        # üretir; projenin geri kalanı ve kullanıcının gördüğü yön saat
        # yönüdür (panel `-in_plane_rotation_deg` gösterir). Yönelim
        # raporu da aynı yönde olmalı, yoksa 136° yerine 224° okunur.
        _full = getattr(t, "in_plane_rotation_full_deg", float("nan"))
        res.roll_full_deg = (360.0 - _full) % 360.0 if _full == _full else _full
        res.tilt_deg = t.total_tilt_deg
        res.tilt_x_deg = t.tilt_x_deg
        res.tilt_y_deg = t.tilt_y_deg

    # --- 3. Kapsama: GT'nin dört köşesini taşı, sensörle kesiştir ---
    corners = np.array([[[0.0, 0.0]], [[gw, 0.0]], [[gw, gh]], [[0.0, gh]]],
                       dtype=np.float64)
    try:
        proj = cv2.perspectiveTransform(
            corners, np.asarray(H, dtype=np.float64)).reshape(-1, 2)
    except cv2.error as e:                                  # noqa: BLE001
        res.messages.append(f"Köşeler taşınamadı: {e}")
        proj = None

    if proj is not None and np.all(np.isfinite(proj)):
        area_pattern = _poly_area(proj)
        clipped = _clip_to_rect(proj, dw, dh)
        area_vis = _poly_area(clipped) if len(clipped) >= 3 else 0.0
        if area_pattern > 1e-9:
            res.coverage_frac = float(area_vis / area_pattern)
        res.sensor_fill_frac = float(area_vis / (dw * dh)) if dw * dh > 0 else 0.0

        # Sensör köşelerinin optik eksene göre açısı — ulaşılan en büyük açı
        res.max_angle_deg = px_to_deg(cfg, math.hypot(scx, scy))
        res.edge_angles_deg = {
            "sol": px_to_deg(cfg, scx),
            "sağ": px_to_deg(cfg, dw - 1 - scx),
            "üst": px_to_deg(cfg, scy),
            "alt": px_to_deg(cfg, dh - 1 - scy),
        }

        # Desen tamamen görünüyor mu
        inside = [(0.0 <= p[0] <= dw) and (0.0 <= p[1] <= dh) for p in proj]
        res.pattern_fully_visible = bool(all(inside))

    # --- 3B. Referans ekran açısal kaynaksa GT'nin açısal ölçeği bilinir ---
    # STOS gibi bir ekranda üretici derece/piksel verir; bu, ground truth'un
    # her pikselinin hangi açıya karşılık geldiğini SABİTLER. O zaman:
    #   * desen yarıçapı elle girilmeden cihaz FOV'undan türetilebilir,
    #   * ölçülen ölçek beklenen ölçekle karşılaştırılabilir (doğrulama).
    scr = getattr(cfg, "oled", None)
    if scr is not None and getattr(scr, "is_angular_source", False):
        res.screen_angular_res_deg = float(scr.angular_res_deg)
        res.screen_implied_focal_mm = float(scr.implied_focal_mm)
        half_fov = fov.fov_x_deg / 2.0
        r_gt = scr.radius_px_for_angle(half_fov)
        if r_gt == r_gt and r_gt > 0:
            res.pattern_radius_from_fov_px = float(r_gt)
            if pattern_radius_px is None:
                pattern_radius_px = r_gt
        # Beklenen GT->dedektör ölçeği: aynı açı iki ekranda kaç piksel?
        #   GT'de   r_gt  = f_scr * tan(theta) / pitch_scr
        #   dedektörde r_det = f_lens * tan(theta) / pitch_det
        # Oran açıdan bağımsızdır (her ikisi de tan tabanlı).
        pitch_det_mm = det.pixel_pitch_um / 1000.0
        pitch_scr_mm = scr.pixel_pitch_um / 1000.0
        if pitch_det_mm > 0 and scr.implied_focal_mm > 0:
            res.expected_scale = float(
                (cfg.lens.focal_length_mm / pitch_det_mm)
                / (scr.implied_focal_mm / pitch_scr_mm))
            if t is not None:
                meas = 0.5 * (abs(t.scale_x) + abs(t.scale_y))
                res.measured_scale = float(meas)
                if res.expected_scale > 0:
                    res.scale_error_pct = float(
                        100.0 * (meas - res.expected_scale) / res.expected_scale)

    # --- 4. Pay: deseni tam görmek için kalan mesafe ---
    # Desen yarıçapı verilmişse dedektördeki karşılığı hesaplanır ve
    # merkez kaçıklığıyla birlikte sensör kenarına olan mesafe bulunur.
    if pattern_radius_px is not None and pattern_radius_px > 0:
        # Homografinin ortalama ölçeği (izotropik yaklaşım)
        A = np.asarray(H, dtype=np.float64)[:2, :2]
        scale = float(np.sqrt(abs(np.linalg.det(A)))) or 1.0
        r_det = pattern_radius_px * scale
        # Merkezden sensör kenarlarına olan en kısa mesafe
        d_edge = min(cxd, cyd, dw - 1 - cxd, dh - 1 - cyd)
        res.margin_px = float(d_edge - r_det)
        res.margin_deg = px_to_deg(cfg, abs(res.margin_px))
        if res.margin_px < 0:
            res.margin_deg = -res.margin_deg
        res.pattern_fully_visible = bool(res.margin_px >= 0)

    res.ok = True
    return res


def format_report(res: PointingResult) -> str:
    """Konsol/rapor için çok satırlı özet."""
    if not res.ok:
        return "Yönelim ölçümü yapılamadı.\n" + "\n".join(
            f"  - {m}" for m in res.messages)

    lines = [
        "YÖNELİM HATALARI",
        f"  decenter : {res.decenter_deg:.4f}°  "
        f"({res.decenter_px:.2f} px, azimut {res.decenter_azimuth_deg:+.1f}°)",
        f"             x {res.decenter_x_deg:+.4f}°  y {res.decenter_y_deg:+.4f}°",
        f"  roll     : {res.roll_full_deg:.4f}°"
        f"   (±90 katlı gösterim: {res.roll_deg:+.4f}°)",
        f"  tilt     : {res.tilt_deg:.4f}°  "
        f"(x {res.tilt_x_deg:+.3f}°, y {res.tilt_y_deg:+.3f}°)",
        "",
        "KAPSAMA",
        f"  nominal FOV      : {res.fov_x_deg:.3f}° × {res.fov_y_deg:.3f}°",
        f"  IFOV             : {res.ifov_urad:.2f} µrad/px",
        f"  sensörde en büyük açı : {res.max_angle_deg:.3f}°",
    ]
    if res.edge_angles_deg:
        e = res.edge_angles_deg
        lines.append(
            f"  kenar açıları    : sol {e['sol']:.2f}°  sağ {e['sağ']:.2f}°  "
            f"üst {e['üst']:.2f}°  alt {e['alt']:.2f}°")
    if math.isfinite(res.coverage_frac):
        lines.append(f"  desenin görünen kısmı : %{100 * res.coverage_frac:.1f}")
        lines.append(f"  sensörün dolan kısmı  : %{100 * res.sensor_fill_frac:.1f}")
    if math.isfinite(res.margin_px):
        durum = "pay var" if res.margin_px >= 0 else "TAŞIYOR"
        lines.append(f"  desen payı       : {res.margin_px:+.1f} px "
                     f"({res.margin_deg:+.3f}°) — {durum}")
    lines.append(f"  desen tamamı görünüyor mu : "
                 f"{'EVET' if res.pattern_fully_visible else 'HAYIR (kırpılıyor)'}")

    if res.screen_angular_res_deg == res.screen_angular_res_deg:
        lines += [
            "",
            "REFERANS EKRAN (açısal kaynak)",
            f"  açısal çözünürlük : {res.screen_angular_res_deg:.4f} °/px"
            f"  →  ima edilen f = {res.screen_implied_focal_mm:.2f} mm",
        ]
        if res.pattern_radius_from_fov_px == res.pattern_radius_from_fov_px:
            lines.append(
                f"  cihaz FOV'unun ekrandaki yarıçapı : "
                f"{res.pattern_radius_from_fov_px:.1f} px")
        if res.expected_scale == res.expected_scale:
            lines.append(
                f"  ölçek doğrulaması : beklenen {res.expected_scale:.4f}  "
                f"ölçülen {res.measured_scale:.4f}  "
                f"fark %{res.scale_error_pct:+.2f}")
    return "\n".join(lines)
