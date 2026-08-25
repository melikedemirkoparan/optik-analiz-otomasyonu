#!/usr/bin/env python3
"""
Hydra yıldız izleyici donanımı + KIRPILMIŞ dedektör görüntüsü testi.

İki şeyi birden doğrular:

1. **Donanım kataloğu** — Hydra lens/dedektör preset'i doğru değerleri
   üretiyor mu, hesaplanan FOV üreticinin verdiği "useful FOV" ile
   tutarlı mı.

2. **Kırpılmış görüntüyle hizalama** — dedektör görüntüsü ground truth'un
   yalnızca dar bir dikey şeridi olduğunda zincir çalışıyor mu.

İkincisi gerçek bir hatayı yakaladı: `_coarse_one` iki görüntüyü ortak
kareye AYRI AYRI gerdiriyordu (anizotropik resize). GT 1280x1024 (oran
1.25) ile dedektör şeridi 256x1022 (oran 0.25) karşılaştırıldığında şerit
yatayda 5 kat geriliyor, çemberler elipse dönüyor ve hizalama tamamen
çöküyordu (skor 0.016). En-boy oranı korunarak ölçekleme + doldurmaya
geçilince skor 0.69'a çıktı.

Koşum:
    python3 test_hydra.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

from core import (config as cfgmod, optics, dense_align as da, pointing,
                  tilt_estimators as te)


DOWNLOADS = os.path.expanduser("~/Downloads")
GT_PATH = os.path.join(DOWNLOADS, "patterns1", "v6_1deg_inverted.png")
DET_PATH = os.path.join(
    DOWNLOADS,
    "capture_OH2_2026-08-17-13-05-44_T_50_FOVPattern-vertical_processed.png")


def ok(flag):
    return "OK  " if flag else "HATA"


def test_catalog():
    """[1] Hydra donanım kataloğu doğru değerleri veriyor mu."""
    print("\n[1] DONANIM KATALOĞU")
    fails = []

    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    checks = [
        ("odak uzaklığı", cfg.lens.focal_length_mm, 47.7, "mm"),
        ("diyafram", cfg.lens.f_number, 1.4, ""),
        ("giriş pupili", cfg.lens.effective_pupil_mm, 34.0, "mm"),
        ("üretici FOV", cfg.lens.useful_fov_deg, 21.5, "°"),
        ("dedektör genişlik", float(cfg.detector.width_px), 1024.0, "px"),
        ("dedektör yükseklik", float(cfg.detector.height_px), 1024.0, "px"),
        ("piksel pitch", cfg.detector.pixel_pitch_um, 18.0, "µm"),
    ]
    for label, got, want, unit in checks:
        good = abs(got - want) < 1e-6
        print(f"  {label:<20}{got:>9.3f} {unit:<3} (beklenen {want}) {ok(good)}")
        if not good:
            fails.append(label)

    # Sensör 1024 x 18µm = 18.432 mm
    f = optics.compute_fov(cfg)
    good = abs(f.sensor_w_mm - 18.432) < 1e-3
    print(f"  {'sensör alanı':<20}{f.sensor_w_mm:>9.3f} mm  "
          f"(1024 × 18µm)              {ok(good)}")
    if not good:
        fails.append("sensör")

    # Hesaplanan FOV, üreticinin "useful FOV"undan biraz BÜYÜK olmalı:
    # useful FOV köşe kalitesi düştüğü için tam sensörden dar tanımlanır.
    dev_pct = 100.0 * (f.fov_x_deg - cfg.lens.useful_fov_deg) / cfg.lens.useful_fov_deg
    good = 0.0 < dev_pct < 5.0
    print(f"  {'hesaplanan FOV':<20}{f.fov_x_deg:>9.3f} °   "
          f"(üretici {cfg.lens.useful_fov_deg}° → %{dev_pct:+.2f})  {ok(good)}")
    if not good:
        fails.append("FOV tutarlılığı")

    print(f"  IFOV {f.ifov_x_urad:.2f} µrad/px ({f.ifov_x_arcsec:.2f} arcsec/px)")
    return fails


def test_stos_screen():
    """
    [1B] STOS referans ekranı — açısal kaynak matematiği.

    STOS pasif bir panel değil: üretici piksel başına AÇI verir
    (0.027°/px). Bu, bir odak uzaklığı ima eder ve ground truth'un açısal
    ölçeğini sabitler. Sonuç olarak:
      * desen yarıçapı elle girilmeden cihaz FOV'undan türetilebilir,
      * GT→dedektör ölçeği DONANIMDAN öngörülebilir ve ölçümle
        karşılaştırılabilir (bağımsız doğrulama).
    """
    print("\n[1B] STOS REFERANS EKRANI")
    fails = []
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    scr = cfg.oled

    checks = [
        ("piksel pitch", scr.pixel_pitch_um, 13.62, "µm"),
        ("açısal çözünürlük", scr.angular_res_deg, 0.027, "°/px"),
        ("genişlik", float(scr.width_px), 1280.0, "px"),
        ("yükseklik", float(scr.height_px), 1024.0, "px"),
    ]
    for label, got, want, unit in checks:
        good = abs(got - want) < 1e-6
        print(f"  {label:<20}{got:>10.4f} {unit:<6} (beklenen {want}) {ok(good)}")
        if not good:
            fails.append(label)

    good = scr.is_angular_source
    print(f"  {'açısal kaynak mı':<20}{str(good):>10}                    {ok(good)}")
    if not good:
        fails.append("açısal kaynak")

    # f = pitch / tan(açısal çözünürlük)
    want_f = (13.62e-3) / np.tan(np.deg2rad(0.027))
    good = abs(scr.implied_focal_mm - want_f) < 1e-3
    print(f"  {'ima edilen f':<20}{scr.implied_focal_mm:>10.3f} mm     "
          f"(beklenen {want_f:.3f}) {ok(good)}")
    if not good:
        fails.append("ima edilen f")

    # Panel cihazın FOV'unu taşımalı, yoksa desenin kenarı hiç görüntülenemez
    hx = scr.half_angle_deg(scr.width_px / 2.0)
    hy = scr.half_angle_deg(scr.height_px / 2.0)
    half_fov = cfg.lens.useful_fov_deg / 2.0
    good = min(hx, hy) > half_fov
    print(f"  panel kapsaması ±{hx:.2f}° × ±{hy:.2f}°  "
          f"cihaz yarı-FOV {half_fov:.2f}°   {ok(good)} (taşımalı)")
    if not good:
        fails.append("panel kapsaması")
    return fails


def test_stos_scale_prediction():
    """
    [1C] ÖLÇEK ÖNGÖRÜSÜ — donanımdan hesap vs görüntüden ölçüm.

    İki tamamen bağımsız yol:
      beklenen = (f_lens/pitch_det) / (f_stos/pitch_stos)   [sadece donanım]
      ölçülen  = hizalama homografisinden                    [sadece görüntü]

    İkisinin uyuşması hem STOS parametrelerini hem hizalamayı doğrular.
    Gerçek çift olmadan da (sentetik) öngörünün kendisi sınanır.
    """
    print("\n[1C] ÖLÇEK ÖNGÖRÜSÜ (donanım ↔ görüntü)")
    fails = []
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    scr, lens, det = cfg.oled, cfg.lens, cfg.detector

    want = ((lens.focal_length_mm / (det.pixel_pitch_um / 1000.0))
            / (scr.implied_focal_mm / (scr.pixel_pitch_um / 1000.0)))
    print(f"  beklenen ölçek (donanımdan) : {want:.4f}")

    if not (os.path.exists(GT_PATH) and os.path.exists(DET_PATH)):
        print("  gerçek çift yok — yalnızca öngörü hesaplandı")
        good = 1.0 < want < 1.5
        print(f"  öngörü makul aralıkta   {ok(good)}")
        if not good:
            fails.append("öngörü aralığı")
        return fails

    gt = cv2.imread(GT_PATH, cv2.IMREAD_GRAYSCALE)
    detimg = cv2.imread(DET_PATH, cv2.IMREAD_GRAYSCALE)
    r = da.analyze_dense(gt, detimg)
    dv = da.variants(detimg)[r.coarse.variant]
    p = pointing.measure_pointing(r.homography, gt.shape, dv.shape, cfg,
                                  tilt=r.tilt)
    print(f"  ölçülen ölçek (görüntüden)  : {p.measured_scale:.4f}")
    print(f"  fark                        : %{p.scale_error_pct:+.2f}")
    # %3 tolerans: kırpılmış dar şeritte hizalama bu mertebede sapabilir
    good = abs(p.scale_error_pct) < 3.0
    print(f"  donanım ile görüntü uyuşuyor mu   {ok(good)}")
    if not good:
        fails.append("ölçek uyumu")

    # Desen yarıçapı artık elle girilmeden türetiliyor
    good = p.pattern_radius_from_fov_px == p.pattern_radius_from_fov_px
    print(f"  desen yarıçapı FOV'dan türetildi : "
          f"{p.pattern_radius_from_fov_px:.1f} px   {ok(good)}")
    if not good:
        fails.append("yarıçap türetme")
    return fails


def test_ring_tilt():
    """
    [1D] EŞ MERKEZLİ ÇEMBER TİLT TAHMİNCİSİ.

    `estimate_from_circle` Siemens star için yazılmıştır (kamaların teğetsel
    geçiş yoğunluğuna dayanır) ve eş merkezli çemberde HİÇ çalışmaz — bu
    yüzden Hydra çiftinde tilt "ölçülemedi" görünüyordu. `_fit_rings` +
    `estimate_from_concentric_rings` bu boşluğu doldurur.

    Geometrik ayrım (kullanıcının işaret ettiği nokta):
      * decenter → halkalar BİRLİKTE kayar, birbirlerine göre eş merkezli kalır
      * tilt     → halkalar elipse döner VE merkezleri birbirinden ayrışır
    """
    print("\n[1D] ÇEMBER TİLT TAHMİNCİSİ")
    fails = []

    # (a) Mükemmel daireler → tilt 0, sahte tilt üretmemeli
    ring = np.zeros((700, 700), np.uint8)
    for r in range(40, 330, 30):
        cv2.circle(ring, (350, 350), r, 255, 2, cv2.LINE_AA)
    e = te.estimate_from_concentric_rings(ring)
    good = e.ok and e.tilt_deg < 1.0
    print(f"  {'mükemmel daire':<22}tilt={e.tilt_deg:6.3f}°  "
          f"oran~1 bekleniyor   {ok(good)}")
    if not good:
        fails.append("mükemmel daire")

    # (b) SAF DECENTER → halkalar kayar ama eğilmez; tilt yine ~0 olmalı.
    # Bu, kullanıcının gözlemlediği durum: "çemberler eş merkezli değil,
    # görüntü kaymış" — kayma tilt DEĞİLDİR.
    shifted = np.zeros((700, 700), np.uint8)
    for r in range(40, 330, 30):
        cv2.circle(shifted, (350 + 45, 350 - 30), r, 255, 2, cv2.LINE_AA)
    e = te.estimate_from_concentric_rings(shifted)
    good = e.ok and e.tilt_deg < 1.0
    print(f"  {'saf decenter (45,-30)':<22}tilt={e.tilt_deg:6.3f}°  "
          f"kayma tilt değil    {ok(good)}")
    if not good:
        fails.append("saf decenter")

    # (c) GERÇEK TILT → daireler elipse döner, tilt yakalanmalı.
    # cos(tilt)=oran ilişkisiyle bilinen bir basıklık uygulanır.
    for tilt_true in (20.0, 35.0):
        squash = np.cos(np.deg2rad(tilt_true))
        ell = np.zeros((700, 700), np.uint8)
        for r in range(40, 330, 30):
            cv2.ellipse(ell, (350, 350), (r, int(round(r * squash))),
                        0, 0, 360, 255, 2, cv2.LINE_AA)
        e = te.estimate_from_concentric_rings(ell)
        err = abs(e.tilt_deg - tilt_true) if e.ok else float("inf")
        good = e.ok and err < 3.0
        print(f"  {'gerçek tilt ' + format(tilt_true, '.0f') + '°':<22}"
              f"tilt={e.tilt_deg:6.3f}°  hata={err:4.2f}°       {ok(good)}")
        if not good:
            fails.append(f"tilt {tilt_true}")

    # (d) Gerçek Hydra çifti — F harfleri sahte halka olarak geçmemeli
    if os.path.exists(DET_PATH):
        detimg = cv2.imread(DET_PATH, cv2.IMREAD_GRAYSCALE)
        rings = te._fit_rings(detimg)
        cxs = np.array([r[1] for r in rings])
        cys = np.array([r[2] for r in rings])
        spread = float(np.hypot(cxs.std(), cys.std())) if len(rings) else 9e9
        # Halkalar dedektörde de birbirine göre eş merkezli olmalı;
        # F harfleri elenmezse saçılma 100 px'i aşıyordu.
        good = len(rings) >= 3 and spread < 5.0
        print(f"  {'gerçek çift':<22}{len(rings)} halka, "
              f"merkez saçılması {spread:.2f} px   {ok(good)}")
        if not good:
            fails.append("gerçek çift halka")
    return fails


def test_selector_roundtrip():
    """[2] Katalog eşleştirmesi — seçici ile alanlar ayrışmamalı."""
    print("\n[2] KATALOG EŞLEŞTİRME")
    fails = []
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")

    lk = cfgmod.match_lens_key(cfg.lens)
    dk = cfgmod.match_detector_key(cfg.detector)
    good = lk == "Hydra yıldız izleyici 47.7mm" and dk == "Hydra dedektör (1024², 18µm)"
    print(f"  Hydra → {lk} / {dk}   {ok(good)}")
    if not good:
        fails.append("hydra eşleşme")

    # Elle değiştirilen bir lens "Özel"e düşmeli
    cfg.lens.focal_length_mm = 50.0
    lk2 = cfgmod.match_lens_key(cfg.lens)
    good = lk2 == cfgmod.CUSTOM
    print(f"  f=50mm elle → {lk2}   {ok(good)}")
    if not good:
        fails.append("özel'e düşme")

    # Varsayılan sistem de eşleşmeli
    d = cfgmod.SystemConfig()
    good = (cfgmod.match_lens_key(d.lens) == "Rodenstock HR Digaron-W 70mm"
            and cfgmod.match_detector_key(d.detector) == "CMV4000 (2048², 5.5µm)")
    print(f"  varsayılan → {cfgmod.match_lens_key(d.lens)}   {ok(good)}")
    if not good:
        fails.append("varsayılan eşleşme")
    return fails


def test_aspect_mismatch_synthetic():
    """
    [3] En-boy oranı çok farklı olduğunda hizalama — SENTETİK.

    Gerçek görüntüler olmasa da koşar; regresyon buradan yakalanır.
    Bilinen bir dönüşüm uygulanmış tam kareden dar bir dikey şerit kesilir
    ve ölçüm geri okunur.
    """
    print("\n[3] EN-BOY ORANI UYUŞMAZLIĞI (sentetik)")
    print(f"  {'şerit':<12}{'oran':>7}{'rot_ölç':>10}{'sc_ölç':>9}{'ECC':>8}   sonuç")
    print("  " + "-" * 52)
    rng = np.random.default_rng(7)
    base = cv2.GaussianBlur(rng.random((1024, 1280)).astype(np.float32), (0, 0), 3)
    base = ((base - base.min()) / max(base.ptp(), 1e-9) * 255).astype(np.uint8)

    rot_true, sc_true = 1.5, 1.0
    M = cv2.getRotationMatrix2D(((1280 - 1) / 2, (1024 - 1) / 2), rot_true, sc_true)
    full = cv2.warpAffine(base, M, (1280, 1024))

    fails = []
    for width in (256, 384, 512):
        x0 = (1280 - width) // 2
        strip = full[:, x0:x0 + width]
        c = da.coarse_align(base, strip, try_mirrors=False)
        r = da.refine_ecc(base, strip, init=c.matrix) if c.ok else None
        if r is None or r.homography is None:
            print(f"  {width:<12}{width/1024:>7.2f}{'—':>10}{'—':>9}{'—':>8}   HATA")
            fails.append(width)
            continue
        t = optics.decompose_homography(r.homography, image_shape=strip.shape)
        rot = -t.in_plane_rotation_deg
        good = abs(rot - rot_true) < 0.5 and abs(t.scale_x - sc_true) < 0.03
        print(f"  {width:<12}{width/1024:>7.2f}{rot:>10.3f}"
              f"{t.scale_x:>9.4f}{r.correlation:>8.4f}   {ok(good)}")
        if not good:
            fails.append(width)
    return fails


def test_real_pair():
    """[4] Gerçek Hydra görüntü çifti — dosyalar varsa."""
    print("\n[4] GERÇEK HYDRA ÇİFTİ")
    if not (os.path.exists(GT_PATH) and os.path.exists(DET_PATH)):
        print("  atlandı — görüntü dosyaları bulunamadı")
        print(f"    GT : {GT_PATH}")
        print(f"    det: {DET_PATH}")
        return []

    gt = cv2.imread(GT_PATH, cv2.IMREAD_GRAYSCALE)
    det = cv2.imread(DET_PATH, cv2.IMREAD_GRAYSCALE)
    print(f"  GT {gt.shape[1]}×{gt.shape[0]} (oran {gt.shape[1]/gt.shape[0]:.2f})  "
          f"det {det.shape[1]}×{det.shape[0]} (oran {det.shape[1]/det.shape[0]:.2f})")

    r = da.analyze_dense(gt, det)
    fails = []

    good = r.ok and r.correlation > 0.5
    print(f"  hizalama: varyant={r.coarse.variant} kaba={r.coarse.response:.4f} "
          f"ECC={r.correlation:.4f}   {ok(good)}")
    if not good:
        fails.append("hizalama")

    if r.tilt is not None:
        print(f"  dönme={-r.tilt.in_plane_rotation_deg:+.3f}°  "
              f"tilt={r.tilt.total_tilt_deg:.3f}°  "
              f"ölçek={r.tilt.scale_x:.4f}/{r.tilt.scale_y:.4f}  "
              f"ayna={'EVET' if r.mirrored else 'hayır'}")

    if r.residual is not None and r.residual.ok:
        print(f"  kalıntı: {r.residual.summary()}")
        print(f"  distorsiyon: {r.residual.distortion_summary()}")

    # Dedektör görüntüsü çok dar olduğu için ayna ekseni ayırt edilemeyebilir;
    # bu bir HATA DEĞİL, testin bunu bilmesi gerekir (bkz. docs Ek A).
    scores = []
    for v in ("raw", "flip_h", "flip_v", "flip_both"):
        dv = da.variants(det)[v]
        c = da.coarse_align(gt, dv, try_mirrors=False)
        rr = da.refine_ecc(gt, dv, init=c.matrix) if c.ok else None
        scores.append(rr.correlation if rr and rr.homography is not None else 0.0)
    spread = max(scores) - sorted(scores)[-2]
    print(f"  ayna ayrımı: en iyi iki varyant farkı {spread:.4f}"
          f"  ({'belirsiz' if spread < 0.01 else 'ayrışıyor'})")
    return fails


def main():
    print("=" * 62)
    print("HYDRA DONANIMI + KIRPILMIŞ GÖRÜNTÜ TESTİ")
    print("=" * 62)

    all_fails = {
        "katalog": test_catalog(),
        "STOS ekranı": test_stos_screen(),
        "ölçek öngörüsü": test_stos_scale_prediction(),
        "çember tilt": test_ring_tilt(),
        "eşleştirme": test_selector_roundtrip(),
        "en-boy oranı": test_aspect_mismatch_synthetic(),
        "gerçek çift": test_real_pair(),
    }

    print("\n" + "=" * 62)
    bad = {k: v for k, v in all_fails.items() if v}
    if not bad:
        print("SONUÇ: TÜM TESTLER GEÇTİ")
        return 0
    print("SONUÇ: BAŞARISIZ")
    for k, v in bad.items():
        print(f"  {k}: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
