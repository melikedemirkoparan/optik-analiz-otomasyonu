"""
Siemens star (merkezi radyal desen) tabanlı tilt ölçümü.

WTW test deseninin merkezindeki büyük dairesel yıldız, gerçekte DAİRE'dir.
Eğik (tilt'li) bir düzlemde görüntülenince ELİPS'e dönüşür. Bu geometrik
gerçek, görüntülerin farklı çözünürlük / kırpma (crop) / aspect farkından
BAĞIMSIZ olarak tilt ölçmemizi sağlar:

  * Elipsin eksen oranı (b/a)  -> düzlem-dışı tilt açısı:  acos(b/a)
  * Elipsin ana eksen açısı     -> düzlem-içi dönme yönü

Bu yüzden tilt'i homografi ayrıştırması yerine (ki o ölçek/crop'a duyarlı)
doğrudan merkezi desenin elips fitinden ölçeriz — çok daha güvenilir.

YILDIZI DİĞER DESENLERDEN AYIRAN ÖZELLİK — teğetsel geçiş yoğunluğu
------------------------------------------------------------------
Test chart'ında merkezi yıldızın dışında da yüksek kenar enerjili bölgeler
var (köşe yıldızları, metin, çerçeve). Bu yüzden "en dıştaki kenar" aramak
tüm chart'ı yakalar. Ayırt edici gerçek özellik şudur: merkezi yıldızın
İÇİNDEKİ her yarıçap halkasında, açısal (teğetsel) yönde çok sayıda
siyah-beyaz geçiş vardır (kama sayısının iki katı, ~110). Yıldız bitince
bu sayı keskin biçimde düşer.

Algoritma:
  1. Merkezi bul (radyal desenin simetri merkezi).
  2. Yarıçapı artırarak her halkada teğetsel geçiş sayısını say -> profil.
  3. Profilin plato seviyesinden ilk KALICI düşüş yarıçapı = yıldızın sınırı.
  4. O sınırın civarında, her ışın için düşüşün olduğu tam yarıçapı ölç.
  5. Bu sınır noktalarına elips fit et.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


# Yıldız yarıçapının görüntü kısa kenarına oranı için makul arama aralığı.
# (Chart'ın merkezi yıldızı kadraja göre tipik olarak bu bantta kalır.)
R_FRAC_MIN = 0.06
R_FRAC_MAX = 0.45


@dataclass
class EllipseFit:
    cx: float
    cy: float
    major_axis: float          # büyük eksen (piksel, tam uzunluk)
    minor_axis: float          # küçük eksen
    angle_deg: float           # elipsin ana ekseninin görüntü x'ine göre açısı
    axis_ratio: float          # minor / major  (1.0 = tam daire = tilt yok)
    tilt_deg: float            # düzlem-dışı tilt = acos(minor/major)
    confidence: float          # 0..1 güven
    ok: bool


# --------------------------- yardımcılar ---------------------------------

def _sample_ring(gray32: np.ndarray, cx: float, cy: float, r: float,
                 n_samp: int = 720):
    """
    (cx,cy) merkezli r yarıçaplı halkayı açısal olarak örnekler.
    Döndürür: (değerler, geçerli_maske) — görüntü dışına taşan örnekler
    maskede False olur.
    """
    th = np.linspace(0, 2 * np.pi, n_samp, endpoint=False)
    xs = cx + r * np.cos(th)
    ys = cy + r * np.sin(th)
    h, w = gray32.shape
    valid = (xs >= 0) & (xs < w - 1) & (ys >= 0) & (ys < h - 1)
    vals = cv2.remap(gray32,
                     xs.astype(np.float32).reshape(1, -1),
                     ys.astype(np.float32).reshape(1, -1),
                     cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE).ravel()
    return vals, valid


def _ring_transition_count(gray32: np.ndarray, cx: float, cy: float, r: float,
                           n_samp: int = 720) -> float:
    """
    Halkadaki teğetsel siyah-beyaz geçiş sayısı.
    Yıldız içinde yüksek (~2 x kama sayısı), dışında düşük.
    Halkanın yarısından fazlası görüntü dışındaysa -1 (geçersiz) döner.
    """
    vals, valid = _sample_ring(gray32, cx, cy, r, n_samp)
    if valid.sum() < n_samp * 0.5:
        return -1.0
    v = vals[valid]
    v = v - v.mean()
    if v.std() < 3.0:            # düz/kontrastsız alan -> desen yok
        return 0.0
    # Hafif açısal yumuşatma: gürültünün sahte geçiş üretmesini engeller
    k = np.ones(3) / 3.0
    v = np.convolve(v, k, mode="same")
    s = np.sign(v)
    s[s == 0] = 1
    return float((np.diff(s) != 0).sum())


def _center_symmetry_score(gray32: np.ndarray, cx: float, cy: float,
                           r_probe: float, n_samp: int = 720) -> float:
    """
    Merkez adayının kalitesi: halka profilinin 180° kaydırıldığında
    kendisiyle ne kadar örtüştüğü.

    Siemens star merkeze göre nokta-simetriktir (çift sayıda kama).
    Merkez doğruysa halka profili yarım tur kaydırıldığında kendisine
    oturur; merkez kaydıysa örtüşme bozulur. Bu ölçüt, geçiş sayısına
    göre çok daha keskin bir tepe verir — geçiş sayısı merkez etrafında
    geniş bir bölgede neredeyse sabit kalır ve merkezi tam yerine
    oturtmaz.
    """
    vals, valid = _sample_ring(gray32, cx, cy, r_probe, n_samp)
    if valid.sum() < n_samp * 0.9:
        return -1e9
    v = vals - vals.mean()
    if v.std() < 1e-3:
        return -1e9
    rolled = np.roll(v, n_samp // 2)
    return float(np.dot(v, rolled) / (np.dot(v, v) + 1e-9))


def _refine_center(gray32: np.ndarray, cx: float, cy: float,
                   r_probe: float, search: float) -> tuple[float, float]:
    """
    Merkezi, nokta-simetri skorunu maksimize edecek şekilde iyileştirir.
    Birden çok yarıçapta ölçüp ortalayarak tek bir halkanın gürültüsüne
    bağlı kalmayı önler.
    """
    probes = [r_probe * s for s in (0.7, 1.0, 1.3)]

    def score(x, y):
        vals = [_center_symmetry_score(gray32, x, y, r) for r in probes]
        vals = [v for v in vals if v > -1e8]
        return sum(vals) / len(vals) if vals else -1e9

    best = (score(cx, cy), cx, cy)
    step = max(1.0, search / 2.0)
    while step >= 0.25:
        improved = True
        while improved:
            improved = False
            for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step),
                           (step, step), (-step, -step),
                           (step, -step), (-step, step)):
                nx, ny = best[1] + dx, best[2] + dy
                s = score(nx, ny)
                if s > best[0]:
                    best = (s, nx, ny)
                    improved = True
        step /= 2.0
    return best[1], best[2]


def _star_radius_from_profile(gray32: np.ndarray, cx: float, cy: float,
                              r_min: float, r_max: float):
    """
    Halka geçiş profilinden yıldızın kaba dış yarıçapını bulur.

    Plato (yıldız içi) seviyesini profilin üst yüzdeliğinden tahmin eder,
    sonra plato'nun yarısının altına düşüp bir daha çıkmadığı ilk yarıçapı
    sınır kabul eder. "En dıştaki kenar" değil "ilk kalıcı düşüş" aranır —
    böylece köşe yıldızları / metin / çerçeve yakalanmaz.

    Döndürür: (r_boundary, plateau_seviyesi) veya (None, 0).
    """
    radii = np.arange(r_min, r_max, max(1.0, (r_max - r_min) / 200.0))
    counts = np.array([_ring_transition_count(gray32, cx, cy, float(r))
                       for r in radii])

    valid = counts >= 0
    if valid.sum() < 10:
        return None, 0.0

    # Plato = yıldız içi seviyesi. İç yarıdaki halkaların üst yüzdeliği.
    inner = counts[valid][:max(5, int(valid.sum() * 0.5))]
    plateau = float(np.percentile(inner, 75))
    if plateau < 20.0:           # anlamlı bir radyal desen yok
        return None, 0.0

    thr = plateau * 0.5

    # Profili yumuşat (tek halkalık gürültü düşüşleri sınır sanılmasın)
    k = np.ones(3) / 3.0
    smooth = np.convolve(np.where(valid, counts, 0.0), k, mode="same")

    # İlk KALICI düşüş: eşiğin altına inip, sonraki birkaç halkada da
    # eşiğin üstüne geri dönmeyen ilk yarıçap.
    persist = 4
    start = max(3, int(len(radii) * 0.10))     # merkeze çok yakını atla
    for i in range(start, len(radii)):
        if not valid[i] or smooth[i] >= thr:
            continue
        j = min(len(radii), i + persist)
        if np.all(smooth[i:j] < thr):
            return float(radii[i]), plateau

    # Düşüş bulunamadı (yıldız kadrajı taşıyor olabilir) -> geçerli en dış halka
    last = np.where(valid)[0]
    return float(radii[last[-1]]), plateau


def _boundary_points(gray32: np.ndarray, cx: float, cy: float,
                     r_guess: float, plateau: float,
                     n_rays: int = 180) -> np.ndarray:
    """
    Kaba yarıçap tahmininin çevresinde, her ışın için yıldızın bittiği
    tam yarıçapı ölçer.

    Her ışın yönünde dar bir açısal dilim alınır; dilim içindeki radyal
    kontrast (kamaların oluşturduğu salınım) yüksekken yıldız içindeyiz,
    düşünce sınıra geldik demektir.
    """
    lo = max(3.0, r_guess * 0.55)
    hi = r_guess * 1.45
    n_r = max(24, int(hi - lo))
    rs = np.linspace(lo, hi, n_r)

    # Her yarıçapta tam halkayı örnekle -> (n_r, n_samp) matris.
    n_samp = max(360, n_rays * 2)
    ring_vals = np.empty((n_r, n_samp), dtype=np.float32)
    ring_ok = np.empty((n_r, n_samp), dtype=bool)
    for i, r in enumerate(rs):
        v, ok = _sample_ring(gray32, cx, cy, float(r), n_samp)
        ring_vals[i] = v
        ring_ok[i] = ok

    # Açısal yönde yerel kontrast: kamalar var oldukça yüksek kalır.
    # Her (yarıçap, açı) için dar açısal pencerede std hesapla.
    win = max(5, n_samp // 72)          # ~bir kama genişliği
    pad = win // 2
    padded = np.concatenate([ring_vals[:, -pad:], ring_vals,
                             ring_vals[:, :pad]], axis=1)
    c1 = np.cumsum(padded, axis=1, dtype=np.float64)
    c2 = np.cumsum(padded.astype(np.float64) ** 2, axis=1)
    n = win
    s1 = c1[:, n:] - c1[:, :-n]
    s2 = c2[:, n:] - c2[:, :-n]
    local_var = np.maximum(s2 / n - (s1 / n) ** 2, 0.0)
    local_std = np.sqrt(local_var)[:, :n_samp]

    # Radyal yönde hafif yumuşatma — tek halkalık gürültü çukurları
    # sınır sanılmasın.
    if n_r >= 5:
        kr = np.ones(3) / 3.0
        local_std = np.apply_along_axis(
            lambda c: np.convolve(c, kr, mode="same"), 0, local_std)

    pts = []
    step = max(1, n_samp // n_rays)
    for a in range(0, n_samp, step):
        col = local_std[:, a]
        okc = ring_ok[:, a]
        if okc.sum() < 4:
            continue

        # HER IŞIN KENDİ referansına göre değerlendirilir. Ortak (global)
        # bir eşik kullanmak, kamaların merkeze doğru sıklaşması ve
        # aydınlatma farkları yüzünden bazı ışınları erken kestiriyordu;
        # sınır noktaları dağılıp elipsi olduğundan küçük/basık gösteriyordu.
        valid_col = col[okc]
        ref = float(np.percentile(valid_col, 80))
        if ref < 1e-3:
            continue
        thr = ref * 0.35

        below = (col < thr) | (~okc)
        # Yıldız içinden dışına ilk KALICI geçiş
        idx = None
        run = max(2, n_r // 12)
        for i in range(1, n_r):
            if below[i] and np.all(below[i:min(n_r, i + run)]):
                idx = i
                break
        if idx is None or idx <= 0:
            continue

        # Alt-piksel hassasiyet: eşiği geçtiği yerde doğrusal ara-değerleme
        prev, cur = col[idx - 1], col[idx]
        if prev > cur and prev >= thr:
            frac = (prev - thr) / max(1e-6, prev - cur)
        else:
            frac = 0.0
        r_b = float(rs[idx - 1] + frac * (rs[idx] - rs[idx - 1]))

        th = 2 * np.pi * a / n_samp
        pts.append((cx + r_b * math.cos(th), cy + r_b * math.sin(th)))

    return np.array(pts, dtype=np.float32) if pts else np.empty((0, 2), np.float32)


# --------------------------- ana tespit ----------------------------------

def _radial_boundary_ellipse(gray: np.ndarray) -> EllipseFit | None:
    """
    Merkezi radyal deseni (Siemens star) bulup dış sınırını elips olarak
    fit eder. Bkz. modül başındaki algoritma açıklaması.
    """
    if gray is None or gray.ndim != 2:
        return None
    h, w = gray.shape[:2]
    short = min(h, w)
    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.float32)

    r_min = short * R_FRAC_MIN
    r_max = short * R_FRAC_MAX

    # 1) Merkez: görüntü merkezinden başla, nokta-simetriyi maksimize ederek iyileştir
    cx, cy = w / 2.0, h / 2.0
    r_probe = short * 0.12
    cx, cy = _refine_center(g, cx, cy, r_probe, search=short * 0.08)

    # 2) Kaba yıldız yarıçapı
    r_guess, plateau = _star_radius_from_profile(g, cx, cy, r_min, r_max)
    if r_guess is None:
        return None

    # 3) Merkezi bulunan yarıçapın ortasında tekrar iyileştir (daha kararlı)
    cx, cy = _refine_center(g, cx, cy, r_guess * 0.6, search=short * 0.02)
    r_guess, plateau = _star_radius_from_profile(g, cx, cy, r_min, r_max)
    if r_guess is None:
        return None

    # 4) Işın bazlı hassas sınır noktaları
    pts = _boundary_points(g, cx, cy, r_guess, plateau)
    if len(pts) < 20:
        return None

    # 5) Aykırı noktaları ele.
    # Önce kaba bir medyan filtresi, sonra elips fit edip artığa göre
    # ikinci bir tur (tek geçişli medyan, gerçek elipsin uzun ekseni
    # boyunca yasal olarak büyük olan yarıçapları da eleyebiliyor).
    r = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    med = float(np.median(r))
    keep = (r > 0.6 * med) & (r < 1.5 * med)
    pts = pts[keep]
    if len(pts) < 15:
        return None

    prelim = cv2.fitEllipse(pts)
    resid_all = _point_ellipse_residuals(pts, prelim)
    mad = float(np.median(np.abs(resid_all - np.median(resid_all))))
    if mad > 1e-6:
        keep2 = np.abs(resid_all - np.median(resid_all)) < 4.0 * mad
        if keep2.sum() >= 15:
            pts = pts[keep2]

    ellipse = cv2.fitEllipse(pts)
    (ex, ey), (d1, d2), ang = ellipse
    major = max(d1, d2)
    minor = min(d1, d2)
    if major <= 1e-6:
        return None

    ratio = min(1.0, max(0.05, minor / major))
    tilt = math.degrees(math.acos(ratio))
    # cv2.fitEllipse açıyı d1 (ilk eksen) için verir; büyük eksene çevir
    angle = ang if d1 >= d2 else (ang + 90.0)
    angle = ((angle + 90) % 180) - 90        # [-90, 90)

    # Güven: kaç ışın sınır verdi + fit'in artık hatası
    n_expected = 180
    coverage = min(1.0, len(pts) / n_expected)
    resid = _ellipse_residual(pts, (ex, ey), (d1, d2), ang)
    fit_q = math.exp(-resid / max(1.0, 0.02 * major))
    conf = float(max(0.0, min(1.0, 0.5 * coverage + 0.5 * fit_q)))

    return EllipseFit(
        cx=float(ex), cy=float(ey), major_axis=float(major),
        minor_axis=float(minor), angle_deg=float(angle),
        axis_ratio=float(ratio), tilt_deg=float(tilt),
        confidence=conf, ok=True,
    )


def _point_ellipse_residuals(pts: np.ndarray, ellipse) -> np.ndarray:
    """
    Her noktanın fit edilen elipse yaklaşık radyal uzaklığı (piksel).
    Aykırı nokta elemesi için kullanılır; işaretli değer döner
    (pozitif = elipsin dışında).
    """
    (ex, ey), (d1, d2), ang = ellipse
    a = max(d1, d2) / 2.0
    b = min(d1, d2) / 2.0
    if a < 1e-6 or b < 1e-6:
        return np.zeros(len(pts), dtype=np.float64)
    th = math.radians(ang if d1 >= d2 else ang + 90.0)
    ct, st = math.cos(th), math.sin(th)
    dx = pts[:, 0] - ex
    dy = pts[:, 1] - ey
    u = dx * ct + dy * st
    v = -dx * st + dy * ct
    q = np.sqrt((u / a) ** 2 + (v / b) ** 2)
    return (q - 1.0) * a


def _ellipse_residual(pts: np.ndarray, center, axes, ang_deg: float) -> float:
    """Noktaların fit edilen elipse ortalama radyal uzaklığı (piksel)."""
    ex, ey = center
    a = max(axes) / 2.0
    b = min(axes) / 2.0
    th = math.radians(ang_deg if axes[0] >= axes[1] else ang_deg + 90.0)
    ct, st = math.cos(th), math.sin(th)
    dx = pts[:, 0] - ex
    dy = pts[:, 1] - ey
    u = dx * ct + dy * st          # ana eksen koordinatı
    v = -dx * st + dy * ct         # yan eksen
    if a < 1e-6 or b < 1e-6:
        return float("inf")
    # Elips denklemi artığını yaklaşık piksel mesafesine çevir
    q = np.sqrt((u / a) ** 2 + (v / b) ** 2)
    return float(np.mean(np.abs(q - 1.0)) * a)


def detect_center_ellipse(gray: np.ndarray) -> EllipseFit:
    """Merkezi Siemens star'ın dış sınırını elips olarak tespit eder."""
    fit = _radial_boundary_ellipse(gray)
    if fit is None:
        return EllipseFit(0, 0, 0, 0, 0, 1.0, 0.0, 0.0, False)
    return fit


def draw_ellipse(gray: np.ndarray, fit: EllipseFit) -> np.ndarray:
    """Debug/önizleme: tespit edilen elipsi görüntü üzerine çizer (BGR)."""
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if not fit.ok:
        return vis
    center = (int(round(fit.cx)), int(round(fit.cy)))
    axes = (int(round(fit.major_axis / 2)), int(round(fit.minor_axis / 2)))
    cv2.ellipse(vis, center, axes, fit.angle_deg, 0, 360, (0, 255, 0), 2)
    cv2.circle(vis, center, 4, (0, 0, 255), -1)
    a = math.radians(fit.angle_deg)
    x2 = int(fit.cx + (fit.major_axis / 2) * math.cos(a))
    y2 = int(fit.cy + (fit.major_axis / 2) * math.sin(a))
    cv2.line(vis, center, (x2, y2), (255, 0, 0), 2)
    return vis


@dataclass
class StarTiltResult:
    gt_ellipse: EllipseFit
    det_ellipse: EllipseFit
    tilt_deg: float            # dedektör düzleminin tilt'i (referansa göre)
    rotation_deg: float        # düzlem-içi dönme farkı
    ok: bool


def analyze_pair(gt_gray: np.ndarray, det_gray: np.ndarray) -> StarTiltResult:
    """
    İki görüntüdeki merkezi yıldızı tespit edip aralarındaki tilt ve
    düzlem-içi dönme farkını hesaplar. Ground truth referans (düz) kabul
    edilir; dedektördeki elips sapması gerçek optik tilt'i verir.

    GT'nin kendi elips oranı 1.0'dan küçükse (baskı/kadraj kaynaklı hafif
    sapma) bu referans olarak çıkarılır.
    """
    gt = detect_center_ellipse(gt_gray)
    det = detect_center_ellipse(det_gray)
    if not (gt.ok and det.ok):
        return StarTiltResult(gt, det, 0.0, 0.0, False)

    # Tilt'i oranlar üzerinden ayrıştır: dedektör oranını GT oranına göre
    # normalize et (GT'deki sistematik sapmayı bölerek at).
    ratio = det.axis_ratio / max(1e-6, gt.axis_ratio)
    ratio = min(1.0, max(0.05, ratio))
    tilt = math.degrees(math.acos(ratio))

    rotation = det.angle_deg - gt.angle_deg
    rotation = ((rotation + 90) % 180) - 90
    return StarTiltResult(gt, det, tilt, rotation, True)


if __name__ == "__main__":
    GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
    DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"
    gt = cv2.imread(GT, 0)
    det = cv2.imread(DET, 0)
    res = analyze_pair(gt, det)
    for tag, e in (("GT ", res.gt_ellipse), ("DET", res.det_ellipse)):
        print(f"{tag} merkez=({e.cx:7.1f},{e.cy:7.1f})  "
              f"major={e.major_axis:7.1f}  minor={e.minor_axis:7.1f}  "
              f"oran={e.axis_ratio:.4f}  aci={e.angle_deg:6.1f}  "
              f"guven={e.confidence:.2f}")
    print("Tilt (dedektör) = %.2f°  |  Rotasyon farkı = %.2f°" % (
        res.tilt_deg, res.rotation_deg))
    cv2.imwrite("/home/test123/Desktop/optik_analiz/data/ellipse_gt.png",
                draw_ellipse(gt, res.gt_ellipse))
    cv2.imwrite("/home/test123/Desktop/optik_analiz/data/ellipse_det.png",
                draw_ellipse(det, res.det_ellipse))
    print("Debug görüntüler data/ altına kaydedildi.")
