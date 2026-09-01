"""
Desen-agnostik yoğun (dense) hizalama — piksel piksel ölçüm.

NEDEN BU MODÜL VAR
------------------
`image_analysis.py` SIFT ile çalışır: köşe/blob bulur, tanımlayıcıyla eşler.
Bu yaklaşım DESENE BAĞIMLIDIR ve kendine-benzer desenlerde çöker:
Siemens star'ın radyal deseninde merkez çevresinde yüzlerce sahte eşleşme
üretip dejenere homografi doğurur (bkz. DEVAM_YONERGESI bölüm 5.2).
Eş merkezli çember paterninde de aynı sorun vardır — her halka birbirine
benzer, hiçbir halka ayırt edici bir "köşe" taşımaz.

Bu modül hiçbir özellik ARAMAZ. Tek sorduğu şudur:

    "Ground truth'u şu dönüşümle warp edersem dedektör görüntüsüne
     ne kadar benzer?"

ve benzerliği artıran yöne gider. Desenin ne olduğu umurunda değildir —
çember, yıldız, ızgara, harf, rastgele doku: hepsi aynı kodla ölçülür.
Tek gereksinim desenin DOKUSU olmasıdır (düz gri bir alan hizalanamaz;
bu bilgi teorik bir sınırdır, kodun eksiği değildir).

ÜÇ KADEME
---------
1. `coarse_align`  — log-polar faz korelasyonu ile çeviri + ölçek + dönme.
   Global bir yöntemdir: yerel minimuma takılmaz, başlangıç tahmini
   gerektirmez. 4 ayna varyantını da tarar. SIFT'in yerini alır.

2. `refine_ecc`    — ECC (Enhanced Correlation Coefficient) ile alt-piksel
   homografi. Kaba kademenin verdiği tahminden başlar, piramitli koşar.
   Çıktısı 3x3 homografidir; `optics.decompose_homography` bunu olduğu gibi
   yiyebilir, yani dönme/tilt/ölçek matematiği DEĞİŞMEDEN çalışır.

3. `residual_flow` — homografi uygulandıktan SONRA kalan kaymayı optik akışla
   HER PİKSEL için ölçer. Homografi ideal bir projektif dönüşümdür; düz
   çizgiyi düz çizgiye götürür. Gerçek mercek götürmez. Dolayısıyla kalan
   artık = DİSTORSİYON. Çıktı tek sayı değil, görüntü boyunca vektör alanıdır.

ÖLÇEK NEDEN VERİDEN ÇÖZÜLÜYOR
-----------------------------
Ground truth her zaman aynı ölçekte gelmez (farklı çözünürlük, farklı kadraj,
OLED'e kırpılarak basılmış olabilir). Bu yüzden distorsiyon ideal
`f*tan(theta)` modeline göre MUTLAK ölçülemez: ölçek bilinmeden sapmanın ne
kadarının distorsiyon, ne kadarının ölçek farkı olduğu ayırt edilemez.
Homografi bu bilinmeyen ölçeği/kadrajı verinin kendisinden çözer ve soğurur;
geriye kalan kalıntı saf distorsiyondur. Referans budur.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from . import optics


# --------------------------------------------------------------------------
# Sonuç yapıları
# --------------------------------------------------------------------------

@dataclass
class CoarseResult:
    """Kaba hizalama çıktısı — benzerlik dönüşümü (çeviri+ölçek+dönme)."""
    ok: bool = False
    variant: str = "raw"           # "raw" | "flip_h" | "flip_v" | "flip_both"
    scale: float = 1.0             # gt -> det ölçek
    rotation_deg: float = 0.0      # gt -> det düzlem-içi dönme
    tx: float = 0.0                # çeviri (det piksel)
    ty: float = 0.0
    response: float = 0.0          # faz korelasyonu güveni (0..1)
    matrix: np.ndarray | None = None   # 3x3 benzerlik dönüşümü (gt -> det)
    # Dönme güvenle çözülemediğinde birden çok aday matris döner; nihai
    # seçimi ECC yapar (kaba skor ile ECC farklı tepeleri işaret edebilir).
    rot_candidates: list = field(default_factory=list)
    rot_ambiguous: bool = False
    messages: list[str] = field(default_factory=list)


def _to_float(img: np.ndarray) -> np.ndarray:
    """Gri görüntüyü 0..1 float32'ye çevirir."""
    a = img.astype(np.float32)
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def _prep(img: np.ndarray) -> np.ndarray:
    """
    Hizalama öncesi hazırlık: kontrast dengeleme + 0..1 normalize.

    CLAHE kullanılır çünkü GT (ekrana basılan ideal desen) ile dedektör
    görüntüsünün pozlaması taban tabana zıt olabilir; yoğunluk tabanlı
    yöntemler mutlak parlaklığa değil YAPIYA bakmalıdır.
    """
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    eq = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(img)
    return _to_float(eq)


def _apodize(img: np.ndarray) -> np.ndarray:
    """
    Kenar penceresi (Hanning). FFT tabanlı yöntemler görüntüyü periyodik
    varsayar; kenardaki süreksizlik spektrumda sahte bir haç deseni üretir
    ve faz korelasyonunu yanıltır. Pencere bunu bastırır.
    """
    h, w = img.shape[:2]
    wy = np.hanning(h).astype(np.float32)
    wx = np.hanning(w).astype(np.float32)
    return img * np.outer(wy, wx)


def variants(img: np.ndarray) -> dict:
    """Ayna varyantları — dedektör görüntüsü sıklıkla yatay aynalıdır."""
    return {
        "raw": img,
        "flip_h": cv2.flip(img, 1),
        "flip_v": cv2.flip(img, 0),
        "flip_both": cv2.flip(img, -1),
    }


# --------------------------------------------------------------------------
# 1. Kademe — kaba hizalama (log-polar faz korelasyonu)
# --------------------------------------------------------------------------

def _highpass(shape) -> np.ndarray:
    """
    Spektrum için yüksek-geçiren pencere.

    Genlik spektrumunun enerjisi ezici biçimde MERKEZDE (düşük frekanslarda)
    toplanır. Log-polar dönüşüm alındığında bu dev tepe, dönme/ölçek bilgisini
    taşıyan orta frekansları boğar ve faz korelasyonu gerçek tepe yerine
    ızgaranın kendi simetrisine (45/90 derece) kilitlenir.

    Bu filtre (Stone/Reddy-Chatterji'nin klasik yaklaşımı) düşük frekansları
    bastırır, orta bandı öne çıkarır:  H = (1-X)*(2-X),  X = cos(pi*f)
    """
    h, w = shape
    y = np.linspace(-0.5, 0.5, h, dtype=np.float32).reshape(-1, 1)
    x = np.linspace(-0.5, 0.5, w, dtype=np.float32).reshape(1, -1)
    X = np.cos(np.pi * y) * np.cos(np.pi * x)
    return (1.0 - X) * (2.0 - X)


def _fft_magnitude(img: np.ndarray) -> np.ndarray:
    """
    Genlik spektrumu (merkezlenmiş, log ölçekli, yüksek-geçiren filtreli).

    Fourier genliği ÇEVİRİDEN BAĞIMSIZDIR: görüntüyü kaydırmak yalnızca fazı
    değiştirir. Bu yüzden ölçek ve dönmeyi çeviriden AYRI çözebiliriz —
    üç bilinmeyeni aynı anda aramak yerine önce genlikten (ölçek, dönme),
    sonra fazdan (çeviri). Yoğunluk tabanlı hizalamanın temel numarası budur.
    """
    f = np.fft.fftshift(np.abs(np.fft.fft2(img)))
    return np.log1p(f) * _highpass(f.shape)


def _logpolar(img: np.ndarray, center=None, n_ang: int = 720,
              n_rad: int = 512) -> tuple:
    """
    Log-polar dönüşüm. Kartezyende ÖLÇEK ve DÖNME olan fark, log-polarda
    iki eksende ÇEVİRİ olur — ve çeviriyi faz korelasyonu doğrudan ölçer.

    Örnekleme ızgarası görüntü boyutundan AYRI tutulur (n_ang x n_rad):
    açısal çözünürlük doğrudan dönme hassasiyetini belirler, görüntünün
    piksel sayısıyla ilgisi yoktur. 720 açısal örnek -> 0.5 derece adım.

    En küçük yarıçap 1 px'e sabitlenmez; log ekseni merkezdeki tekillikten
    uzak başlatılır, yoksa örneklerin yarısı birkaç pikselin içinde harcanır.

    Döndürür: (logpolar görüntü, log-ölçek katsayısı M) — M, yatay kaymayı
    ölçeğe çevirmek için gerekir:  x = M * log(r).
    """
    h, w = img.shape[:2]
    if center is None:
        center = ((w - 1) / 2.0, (h - 1) / 2.0)
    max_radius = min(center[0], center[1], w - center[0], h - center[1])
    max_radius = max(max_radius, 2.0)

    M = n_rad / np.log(max_radius)
    lp = cv2.warpPolar(img, (n_rad, n_ang), center, max_radius,
                       cv2.INTER_LINEAR + cv2.WARP_POLAR_LOG)
    return lp, M


def _phase_correlate(a: np.ndarray, b: np.ndarray) -> tuple:
    """Faz korelasyonu — (kayma_x, kayma_y, güven)."""
    (sx, sy), resp = cv2.phaseCorrelate(np.ascontiguousarray(a),
                                        np.ascontiguousarray(b))
    return float(sx), float(sy), float(resp)


def _similarity_matrix(scale: float, rot_deg: float,
                       tx: float, ty: float,
                       center_src, center_dst) -> np.ndarray:
    """
    Benzerlik dönüşümünü 3x3 homografi olarak kurar.

    Dönme ve ölçek KAYNAK GÖRÜNTÜNÜN MERKEZİ etrafında uygulanır (log-polar
    çözümü orayı referans alır), sonra hedef merkeze taşınır ve faz
    korelasyonundan gelen çeviri eklenir.

    İŞARET: Görüntü koordinatlarında y ekseni AŞAĞI bakar, bu yüzden standart
    matematiksel dönme matrisi ekranda ters yönde döner. Burada OpenCV'nin
    `getRotationMatrix2D` konvansiyonu kullanılır (pozitif açı = saat yönünün
    tersi), böylece ölçülen açı projenin geri kalanıyla aynı işarettedir.
    """
    th = np.deg2rad(-rot_deg)
    c, s = np.cos(th) * scale, np.sin(th) * scale
    cx, cy = center_src
    dx, dy = center_dst

    # kaynak merkezi orijine taşı -> döndür+ölçekle -> hedef merkeze taşı
    T1 = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], dtype=np.float64)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    T2 = np.array([[1, 0, dx + tx], [0, 1, dy + ty], [0, 0, 1]], dtype=np.float64)
    return T2 @ R @ T1


def rotational_symmetry_order(img: np.ndarray,
                              center: tuple | None = None) -> int:
    """
    Desenin kendi dönme simetrisi katı: 4 (90°), 2 (180°) ya da 1 (yok).

    Görüntü merkez etrafında döndürülüp kendisiyle karşılaştırılır. Yüksek
    korelasyon "bu desen o açı kadar döndürülünce kendine benziyor" demektir
    — o zaman ölçülen dönme ancak 360/kat modülünde anlamlıdır.

    Yalnızca merkeze sığan DAİRE içi karşılaştırılır; köşeler dönmede
    görüntüden çıkar ve karşılaştırmayı bozar.
    """
    if img is None or img.ndim != 2:
        return 1
    h, w = img.shape[:2]
    c = center if center is not None else ((w - 1) / 2.0, (h - 1) / 2.0)
    r = min(c[0], c[1], w - 1 - c[0], h - 1 - c[1])
    if r < 20:
        return 1
    yy, xx = np.mgrid[0:h, 0:w]
    mask = ((xx - c[0]) ** 2 + (yy - c[1]) ** 2) < r * r
    base = img[mask].astype(np.float64)
    base -= base.mean()
    nb = np.linalg.norm(base)
    if nb < 1e-9:
        return 1

    def self_ncc(angle: float) -> float:
        M = cv2.getRotationMatrix2D((float(c[0]), float(c[1])), angle, 1.0)
        rot = cv2.warpAffine(img, M, (w, h))[mask].astype(np.float64)
        rot -= rot.mean()
        nr = np.linalg.norm(rot)
        return float(base @ rot / (nb * nr)) if nr > 1e-9 else 0.0

    if self_ncc(90.0) >= SYMMETRY_NCC_MIN and self_ncc(270.0) >= SYMMETRY_NCC_MIN:
        return 4
    if self_ncc(180.0) >= SYMMETRY_NCC_MIN:
        return 2
    return 1


def _score_alignment(gt: np.ndarray, det: np.ndarray, M: np.ndarray) -> float:
    """
    Bir dönüşümün ne kadar iyi olduğunu ölçer: warp edilmiş GT ile dedektörün
    ÖRTÜŞEN bölgedeki normalize korelasyonu (-1..1).

    Yalnızca geçerli (warp sonrası dolu) pikseller sayılır — yoksa boş siyah
    alanlar korelasyonu sahte biçimde yükseltir. Bu skor, aday varyantlar
    arasında seçim yapmanın DESENDEN BAĞIMSIZ ölçütüdür.
    """
    h, w = det.shape[:2]
    warped = cv2.warpPerspective(gt, M, (w, h), flags=cv2.INTER_LINEAR,
                                 borderValue=0)
    valid = cv2.warpPerspective(np.ones_like(gt), M, (w, h),
                                flags=cv2.INTER_NEAREST, borderValue=0) > 0.5
    if valid.sum() < 0.05 * h * w:
        return -1.0
    a = warped[valid].astype(np.float64)
    b = det[valid].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    da, db = np.linalg.norm(a), np.linalg.norm(b)
    if da < 1e-9 or db < 1e-9:
        return -1.0
    return float(np.dot(a, b) / (da * db))


def _coarse_one(gt: np.ndarray, det: np.ndarray) -> tuple:
    """
    Tek varyant için kaba hizalama. (ölçek, dönme, tx, ty, güven, matris).

    Adımlar:
      1. Her iki görüntünün genlik spektrumu (çeviriden bağımsız).
      2. Log-polar dönüşüm -> ölçek ve dönme, çeviriye dönüşür.
      3. Faz korelasyonu -> log-polar kayma.
      4. Kayma -> ölçek ve dönme açısı.
      5. Ölçek+dönme geri uygulanıp faz korelasyonu ile çeviri ölçülür.
    """
    # Her iki görüntüyü aynı boyuta getir — FFT karşılaştırması için şart.
    #
    # DİKKAT: Burada ÖLÇEKLEME yapılır, sıfırla doldurma DEĞİL. İki görüntü
    # çok farklı çözünürlükte olabilir (GT 894x730, dedektör 1600x1600 gibi).
    # Küçük olanı büyük bir tuvalin ortasına oturtmak, etrafında dev bir sıfır
    # çerçevesi bırakır; bu yapay kenar spektrumu domine eder ve gerçek ölçek
    # farkı hiç görünmez olur (ölçüm 1.0'a yapışır).
    #
    # Ölçekleme ise ölçek farkını YOK ETMEZ, yalnızca BİLİNEN bir çarpanla
    # kaydırır — sonuçta geri çıkarılır (bkz. `pre_scale`).
    # EN-BOY ORANI KORUNUR. İki görüntüyü ortak kareye ayrı ayrı gerdirmek
    # (anizotropik resize) en-boy oranları farklı olduğunda deseni EZER:
    # GT 1280x1024 (oran 1.25) ile kırpılmış bir dedektör şeridi 256x1022
    # (oran 0.25) karşılaştırıldığında şerit yatayda 5 kat gerilir, çemberler
    # elipse döner ve hizalama tamamen çöker.
    #
    # Bunun yerine her görüntü KENDİ oranını koruyarak ölçeklenir ve ortak
    # tuvalin ortasına oturtulur. Doldurma burada zararsızdır: her iki
    # görüntüye de aynı işlem uygulandığı için spektrumdaki kenar etkisi
    # ortaktır ve korelasyonu tek yönde saptırmaz (bkz. `pre_scale`).
    H_ref = int(cv2.getOptimalDFTSize(max(gt.shape[0], det.shape[0])))
    W_ref = int(cv2.getOptimalDFTSize(max(gt.shape[1], det.shape[1])))

    def fit_keep_aspect(img):
        """Oranı koruyarak ölçekler, ortak tuvalin ortasına yerleştirir."""
        ih, iw = img.shape[:2]
        s = min(W_ref / iw, H_ref / ih)
        nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
        interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
        small = cv2.resize(img, (nw, nh), interpolation=interp)
        canvas = np.zeros((H_ref, W_ref), np.float32)
        oy, ox = (H_ref - nh) // 2, (W_ref - nw) // 2
        canvas[oy:oy + nh, ox:ox + nw] = small
        return canvas, s

    g_r, s_g = fit_keep_aspect(gt)
    d_r, s_d = fit_keep_aspect(det)

    # Uygulanan ölçekler izotropik olduğu için çarpan doğrudan oranlarıdır.
    pre_scale = float(s_d / s_g) if s_g > 0 else 1.0

    g = _apodize(g_r)
    d = _apodize(d_r)

    # 1-2. Genlik spektrumu -> log-polar
    lp_g, M_log = _logpolar(_fft_magnitude(g))
    lp_d, _ = _logpolar(_fft_magnitude(d))

    # 3. Log-polarda kayma: x -> ölçek (log yarıçap), y -> dönme (açı)
    sx, sy, resp = _phase_correlate(lp_g, lp_d)

    # 4. Kaymayı fiziksel değerlere çevir.
    # warpPolar açıyı satırlara yayar: satır sayısı 360 dereceye karşılık gelir.
    #
    # İşaret: phaseCorrelate(a, b) "b'yi a'ya götüren" kaymayı verir. Burada
    # a = GT spektrumu, b = dedektör spektrumu. Dedektör GT'den s kat BÜYÜKSE
    # spektrumu 1/s kat KÜÇÜLÜR (Fourier ölçek karşıtlığı) ve log-polarda
    # SOLA kayar. Dolayısıyla gt->det ölçeği exp(-sx/M) olur.
    # Ölçüm yeniden boyutlandırılmış uzayda yapıldı; oradaki ölçek farkını
    # ORİJİNAL görüntülerin ölçek farkına çevirmek için pre_scale ile böleriz.
    scale_resized = float(np.exp(-sx / M_log)) if M_log > 0 else 1.0
    scale = scale_resized / pre_scale if pre_scale > 0 else scale_resized
    rot = -sy * 360.0 / lp_g.shape[0]

    # Genlik spektrumu 180 derece simetriktir: dönme açısı ancak 180'e kadar
    # belirlenebilir. İki adayı da deneyip skoru yükseği seçeriz.
    if not (0.02 < scale < 50.0):
        scale = 1.0

    cs = ((gt.shape[1] - 1) / 2.0, (gt.shape[0] - 1) / 2.0)
    cd = ((det.shape[1] - 1) / 2.0, (det.shape[0] - 1) / 2.0)

    # Denenecek dönme adayları.
    #
    # Normalde faz korelasyonunun verdiği açı ve onun 180° eşi yeterlidir
    # (genlik spektrumu 180° simetriktir). AMA DAİRESEL SİMETRİK DESENLERDE
    # (eş merkezli çember) spektrum da dairesel simetrik olur; log-polarda
    # dönme ekseninde tepe OLUŞMAZ ve faz korelasyonu güvenle 0° okur.
    # Ölçülen örnek: Hydra çemberi, gerçek dönme 136°, faz korelasyonu 0.09°
    # (güven 0.126) — tamamen yanlış ama sessizce kabul edilirdi.
    #
    # Bu yüzden güven düşükse TÜM AÇI ARALIĞI taranır. Tarama pahalıdır
    # ama yalnızca simetrik/zayıf sinyalli durumlarda devreye girer.
    cand_angles = [rot, rot + 180.0]
    if resp < ROT_CONF_MIN:
        cand_angles += list(np.arange(0.0, 360.0, ROT_SCAN_STEP))

    scored = []
    for rot_cand in cand_angles:
        # 5. Ölçek+dönmeyi uygula, kalan çeviriyi faz korelasyonuyla ölç
        M0 = _similarity_matrix(scale, rot_cand, 0.0, 0.0, cs, cd)
        warped = cv2.warpPerspective(gt, M0, (det.shape[1], det.shape[0]))
        tx, ty, tresp = _phase_correlate(_apodize(warped), _apodize(det))
        M = _similarity_matrix(scale, rot_cand, tx, ty, cs, cd)
        score = _score_alignment(gt, det, M)
        scored.append((scale, rot_cand, tx, ty, score, M, tresp))

    scored.sort(key=lambda c: c[4], reverse=True)
    best = scored[0]
    # Tarama yapıldıysa en iyi birkaç adayı da taşı — nihai kararı ECC verir.
    ambiguous = resp < ROT_CONF_MIN
    cands = [c[5] for c in scored[:ROT_TOPK]] if ambiguous else [best[5]]
    return best + (cands, ambiguous)


def coarse_align(gt: np.ndarray, det: np.ndarray,
                 try_mirrors: bool = True) -> CoarseResult:
    """
    Kaba hizalama — desen bilmeden çeviri + ölçek + dönme bulur.

    SIFT'ten farkı: hiçbir özellik aranmaz, tanımlayıcı eşlenmez. Bu yüzden
    kendine-benzer desenlerde (Siemens star, eş merkezli çember) dejenere
    çözüm ÜRETİLEMEZ — çözüm uzayı zaten 4 parametreyle sınırlıdır ve
    görüntüyü noktaya çökerten bir dönüşüm yoktur.

    try_mirrors: dedektörün 4 ayna varyantı da denenir, en yüksek korelasyon
    skorunu veren seçilir.
    """
    res = CoarseResult()
    g = _prep(gt)
    cands = variants(_prep(det)) if try_mirrors else {"raw": _prep(det)}

    best = None
    for vname, dv in cands.items():
        try:
            (scale, rot, tx, ty, score, M, resp,
             rot_cands, rot_amb) = _coarse_one(g, dv)
        except cv2.error as e:                          # noqa: BLE001
            res.messages.append(f"{vname}: hizalama hatası ({e})")
            continue
        if best is None or score > best[0]:
            best = (score, vname, scale, rot, tx, ty, M, resp,
                    rot_cands, rot_amb)

    if best is None:
        res.messages.append("Kaba hizalama başarısız — hiçbir varyant çözülemedi.")
        return res

    score, vname, scale, rot, tx, ty, M, resp, rot_cands, rot_amb = best

    # Dönme açısını -180..+180 aralığına indir.
    #
    # DİKKAT: Burada ±90'a KATLAMA YAPILMAZ. Eski davranış 136 dereceyi
    # 44 dereceye katlıyordu ve gerçek yönelim bilgisi kayboluyordu.
    # ±90 indirgemesi yalnızca "eksen yönü" anlamlı olduğunda (elips ana
    # ekseni gibi) doğrudur; bir GÖRÜNTÜNÜN dönmesi 0..360 arasında
    # anlamlıdır ve 136 ile 44 farklı yönelimlerdir.
    # Not: `matrix` zaten gerçek açıyla kurulmuştur; bu yalnızca rapor değeri.
    while rot > 180:
        rot -= 360
    while rot < -180:
        rot += 360

    res.ok = score > 0.1
    res.variant = vname
    res.scale = scale
    res.rotation_deg = rot
    res.tx, res.ty = tx, ty
    res.response = score
    res.matrix = M
    res.rot_candidates = rot_cands
    res.rot_ambiguous = bool(rot_amb)
    if rot_amb:
        # Bu bir arıza değil, YÖNTEM SEÇİMİDİR: kestirme yol (faz
        # korelasyonu) bu desende çalışmadığı için pahalı ama sağlam yol
        # (tam açı taraması + ECC) koşuldu. Sonuç doğru, yalnızca daha
        # uzun sürüyor — o yüzden uyarı değil bilgi olarak raporlanır.
        res.messages.append(
            "Bilgi: dönme faz korelasyonuyla okunamadı (dairesel simetrik "
            "desen) — tam açı taraması yapıldı, nihai seçim ECC ile.")
    if not res.ok:
        res.messages.append(
            f"Kaba hizalama güveni düşük (korelasyon {score:.3f}) — "
            "görüntülerde ortak doku az olabilir.")
    return res


# --------------------------------------------------------------------------
# 2. Kademe — ince hizalama (ECC, alt-piksel homografi)
# --------------------------------------------------------------------------

@dataclass
class RefineResult:
    """ECC ince hizalama çıktısı."""
    ok: bool = False
    homography: np.ndarray | None = None    # 3x3, gt -> det
    correlation: float = 0.0                # ECC skoru (0..1)
    iterations: int = 0
    variant: str = "raw"
    messages: list[str] = field(default_factory=list)


def _pyramid(img: np.ndarray, levels: int) -> list:
    """Görüntü piramidi — en kabadan en inceye sıralı."""
    out = [img]
    for _ in range(levels - 1):
        out.append(cv2.pyrDown(out[-1]))
    return out[::-1]


def refine_ecc(gt: np.ndarray, det: np.ndarray,
               init: np.ndarray | None = None,
               levels: int = 3,
               iterations: int = 200,
               eps: float = 1e-7,
               motion: str = "homography") -> RefineResult:
    """
    ECC ile alt-piksel homografi.

    ECC (Enhanced Correlation Coefficient) yoğunlukları doğrudan hizalar ve
    aydınlatma farkına karşı bağışıktır — parlaklık/kontrast farkını modelin
    içinde soğurur. Bu, GT'nin ideal desen, dedektörün ise gerçek pozlamalı
    görüntü olduğu bu projede kritiktir.

    Piramitli koşar: önce kaba seviyede çözer, çözümü bir üst seviyeye ölçekler.
    Böylece hem hızlanır hem de yerel minimuma takılma riski azalır.

    init: başlangıç tahmini (kaba kademeden gelen 3x3). Yoksa birim matris.
    motion: "homography" (8 serbestlik, tilt dahil) | "affine" (6) |
            "euclidean" (4). Tilt ölçülecekse homografi gerekir.
    """
    res = RefineResult()
    g = _prep(gt)
    d = _prep(det)

    warp_mode = {
        "homography": cv2.MOTION_HOMOGRAPHY,
        "affine": cv2.MOTION_AFFINE,
        "euclidean": cv2.MOTION_EUCLIDEAN,
    }.get(motion, cv2.MOTION_HOMOGRAPHY)

    H = np.eye(3, dtype=np.float32) if init is None else \
        np.asarray(init, dtype=np.float32).copy()

    # Piramit seviyeleri: en küçük kenar 64 px'in altına inmesin
    min_side = min(g.shape[0], g.shape[1], d.shape[0], d.shape[1])
    levels = max(1, min(levels, int(np.log2(max(min_side / 64.0, 1))) + 1))

    gp = _pyramid(g, levels)
    dp = _pyramid(d, levels)

    # Homografiyi en kaba seviyeye indir: çeviri terimleri ölçekle küçülür.
    s0 = 1.0 / (2 ** (levels - 1))
    S = np.array([[s0, 0, 0], [0, s0, 0], [0, 0, 1]], dtype=np.float32)
    Hc = S @ H @ np.linalg.inv(S)

    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                iterations, eps)
    cc = 0.0
    for lvl, (gi, di) in enumerate(zip(gp, dp)):
        if warp_mode != cv2.MOTION_HOMOGRAPHY:
            W = Hc[:2, :].astype(np.float32).copy()
        else:
            W = Hc.astype(np.float32).copy()
        try:
            cc, W = cv2.findTransformECC(gi, di, W, warp_mode, criteria,
                                         None, 5)
        except cv2.error as e:                          # noqa: BLE001
            res.messages.append(
                f"ECC seviye {lvl} yakınsamadı ({e.err.strip() if hasattr(e, 'err') else e})")
            break

        Hc = np.eye(3, dtype=np.float32)
        if warp_mode != cv2.MOTION_HOMOGRAPHY:
            Hc[:2, :] = W
        else:
            Hc = W

        # Bir sonraki (iki kat büyük) seviyeye ölçekle
        if lvl < levels - 1:
            U = np.array([[2, 0, 0], [0, 2, 0], [0, 0, 1]], dtype=np.float32)
            Hc = U @ Hc @ np.linalg.inv(U)

    if Hc is None or not np.all(np.isfinite(Hc)):
        res.messages.append("ECC geçersiz homografi üretti.")
        return res

    Hc = Hc / Hc[2, 2] if abs(Hc[2, 2]) > 1e-12 else Hc
    res.homography = Hc.astype(np.float64)
    res.correlation = float(cc)
    res.ok = bool(cc > 0.3)
    if not res.ok:
        res.messages.append(
            f"ECC güveni düşük (korelasyon {cc:.3f}) — hizalama güvenilmez.")
    return res


# --------------------------------------------------------------------------
# 3. Kademe — piksel piksel kalıntı (distorsiyon alanı)
# --------------------------------------------------------------------------

# Farnebäck optik akış pencere boyutu (px). Kalıntı ölçümünün uzamsal
# çözünürlüğünü ve geçerlilik maskesinin aşındırma yarıçapını birlikte
# belirler; ikisi tutarlı kalsın diye tek yerde tanımlıdır.
WINSIZE = 25

# Ayna varyantları arasındaki ECC farkı bunun altındaysa seçim keyfî sayılır.
# Gerçek bir ayrımda fark 0.1 mertebesindedir (ölçüldü: 0.759 vs 0.868);
# dejenere durumda tam 0.0000 çıkar.
MIRROR_MARGIN_MIN = 0.01

# Desenin KENDİ dönme simetrisi bu eşiğin üstündeyse "kendini tekrar ediyor"
# sayılır. Ölçülen: FOV deseni 90/180/270°'de kendisiyle 0.965 korelasyon
# veriyor (aynalanmış hâliyle 0.896 — yani ayna AYIRT EDİLEBİLİR, dönme
# değil). Böyle bir desende roll ancak 360/kat kadar bir modül içinde
# bilinebilir; bu bir ölçüm hatası değil, desenin bilgi içermemesidir.
SYMMETRY_NCC_MIN = 0.90

# Log-polar faz korelasyonunun güveni bunun altındaysa dönme okunamamış
# sayılır ve açı taraması devreye girer. Dairesel simetrik desenlerde
# (eş merkezli çember) spektrumda dönme tepesi oluşmaz; güven ~0.13'te kalır
# ve okunan açı anlamsızdır.
ROT_CONF_MIN = 0.25
ROT_SCAN_STEP = 10.0     # tarama adımı (derece); ECC kalanını toparlar
ROT_TOPK = 6             # ECC'ye taşınacak en iyi aday açı sayısı


@dataclass
class ResidualResult:
    """
    Homografi sonrası kalan piksel-piksel sapma alanı.

    Homografi İDEAL bir projektif dönüşümdür: düz çizgiyi düz çizgiye götürür.
    Gerçek mercek götürmez. Homografi ölçek/kadraj/dönme/tilt'i soğurduktan
    sonra geriye kalan sapma DİSTORSİYONDUR. Tek sayı değil, alan.
    """
    ok: bool = False
    flow: np.ndarray | None = None        # (H, W, 2) float32 — piksel başına (dx, dy)
    magnitude: np.ndarray | None = None   # (H, W) float32 — sapma büyüklüğü (px)
    valid: np.ndarray | None = None       # (H, W) bool — ölçümün geçerli olduğu yerler
    center: tuple = (0.0, 0.0)            # distorsiyon merkezi (det piksel)

    # Radyal distorsiyon profili (merkezden uzaklığa göre ortalama radyal sapma)
    radius_px: np.ndarray | None = None
    radial_dev_px: np.ndarray | None = None
    radial_count: np.ndarray | None = None

    # Özet istatistikler (px)
    max_dev_px: float = 0.0
    rms_dev_px: float = 0.0
    p95_dev_px: float = 0.0
    outlier_frac: float = 0.0     # eşleşme belirsizliğine düşen piksel oranı

    # Radyal distorsiyon modeli:  dr(r) = a1*r + a3*r^3 + a5*r^5
    # a1 artık ÖLÇEK serbestliğidir (homografi ölçeği tam soğuramamış olabilir),
    # a3/a5 gerçek distorsiyon katsayılarıdır.
    k_scale: float = 0.0          # a1 — birimsiz artık ölçek
    k3: float = 0.0               # a3 — 3. derece (klasik k1 distorsiyonu)
    k5: float = 0.0               # a5 — 5. derece
    model_ok: bool = False
    fit_rms_px: float = 0.0       # modelin profile uyum hatası
    edge_distortion_px: float = 0.0   # ölçek çıkarıldıktan sonra kenardaki saf distorsiyon
    edge_distortion_pct: float = 0.0  # aynı değerin kenar yarıçapına oranı (%)
    radial_fraction: float = 0.0      # kalıntının radyal modelle açıklanan oranı (0..1)

    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return "ölçülemedi"
        return (f"RMS {self.rms_dev_px:.2f} px, "
                f"%95 {self.p95_dev_px:.2f} px, "
                f"en büyük {self.max_dev_px:.2f} px")

    # Radyallik bu oranın altındaysa kalıntı distorsiyon sayılmaz. Eşik
    # yüksek tutulur: gerçek distorsiyon 1.00'a çok yakın radyallik verir
    # (sentetik doğrulamada 0.999-1.000), keskinlik/örnekleme artefaktı ise
    # radyal modele kısmen uysa da 0.9'un altında kalır.
    RADIAL_MIN = 0.90
    # Kalıntı bu büyüklüğün altındaysa "distorsiyon yok" denir; sıfıra yakın
    # bir alanın şekline bakmak anlamsızdır.
    NEGLIGIBLE_PX = 0.5

    @property
    def negligible(self) -> bool:
        """Kalıntı ölçüm gürültüsü seviyesinde mi."""
        return bool(self.ok and self.rms_dev_px < self.NEGLIGIBLE_PX)

    @property
    def distortion_trustworthy(self) -> bool:
        """
        Distorsiyon sayısı okunmaya değer mi.

        Radyallik düşükse kalıntı büyük olsa bile distorsiyon DEĞİLDİR;
        tipik olarak iki görüntünün keskinlik/örnekleme farkıdır.
        """
        return bool(self.model_ok and not self.negligible
                    and self.radial_fraction >= self.RADIAL_MIN)

    def distortion_summary(self) -> str:
        """Ölçek serbestliğinden ARINDIRILMIŞ distorsiyon özeti."""
        if not self.model_ok:
            return "ölçülemedi"
        # Önce BÜYÜKLÜK: kalıntı gürültü seviyesindeyse şeklini sormak
        # anlamsızdır — "radyal değil" demek yanlış teşhis olur.
        if self.negligible:
            return (f"distorsiyon yok (kalıntı RMS {self.rms_dev_px:.2f} px, "
                    f"gürültü seviyesinde)")
        if not self.distortion_trustworthy:
            # İki farklı red sebebi var ve kullanıcıyı FARKLI yere yollarlar:
            # kalıntı tamamen yapısızsa artefakt, kısmen radyalse "belki
            # distorsiyon ama ayırt edilemiyor". İkisini aynı cümleyle
            # raporlamak, ikinci durumda gerçek bir bulguyu gömer.
            if self.radial_fraction >= 0.35:
                return (f"kesin değil — kalıntı kısmen radyal "
                        f"(radyallik {self.radial_fraction:.2f}, eşik "
                        f"{self.RADIAL_MIN:.2f}); eğilim "
                        f"{'fıçı' if self.edge_distortion_px < 0 else 'yastık'}"
                        f" yönünde ({self.edge_distortion_px:+.2f} px) ama "
                        f"gürültüden ayrılmıyor")
            return (f"ölçülemedi — kalıntı radyal değil "
                    f"(radyallik {self.radial_fraction:.2f}); "
                    f"büyük olasılıkla keskinlik/örnekleme farkı, distorsiyon değil")
        kind = "fıçı (barrel)" if self.edge_distortion_px < 0 else "yastık (pincushion)"
        if abs(self.edge_distortion_pct) < 0.05:
            kind = "ihmal edilebilir"
        return (f"{kind}: kenarda {self.edge_distortion_px:+.2f} px "
                f"({self.edge_distortion_pct:+.2f} %), "
                f"radyallik {self.radial_fraction:.2f}")


def residual_flow(gt: np.ndarray, det: np.ndarray, H: np.ndarray,
                  variant: str = "raw",
                  center: tuple | None = None,
                  smooth_sigma: float = 1.0) -> ResidualResult:
    """
    Homografi uygulandıktan SONRA kalan kaymayı HER PİKSEL için ölçer.

    Yöntem: GT'yi homografiyle dedektör düzlemine warp et, sonra warp edilmiş
    GT ile dedektör arasında yoğun optik akış (Farnebäck) hesapla. Akış vektörü
    o pikseldeki artık sapmadır.

    Farnebäck seçildi çünkü desen bilmez — her piksel çevresindeki yoğunluk
    yüzeyini polinomla modeller. Köşe/kenar aramaz, dolayısıyla çember, yıldız,
    ızgara ya da rastgele doku farketmez.

    variant: dedektörün ayna varyantı (kaba kademeden gelir); homografi bu
             varyant için çözüldüğü için aynı varyant uygulanmalıdır.
    center:  radyal profilin merkezi. Verilmezse dedektör görüntüsünün merkezi
             kullanılır (optik eksenin sensör merkezinde olduğu varsayımı).
    """
    res = ResidualResult()
    if H is None or not np.all(np.isfinite(H)):
        res.messages.append("Homografi yok — kalıntı ölçülemez.")
        return res

    d_full = variants(det).get(variant, det)
    g = _prep(gt)
    d = _prep(d_full)

    h, w = d.shape[:2]
    warped = cv2.warpPerspective(g, H, (w, h), flags=cv2.INTER_LINEAR,
                                 borderValue=0)
    valid = cv2.warpPerspective(np.ones_like(g), H, (w, h),
                                flags=cv2.INTER_NEAREST, borderValue=0) > 0.5

    # Kenarları içeri çek: warp sınırındaki yarım pikseller sahte akış üretir.
    # Aşındırma yarıçapı akış PENCERESİNE bağlıdır — Farnebäck her pikselde
    # winsize genişliğinde bir komşuluğa bakar, dolayısıyla sınıra winsize/2'den
    # yakın her piksel warp dışındaki boşluğu "görür" ve sahte akış üretir.
    # Sert kenarlı desenlerde (satranç tahtası) bu kirlenme belirgindir.
    erode_px = int(WINSIZE // 2) | 1
    valid = cv2.erode(valid.astype(np.uint8),
                      np.ones((erode_px, erode_px), np.uint8)).astype(bool)
    if valid.sum() < 0.02 * h * w:
        res.messages.append("Örtüşen alan çok küçük — kalıntı ölçülemez.")
        return res

    # Optik akış uint8 ister
    a = np.clip(warped * 255, 0, 255).astype(np.uint8)
    b = np.clip(d * 255, 0, 255).astype(np.uint8)

    flow = cv2.calcOpticalFlowFarneback(
        a, b, None,
        pyr_scale=0.5, levels=4, winsize=WINSIZE, iterations=5,
        poly_n=7, poly_sigma=1.5, flags=0)

    if smooth_sigma > 0:
        flow = cv2.GaussianBlur(flow, (0, 0), smooth_sigma)

    mag = np.linalg.norm(flow, axis=2)
    flow[~valid] = 0.0
    mag[~valid] = 0.0

    res.flow = flow.astype(np.float32)
    res.magnitude = mag.astype(np.float32)
    res.valid = valid

    vals = mag[valid]
    if vals.size == 0:
        res.messages.append("Geçerli piksel kalmadı.")
        return res

    # Özet istatistikler AYKIRI DEĞERE DAYANIKLI olmalıdır. Periyodik
    # desenlerde (satranç, ızgara) optik akış tek tük pikselde komşu periyoda
    # atlar; bunlar gerçek distorsiyon değil eşleşme belirsizliğidir. Ham RMS
    # bu birkaç pikselden şişer ve temiz bir ölçümü "distorsiyonlu" gösterir.
    # Bu yüzden RMS %99'luk dilimle sınırlanmış değerlerden hesaplanır;
    # aykırı piksel sayısı ayrıca raporlanır.
    hi = float(np.percentile(vals, 99))
    clipped = np.minimum(vals, max(hi, 1e-6))
    res.max_dev_px = float(vals.max())
    res.rms_dev_px = float(np.sqrt((clipped ** 2).mean()))
    res.p95_dev_px = float(np.percentile(vals, 95))
    res.outlier_frac = float((vals > max(hi * 3.0, 1.0)).mean())

    # --- Radyal profil ---
    cx, cy = center if center is not None else ((w - 1) / 2.0, (h - 1) / 2.0)
    res.center = (float(cx), float(cy))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    r = np.hypot(dx, dy)
    # Radyal bileşen: sapmanın merkezden dışa doğru olan izdüşümü.
    # Distorsiyon radyaldir; teğetsel bileşen çoğunlukla gürültü/merkez hatası.
    with np.errstate(invalid="ignore", divide="ignore"):
        ur = np.where(r > 1e-6, dx / r, 0.0)
        vr = np.where(r > 1e-6, dy / r, 0.0)
    radial = flow[:, :, 0] * ur + flow[:, :, 1] * vr

    nbins = 64
    rmax = float(r[valid].max()) if valid.any() else 0.0
    if rmax > 0:
        idx = np.clip((r / rmax * nbins).astype(int), 0, nbins - 1)
        sums = np.bincount(idx[valid], weights=radial[valid], minlength=nbins)
        cnts = np.bincount(idx[valid], minlength=nbins).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            prof = np.where(cnts > 0, sums / np.maximum(cnts, 1), np.nan)
        res.radius_px = (np.arange(nbins) + 0.5) * rmax / nbins
        res.radial_dev_px = prof
        res.radial_count = cnts
        _fit_radial_model(res, rmax)

    res.ok = True
    return res


def _fit_radial_model(res: ResidualResult, rmax: float,
                      min_count: int = 200) -> None:
    """
    Radyal profile  dr(r) = a1*r + a3*r^3 + a5*r^5  modelini uydurur.

    NEDEN a1 (ölçek) TERİMİ VAR
    ---------------------------
    Homografi "en iyi uyum" ararken distorsiyonlu bir görüntüyü hafifçe
    büyütüp/küçültüp toplam hatayı azaltır. Bu, kalıntı profilini merkezde
    yukarı kaydırır: ölçülen eğri artık saf distorsiyon değil,
    "distorsiyon + artık ölçek" toplamıdır.

    Ground truth'un ölçeği zaten bilinmediği için bu serbestlik KAÇINILMAZDIR
    ve fiziksel olarak doğrudur. Ama distorsiyonu raporlarken ikisini
    ayırmazsak distorsiyon olduğundan KÜÇÜK görünür. a1'i modele dahil edip
    distorsiyonu yalnızca a3/a5'ten okuyarak bu karışmayı önleriz.

    Ağırlık olarak bin başına piksel sayısı kullanılır — dış halkalarda çok,
    merkeze yakın az piksel vardır.
    """
    r = res.radius_px
    d = res.radial_dev_px
    c = res.radial_count
    if r is None or d is None or c is None:
        return
    m = np.isfinite(d) & (c >= min_count)
    if m.sum() < 6 or rmax <= 0:
        res.messages.append("Radyal profil model uydurmak için yetersiz.")
        return

    x = r[m] / rmax                 # 0..1 normalize yarıçap (sayısal kararlılık)
    y = d[m]
    w = np.sqrt(c[m])

    A = np.stack([x, x ** 3, x ** 5], axis=1)
    try:
        coef, *_ = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)
    except np.linalg.LinAlgError:
        res.messages.append("Radyal model çözülemedi.")
        return

    a1, a3, a5 = (float(v) for v in coef)
    res.k_scale, res.k3, res.k5 = a1, a3, a5
    fit = A @ coef
    res.fit_rms_px = float(np.sqrt((((fit - y) ** 2) * c[m]).sum() / c[m].sum()))

    # Kenardaki SAF distorsiyon: ölçek terimi çıkarılmış hali (x = 1)
    res.edge_distortion_px = a3 + a5
    res.edge_distortion_pct = 100.0 * (a3 + a5) / rmax if rmax > 0 else 0.0
    res.model_ok = True

    # --- Radyallik denetimi ---
    # Distorsiyon RADYAL bir olgudur: sapma merkezden uzaklığa bağlıdır, hangi
    # yönde bakıldığına değil. Ölçülen alanın radyal modelle AÇIKLANAN kısmı
    # küçükse, kalıntı distorsiyon değil başka bir şeydir — tipik olarak iki
    # görüntünün keskinlik/örnekleme farkı (GT 894x730 iken dedektör 1600x1600
    # olduğunda ince harfler ve kamalar birebir örtüşmez ve optik akış bunu
    # kayma sanır). O durumda sapma desenin AYRINTILI bölgelerine yığılır,
    # düzgün halkalar oluşturmaz.
    #
    # Bu oran raporlanmazsa artefakt "fıçı distorsiyonu" diye okunur.
    total_var = float((((y - y.mean()) ** 2) * c[m]).sum() / c[m].sum())
    if total_var > 1e-12:
        res.radial_fraction = float(max(0.0, 1.0 - (res.fit_rms_px ** 2) / total_var))
    else:
        res.radial_fraction = 0.0


# --------------------------------------------------------------------------
# 2B. Kademe — ÖLÇÜME GİREN BÖLGE (hizalamayı GT'nin tamamıyla yapma)
# --------------------------------------------------------------------------
#
# ECC'nin şablonu ground truth'un TAMAMIDIR ve şablonun her pikseli
# korelasyon katsayısına tam ağırlıkla girer. Oysa GT'nin büyük bir kısmı
# ölçüme hiç katılmaz:
#
#   * dedektöre DÜŞMEYEN kısım — kadraj dışında kalır, karşılığı yoktur;
#   * DESEN İÇERMEYEN kısım — referans ekranın boş kenarı, sabit bir zemin.
#
# Ölçülen bir çiftte (STOS deseni, CMV4000): GT 1280×1024, deseni taşıyan
# daire r=404 (çerçevenin %39'u), dedektöre düşen bölge çerçevenin %81'i.
# Yani şablonun %61'i sabit siyah. Sabit bölge korelasyonun payına hiç
# katkı vermez ama paydasında durur; hedef fonksiyonu düzleştirir.
#
# Bu kademe, hizalamayı bu iki kısıtın KESİŞİMİNDE tekrar çözer. Bölge
# ölçülmüş homografiden çıkar (dedektöre düşen kısım ancak H bilinince
# belli olur), o yüzden ikinci geçiştir — birinci geçiş bölgeyi bulmak
# için, ikincisi orada çözmek için.


def content_bbox(img: np.ndarray, pad: int = WINSIZE) -> tuple | None:
    """
    Desenin kapladığı kutu (x, y, w, h) — zeminden sapan pikseller.

    Eşik ZEMİNDEN SAPMA üzerinden kurulur, parlaklık üzerinden değil:
    desen siyah zeminde beyaz da olabilir (v6_inverted), beyaz zeminde
    siyah da. Medyan zemin kabul edilir, ondan belirgin sapan her piksel
    içerik sayılır.
    """
    a = img.astype(np.float32)
    bg = float(np.median(a))
    lo, hi = np.percentile(a, (1.0, 99.0))
    thr = max(8.0, 0.15 * float(hi - lo))
    ink = np.abs(a - bg) > thr
    if not ink.any():
        return None
    ys, xs = np.nonzero(ink)
    x0, x1 = int(xs.min()) - pad, int(xs.max()) + 1 + pad
    y0, y1 = int(ys.min()) - pad, int(ys.max()) + 1 + pad
    h, w = img.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def visible_bbox(gt_shape: tuple, det_shape: tuple,
                 H: np.ndarray, pad: int = WINSIZE) -> tuple | None:
    """
    Dedektör çerçevesinin GT'deki karşılığının kutusu (x, y, w, h).

    Dedektörün dört köşesi H^-1 ile GT'ye taşınır; GT çerçevesiyle
    kesiştirilen bölgenin kutusu döner. GT'nin bu kutunun dışında kalan
    kısmı hiçbir dedektör pikseline karşılık gelmez.
    """
    if H is None or not np.all(np.isfinite(H)):
        return None
    try:
        Hinv = np.linalg.inv(np.asarray(H, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    dh, dw = float(det_shape[0]), float(det_shape[1])
    corners = np.array([[[0.0, 0.0]], [[dw, 0.0]], [[dw, dh]], [[0.0, dh]]],
                       dtype=np.float64)
    try:
        back = cv2.perspectiveTransform(corners, Hinv).reshape(-1, 2)
    except cv2.error:                                       # noqa: BLE001
        return None
    if not np.all(np.isfinite(back)):
        return None
    gh, gw = gt_shape[0], gt_shape[1]
    x0 = max(0, int(np.floor(back[:, 0].min())) - pad)
    y0 = max(0, int(np.floor(back[:, 1].min())) - pad)
    x1 = min(gw, int(np.ceil(back[:, 0].max())) + pad)
    y1 = min(gh, int(np.ceil(back[:, 1].max())) + pad)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def measurable_region(gt: np.ndarray, det_shape: tuple, H: np.ndarray,
                      min_gain: float = 0.05) -> tuple | None:
    """
    Hizalamanın gerçekten yapılması gereken GT bölgesi:
    (dedektöre düşen kutu) ∩ (desen içeren kutu).

    `min_gain`: kırpma GT alanının bu kadarını atmıyorsa None döner —
    ikinci bir ECC koşusu bedava değil, kazanç yoksa koşulmaz.
    """
    gh, gw = gt.shape[:2]
    boxes = [b for b in (visible_bbox(gt.shape, det_shape, H),
                         content_bbox(gt)) if b is not None]
    if not boxes:
        return None
    x0 = max(b[0] for b in boxes)
    y0 = max(b[1] for b in boxes)
    x1 = min(b[0] + b[2] for b in boxes)
    y1 = min(b[1] + b[3] for b in boxes)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    if (x1 - x0) * (y1 - y0) > (1.0 - min_gain) * gw * gh:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _score_on_region(gt: np.ndarray, det: np.ndarray, H: np.ndarray,
                     box: tuple) -> float:
    """
    İki homografiyi KARŞILAŞTIRILABİLİR biçimde puanlar.

    ECC korelasyonu bunun için kullanılamaz: kırpılmış şablonun korelasyonu
    kırpılmamışınkiyle aynı şeyi ölçmez (payda değişir), kırpılmış olan
    neredeyse her zaman yüksek çıkar. Bu yüzden her iki aday da SABİT bir
    bölgede — ölçüme giren kutuda — aynı NCC ile puanlanır.
    """
    if H is None or not np.all(np.isfinite(H)):
        return -1.0
    x, y, w, h = box
    sub = gt[y:y + h, x:x + w]
    T = np.array([[1.0, 0.0, x], [0.0, 1.0, y], [0.0, 0.0, 1.0]])
    return _score_alignment(sub, det, np.asarray(H, dtype=np.float64) @ T)


def refine_on_region(gt: np.ndarray, det: np.ndarray, H: np.ndarray,
                     box: tuple, **kw) -> RefineResult:
    """
    ECC'yi yalnızca `box` bölgesiyle tekrar koşar; sonucu TAM GT
    koordinatlarına geri çevirir.

    Kırpma koordinat sistemini kaydırdığı için homografi öteleme matrisiyle
    sarılır:  H_tam = H_kırpık · T^-1,  T = kırpık -> tam ötelemesi.
    Bu sarma unutulursa homografi kutunun köşesi kadar kayar ve decenter
    tamamen yanlış çıkar.
    """
    x, y, w, h = box
    sub = gt[y:y + h, x:x + w]
    T = np.array([[1.0, 0.0, x], [0.0, 1.0, y], [0.0, 0.0, 1.0]])
    res = refine_ecc(sub, det, init=np.asarray(H, dtype=np.float64) @ T, **kw)
    if res.homography is not None:
        res.homography = res.homography @ np.linalg.inv(T)
    return res


# --------------------------------------------------------------------------
# Tam zincir — tek çağrı
# --------------------------------------------------------------------------

@dataclass
class DenseResult:
    """Üç kademenin birleşik çıktısı."""
    ok: bool = False
    coarse: CoarseResult | None = None
    refine: RefineResult | None = None
    residual: ResidualResult | None = None
    tilt: optics.TiltResult | None = None
    # Ayna varyantları arasındaki ECC farkı ihmal edilebilirse seçim
    # keyfîdir. Ölçülen büyüklükleri (decenter, ölçek, kapsama) etkilemez
    # ama ROLL bundan etkilenebilir — kullanıcı sayıya güvenmeden önce
    # bunu bilmeli. Dar kırpılmış görüntülerde tipiktir.
    mirror_ambiguous: bool = False
    mirror_margin: float = 0.0     # en iyi iki varyantın ECC farkı
    # Desenin kendi dönme simetrisi (4 = 90°'de kendini tekrar ediyor).
    # Böyle bir desende roll ancak `rotation_modulus_deg` modülünde
    # bilinebilir — bu bir ölçüm eksikliği değil, desenin sınırıdır.
    symmetry_order: int = 1
    # Ölçüme giren bölge (bkz. 2B kademesi): hizalamanın gerçekten
    # yapıldığı GT kutusu (x, y, w, h) ve ikinci geçişin benimsenip
    # benimsenmediği. `region_used` False ise homografi GT'nin tamamıyla
    # çözülmüş demektir.
    region_box: tuple = ()
    region_used: bool = False
    region_score_before: float = float("nan")
    region_score_after: float = float("nan")
    messages: list[str] = field(default_factory=list)

    @property
    def homography(self) -> np.ndarray | None:
        return self.refine.homography if self.refine is not None else None

    @property
    def rotation_deg(self) -> float:
        """Düzlem-içi dönme (projenin `optics` konvansiyonunda)."""
        return self.tilt.in_plane_rotation_deg if self.tilt else float("nan")

    @property
    def tilt_deg(self) -> float:
        return self.tilt.total_tilt_deg if self.tilt else float("nan")

    @property
    def rotation_modulus_deg(self) -> float:
        """Roll'ün belirlenebildiği modül (simetri yoksa 360°)."""
        return 360.0 / self.symmetry_order if self.symmetry_order > 1 else 360.0

    @property
    def mirrored(self) -> bool:
        v = self.coarse.variant if self.coarse else "raw"
        return v in ("flip_h", "flip_v", "flip_both")

    @property
    def correlation(self) -> float:
        return self.refine.correlation if self.refine else 0.0


def analyze_dense(gt: np.ndarray, det: np.ndarray,
                  try_mirrors: bool = True,
                  with_residual: bool = True,
                  center: tuple | None = None) -> DenseResult:
    """
    Desen-agnostik tam analiz: kaba hizalama -> ECC -> piksel piksel kalıntı.

    `image_analysis.analyze`'ın yoğunluk tabanlı karşılığıdır; aynı
    `optics.decompose_homography` ile ayrıştırılır, dolayısıyla dönme/tilt
    değerleri SIFT yoluyla doğrudan karşılaştırılabilir.

    Girdi olarak dosya yolu değil GRİ DİZİ alır — çağıran taraf görüntüyü
    zaten yüklemiş olur ve geçici dosya yazmak gerekmez.

    HİZALAMA GT'NİN TAMAMIYLA YAPILMAZ. İki kısıt uygulanır (bkz. 2B):
    desen içermeyen kenar en baştan atılır, dedektöre düşmeyen kısım ise
    homografi bir kez çözüldükten sonra atılıp hizalama tekrarlanır.
    """
    res = DenseResult()

    # --- 0. Şablonu desenin kapladığı kutuya indir ---
    #
    # Bu kırpma homografi GEREKTİRMEZ — yalnızca GT'nin kendi içeriğine
    # bakar — o yüzden kaba kademeden ÖNCE yapılabilir. Kazancı da orada:
    # kaba kademe genlik spektrumu üzerinden çalışır ve boş bir çerçeve
    # kenarı spektrumu domine eder. Ölçülen çiftte GT'nin %61'i sabit
    # siyahtı; o kısım ne ölçeğe ne dönmeye bilgi taşır.
    #
    # Homografi kırpılmış şablon için çözülür ve EN SONDA tam GT
    # koordinatlarına geri çevrilir (H_tam = H_kırpık · T0^-1).
    gt_full = gt
    T0 = np.eye(3, dtype=np.float64)
    box0 = content_bbox(gt)
    if box0 is not None and box0[2] * box0[3] < 0.95 * gt.shape[0] * gt.shape[1]:
        _x, _y, _w, _h = box0
        gt = gt[_y:_y + _h, _x:_x + _w]
        T0 = np.array([[1.0, 0.0, _x], [0.0, 1.0, _y], [0.0, 0.0, 1.0]])
        res.messages.append(
            f"Bilgi: ground truth deseninin kapladığı kutuya kırpıldı "
            f"({_w}×{_h} px, çerçevenin "
            f"%{100.0 * _w * _h / (gt_full.shape[0] * gt_full.shape[1]):.0f}'i) "
            f"— boş kenar hizalamaya girmiyor.")
    else:
        box0 = None

    # Ayna varyantı seçimi ECC'YE bırakılır, kaba kademeye değil.
    #
    # Kaba korelasyon varyantlar arasında zayıf bir ayırt edicidir: gerçek bir
    # görüntü çiftinde raw 0.386, flip_h 0.338 verip YANLIŞ olanı seçebilir,
    # oysa ECC aynı çiftte flip_h'yi 0.868'e karşı 0.759 ile net ayırır.
    # Yanlış varyant tüm zinciri saptırdığı için seçim en ayırt edici ölçüte
    # dayanmalıdır. Bedeli her varyant için bir ECC koşusudur.
    cand_names = list(variants(det).keys()) if try_mirrors else ["raw"]

    best = None
    all_scores = []
    for vname in cand_names:
        dv = variants(det)[vname]
        c = coarse_align(gt, dv, try_mirrors=False)
        if not c.ok:
            continue
        c.variant = vname

        # Kaba kademe dönmeyi güvenle çözemediyse birden çok aday açı döner.
        # KARAR ECC'YE BIRAKILIR — kaba skor ile ECC farklı tepeleri işaret
        # edebilir. Ölçülen örnek (Hydra çemberi): kaba skor 135°'yi (0.625)
        # 315°'ye (0.640) yakın gösterirken ECC 315°'de 0.914, 135°'de 0.802
        # veriyor. Kaba skorla seçmek yanlış açıya kilitlerdi.
        cand_matrices = list(getattr(c, "rot_candidates", None) or [c.matrix])
        r = None
        for M0 in cand_matrices:
            rr = refine_ecc(gt, dv, init=M0)
            if rr.homography is None:
                continue
            if r is None or rr.correlation > r.correlation:
                r = rr
        if r is None:
            continue
        all_scores.append((r.correlation, vname))
        if best is None or r.correlation > best[0]:
            best = (r.correlation, c, r, dv)

    if best is None:
        res.messages.append("Hizalama başarısız — hiçbir ayna varyantı çözülemedi.")
        return res

    _cc, res.coarse, res.refine, det_v = best
    res.messages.extend(res.coarse.messages)
    res.messages.extend(res.refine.messages)

    # --- 2B. ÖLÇÜME GİREN BÖLGEDE YENİDEN ÇÖZ ---
    #
    # Birinci geçiş GT'nin tamamıyla yapıldı — mecburen, çünkü hangi GT
    # bölgesinin dedektöre düştüğü ancak homografi bilinince belli olur.
    # Artık belli: bölge hesaplanır ve hizalama ORADA tekrar çözülür.
    #
    # Benimseme ölçütü ECC korelasyonu DEĞİLDİR — kırpılmış şablonun
    # korelasyonu kırpılmamışınkiyle karşılaştırılamaz (bkz.
    # `_score_on_region`). İki aday da sabit bölgede aynı NCC ile
    # puanlanır ve ikinci geçiş yalnızca KESİN olarak iyileştiriyorsa
    # benimsenir; aksi hâlde birinci geçişin sonucu korunur.
    box = measurable_region(gt, det_v.shape, res.refine.homography)
    if box is not None:
        res.region_box = tuple(int(v) for v in box)
        before = _score_on_region(gt, det_v, res.refine.homography, box)
        r2 = refine_on_region(gt, det_v, res.refine.homography, box)
        after = _score_on_region(gt, det_v, r2.homography, box)
        res.region_score_before, res.region_score_after = (
            float(before), float(after))
        gw_, gh_ = gt.shape[1], gt.shape[0]
        oran = 100.0 * box[2] * box[3] / float(gw_ * gh_)
        if r2.homography is not None and after > before + 1e-6:
            r2.variant = res.refine.variant
            res.refine = r2
            res.region_used = True
            res.messages.append(
                f"Bilgi: hizalama ground truth'un ölçüme giren kısmıyla "
                f"({box[2]}×{box[3]} px, çerçevenin %{oran:.0f}'i) yeniden "
                f"çözüldü — örtüşme skoru {before:.4f} → {after:.4f}.")
        else:
            res.messages.append(
                f"Bilgi: ölçüme giren bölgeyle ({box[2]}×{box[3]} px) ikinci "
                f"geçiş denendi ama iyileştirmedi ({before:.4f} → "
                f"{after:.4f}); tam kareyle çözülen homografi korundu.")

    # --- 2C. Homografiyi TAM GT koordinatlarına geri çevir ---
    #
    # Buradan sonrası (tilt ayrıştırması, kalıntı, çağıranın `pointing`
    # çağrısı) tam GT'yi varsayar. Dönüşüm burada yapılır, tilt
    # ayrıştırmasından ÖNCE: homografinin perspektif satırı öteleme ile
    # sarıldığında değişir, dolayısıyla ayrıştırma hangi koordinatta
    # yapıldığına duyarlıdır.
    if box0 is not None:
        _T0inv = np.linalg.inv(T0)
        if res.refine.homography is not None:
            res.refine.homography = res.refine.homography @ _T0inv
        if res.region_box:
            _rx, _ry, _rw, _rh = res.region_box
            res.region_box = (_rx + box0[0], _ry + box0[1], _rw, _rh)

    # Desenin kendi dönme simetrisi — belirsizliği DOĞRU teşhis etmek için
    # gerekli (aşağıya bakınız).
    res.symmetry_order = rotational_symmetry_order(gt)

    # Ayna seçimi ne kadar kesin?
    #
    # DİKKAT — eskiden "en iyi iki skor eşitse ayna belirsiz" deniyordu ve bu
    # YANLIŞ TEŞHİSTİ. Dört varyantın ikisi aynanın aynı tarafındadır:
    #     flip_both = raw    + 180°        (ayna değil, DÖNME)
    #     flip_v    = flip_h + 180°
    # Ölçülen gerçek çiftte skorlar raw 0.8347 / flip_both 0.8347 (eşit) ve
    # flip_h 0.7871 / flip_v 0.7872 idi: yani ayna NET biçimde çözülmüştü,
    # eşit çıkan iki aday birbirinin 180° dönmüş hâliydi. Panel buna
    # "ayna ekseni belirsiz" diyordu.
    #
    # Doğrusu: ayna belirsizliği İKİ GRUP arasındaki fark küçükse vardır.
    # Grup içindeki eşitlik dönme belirsizliğidir ve desenin simetrisiyle
    # açıklanır (bkz. `symmetry_order`) — uyarı değil, desenin sınırıdır.
    if len(all_scores) >= 2:
        groups = {"a": ("raw", "flip_both"), "b": ("flip_h", "flip_v")}
        gbest = {}
        for key, names in groups.items():
            vals = [sc for sc, vn in all_scores if vn in names]
            if vals:
                gbest[key] = max(vals)
        if len(gbest) == 2:
            res.mirror_margin = float(abs(gbest["a"] - gbest["b"]))
            res.mirror_ambiguous = bool(res.mirror_margin < MIRROR_MARGIN_MIN)
        else:
            top2 = sorted((sc for sc, _ in all_scores), reverse=True)[:2]
            res.mirror_margin = float(top2[0] - top2[1])
            res.mirror_ambiguous = bool(res.mirror_margin < MIRROR_MARGIN_MIN)
        if res.mirror_ambiguous:
            res.messages.append(
                f"Ayna ekseni belirsiz (aynalı/aynasız varyantların farkı "
                f"{res.mirror_margin:.4f}) — dönme bu belirsizlikten "
                f"etkilenebilir; decenter ve kapsama etkilenmez.")

    res.tilt = optics.decompose_homography(res.refine.homography,
                                           image_shape=det_v.shape)

    # AYNA VARYANTININ KATTIĞI DÖNMEYİ GERİ AL.
    #
    # Homografi, varyantı UYGULANMIŞ dedektöre göre çözülür. Ama `flip_v` ve
    # `flip_both` kendi içlerinde 180°'lik bir dönme taşır:
    #     flip_v    = flip_h + 180°
    #     flip_both = raw    + 180°
    # Bu 180°, ölçülen açıya karışır ve kullanıcı gerçek yönelim yerine
    # 180° kaymış bir değer okur. Ölçülen örnek: gerçek dönme 135.9°,
    # flip_both üzerinden okunan 43.97° — aradaki fark tam bu katkı.
    #
    # Yalnızca RAPOR değeri düzeltilir; homografinin kendisine dokunulmaz
    # (o, varyantı uygulanmış görüntü için doğrudur ve warp/kalıntı
    # hesapları ona dayanır).
    if res.tilt is not None and res.coarse.variant in ("flip_v", "flip_both"):
        full = getattr(res.tilt, "in_plane_rotation_full_deg", float("nan"))
        if full == full:
            res.tilt.in_plane_rotation_full_deg = (full + 180.0) % 360.0

    if with_residual:
        # `det_v` varyantı ZATEN uygulanmış görüntüdür; burada variant="raw"
        # verilmezse ayna ikinci kez uygulanır ve kalıntı tamamen bozulur.
        res.residual = residual_flow(gt_full, det_v, res.refine.homography,
                                     variant="raw", center=center)
        res.messages.extend(res.residual.messages)

    res.ok = bool(res.refine.ok)
    return res


def distortion_map_bgr(res: ResidualResult, max_px: float | None = None
                       ) -> np.ndarray | None:
    """
    Kalıntı büyüklüğünü renkli ısı haritasına çevirir (GUI önizlemesi için).

    Ölçek `max_px` ile sabitlenebilir; verilmezse %99'luk dilim kullanılır
    (tek bir aykırı piksel tüm haritayı söndürmesin diye).
    """
    if not res.ok or res.magnitude is None:
        return None
    m = res.magnitude.copy()
    if max_px is None:
        vals = m[res.valid] if res.valid is not None else m.ravel()
        max_px = float(np.percentile(vals, 99)) if vals.size else 1.0
    max_px = max(max_px, 1e-6)
    norm = np.clip(m / max_px, 0, 1)
    img = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    if res.valid is not None:
        img[~res.valid] = (40, 40, 40)
    return img
