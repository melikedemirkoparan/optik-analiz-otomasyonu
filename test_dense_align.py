#!/usr/bin/env python3
"""
Yoğun (dense) hizalama doğrulaması — desen-agnostik olduğunu KANITLAR.

Bu testin amacı `core/dense_align.py`'ın iddiasını sınamaktır:
"hangi deseni verirsen ver, piksel seviyesinde çalışır".

Bu yüzden testler bilerek BİRBİRİNE HİÇ BENZEMEYEN desenlerle koşar:
rastgele doku, eş merkezli çember, Siemens star, satranç tahtası, nokta
ızgarası, tek yönlü çizgiler. Hepsinde aynı kod, aynı parametreler.

Koşum:
    python3 test_dense_align.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

from core import dense_align as da, optics


SP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Kabul eşikleri — sentetik veride bu değerlerin altında kalınmalı
TOL_ROT_DEG = 0.35        # dönme hatası
TOL_SCALE = 0.005         # bağıl ölçek hatası
TOL_CLEAN_RESIDUAL = 0.6  # distorsiyonsuz çiftte kalıntı RMS (px)


# --------------------------------------------------------------------------
# Desen üreteçleri — "sınırsız garip desen" iddiasını sınamak için
# --------------------------------------------------------------------------

def pat_random(w=512, h=512, seed=1):
    """Rastgele izotropik doku — hiçbir yapısı yok."""
    rng = np.random.default_rng(seed)
    a = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 2.5)
    return ((a - a.min()) / max(a.ptp(), 1e-9) * 255).astype(np.uint8)


def pat_concentric(w=512, h=512, n=14, marks=True):
    """
    Eş merkezli çember — SIFT'i çökerten kendine-benzer desen.

    `marks`: projedeki gerçek paternde olduğu gibi simetri kırıcı işaretler
    ekler. Saf çemberler DAİRESEL SİMETRİKTİR: dönme fiziksel olarak
    ölçülemez, hiçbir algoritma ölçemez. Gerçek `generate_circle_pattern.py`
    bu yüzden dört köşeye farklı açılarda F harfi koyar. Simetri kırıcı
    olmadan dönme sormak, ölçüme cevabı olmayan bir soru sormaktır.
    """
    img = np.full((h, w), 255, np.uint8)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    for i in range(1, n + 1):
        r = int(min(cx, cy) * i / (n + 1))
        cv2.circle(img, (int(cx), int(cy)), r, 0, 2, cv2.LINE_AA)
    if marks:
        # F'lerin yerine geçen asimetrik işaretler (dört azimut, farklı boy)
        for az, size in ((45, 26), (135, 18), (225, 30), (315, 22)):
            a = np.deg2rad(az)
            fx = int(cx + min(cx, cy) * 0.62 * np.cos(a))
            fy = int(cy + min(cx, cy) * 0.62 * np.sin(a))
            cv2.rectangle(img, (fx - size // 2, fy - size // 3),
                          (fx + size // 2, fy + size // 3), 0, -1)
    return img


def pat_siemens(w=512, h=512, spokes=36):
    """Siemens star — radyal, kendine-benzer."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    th = np.arctan2(yy - cy, xx - cx)
    r = np.hypot(xx - cx, yy - cy)
    v = (np.sin(th * spokes) > 0).astype(np.uint8) * 255
    v[r > min(cx, cy) * 0.9] = 255
    return v


def pat_checker(w=512, h=512, size=32):
    """Satranç tahtası — periyodik, çok sayıda özdeş köşe."""
    yy, xx = np.mgrid[0:h, 0:w]
    return (((xx // size + yy // size) % 2) * 255).astype(np.uint8)


def pat_dots(w=512, h=512, step=40):
    """Nokta ızgarası — ayrık, seyrek."""
    img = np.full((h, w), 255, np.uint8)
    for y in range(step // 2, h, step):
        for x in range(step // 2, w, step):
            cv2.circle(img, (x, y), 6, 0, -1, cv2.LINE_AA)
    return img


def pat_lines(w=512, h=512, step=24):
    """Tek yönlü çizgiler — bilerek KÖTÜ bir desen (tek eksende bilgi yok)."""
    img = np.full((h, w), 255, np.uint8)
    for y in range(0, h, step):
        cv2.line(img, (0, y), (w, y), 0, 3)
    return img


PATTERNS = [
    ("rastgele doku", pat_random),
    ("eş merkezli çember", pat_concentric),
    ("Siemens star", pat_siemens),
    ("satranç tahtası", pat_checker),
    ("nokta ızgarası", pat_dots),
]


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def warp_similarity(img, rot_deg=0.0, scale=1.0, tx=0.0, ty=0.0):
    """Bilinen benzerlik dönüşümü uygular. Döner: (görüntü, gerçek 3x3)."""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D(((w - 1) / 2.0, (h - 1) / 2.0), rot_deg, scale)
    M[0, 2] += tx
    M[1, 2] += ty
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR)
    return out, np.vstack([M, [0, 0, 1]])


def apply_barrel(img, k1):
    """
    Bilinen radyal distorsiyon enjekte eder.
    Normalize yarıçap kısa kenarın yarısına göredir: rn = r / (min(w,h)/2)
    """
    h, w = img.shape[:2]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = xx - cx, yy - cy
    rn = np.hypot(dx, dy) / (min(w, h) / 2.0)
    f = 1.0 + k1 * rn ** 2
    return cv2.remap(img, (cx + dx * f).astype(np.float32),
                     (cy + dy * f).astype(np.float32),
                     cv2.INTER_LINEAR, borderValue=0)


def measure(gt, det):
    """Tam zincir: kaba -> ECC -> ayrıştırma. Döner: (rot, scale, tilt, ecc)."""
    c = da.coarse_align(gt, det, try_mirrors=False)
    r = da.refine_ecc(gt, det, init=c.matrix)
    if r.homography is None:
        return None
    t = optics.decompose_homography(r.homography, image_shape=det.shape)
    # optics konvansiyonu dönmeyi ters işaretle verir (mevcut SIFT yolu da
    # aynı şekilde raporlar) — testte gerçek açıyla kıyaslamak için çeviriyoruz.
    return (-t.in_plane_rotation_deg, t.scale_x, t.total_tilt_deg, r.correlation)


def ok(flag):
    return "OK  " if flag else "HATA"


# --------------------------------------------------------------------------
# Testler
# --------------------------------------------------------------------------

def test_pattern_agnostic():
    """[1] Aynı dönüşüm, çok farklı desenler — hepsi doğru ölçülmeli."""
    print("\n[1] DESEN-AGNOSTİKLİK — aynı dönüşüm, farklı desenler")
    print(f"{'desen':<22}{'rot_ölç':>9}{'sc_ölç':>9}{'ECC':>8}   sonuç")
    print("-" * 60)
    rot_true, sc_true = 4.0, 1.12
    fails = []
    for name, gen in PATTERNS:
        base = gen()
        det, _ = warp_similarity(base, rot_deg=rot_true, scale=sc_true)
        m = measure(base, det)
        if m is None:
            print(f"{name:<22}{'—':>9}{'—':>9}{'—':>8}   HATA (çözülemedi)")
            fails.append(name)
            continue
        rot, sc, tilt, cc = m
        good = (abs(rot - rot_true) < TOL_ROT_DEG and
                abs(sc - sc_true) / sc_true < TOL_SCALE)
        print(f"{name:<22}{rot:9.3f}{sc:9.4f}{cc:8.4f}   {ok(good)}")
        if not good:
            fails.append(name)
    return fails


def test_transform_sweep():
    """[2] Tek desen, geniş dönüşüm aralığı."""
    print("\n[2] DÖNÜŞÜM TARAMASI — rastgele doku")
    print(f"{'gerçek rot':>11}{'gerçek sc':>11}{'rot_ölç':>10}"
          f"{'sc_ölç':>10}{'tilt':>8}   sonuç")
    print("-" * 62)
    base = pat_random()
    cases = [(0.0, 1.0), (1.583, 1.0), (5.0, 1.0), (-12.0, 0.8),
             (30.0, 1.1), (-25.0, 0.9), (0.0, 1.25)]
    fails = []
    for rot_true, sc_true in cases:
        det, _ = warp_similarity(base, rot_deg=rot_true, scale=sc_true)
        m = measure(base, det)
        if m is None:
            fails.append((rot_true, sc_true))
            continue
        rot, sc, tilt, cc = m
        good = (abs(rot - rot_true) < TOL_ROT_DEG and
                abs(sc - sc_true) / sc_true < TOL_SCALE)
        print(f"{rot_true:11.3f}{sc_true:11.3f}{rot:10.3f}"
              f"{sc:10.4f}{tilt:8.3f}   {ok(good)}")
        if not good:
            fails.append((rot_true, sc_true))
    return fails


def test_no_false_distortion():
    """
    [3] Distorsiyon YOKKEN kalıntı sıfıra yakın olmalı.

    En tehlikeli hata sınıfı budur: ölçüm katmanının gürültüden distorsiyon
    UYDURMASI. Sıfır distorsiyonlu çiftte kalıntı büyükse tüm harita çöpe gider.
    """
    print("\n[4] SAHTE DİSTORSİYON ÜRETMİYOR — temiz çiftte kalıntı")
    print(f"{'desen':<22}{'RMS px':>9}{'kenar px':>10}   sonuç")
    print("-" * 50)
    fails = []
    for name, gen in PATTERNS:
        base = gen()
        det, _ = warp_similarity(base, rot_deg=2.0, scale=1.05)
        c = da.coarse_align(base, det, try_mirrors=False)
        r = da.refine_ecc(base, det, init=c.matrix)
        q = da.residual_flow(base, det, r.homography, variant=c.variant)
        good = q.ok and q.rms_dev_px < TOL_CLEAN_RESIDUAL
        edge = q.edge_distortion_px if q.model_ok else float("nan")
        print(f"{name:<22}{q.rms_dev_px:9.3f}{edge:10.3f}   {ok(good)}")
        if not good:
            fails.append(name)
    return fails


def test_distortion_recovery():
    """
    [3] Bilinen distorsiyon geri okunuyor mu.

    Beklenen değer profilin ulaştığı EN DIŞ yarıçapta hesaplanır (köşelere
    yakın), çünkü `edge_distortion_px` orada tanımlıdır — kısa kenar yarısıyla
    kıyaslamak yanlış olur.
    """
    print("\n[3] DİSTORSİYON GERİ OKUMA — bilinen k1 enjekte edildi")
    print(f"{'k1':>8}{'ölçülen':>11}{'beklenen':>11}{'oran':>8}"
          f"{'fitRMS':>9}   sonuç")
    print("-" * 60)
    base = pat_random()
    R = 256.0
    fails = []
    for k1 in [0.0, 0.01, 0.02, 0.05, -0.02, -0.04]:
        det = apply_barrel(base, k1)
        c = da.coarse_align(base, det, try_mirrors=False)
        r = da.refine_ecc(base, det, init=c.matrix)
        q = da.residual_flow(base, det, r.homography, variant=c.variant)
        if not q.model_ok:
            print(f"{k1:8.3f}{'—':>11}{'—':>11}{'—':>8}{'—':>9}   HATA")
            fails.append(k1)
            continue
        rmax = float(q.radius_px[-1])
        exp = -k1 * (rmax / R) ** 2 * rmax
        if abs(exp) < 1e-6:
            good = abs(q.edge_distortion_px) < 1.0
            ratio = float("nan")
        else:
            ratio = q.edge_distortion_px / exp
            # Büyük distorsiyonda optik akış zayıflar; %20 tolerans.
            good = 0.80 < ratio < 1.20
        print(f"{k1:8.3f}{q.edge_distortion_px:11.2f}{exp:11.2f}"
              f"{ratio:8.3f}{q.fit_rms_px:9.3f}   {ok(good)}")
        if not good:
            fails.append(k1)
    return fails


def test_vs_sift():
    """
    [5] SIFT ile karşılaştırma — kendine-benzer desende üstünlük.

    Bu testin varlık sebebi: eş merkezli çember ve Siemens star gibi
    desenlerde SIFT sahte eşleşmelerden tamamen uydurma bir dönme
    üretebiliyor ve mevcut dejenerelik kontrolleri bunu HER ZAMAN
    yakalamıyor. Yoğun yöntem tüm piksellere baktığı için kandırılamaz.
    """
    print("\n[5] SIFT KARŞILAŞTIRMASI — kendine-benzer desenler")
    from core import image_analysis as ia
    from core.config import SystemConfig
    cfg = SystemConfig()
    os.makedirs(SP, exist_ok=True)
    gp, dp = os.path.join(SP, "_dense_gt.png"), os.path.join(SP, "_dense_det.png")

    print(f"{'desen':<22}{'gerçek':>8}{'SIFT':>10}{'yoğun':>10}   kazanan")
    print("-" * 62)
    rot_true = 3.0
    rows = []
    for name, gen in [("eş merkezli çember", pat_concentric),
                      ("Siemens star", pat_siemens)]:
        base = gen()
        det, _ = warp_similarity(base, rot_deg=rot_true)
        cv2.imwrite(gp, base)
        cv2.imwrite(dp, det)

        m = ia.analyze(gp, dp, cfg)
        s_rot = (-m.tilt.in_plane_rotation_deg
                 if (m.tilt is not None and not m.degenerate) else float("nan"))
        d = measure(base, det)
        d_rot = d[0] if d is not None else float("nan")

        s_err = abs(s_rot - rot_true) if np.isfinite(s_rot) else float("inf")
        d_err = abs(d_rot - rot_true) if np.isfinite(d_rot) else float("inf")
        win = "yoğun" if d_err < s_err else ("SIFT" if s_err < d_err else "eşit")
        s_txt = f"{s_rot:.2f}" if np.isfinite(s_rot) else "reddetti"
        print(f"{name:<22}{rot_true:8.2f}{s_txt:>10}{d_rot:10.2f}   {win}")
        rows.append((name, s_err, d_err))

    for p in (gp, dp):
        if os.path.exists(p):
            os.remove(p)
    # Yoğun yöntem hiçbir desende SIFT'ten belirgin kötü olmamalı
    return [n for n, se, de in rows if de > max(se, TOL_ROT_DEG) + 0.3]


def test_degenerate_input():
    """
    [6] Bilgi taşımayan girdide DÜRÜST davranmalı.

    Düz gri alanda hizalama teorik olarak imkânsızdır. Doğru davranış
    bir sayı uydurmak değil, güveni düşük raporlamaktır.
    Tek yönlü çizgi deseni de sınırdadır: dik eksende bilgi yoktur.
    """
    print("\n[6] BİLGİSİZ GİRDİDE DÜRÜSTLÜK")
    fails = []

    flat = np.full((512, 512), 128, np.uint8)
    det, _ = warp_similarity(flat, rot_deg=3.0)
    c = da.coarse_align(flat, det, try_mirrors=False)
    good = not c.ok
    print(f"{'düz gri alan':<22}kaba_ok={c.ok!s:<6} skor={c.response:7.3f}   "
          f"{ok(good)} (reddetmesi bekleniyor)")
    if not good:
        fails.append("düz gri")

    # Periyodik desende çeviri ancak PERİYOT MODÜLO ölçülebilir: 24 px aralıklı
    # çizgilerde 17 px kayma ile 17+24k px kayma görüntü olarak AYIRT EDİLEMEZ.
    # Bu matematiksel bir belirsizliktir, ölçüm hatası değil. Doğru beklenti
    # "17 çıksın" değil, "periyodun katı kadar farkla 17'ye denk gelsin"dir.
    period = 24.0
    lines = pat_lines(step=int(period))
    det, _ = warp_similarity(lines, tx=0.0, ty=17.0)
    c = da.coarse_align(lines, det, try_mirrors=False)
    r = da.refine_ecc(lines, det, init=c.matrix)
    ty_meas = r.homography[1, 2] if r.homography is not None else float("nan")
    resid = abs((ty_meas - 17.0 + period / 2) % period - period / 2)
    good = resid < 1.5
    print(f"{'tek yönlü çizgiler':<22}ty_gerçek=17.0  ty_ölç={ty_meas:8.2f}  "
          f"periyot artığı={resid:5.2f}   {ok(good)}")
    if not good:
        fails.append("çizgiler")
    return fails


def test_artifact_not_called_distortion():
    """
    [7] Keskinlik/örnekleme artefaktını distorsiyon SANMAMALI.

    Bu, projedeki "panel ↔ tablo ayrışması" ile aynı sınıftan bir hatadır:
    ölçüm katmanının ölçemediği yerde bir sayı uydurması. Burada somut
    biçimi şudur — GT ile dedektör çok farklı çözünürlükteyse (894x730 vs
    1600x1600) ince harfler ve kamalar birebir örtüşmez, optik akış bunu
    kayma sanır ve radyal modele uydurulunca "fıçı distorsiyonu" gibi okunur.

    Ayırt edici ölçüt RADYALLİK'tir: gerçek distorsiyon merkezden uzaklığa
    bağlıdır (radyallik ~1.00), artefakt ise desenin ayrıntılı bölgelerine
    yığılır (radyallik belirgin biçimde düşük).
    """
    print("\n[7] ARTEFAKTI DİSTORSİYON SANMIYOR")
    fails = []
    base = pat_random()

    # (a) Gerçek distorsiyon KABUL edilmeli
    for k1 in (0.02, -0.03):
        q = da.analyze_dense(base, apply_barrel(base, k1),
                             try_mirrors=False).residual
        good = q.distortion_trustworthy and q.radial_fraction > 0.9
        print(f"{'gerçek distorsiyon k1=' + format(k1, '+.2f'):<34}"
              f"radyallik={q.radial_fraction:5.2f}   {ok(good)}")
        if not good:
            fails.append(f"k1={k1}")

    # (b) Distorsiyonsuz çift "yok" demeli, "radyal değil" değil
    det, _ = warp_similarity(base, rot_deg=2.0, scale=1.05)
    q = da.analyze_dense(base, det, try_mirrors=False).residual
    good = q.negligible and "distorsiyon yok" in q.distortion_summary()
    print(f"{'distorsiyonsuz çift':<34}RMS={q.rms_dev_px:8.3f}   {ok(good)}")
    if not good:
        fails.append("distorsiyonsuz")

    # (c) Keskinlik farkı REDDEDİLMELİ.
    # Dedektörü 2x büyütüp bulanıklaştırarak gerçek çiftteki durumu taklit et:
    # geometri aynı (distorsiyon YOK), yalnızca örnekleme/keskinlik farklı.
    h, w = base.shape
    soft = cv2.GaussianBlur(cv2.resize(base, (w * 2, h * 2),
                                       interpolation=cv2.INTER_CUBIC),
                            (0, 0), 2.0)
    q = da.analyze_dense(base, soft, try_mirrors=False).residual
    good = (q is not None) and (not q.distortion_trustworthy)
    print(f"{'keskinlik/örnekleme farkı':<34}"
          f"radyallik={q.radial_fraction:5.2f}   {ok(good)} (reddetmeli)")
    if not good:
        fails.append("keskinlik farkı")
    return fails


def main():
    print("=" * 62)
    print("YOĞUN (DENSE) HİZALAMA DOĞRULAMASI")
    print("=" * 62)

    all_fails = {}
    all_fails["desen-agnostiklik"] = test_pattern_agnostic()
    all_fails["dönüşüm taraması"] = test_transform_sweep()
    all_fails["distorsiyon geri okuma"] = test_distortion_recovery()
    all_fails["sahte distorsiyon"] = test_no_false_distortion()
    all_fails["SIFT karşılaştırması"] = test_vs_sift()
    all_fails["bilgisiz girdi"] = test_degenerate_input()
    all_fails["artefakt ayrımı"] = test_artifact_not_called_distortion()

    print("\n" + "=" * 62)
    bad = {k: v for k, v in all_fails.items() if v}
    if not bad:
        print("SONUÇ: TÜM TESTLER GEÇTİ")
        print("Yoğun hizalama desenden bağımsız çalışıyor, bilinen dönüşümleri")
        print("ve distorsiyonu geri okuyor, bilgisiz girdide sayı uydurmuyor.")
        return 0
    print("SONUÇ: BAŞARISIZ")
    for k, v in bad.items():
        print(f"  {k}: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
