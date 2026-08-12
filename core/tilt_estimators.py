"""
Çoklu tilt ölçüm katmanı — yıldıza bağımlı olmayan, belirsizlik raporlayan.

NEDEN BU MODÜL VAR
------------------
Eski akış tek dayanağa bağlıydı: merkezi Siemens star. O desen kadrajda
değilse `siemens_star.analyze_pair` `ok=False` döner ve düzlem-dışı tilt
TAMAMEN kaybolurdu; geriye yalnızca ölçek/crop'a duyarlı keystone kalırdı.

Ayrıca elips yöntemi küçük açılarda doğası gereği duyarsızdır:

    b/a = cos(tilt)   ->   d(b/a)/d(tilt) = -sin(tilt)

tilt -> 0 iken türev de 0'a gider. 1° tilt eksen oranını yalnızca ~1.5e-4
değiştirir; bu ölçüm gürültüsünün altındadır. Eski kod bu durumda oranı
1.0'a KIRPIP `arccos(1.0) = 0.000°` üretiyordu — yani "ölçemedim" durumu
ekranda "tilt yok" gibi görünüyordu. Bu modül o ayrımı korur: her ölçüm
kendi BELİRSİZLİĞİNİ taşır ve kırpma olduysa `clamped` ile işaretlenir.

TEMEL İLKE — bilinen geometri şart
----------------------------------
Tek görüntüden düzlem-dışı tilt çıkarmak, sahnede bilinen bir geometri
olmasını gerektirir. "Gerçekte daire" bilgisi olmadan bir elipsin eliptikliği
hiçbir şey ifade etmez: eğik daire ile gerçekten eliptik nesne ayırt edilemez.
Bu yüzden buradaki her estimator bir ÖNKOŞULA dayanır ve önkoşulu
sağlanmıyorsa ölçüm üretmez — uydurmaz.

ESTIMATOR'LAR
-------------
  circle_ellipse   Bilinen dairesel desen (Siemens star dahil) -> acos(b/a)
  grid_vanishing   Izgara/çizgi deseni -> kaçış noktası geometrisi
  homography       SIFT homografisi -> perspektif terimleri (fallback)

Yeni desen tipleri `ESTIMATORS` listesine eklenerek katılır; birleştirici
ve GUI değişmez.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import siemens_star


# Elips oranının 1.0'ı bu kadar aşması ölçüm gürültüsü sayılır (fiziksel
# olarak oran > 1 imkânsızdır — daire perspektifle yuvarlaklaşamaz).
RATIO_NOISE_TOL = 0.01

# Tipik eksen-oranı ölçüm gürültüsü. Sentetik doğrulamadan (test_tilt_synth)
# gelen ~0.29° tepe hatası ve gerçek çiftlerdeki GT/DET oran farkı ile uyumlu.
RATIO_SIGMA = 0.002


@dataclass
class TiltEstimate:
    """
    Tek bir yöntemin ürettiği tilt ölçümü.

    experimental  Yöntem henüz bağımsız olarak doğrulanmadı. Raporlanır ama
                  birincil ölçüm olarak SEÇİLMEZ — doğrulanmamış bir sayıyı
                  kullanıcıya kesin değer diye sunmak bu katmanın amacına
                  aykırıdır.

    tilt_deg     ölçülen düzlem-dışı tilt (derece)
    sigma_deg    1-sigma belirsizlik. Küçük açılarda elips yöntemi için bu
                 değer BÜYÜKTÜR — sayının kendisinden daha bilgilendiricidir.
    confidence   0..1 yöntemin kendi kalite skoru (tespit netliği vb.)
    method       yöntem kimliği ("circle_ellipse", "grid_vanishing", ...)
    ok           ölçüm üretilebildi mi
    clamped      değer fiziksel sınıra kırpıldı mı (tilt gürültünün altında)
    detail       arayüzde/raporda gösterilecek kısa açıklama
    """
    tilt_deg: float = float("nan")
    sigma_deg: float = float("inf")
    confidence: float = 0.0
    method: str = ""
    ok: bool = False
    clamped: bool = False
    experimental: bool = False
    detail: str = ""

    @property
    def resolvable(self) -> bool:
        """
        Ölçüm, kendi belirsizliğinden ayırt edilebilir mi?
        False ise "tilt = X" demek yanıltıcıdır; "tilt < sigma" demek doğrudur.
        """
        return self.ok and math.isfinite(self.sigma_deg) and \
            self.tilt_deg > self.sigma_deg


@dataclass
class TiltReport:
    """Tüm yöntemlerin sonucu + birleştirilmiş nihai değer."""
    estimates: list[TiltEstimate] = field(default_factory=list)
    tilt_deg: float = float("nan")
    sigma_deg: float = float("inf")
    confidence: float = 0.0
    primary_method: str = ""
    ok: bool = False
    messages: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return self.ok and math.isfinite(self.sigma_deg) and \
            self.tilt_deg > self.sigma_deg

    def summary(self) -> str:
        """Arayüzde tek satırda gösterilebilecek dürüst özet."""
        if not self.ok:
            return "ölçülemedi"
        if not self.resolvable:
            return f"< {self.sigma_deg:.2f}° (gürültü sınırı altında)"
        return f"{self.tilt_deg:.3f}° ± {self.sigma_deg:.2f}°"


# --------------------------------------------------------------------------
# 1) Bilinen dairesel desen — Siemens star ve benzeri
# --------------------------------------------------------------------------

def _tilt_from_ratio(ratio: float) -> tuple[float, bool]:
    """
    Eksen oranından tilt. Kırpma olup olmadığını da bildirir.

    Oranın 1.0'ı aşması ölçüm gürültüsüdür; tolerans içindeyse 0°'e kırpılır
    ve `clamped=True` döner — "tilt yok" ile "tilt ölçülemedi" karışmasın.
    """
    if ratio > 1.0:
        return 0.0, True
    ratio = max(0.05, ratio)
    return math.degrees(math.acos(ratio)), False


def _ratio_sigma_to_deg(ratio: float, sigma_ratio: float = RATIO_SIGMA) -> float:
    """
    Eksen oranı gürültüsünü derece cinsi belirsizliğe çevirir.

        tilt = acos(r)  ->  d(tilt)/dr = -1 / sqrt(1 - r^2)

    r -> 1 (küçük tilt) iken bu türev patlar: aynı oran gürültüsü çok daha
    büyük açı belirsizliği demektir. Küçük açılarda yöntemin neden duyarsız
    olduğunun matematiksel ifadesi budur.
    """
    r = min(1.0, max(0.0, ratio))

    # r -> 1 iken türev sonsuza gider ve sigma anlamsız büyür ("< 81°" gibi
    # hiçbir şey söylemeyen bir üst sınır). Doğru üst sınır, oran gürültüsü
    # kadar BASIKLIĞIN karşılık geldiği açıdır:
    #
    #     acos(1 - sigma_ratio)
    #
    # yani "bu kadar oran gürültüsü en fazla şu açıyı gizleyebilir".
    # Tipik sigma_ratio=0.002 için bu ~3.6° — yöntemin gerçek çözünürlük
    # sınırı budur ve kullanıcıya anlamlı bir şey söyler.
    floor_deg = math.degrees(math.acos(max(0.0, 1.0 - sigma_ratio)))

    denom = math.sqrt(max(1e-12, 1.0 - r * r))
    if denom < 1e-6:
        return floor_deg

    # Doğrusal yaklaşım (sigma_ratio/denom) yalnızca r yeterince 1'den
    # uzakken geçerlidir; r -> 1 iken sonsuza gider. Gerçek belirsizlik
    # hiçbir zaman floor_deg'i aşamaz — o, oran gürültüsünün gizleyebileceği
    # EN BÜYÜK açıdır. Bu yüzden tavan olarak floor_deg uygulanır.
    linear = math.degrees(sigma_ratio / denom)
    return min(linear, floor_deg)


def estimate_from_circle(gt_gray: np.ndarray, det_gray: np.ndarray) -> TiltEstimate:
    """
    Bilinen dairesel desenin elips fitinden tilt.

    Önkoşul: her iki görüntüde de dairesel (radyal) desen tespit edilebilmeli.
    GT'nin kendi oranı referans alınır — baskı/kadraj kaynaklı sistematik
    sapma bölünerek atılır.
    """
    est = TiltEstimate(method="circle_ellipse")
    try:
        gt = siemens_star.detect_center_ellipse(gt_gray)
        det = siemens_star.detect_center_ellipse(det_gray)
    except Exception as e:                                  # noqa: BLE001
        est.detail = f"elips tespiti hata verdi: {e}"
        return est

    if not (gt.ok and det.ok):
        missing = []
        if not gt.ok:
            missing.append("ground truth")
        if not det.ok:
            missing.append("dedektör")
        est.detail = ("dairesel desen bulunamadı: " + ", ".join(missing) +
                      " görüntüsünde")
        return est

    # Tespit güveni eşiği. `detect_center_ellipse` desensiz/gürültülü
    # görüntülerde de bir elips "bulabilir" (ok=True) — kapsama ve fit
    # artığı tesadüfen makul çıkabilir. Kılavuzun sağlık kontrolünde
    # kullandığı 0.7 sınırını burada da uygularız: bunun altındaki bir
    # tespitten tilt üretmek, olmayan bir desenden sayı uydurmaktır.
    conf = float(min(gt.confidence, det.confidence))
    if conf < 0.7:
        est.confidence = conf
        est.detail = (f"dairesel desen güvenilir biçimde tespit edilemedi "
                      f"(güven {conf:.2f} < 0.70)")
        return est

    ratio = det.axis_ratio / max(1e-6, gt.axis_ratio)
    if ratio > 1.0 + RATIO_NOISE_TOL:
        # Gürültüyle açıklanamayacak kadar büyük — ölçüm güvenilmez.
        est.detail = (f"eksen oranı fiziksel dışı ({ratio:.4f} > 1) — "
                      "desen tespiti şüpheli")
        est.confidence = 0.0
        return est

    tilt, clamped = _tilt_from_ratio(ratio)
    sigma = _ratio_sigma_to_deg(min(1.0, ratio))

    est.tilt_deg = tilt
    est.sigma_deg = sigma
    est.clamped = clamped
    est.confidence = conf
    est.ok = True
    est.detail = (f"GT oran {gt.axis_ratio:.4f} · DET oran {det.axis_ratio:.4f} "
                  f"· normalize {ratio:.4f}")
    return est


# --------------------------------------------------------------------------
# 2) Izgara / düz çizgi deseni — kaçış noktası geometrisi
# --------------------------------------------------------------------------

def _line_segments(gray: np.ndarray, min_frac: float = 0.12):
    """
    Görüntüdeki uzun düz çizgi parçalarını bulur.
    Kısa kenarın `min_frac` katından kısa parçalar elenir (gürültü/metin).
    """
    h, w = gray.shape[:2]
    short = min(h, w)
    min_len = short * min_frac

    g = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(g, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360.0,
                            threshold=int(min_len * 0.6),
                            minLineLength=min_len,
                            maxLineGap=short * 0.02)
    if lines is None:
        return np.empty((0, 4), np.float32)
    return lines.reshape(-1, 4).astype(np.float32)


def _group_by_direction(segs: np.ndarray, tol_deg: float = 25.0):
    """
    Çizgileri baskın iki yöne ayırır (ızgaranın iki ekseni).
    Döndürür: (grup_a, grup_b) — her biri (N,4) segment dizisi.
    """
    if len(segs) < 4:
        return np.empty((0, 4), np.float32), np.empty((0, 4), np.float32)

    ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1],
                                segs[:, 2] - segs[:, 0]))
    ang = (ang + 180.0) % 180.0          # yönsüz: [0,180)

    # En kalabalık yön = birinci eksen
    hist, edges_ = np.histogram(ang, bins=36, range=(0, 180))
    i1 = int(np.argmax(hist))
    a1 = (edges_[i1] + edges_[i1 + 1]) / 2.0

    # İkinci eksen: birinciden yeterince uzak en kalabalık yön
    mask_far = np.minimum(np.abs(np.arange(36) * 5 + 2.5 - a1),
                          180 - np.abs(np.arange(36) * 5 + 2.5 - a1)) > 30
    if not mask_far.any():
        return segs, np.empty((0, 4), np.float32)
    h2 = np.where(mask_far, hist, 0)
    i2 = int(np.argmax(h2))
    a2 = (edges_[i2] + edges_[i2 + 1]) / 2.0

    def near(a_ref):
        d = np.abs(ang - a_ref)
        d = np.minimum(d, 180.0 - d)
        return d < tol_deg

    return segs[near(a1)], segs[near(a2)]


def _vanishing_point(segs: np.ndarray):
    """
    Paralel olması gereken çizgilerin kaçış noktasını en küçük karelerle bulur.

    Her çizgi homojen koordinatta l = p1 x p2. Kaçış noktası v, tüm
    çizgilere aitse l·v = 0. SVD ile en küçük tekil vektör çözümdür.
    Döndürür: (v_homojen, artık_hata) veya (None, inf).
    """
    if len(segs) < 3:
        return None, float("inf")
    p1 = np.column_stack([segs[:, 0], segs[:, 1], np.ones(len(segs))])
    p2 = np.column_stack([segs[:, 2], segs[:, 3], np.ones(len(segs))])
    lines = np.cross(p1, p2)
    n = np.linalg.norm(lines[:, :2], axis=1, keepdims=True)
    lines = lines / np.maximum(n, 1e-9)

    _, s, vt = np.linalg.svd(lines)
    v = vt[-1]
    resid = float(s[-1] / max(1e-9, s[0]))
    return v, resid


def estimate_from_grid(det_gray: np.ndarray,
                       focal_px: float | None = None) -> TiltEstimate:
    """
    Izgara / düz çizgi deseninden tilt — kaçış noktası yöntemi.

    Önkoşul: dedektör görüntüsünde, gerçekte PARALEL olan uzun düz çizgiler
    bulunmalı (ızgara, çerçeve, çizgi çifti deseni).

    Fizik: gerçekte paralel çizgiler perspektifte tek bir kaçış noktasında
    buluşur. Kaçış noktasının görüntü merkezine uzaklığı d (piksel) ve odak
    uzaklığı f (piksel) ise, o yöndeki düzlem eğikliği:

        tilt = atan(f / d)        (d -> sonsuz iken tilt -> 0)

    Bu yöntem küçük açılarda elipsten DAHA duyarlıdır: eğiklik azaldıkça
    kaçış noktası uzaklaşır ve konumu ölçmek kolaylaşır.
    """
    est = TiltEstimate(method="grid_vanishing")

    if focal_px is None or not math.isfinite(focal_px) or focal_px <= 0:
        est.detail = "odak uzaklığı piksel cinsinden bilinmiyor"
        return est

    segs = _line_segments(det_gray)
    if len(segs) < 6:
        est.detail = f"yeterli düz çizgi yok ({len(segs)} parça)"
        return est

    ga, gb = _group_by_direction(segs)
    h, w = det_gray.shape[:2]
    cx, cy = w / 2.0, h / 2.0

    tilts, weights, parts = [], [], []
    for name, grp in (("eksen-1", ga), ("eksen-2", gb)):
        if len(grp) < 3:
            continue
        v, resid = _vanishing_point(grp)
        if v is None or abs(v[2]) < 1e-12:
            # Kaçış noktası sonsuzda -> BU YÖNDE eğiklik yok. Bu bir ölçüm
            # değil, bilgi yokluğudur: tek eksen etrafında eğilmiş bir
            # düzlemde diğer eksenin çizgileri paralel KALIR. Bunu 0° ölçümü
            # sayıp ortalamaya katmak, gerçek tilt'i yarıya böler.
            parts.append(f"{name}: paralel — bu eksende eğiklik yok")
            continue
        vx, vy = v[0] / v[2], v[1] / v[2]
        d = math.hypot(vx - cx, vy - cy)
        if not math.isfinite(d):
            continue

        # Kaçış noktası görüntüye yakınsa bu bir ölçüm DEĞİLDİR.
        # Fizik: gerçek bir düzlem tilt'i kaçış noktasını kadrajın çok
        # dışına atar (d >> f). VP kadrajın içinde/kenarında çıkıyorsa
        # çizgiler aslında paralel değildir — Hough gürültüsü, metin
        # kenarları veya radyal desenin ışınları tek noktada "buluşuyor"
        # gibi görünür. Bu durumda atan(f/d) 90°'e yakın saçma bir açı
        # üretir. Böyle bir grubu ölçüm saymak yerine atıyoruz.
        if d < max(w, h) * 0.75:
            parts.append(f"{name}: VP kadraj içinde ({d:.0f}px) — reddedildi")
            continue

        t = math.degrees(math.atan(focal_px / d))
        # Geometrik akıl sağlığı: bu düzenekte 45°'yi aşan tilt beklenmiyor.
        if t > 45.0:
            parts.append(f"{name}: {t:.1f}° — makul aralık dışı, reddedildi")
            continue
        # Kaçış noktası ne kadar uzaksa açı o kadar küçük; çok uzaktaysa
        # sayısal olarak 0'a yakınsar — bu doğru davranıştır.
        wgt = 1.0 / (1.0 + 50.0 * resid)
        tilts.append(t)
        weights.append(wgt)
        parts.append(f"{name}: {t:.2f}° (VP uzaklık {d:.0f}px)")

    if not tilts:
        est.detail = "kaçış noktası çözülemedi"
        return est

    # İki eksenden gelen açılar AYRI eğiklik bileşenleridir, aynı büyüklüğün
    # tekrarlı ölçümü değil. Ortalamak yanlış olur (tek eksende eğik bir
    # düzlemde değeri yarıya böler); toplam eğiklik bileşenlerin bileşkesidir.
    tw = float(np.sum(weights))
    tilt = float(math.hypot(*tilts)) if len(tilts) > 1 else float(tilts[0])
    # Belirsizlik: en zayıf bileşenin ağırlığından türetilir.
    sigma = max(0.2, 1.0 / max(1e-6, min(weights)) - 1.0 + 0.2)

    est.tilt_deg = tilt
    est.sigma_deg = sigma
    est.confidence = float(min(1.0, tw / 2.0))
    est.ok = True
    # DOĞRULAMA DURUMU: sentetik ızgara testinde bu yöntem ~7° sistematik
    # sapma gösterdi (bkz. test_tilt_multi.py). Muhtemel sebepler: ana nokta
    # (principal point) görüntü merkezi varsayılıyor ve lens distorsiyonu
    # hesaba katılmıyor. Kalibre edilene kadar yalnızca destekleyici veri
    # olarak raporlanır; birincil ölçüm seçilmez.
    est.experimental = True
    est.detail = " · ".join(parts) + " [deneysel — kalibre edilmedi]"
    return est


# --------------------------------------------------------------------------
# 3) Homografi perspektif terimleri — son çare
# --------------------------------------------------------------------------

def estimate_from_homography(tilt_result) -> TiltEstimate:
    """
    SIFT homografisinin perspektif terimlerinden tilt.

    Önkoşul: geçerli homografi. UYARI: bu ölçüm ölçek ve kırpma farkına
    duyarlıdır — GT ile dedektör farklı kadrajdaysa fark buraya sızar.
    Bu yüzden belirsizliği kasıtlı olarak yüksek verilir ve yalnızca
    diğer yöntemler yoksa birincil seçilir.
    """
    est = TiltEstimate(method="homography")
    if tilt_result is None:
        est.detail = "homografi yok"
        return est
    if not getattr(tilt_result, "homography_ok", False):
        est.detail = "homografi dejenerelik denetiminden geçemedi"
        return est

    est.tilt_deg = float(tilt_result.total_tilt_deg)
    # Ölçek/crop duyarlılığı yüzünden geniş belirsizlik.
    est.sigma_deg = max(1.0, abs(est.tilt_deg) * 0.5)
    est.confidence = 0.3
    est.ok = True
    # Homografi tilt'i, GT ile dedektör farklı kadraj/ölçekteyken sistematik
    # olarak sapar — kılavuzun "keystone ikincildir" uyarısının kaynağı budur.
    # Dolayısıyla doğrulanmış bir ölçüm sayılmaz: raporlanır, ama dairesel
    # desen ölçümü varken onun önüne geçmemelidir.
    est.experimental = True
    est.detail = (f"keystone X={tilt_result.tilt_x_deg:+.3f}° "
                  f"Y={tilt_result.tilt_y_deg:+.3f}° "
                  "(ölçek/kırpma farkına duyarlı)")
    return est


# --------------------------------------------------------------------------
# Birleştirici
# --------------------------------------------------------------------------

def _focal_px(cfg) -> float | None:
    """Odak uzaklığını piksel cinsine çevirir (kaçış noktası yöntemi için)."""
    try:
        f_mm = cfg.lens.focal_length_mm
        pitch_mm = cfg.detector.pixel_pitch_um / 1000.0
        if f_mm > 0 and pitch_mm > 0:
            return f_mm / pitch_mm
    except Exception:                                       # noqa: BLE001
        pass
    return None


def measure_tilt(gt_gray: np.ndarray, det_gray: np.ndarray,
                 cfg=None, homography_tilt=None) -> TiltReport:
    """
    Mevcut tüm yöntemleri dener, sonuçları belirsizliklerine göre tartar.

    Seçim mantığı:
      * Önkoşulu sağlanan her yöntem bir ölçüm üretir.
      * Birincil yöntem, ölçümü ÇÖZÜLEBİLİR olanlar arasından en küçük
        belirsizliğe sahip olandır (sigma küçük = güvenilir).
      * Hiçbiri çözülebilir değilse en dar üst sınırı veren seçilir ve
        rapor "< sigma" biçiminde dürüst bir üst sınır sunar.
      * Hiç ölçüm yoksa ok=False — sayı UYDURULMAZ.
    """
    rep = TiltReport()

    rep.estimates.append(estimate_from_circle(gt_gray, det_gray))
    rep.estimates.append(estimate_from_grid(
        det_gray, focal_px=_focal_px(cfg) if cfg else None))
    rep.estimates.append(estimate_from_homography(homography_tilt))

    usable = [e for e in rep.estimates if e.ok]
    if not usable:
        rep.messages.append(
            "Tilt ölçülemedi — hiçbir yöntemin önkoşulu sağlanmadı. "
            "Görüntüde bilinen geometri (dairesel desen veya paralel çizgi "
            "ızgarası) bulunmalıdır.")
        for e in rep.estimates:
            if e.detail:
                rep.messages.append(f"  · {e.method}: {e.detail}")
        return rep

    # Doğrulanmamış yöntemler birincil ölçüm olarak SEÇİLMEZ. Elde yalnızca
    # deneysel ölçüm varsa nihai sonuç "ölçülemedi"dir: doğrulanmamış bir
    # sayıyı tek dayanak yapmak, bu katmanın engellemek için var olduğu
    # davranışın ta kendisidir (gürültüden tilt uydurmak).
    validated = [e for e in usable if not e.experimental]
    if not validated:
        rep.messages.append(
            "Tilt ölçülemedi — yalnızca doğrulanmamış yöntemler sonuç verdi. "
            "Görüntüde güvenilir bir dairesel desen bulunmuyor.")
        for e in rep.estimates:
            if e.ok and e.experimental:
                rep.messages.append(
                    f"  · {e.method}: {e.tilt_deg:.2f}° (deneysel — "
                    "yalnızca gösterge)")
            elif e.detail:
                rep.messages.append(f"  · {e.method}: {e.detail}")
        return rep

    resolvable = [e for e in validated if e.resolvable]
    pool = resolvable if resolvable else validated
    best = min(pool, key=lambda e: e.sigma_deg)

    rep.tilt_deg = best.tilt_deg
    rep.sigma_deg = best.sigma_deg
    rep.confidence = best.confidence
    rep.primary_method = best.method
    rep.ok = True

    if not resolvable:
        rep.messages.append(
            f"Tilt, ölçüm gürültüsünün altında: < {best.sigma_deg:.2f}°. "
            "Bu 'tilt yok' demek DEĞİL, 'bu yöntemle ayırt edilemiyor' demektir.")
    if best.clamped:
        rep.messages.append(
            "Eksen oranı 1.0'ı aştığı için 0°'e kırpıldı — gerçek tilt "
            "sıfır civarında ama işareti/büyüklüğü çözülemiyor.")

    # Yöntemler arası tutarsızlık uyarısı: birbirinin hata payına düşmüyorlarsa
    for e in resolvable:
        if e is best:
            continue
        gap = abs(e.tilt_deg - best.tilt_deg)
        if gap > 2.0 * (e.sigma_deg + best.sigma_deg):
            rep.messages.append(
                f"Uyarı: {e.method} ({e.tilt_deg:.2f}°) ile "
                f"{best.method} ({best.tilt_deg:.2f}°) uyuşmuyor — "
                "görüntü kalitesini kontrol edin.")
    return rep
