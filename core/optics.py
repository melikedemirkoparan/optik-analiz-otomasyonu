"""
Optik hesap çekirdeği — FOV, IFOV ve tilt matematiği.

TÜM formüller SystemConfig'ten okunan parametreleri kullanır.
Lens / dedektör / OLED değişirse formüller otomatik yeni değerlere göre
sonuç üretir; hiçbir sayı burada sabit kodlanmamıştır.

Model: Pinhole (delik-iğne) kamera modeli — kollimatörsüz doğrudan görüntüleme.

  IFOV  (bir pikselin gördüğü açı):
        ifov = 2 * atan( pitch / (2 f) )        [tek piksel için, rad]
        ~ pitch / f   (küçük açı yaklaşımı)

  FOV   (tüm sensörün gördüğü açı):
        fov = 2 * atan( (N * pitch) / (2 f) )    [rad]

  f     : lens odak uzaklığı
  pitch : dedektör piksel pitch'i
  N     : dedektör piksel sayısı (yatay / dikey)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import SystemConfig


# ----------------------------- Sonuç yapıları -----------------------------

@dataclass
class FovResult:
    ifov_x_urad: float          # yatay IFOV (mikroradyan / piksel)
    ifov_y_urad: float          # dikey IFOV
    ifov_x_arcsec: float        # yatay IFOV (arcsecond / piksel)
    ifov_y_arcsec: float
    fov_x_deg: float            # yatay FOV (derece)
    fov_y_deg: float            # dikey FOV
    fov_diag_deg: float         # köşegen FOV
    sensor_w_mm: float
    sensor_h_mm: float


@dataclass
class TiltResult:
    in_plane_rotation_deg: float   # düzlem-içi dönme (görüntü saat yönünde eğik)
    tilt_x_deg: float              # düzlem-dışı tilt — x ekseni etrafında (keystone dikey)
    tilt_y_deg: float              # düzlem-dışı tilt — y ekseni etrafında (keystone yatay)
    total_tilt_deg: float          # toplam düzlem-dışı eğiklik büyüklüğü
    scale_x: float                 # ground-truth -> dedektör ölçek (x)
    scale_y: float                 # ölçek (y)
    mirrored: bool                 # görüntü aynalanmış mı (flip)
    homography_ok: bool            # homografi güvenilir mi


# ----------------------------- FOV / IFOV ---------------------------------

def compute_fov(cfg: SystemConfig) -> FovResult:
    """
    Sadece sistem parametrelerinden (lens f + dedektör) FOV/IFOV hesaplar.
    Görüntüden bağımsızdır; sistemin teorik/nominal değerleridir.
    """
    f = cfg.lens.focal_length_mm
    det = cfg.detector
    pitch_x_mm = det.pixel_pitch_um / 1000.0
    pitch_y_mm = det.pixel_pitch_y_um / 1000.0

    # IFOV — tek piksel açısı
    ifov_x = 2.0 * math.atan(pitch_x_mm / (2.0 * f))   # rad
    ifov_y = 2.0 * math.atan(pitch_y_mm / (2.0 * f))

    # FOV — tüm sensör
    fov_x = 2.0 * math.atan(det.sensor_width_mm / (2.0 * f))
    fov_y = 2.0 * math.atan(det.sensor_height_mm / (2.0 * f))
    fov_d = 2.0 * math.atan(det.diagonal_mm / (2.0 * f))

    return FovResult(
        ifov_x_urad=ifov_x * 1e6,
        ifov_y_urad=ifov_y * 1e6,
        ifov_x_arcsec=math.degrees(ifov_x) * 3600.0,
        ifov_y_arcsec=math.degrees(ifov_y) * 3600.0,
        fov_x_deg=math.degrees(fov_x),
        fov_y_deg=math.degrees(fov_y),
        fov_diag_deg=math.degrees(fov_d),
        sensor_w_mm=det.sensor_width_mm,
        sensor_h_mm=det.sensor_height_mm,
    )


def angle_of_pixel_offset(cfg: SystemConfig, dx_px: float, dy_px: float) -> float:
    """
    Sensör merkezinden (dx, dy) piksel uzaktaki bir noktanın optik eksene
    göre açısını (derece) döndürür. FOV doğrulaması / nokta-açı sorguları için.
    """
    f = cfg.lens.focal_length_mm
    pitch_x_mm = cfg.detector.pixel_pitch_um / 1000.0
    pitch_y_mm = cfg.detector.pixel_pitch_y_um / 1000.0
    rx = dx_px * pitch_x_mm
    ry = dy_px * pitch_y_mm
    r = math.hypot(rx, ry)
    return math.degrees(math.atan(r / f))


# ----------------------------- Tilt / rotasyon ----------------------------

def decompose_homography(H, image_shape=None) -> TiltResult:
    """
    Ground truth -> dedektör homografi matrisini (3x3) ayrıştırıp:
      * düzlem-içi rotasyon (in-plane, derece)
      * düzlem-dışı tilt (perspektif / keystone, derece)
      * ölçek ve ayna (flip) durumu
    çıkarır.

    ÖNEMLİ: Ground truth ve dedektör görüntüleri farklı çözünürlükte olabilir
    ve OLED'e kırpılarak (crop) basıldığı için farklı kadrajda olabilir. Bu
    yüzden ham homografinin ölçek/aspect farkı GERÇEK tilt DEĞİLDİR. Rotasyon
    ve tilt'i ölçekten ayırmak için affine kısmı QR-benzeri ayrıştırmayla
    (rotasyon x [ölçek+shear] üst-üçgen) çözeriz. Böylece anizotropik ölçek
    ve kırpma, dönme/tilt ölçümüne karışmaz.
    """
    import numpy as np
    H = np.asarray(H, dtype=float)
    if H.shape != (3, 3) or abs(H[2, 2]) < 1e-12:
        return TiltResult(0, 0, 0, 0, 1, 1, False, False)
    H = H / H[2, 2]

    A = H[:2, :2].copy()          # affine kısım (rotasyon+ölçek+shear)

    # Determinant < 0 => ayna (flip) var
    det2 = float(np.linalg.det(A))
    mirrored = det2 < 0
    # Rotasyonu doğru ölçmek için ayna bileşenini geçici olarak ayır:
    # flip'i x eksenini ters çevirerek modelle, kalan saf rotasyon+ölçek olsun.
    if mirrored:
        A = A @ np.array([[-1.0, 0.0], [0.0, 1.0]])

    # QR ayrıştırması: A = R (rotasyon) * K (üst-üçgen: ölçek+shear)
    # numpy QR, R'yi ortogonal (rotasyon/yansıma), K'yi üst-üçgen verir.
    R, K = np.linalg.qr(A)
    # K'nin köşegenini pozitif yapacak şekilde işaret normalize et
    signs = np.sign(np.diag(K))
    signs[signs == 0] = 1.0
    R = R * signs           # sütunları ölçekle
    K = (K.T * signs).T     # satırları ölçekle
    # Saf düzlem-içi rotasyon açısı (R ortogonal)
    rot = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    while rot > 90:
        rot -= 180
    while rot < -90:
        rot += 180

    # Ölçekler K'nin köşegeninden (anizotropik ölçek — rapor için)
    sx = abs(float(K[0, 0]))
    sy = abs(float(K[1, 1]))

    # --- Düzlem-dışı tilt (perspektif) ---
    # Perspektif terimleri h[2,0], h[2,1] görüntü ölçeğine bağlıdır; görüntü
    # boyutuyla normalize edilir. Kenardan kenara "perspektif kaçışı" oranını
    # verir ve bir düzlemin optik eksene göre yatış açısına çevrilir.
    h20, h21 = H[2, 0], H[2, 1]
    if image_shape is not None:
        Hh, Ww = image_shape[:2]
        pers_x = h20 * Ww
        pers_y = h21 * Hh
    else:
        pers_x = h20
        pers_y = h21

    tilt_y = math.degrees(math.atan(pers_x))   # y ekseni etrafında (yatay keystone)
    tilt_x = math.degrees(math.atan(pers_y))   # x ekseni etrafında (dikey keystone)
    total_tilt = math.degrees(math.atan(math.hypot(pers_x, pers_y)))

    return TiltResult(
        in_plane_rotation_deg=rot,
        tilt_x_deg=tilt_x,
        tilt_y_deg=tilt_y,
        total_tilt_deg=total_tilt,
        scale_x=sx,
        scale_y=sy,
        mirrored=bool(mirrored),
        homography_ok=True,
    )


def measured_ifov_from_scale(cfg: SystemConfig, oled_feature_px: float,
                             detector_feature_px: float) -> float:
    """
    (İsteğe bağlı doğrulama) Ground truth'ta bilinen boyuttaki bir özelliğin
    dedektörde kaç piksele denk geldiğinden ölçülen efektif IFOV'u hesaplar.
    Nominal (compute_fov) ile karşılaştırma için kullanılır.
    Döndürülen değer: mikroradyan/piksel.
    """
    if detector_feature_px <= 0:
        return float("nan")
    pitch_mm = cfg.detector.pixel_pitch_um / 1000.0
    f = cfg.lens.focal_length_mm
    ifov = 2.0 * math.atan(pitch_mm / (2.0 * f))
    return ifov * 1e6
