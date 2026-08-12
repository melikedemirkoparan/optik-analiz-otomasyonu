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


def load_image_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Görüntü okunamadı: {path}")
    return img


def _normalize(img: np.ndarray) -> np.ndarray:
    """Kontrast dengeleme — farklı aydınlatmadaki iki görüntüyü eşlemek için."""
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(img)


def _detect_and_match(gt: np.ndarray, det: np.ndarray,
                      use_sift: bool = True):
    """İki gri görüntüde feature bulur ve eşler. (kp1, kp2, good_matches)."""
    if use_sift and hasattr(cv2, "SIFT_create"):
        detector = cv2.SIFT_create(nfeatures=4000)
        norm = cv2.NORM_L2
    else:
        detector = cv2.ORB_create(nfeatures=6000)
        norm = cv2.NORM_HAMMING

    kp1, des1 = detector.detectAndCompute(gt, None)
    kp2, des2 = detector.detectAndCompute(det, None)
    if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
        return kp1, kp2, []

    bf = cv2.BFMatcher(norm)
    raw = bf.knnMatch(des1, des2, k=2)
    good = []
    for m_n in raw:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:   # Lowe oran testi
            good.append(m)
    return kp1, kp2, good


def _variants(det: np.ndarray):
    """Dedektör görüntüsünün olası ayna varyantlarını üretir."""
    return {
        "raw": det,
        "flip_h": cv2.flip(det, 1),
        "flip_v": cv2.flip(det, 0),
        "flip_both": cv2.flip(det, -1),
    }


def _inlier_spread(pts: np.ndarray, shape) -> float:
    """
    Inlier noktalarının görüntüye ne kadar yayıldığı (0..~1).

    Siemens star gibi kendine-benzer radyal desenlerde SIFT, yıldızın
    MERKEZİ çevresinde yüzlerce sahte eşleşme üretir. Hepsi tek noktada
    toplandığında RANSAC bunları dejenere (görüntüyü tek noktaya çökerten)
    bir homografiyle "açıklar" ve reproj hatası sahte biçimde düşük çıkar.
    Yayılımı ölçerek bu durumu yakalarız.
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


def analyze(gt_path: str, det_path: str, cfg: SystemConfig,
            use_sift: bool = True) -> MatchResult:
    """
    Ground truth ve dedektör görüntüsünü eşleyip homografi + tilt çıkarır.
    Ayna varyantlarını otomatik dener, en iyi eşleşeni seçer.
    """
    gt = _normalize(load_image_gray(gt_path))
    det_orig = _normalize(load_image_gray(det_path))

    # Inlier'ların görüntüye yayılması için asgari eşik. Bunun altındaki
    # eşleşme kümesi (tipik olarak yıldız merkezine yığılmış) dejenere
    # homografi üretir; güvenilmez sayılır.
    MIN_SPREAD = 0.08

    best = None
    fallback = None            # hiçbir aday sağlam değilse en iyisi
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
        reproj = float(err[mask_flat].mean()) if mask_flat.any() else float("inf")

        # Dejenerelik denetimi: inlier yayılımı + geometrik akıl sağlığı
        spread = _inlier_spread(src.reshape(-1, 2)[mask_flat], gt.shape)
        sane = _homography_is_sane(H, gt.shape, dv.shape) and spread >= MIN_SPREAD

        cand = {
            "variant": vname, "H": H, "matches": len(good),
            "inliers": inliers, "reproj": reproj, "shape": dv.shape,
            "spread": spread, "sane": sane,
        }

        # Sağlam adaylar her zaman dejenerelere tercih edilir.
        if sane:
            if best is None or (cand["inliers"], -cand["reproj"]) > \
                    (best["inliers"], -best["reproj"]):
                best = cand
        else:
            if fallback is None or cand["inliers"] > fallback["inliers"]:
                fallback = cand

    if best is None and fallback is None:
        return MatchResult(None, 0, 0, False, "raw", float("nan"),
                           None, gt.shape, det_orig.shape)

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
        gt_shape=gt.shape,
        det_shape=det_orig.shape,
        degenerate=degenerate,
        inlier_spread=chosen["spread"],
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
