"""
Görüntü eşleme ve analiz modülü.

Klasörden yüklenen iki görüntüyü (ground truth + dedektör görüntüsü)
eşler, aralarındaki homografiyi bulur ve optik çekirdeğe (optics.py)
tilt/rotasyon ayrıştırması için besler.

Akış:
  1. Görüntüleri gri tonlamaya çevir, normalize et.
  2. Feature (ORB / SIFT) tespit + eşleme.
  3. Ayna (mirror/flip) otomatik tespiti — dedektör görüntüsü genellikle
     yatay flip'li olur; ham + flip'li iki varyantı deneyip daha çok
     eşleşme vereni seçeriz.
  4. RANSAC ile homografi tahmini.
  5. Homografiyi optics.decompose_homography'ye verip tilt sonucu al.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np

from .config import SystemConfig
from . import optics


@dataclass
class MatchResult:
    homography: np.ndarray | None
    num_matches: int
    num_inliers: int
    mirrored: bool
    detector_variant: str          # "raw" | "flip_h" | "flip_v" | "flip_both"
    reproj_error_px: float
    tilt: optics.TiltResult | None
    gt_shape: tuple
    det_shape: tuple
    degenerate: bool = False       # homografi dejenere mi (bkz. _homography_is_sane)
    inlier_spread: float = 0.0     # inlier'ların yayılımı (kısa kenara oran)
    gt_inverted: bool = False      # GT kontrastı terslenerek mi eşleşti
    guided: bool = False           # yoğun hizalamanın homografisiyle güdümlü mü
    guided_matches: int = 0        # güdümlü aşamada kapıdan geçen eşleşme sayısı


def load_image_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Görüntü okunamadı: {path}")
    return img


def _normalize(img: np.ndarray) -> np.ndarray:
    """Kontrast dengeleme — farklı aydınlatmadaki iki görüntüyü eşlemek için."""
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(img)


def _ink_sign(img: np.ndarray) -> float:
    """
    Desenin zemine göre işareti: + parlak desen/koyu zemin, − tersi.

    Ortalama ile medyan farkı kullanılır. Çizgi/halka desenlerinde "mürekkep"
    pikselleri azınlıktadır, bu yüzden medyan zemini, ortalama ise zemin +
    mürekkep karışımını temsil eder; farkın işareti polariteyi verir.
    """
    a = img.astype(np.float32)
    return float(a.mean() - np.median(a))


def match_polarity(gt: np.ndarray, det: np.ndarray) -> tuple:
    """
    GT'nin kontrast polaritesini dedektöre uydurur. (gt_uygun, terslendi_mi).

    Hem SIFT hem ECC polariteye duyarlıdır: beyaz zeminli bir ground truth
    (siyah çizgi) koyu zeminli bir çekimle (parlak çizgi) eşleşmez — ölçüldü,
    SIFT tek eşleşme bulamıyor, yoğun hizalama da "hiçbir ayna varyantı
    çözülemedi" diyor. Tersleme geometriyi değiştirmez, yalnızca eşleşmeyi
    mümkün kılar.
    """
    if _ink_sign(gt) * _ink_sign(det) < 0:
        return cv2.bitwise_not(gt), True
    return gt, False


def _describe(img: np.ndarray, use_sift: bool = True):
    """Tek görüntünün keypoint + tanımlayıcılarını çıkarır. (kp, des, norm)."""
    if use_sift and hasattr(cv2, "SIFT_create"):
        detector = cv2.SIFT_create(nfeatures=4000)
        norm = cv2.NORM_L2
    else:
        detector = cv2.ORB_create(nfeatures=6000)
        norm = cv2.NORM_HAMMING
    kp, des = detector.detectAndCompute(img, None)
    return kp, des, norm


def _match_desc(des1, des2, norm, ratio: float = 0.75,
                cross_check: bool = True):
    """
    Lowe oran testi + KARŞILIKLI (mutual) en yakın komşu denetimi.

    Karşılıklı denetim şart, çünkü kendine-benzer desenlerde tanımlayıcılar
    ayırt edici değildir ve ÇOKTAN-TEKE eşleşme oluşur: ölçülen gerçek bir
    çiftte 34 "iyi" eşleşme yalnızca 14 farklı dedektör keypoint'ine
    gidiyordu (en çok kullanılan hedef 4 kez). RANSAC bu kümeyi ancak
    görüntüyü tek noktaya çökerten bir homografiyle "açıklayabiliyor";
    dejenere sonucun kaynağı buydu. Karşılıklı denetim bir hedefin yalnızca
    kendi en iyi kaynağıyla eşleşmesine izin verir, çöküşü doğuran
    tekrarları baştan siler.
    """
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return []
    bf = cv2.BFMatcher(norm)
    good = []
    for m_n in bf.knnMatch(des1, des2, k=2):
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < ratio * n.distance:   # Lowe oran testi
            good.append(m)
    if not cross_check or not good:
        return good
    back = {}
    for m_n in bf.knnMatch(des2, des1, k=1):
        if m_n:
            back[m_n[0].queryIdx] = m_n[0].trainIdx
    return [m for m in good if back.get(m.trainIdx, -1) == m.queryIdx]


def _detect_and_match(gt: np.ndarray, det: np.ndarray,
                      use_sift: bool = True, cross_check: bool = True):
    """İki gri görüntüde feature bulur ve eşler. (kp1, kp2, good_matches)."""
    kp1, des1, norm = _describe(gt, use_sift)
    kp2, des2, _ = _describe(det, use_sift)
    if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
        return kp1, kp2, []
    return kp1, kp2, _match_desc(des1, des2, norm, cross_check=cross_check)


def _variants(det: np.ndarray):
    """Dedektör görüntüsünün olası ayna varyantlarını üretir."""
    return {
        "raw": det,
        "flip_h": cv2.flip(det, 1),
        "flip_v": cv2.flip(det, 0),
        "flip_both": cv2.flip(det, -1),
    }


def _polarities(gt: np.ndarray):
    """
    Ground truth'un kontrast polaritesi varyantları.

    SIFT tanımlayıcısı gradyan YÖNÜNE dayanır ve kontrast terslemesine
    bağışık DEĞİLDİR: beyaz zeminli bir desen (siyah çizgi) ile koyu
    zeminli bir çekim (parlak çizgi) arasında tek bir eşleşme bile
    bulunamaz. Ölçüldü — aynı desenin düz hâliyle 0, terslenmiş hâliyle
    34 eşleşme. Ayna varyantları denenirken polarite de denenmeli.
    """
    return {"duz": gt, "ters": cv2.bitwise_not(gt)}


def _inlier_spread(pts: np.ndarray, shape) -> float:
    """
    Inlier noktalarının görüntüye ne kadar yayıldığı (0..~1).

    Siemens star gibi kendine-benzer radyal desenlerde SIFT, yıldızın
    MERKEZİ çevresinde yüzlerce sahte eşleşme üretir. Hepsi tek noktada
    toplandığında RANSAC bunları dejenere (görüntüyü tek noktaya çökerten)
    bir homografiyle "açıklar" ve reproj hatası sahte biçimde düşük çıkar.
    Yayılımı ölçerek bu durumu yakalarız.

    DİKKAT — bu ölçüm İKİ TARAFTA DA yapılmalıdır (bkz. `_pair_spread`).
    Ölçülen bir vakada kaynak tarafı 0.106 ile eşiği geçerken hedef tarafı
    0.021'di: noktalar GT'de yayılmış, dedektörde tek kümeye çökmüştü.
    Tek tarafa bakan denetim bu çöküşü göremez.
    """
    if len(pts) < 4:
        return 0.0
    p = pts.reshape(-1, 2)
    short = float(min(shape[:2]))
    if short <= 0:
        return 0.0
    # İki eksendeki standart sapmanın küçüğü — noktalar bir çizgide
    # toplansa bile düşük çıkar.
    return float(min(p[:, 0].std(), p[:, 1].std()) / short)


def _pair_spread(src: np.ndarray, dst: np.ndarray,
                 gt_shape, det_shape) -> float:
    """İki taraftaki yayılımın KÜÇÜĞÜ — hangi tarafta çökerse çöksün yakalar."""
    return min(_inlier_spread(src, gt_shape), _inlier_spread(dst, det_shape))


def _homography_is_sane(H: np.ndarray, gt_shape, det_shape) -> bool:
    """
    Homografinin fiziksel olarak anlamlı olup olmadığını denetler.

    Reddedilen durumlar:
      * Görüntüyü noktaya/çizgiye çökerten (dejenere) dönüşümler.
      * Aşırı ölçek değişimi (x50'den fazla büyütme/küçültme).
      * Köşe sırasını bozan (kendisiyle kesişen) dönüşümler.
    """
    if H is None or not np.all(np.isfinite(H)):
        return False

    h, w = gt_shape[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    try:
        proj = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    except cv2.error:
        return False
    if not np.all(np.isfinite(proj)):
        return False

    # Çökme testi: izdüşen dörtgenin alanı kaynağın çok küçük bir kısmıysa
    # (veya sıfırsa) dönüşüm dejeneredir.
    def poly_area(p):
        x, y = p[:, 0], p[:, 1]
        return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) -
                               np.dot(y, np.roll(x, -1))))

    src_area = float(w * h)
    dst_area = poly_area(proj)
    if src_area <= 0 or dst_area <= 0:
        return False

    scale = (dst_area / src_area) ** 0.5
    if not (0.02 < scale < 50.0):
        return False

    # İzdüşen dörtgenin kenar uzunlukları birbirine göre makul olmalı;
    # bir kenar neredeyse sıfırsa şekil bir çizgiye çökmüştür.
    edges = [float(np.linalg.norm(proj[i] - proj[(i + 1) % 4]))
             for i in range(4)]
    if min(edges) < 1e-3 or min(edges) / max(edges) < 0.02:
        return False

    # Dörtgen dışbükey ve köşe sırası korunmuş olmalı (kendini kesmemeli)
    cross_signs = []
    for i in range(4):
        a = proj[(i + 1) % 4] - proj[i]
        b = proj[(i + 2) % 4] - proj[(i + 1) % 4]
        cross_signs.append(np.sign(a[0] * b[1] - a[1] * b[0]))
    if len(set(s for s in cross_signs if s != 0)) > 1:
        return False

    return True


# Inlier'ların görüntüye yayılması için asgari eşik. Bunun altındaki
# eşleşme kümesi (tipik olarak desen merkezine yığılmış) dejenere
# homografi üretir; güvenilmez sayılır.
MIN_SPREAD = 0.08

# Güdümlü aşamada bir eşleşmenin ön-warp'lanmış konumundan sapabileceği
# en büyük mesafe (piksel). Ön-warp doğruysa doğru eşleşmeler birkaç
# piksel içindedir; bu kapı "başka bir halkaya kilitlenmiş" eşleşmeleri
# eler. Ölçülen çiftte 123 ham eşleşmenin yalnızca 14'ü kapıdan geçti.
GUIDED_RADIUS_PX = 20.0
GUIDED_MIN_MATCHES = 6


def _guided_match(gt: np.ndarray, det_v: np.ndarray, H_prior: np.ndarray,
                  use_sift: bool = True):
    """
    Yoğun hizalamanın homografisiyle GÜDÜMLÜ eşleme.

    Neden: kör (küresel) SIFT bu tür desenlerde çöküyor — 1.3 Mpx'lik bir
    ground truth'ta yalnızca ~120 keypoint bulunuyor ve tanımlayıcılar
    birbirinin aynı olduğu için doğru eşleşme seçilemiyor. Oysa GT önce
    yoğun yolun homografisiyle dedektör düzlemine warp edilirse iki
    görüntü neredeyse çakışık olur; SIFT'in çözmesi gereken artık ~0°
    dönme ve birkaç pikselik kaymadır. Ölçüldü: keypoint 122 → 458,
    kapıdan geçen temiz eşleşme 8, artık dönüşüm ölçek 1.0021 / dönme
    -0.010°, bileşik dönme yoğun yolun ölçtüğünden 0.01° farklı.

    Bu, "yoğun yolun sayısını kopyalamak" DEĞİLDİR: ön-bilgi yanlışsa
    warp tutmaz, yarıçap kapısından yeterli eşleşme geçmez ve fonksiyon
    None döner — yani yanlış bir ön-bilgi sessizce onaylanamaz.

    Döndürülen: (H_toplam, istatistik) ya da None.
    """
    if H_prior is None or not np.all(np.isfinite(H_prior)):
        return None
    h, w = det_v.shape[:2]
    try:
        warp = cv2.warpPerspective(gt, np.asarray(H_prior, dtype=np.float64),
                                   (w, h))
    except cv2.error:
        return None

    kp1, des1, norm = _describe(warp, use_sift)
    kp2, des2, _ = _describe(det_v, use_sift)
    if des1 is None or des2 is None:
        return None
    # Ön-warp sonrası iki görüntü zaten benzer; oran testi biraz gevşetilir,
    # ayıklamayı yarıçap kapısı yapar.
    good = _match_desc(des1, des2, norm, ratio=0.8, cross_check=True)
    if not good:
        return None

    sel = []
    for m in good:
        p1 = np.asarray(kp1[m.queryIdx].pt)
        p2 = np.asarray(kp2[m.trainIdx].pt)
        if float(np.hypot(*(p1 - p2))) <= GUIDED_RADIUS_PX:
            sel.append(m)
    if len(sel) < GUIDED_MIN_MATCHES:
        return None

    src = np.float32([kp1[m.queryIdx].pt for m in sel]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in sel]).reshape(-1, 1, 2)
    # Artık dönüşüm AFİN ile aranır, projektif ile değil. Projektif serbestlik
    # bir avuç noktayla saçmalıyor: aynı veride 69° tilt uydurdu. Afin,
    # kalan kayma/ölçek/kesme için yeterli, çökmeye ise izin vermez.
    M, mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC,
                                   ransacReprojThreshold=2.0)
    if M is None or mask is None or int(mask.sum()) < GUIDED_MIN_MATCHES:
        return None

    M3 = np.vstack([M, [0.0, 0.0, 1.0]])
    # Artık dönüşüm KÜÇÜK olmalı. Büyükse ön-warp tutmamış demektir ve
    # eşleşmeler gürültüdür — sessizce kabul edilmez.
    res_scale = float(np.hypot(M3[0, 0], M3[1, 0]))
    res_rot = abs(float(np.degrees(np.arctan2(M3[1, 0], M3[0, 0]))))
    if not (0.9 < res_scale < 1.1) or res_rot > 5.0:
        return None

    mf = mask.reshape(-1).astype(bool)
    proj = cv2.perspectiveTransform(src, M3)
    err = np.sqrt(((proj - dst) ** 2).sum(axis=2)).reshape(-1)
    H_total = M3 @ np.asarray(H_prior, dtype=np.float64)
    spread = _pair_spread(src.reshape(-1, 2)[mf], dst.reshape(-1, 2)[mf],
                          det_v.shape, det_v.shape)
    if not _homography_is_sane(H_total, gt.shape, det_v.shape):
        return None

    return H_total, {
        "matches": len(good), "guided_matches": len(sel),
        "inliers": int(mf.sum()),
        "reproj": float(err[mf].mean()) if mf.any() else float("inf"),
        "spread": spread,
    }


def analyze(gt_path: str, det_path: str, cfg: SystemConfig,
            use_sift: bool = True,
            prior_H: np.ndarray | None = None,
            prior_variant: str | None = None) -> MatchResult:
    """
    Ground truth ve dedektör görüntüsünü eşleyip homografi + tilt çıkarır.
    Ayna ve polarite varyantlarını otomatik dener, en iyi eşleşeni seçer.

    prior_H / prior_variant: yoğun hizalamanın (dense_align) homografisi ve
        seçtiği ayna varyantı. Kör eşleme dejenere çıkarsa güdümlü eşleme
        bununla denenir (bkz. `_guided_match`). Verilmezse davranış eskisiyle
        aynıdır.
    """
    gt_raw = _normalize(load_image_gray(gt_path))
    det_orig = _normalize(load_image_gray(det_path))

    best = None
    fallback = None            # hiçbir aday sağlam değilse en iyisi
    for pname, gt in _polarities(gt_raw).items():
        for vname, dv in _variants(det_orig).items():
            kp1, kp2, good = _detect_and_match(gt, dv, use_sift=use_sift)
            if len(good) < 12:
                continue
            src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            if H is None:
                continue
            inliers = int(mask.sum())
            if inliers < 10:
                continue

            mask_flat = mask.reshape(-1).astype(bool)
            # Yeniden-izdüşüm hatası (inlier'lar üzerinde)
            proj = cv2.perspectiveTransform(src, H)
            err = np.sqrt(((proj - dst) ** 2).sum(axis=2)).reshape(-1)
            reproj = (float(err[mask_flat].mean()) if mask_flat.any()
                      else float("inf"))

            # Dejenerelik denetimi: İKİ TARAFLI yayılım + geometrik akıl sağlığı
            spread = _pair_spread(src.reshape(-1, 2)[mask_flat],
                                  dst.reshape(-1, 2)[mask_flat],
                                  gt.shape, dv.shape)
            sane = (_homography_is_sane(H, gt.shape, dv.shape)
                    and spread >= MIN_SPREAD)

            cand = {
                "variant": vname, "H": H, "matches": len(good),
                "inliers": inliers, "reproj": reproj, "shape": dv.shape,
                "spread": spread, "sane": sane, "inverted": pname == "ters",
                "guided": False, "guided_matches": 0,
            }

            # Sağlam adaylar her zaman dejenerelere tercih edilir.
            if sane:
                if best is None or (cand["inliers"], -cand["reproj"]) > \
                        (best["inliers"], -best["reproj"]):
                    best = cand
            else:
                if fallback is None or cand["inliers"] > fallback["inliers"]:
                    fallback = cand

    # Kör eşleme sonuç veremediyse yoğun hizalamanın homografisiyle güdümlü
    # dene. Bu, dejenereliğin SEBEBİNİ ortadan kaldırır: SIFT'in artık
    # 136°'lik dönmeyi ve 1.25'lik ölçeği kendi başına bulması gerekmez.
    if best is None and prior_H is not None:
        variants = _variants(det_orig)
        names = ([prior_variant] if prior_variant in variants
                 else list(variants.keys()))
        for vname in names:
            for pname, gt in _polarities(gt_raw).items():
                g = _guided_match(gt, variants[vname], prior_H,
                                  use_sift=use_sift)
                if g is None:
                    continue
                H_total, st = g
                best = {
                    "variant": vname, "H": H_total, "shape": variants[vname].shape,
                    "sane": True, "inverted": pname == "ters", "guided": True,
                    **st,
                }
                break
            if best is not None:
                break

    if best is None and fallback is None:
        return MatchResult(None, 0, 0, False, "raw", float("nan"),
                           None, gt_raw.shape, det_orig.shape)

    chosen = best if best is not None else fallback
    degenerate = best is None

    if degenerate:
        # Homografi güvenilmez: tilt/rotasyon türetme. Ayna bilgisi yine de
        # varyant adından okunabilir (o, eşleşme sayısına dayanır).
        tilt = None
    else:
        tilt = optics.decompose_homography(chosen["H"], image_shape=chosen["shape"])

    mirrored = chosen["variant"] in ("flip_h", "flip_v", "flip_both")
    if tilt is not None and tilt.mirrored:
        mirrored = True

    return MatchResult(
        homography=None if degenerate else chosen["H"],
        num_matches=chosen["matches"],
        num_inliers=chosen["inliers"],
        mirrored=mirrored,
        detector_variant=chosen["variant"],
        reproj_error_px=chosen["reproj"],
        tilt=tilt,
        gt_shape=gt_raw.shape,
        det_shape=det_orig.shape,
        degenerate=degenerate,
        inlier_spread=chosen["spread"],
        gt_inverted=bool(chosen.get("inverted", False)),
        guided=bool(chosen.get("guided", False)),
        guided_matches=int(chosen.get("guided_matches", 0)),
    )


def make_overlay(gt_path: str, det_path: str, result: MatchResult) -> np.ndarray:
    """
    Ground truth'u homografiyle dedektör düzlemine warp edip üst üste bindirir.
    Hizalamanın görsel doğrulaması için renkli overlay (BGR) döndürür.

    Kırmızı = dedektör, Yeşil = hizalanmış ground truth.
    İyi hizalamada iki kanal örtüşür ve görüntü SARI görünür; kayma varsa
    kırmızı/yeşil hayaletler ayrışır.

    İki görüntünün pozlaması çok farklı olabildiği için her kanal ayrı ayrı
    kontrast-normalize edilir — aksi halde parlak olan kanal diğerini
    tamamen bastırır ve örtüşme görsel olarak okunamaz.
    """
    gt = load_image_gray(gt_path)
    det = load_image_gray(det_path)
    variants = _variants(det)
    det_v = variants.get(result.detector_variant, det)

    if result.homography is None:
        return cv2.cvtColor(det_v, cv2.COLOR_GRAY2BGR)

    h, w = det_v.shape[:2]
    warped = cv2.warpPerspective(gt, result.homography, (w, h))

    # Warp sonrası boş (siyah) bölgeleri maskele — normalizasyonu bozmasınlar
    valid = cv2.warpPerspective(np.full(gt.shape, 255, np.uint8),
                                result.homography, (w, h)) > 0

    def norm(ch: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
        m = mask if mask is not None else np.ones(ch.shape, bool)
        if not m.any():
            return ch
        lo, hi = np.percentile(ch[m], (2, 98))
        if hi - lo < 1e-3:
            return ch
        out = np.clip((ch.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
        return out.astype(np.uint8)

    overlay = np.zeros((h, w, 3), dtype=np.uint8)
    overlay[:, :, 2] = norm(det_v)                       # kırmızı: dedektör
    overlay[:, :, 1] = np.where(valid, norm(warped, valid), 0)   # yeşil: GT
    return overlay
