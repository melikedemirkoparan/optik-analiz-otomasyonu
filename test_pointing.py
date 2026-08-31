#!/usr/bin/env python3
"""
Yönelim hatası (decenter / tilt / roll) + FOV kapsaması doğrulaması.

Bilinen kaydırma, dönme ve kırpma uygulanmış sentetik çiftlerde ölçümün
geri okunup okunmadığını sınar. Üçü de `dense_align` homografisinden
türetildiği için bu test aynı zamanda o zincirin regresyon koruması.

Koşum:
    python3 test_pointing.py
"""
from __future__ import annotations

import math
import os
import sys

import cv2
import numpy as np

from core import pointing, config as cfgmod, dense_align as da


TOL_PX = 0.5          # decenter piksel toleransı
TOL_ROLL_DEG = 0.05   # roll toleransı
TOL_COVER = 0.02      # kapsama oranı toleransı


def ok(flag):
    return "OK  " if flag else "HATA"


def texture(n=1024, seed=3):
    rng = np.random.default_rng(seed)
    a = cv2.GaussianBlur(rng.random((n, n)).astype(np.float32), (0, 0), 3)
    return ((a - a.min()) / max(a.ptp(), 1e-9) * 255).astype(np.uint8)


def align(gt, det):
    c = da.coarse_align(gt, det, try_mirrors=False)
    if not c.ok:
        return None
    r = da.refine_ecc(gt, det, init=c.matrix)
    return r.homography if r.homography is not None else None


def test_decenter():
    """[1] Bilinen kaydırma → decenter, piksel ve açı olarak."""
    print("\n[1] DECENTER (bore-sight kaçıklığı)")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    base = texture()
    print(f"  1 px = {pointing.px_to_deg(cfg, 1.0):.5f}°  "
          f"(IFOV {cfg.detector.pixel_pitch_um}µm / f={cfg.lens.focal_length_mm}mm)")
    print(f"  {'dx':>5}{'dy':>5}{'dx_ölç':>10}{'dy_ölç':>10}"
          f"{'açı_ölç':>10}{'açı_bek':>10}   sonuç")
    print("  " + "-" * 58)

    fails = []
    for dx, dy in [(0, 0), (20, 0), (0, -35), (50, 30), (-15, -15)]:
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        det = cv2.warpAffine(base, M, base.shape[::-1])
        H = align(base, det)
        if H is None:
            print(f"  {dx:>5}{dy:>5}   hizalanamadı   HATA")
            fails.append((dx, dy))
            continue
        p = pointing.measure_pointing(H, base.shape, det.shape, cfg)
        want = pointing.px_to_deg(cfg, math.hypot(dx, dy))
        good = (abs(p.decenter_x_px - dx) < TOL_PX
                and abs(p.decenter_y_px - dy) < TOL_PX
                and abs(p.decenter_deg - want) < 0.01)
        print(f"  {dx:>5}{dy:>5}{p.decenter_x_px:>10.3f}{p.decenter_y_px:>10.3f}"
              f"{p.decenter_deg:>10.4f}{want:>10.4f}   {ok(good)}")
        if not good:
            fails.append((dx, dy))
    return fails


def test_roll_tilt():
    """[2] Bilinen dönme → roll; saf dönmede tilt sıfır kalmalı."""
    print("\n[2] ROLL / TILT")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    base = texture()
    print(f"  {'roll_ger':>9}{'roll_ölç':>10}{'tilt_ölç':>10}"
          f"{'decenter':>10}   sonuç")
    print("  " + "-" * 48)

    fails = []
    for rot in [0.0, 1.5, -4.0, 12.0]:
        h, w = base.shape
        M = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), rot, 1.0)
        det = cv2.warpAffine(base, M, (w, h))
        H = align(base, det)
        if H is None:
            print(f"  {rot:>9.2f}   hizalanamadı   HATA")
            fails.append(rot)
            continue
        p = pointing.measure_pointing(H, base.shape, det.shape, cfg)
        # optics konvansiyonu dönmeyi ters işaretle verir
        roll = -p.roll_deg
        # Saf dönmede tilt ve decenter sıfır olmalı — sahte hata üretilmemeli
        good = (abs(roll - rot) < TOL_ROLL_DEG
                and p.tilt_deg < 0.05
                and p.decenter_px < TOL_PX)
        print(f"  {rot:>9.2f}{roll:>10.3f}{p.tilt_deg:>10.4f}"
              f"{p.decenter_px:>10.3f}   {ok(good)}")
        if not good:
            fails.append(rot)
    return fails


def _disk_square_overlap(r: float, a: float) -> float:
    """
    Merkezleri çakışık dairenin (yarıçap r) kareyle (yarı-kenar a) kesişim
    alanı — analitik.

    Beklenti ölçüm kodundan BAĞIMSIZ üretilsin diye kapalı formülle yazıldı;
    aynı kırpma rutinini çağırıp karşılaştırmak testi tautolojiye çevirirdi.
    """
    if r <= a:
        return math.pi * r * r                    # daire tamamen karede
    if r >= a * math.sqrt(2.0):
        return 4.0 * a * a                        # kare tamamen dairede
    # Daire kenarları kesiyor, köşeler dışarıda: dört AYRIK daire dilimi
    # (köşeler dairenin dışında olduğu için dilimler örtüşmez) düşülür.
    seg = r * r * (math.acos(a / r) - (a / r) * math.sqrt(1.0 - (a / r) ** 2))
    return math.pi * r * r - 4.0 * seg


def test_coverage():
    """
    [3] Kapsama — payda EKRANIN TAMAMI DEĞİL, cihazın gördüğü orta daire.

    Ground truth referans ekranın tüm karesidir; cihaz onun yalnızca
    ortasındaki daireyi görebilir. "Desenin ne kadarı kullanıldı" sorusunun
    paydası bu yüzden o dairedir. Beklenti daire∩kare kesişiminden analitik
    hesaplanır; ölçümün bunu geri vermesi bölgenin doğru kurulduğunu
    kanıtlar.
    """
    print("\n[3] KAPSAMA (desenin ne kadarı sensörde)")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    base = texture()
    n = base.shape[0]
    print(f"  {'kesit':>7}{'yarıçap':>9}{'görünen_ölç':>13}{'görünen_bek':>13}"
          f"{'sensör_dolu':>13}   sonuç")
    print("  " + "-" * 62)

    fails = []
    # (kesit, desen yarıçapı) — sırasıyla: daire kesite sığıyor / kesit
    # dairenin içinde / daire kenarları kesiyor / daire tam kadrajda.
    for side, radius in ((500, 200.0), (500, 403.0),
                         (700, 403.0), (1024, 403.0)):
        o = (n - side) // 2
        crop = base[o:o + side, o:o + side]
        H = align(base, crop)
        if H is None:
            print(f"  {side:>7}{radius:>9.0f}   hizalanamadı   HATA")
            fails.append((side, radius))
            continue
        p = pointing.measure_pointing(H, base.shape, crop.shape, cfg,
                                      pattern_radius_px=radius)
        want = (_disk_square_overlap(radius, side / 2.0)
                / (math.pi * radius * radius))
        good = abs(p.coverage_frac - want) < TOL_COVER
        print(f"  {side:>7}{radius:>9.0f}{100*p.coverage_frac:>12.1f}%"
              f"{100*want:>12.1f}%{100*p.sensor_fill_frac:>12.1f}%   {ok(good)}")
        if not good:
            fails.append((side, radius))

    # Payda gerçekten daire mi — ekranın tamamı olsaydı 1024² = 1.048.576.
    crop = base
    H = align(base, crop)
    p = pointing.measure_pointing(H, base.shape, crop.shape, cfg,
                                  pattern_radius_px=403.0)
    want_area = math.pi * 403.0 ** 2
    good = (abs(p.pattern_area_gt_px - want_area) < 0.01 * want_area
            and p.pattern_area_gt_px < 0.6 * n * n)
    print(f"  payda: {p.pattern_area_gt_px:,.0f} px (daire {want_area:,.0f}, "
          f"ekranın tamamı {n*n:,})   {ok(good)}")
    if not good:
        fails.append("payda ekranın tamamı")

    # Yarıçap hiç bilinmiyorsa bölge zorunlu olarak tüm ekrandır — ve bunu
    # SÖYLER. Pasif panelli preset'te otomatik türetme yoktur.
    cfg_pasif = cfgmod.system_from_preset("CMV4000 + Rodenstock 70mm")
    side = 500
    o = (n - side) // 2
    crop = base[o:o + side, o:o + side]
    H = align(base, crop)
    p = pointing.measure_pointing(H, base.shape, crop.shape, cfg_pasif)
    want = (side * side) / float(n * n)
    good = (abs(p.coverage_frac - want) < TOL_COVER
            and "tüm ekran" in p.ref_region)
    print(f"  yarıçap bilinmiyor → bölge {p.ref_region!r}, "
          f"kapsama {100*p.coverage_frac:.1f}% (bek {100*want:.1f}%)   {ok(good)}")
    if not good:
        fails.append("yarıçapsız geri düşüş")

    # Kenar açıları simetrik ve FOV yarısına eşit olmalı
    crop = base
    H = align(base, crop)
    p = pointing.measure_pointing(H, base.shape, crop.shape, cfg)
    e = p.edge_angles_deg
    half_fov = p.fov_x_deg / 2.0
    good = all(abs(v - half_fov) < 0.3 for v in e.values())
    print(f"  kenar açıları: " + "  ".join(f"{k} {v:.2f}°" for k, v in e.items())
          + f"   (yarı-FOV {half_fov:.2f}°)   {ok(good)}")
    if not good:
        fails.append("kenar açıları")
    return fails


def test_margin():
    """[4] Desen payı — verilen yarıçapla sığıyor mu kararı."""
    print("\n[4] DESEN PAYI (sensöre sığıyor mu)")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    base = texture()
    n = base.shape[0]
    side = 500
    o = (n - side) // 2
    crop = base[o:o + side, o:o + side]
    H = align(base, crop)
    if H is None:
        print("  hizalanamadı")
        return ["hizalama"]

    fails = []
    # Kesit GT'nin merkezi 500x500'ü; ölçek ~1, yani yarıçap 200 sığar, 400 taşar.
    print(f"  {'yarıçap':>9}{'pay_px':>10}{'pay_deg':>10}{'sığıyor':>10}   sonuç")
    print("  " + "-" * 46)
    for radius, expect_fit in ((200.0, True), (400.0, False)):
        p = pointing.measure_pointing(H, base.shape, crop.shape, cfg,
                                      pattern_radius_px=radius)
        good = p.pattern_fully_visible == expect_fit
        print(f"  {radius:>9.0f}{p.margin_px:>10.1f}{p.margin_deg:>10.3f}"
              f"{str(p.pattern_fully_visible):>10}   {ok(good)}")
        if not good:
            fails.append(radius)

    # Bağlayan sınır her zaman sensörün kenarı DEĞİL. Hydra'da lensin
    # görüntü dairesi 503 px, sensörün yarı-kenarı 512 px: r=480'lik bir
    # desen dikdörtgene 31.5 px payla sığar ama daireye yalnızca 23.1 px
    # payla. Bu ayrım pratikte "daha büyük dedektör al" ile "lensi değiştir"
    # arasındaki farktır.
    H_full = align(base, base)
    if H_full is None:
        return fails + ["tam kadraj hizalama"]
    p = pointing.measure_pointing(H_full, base.shape, base.shape, cfg,
                                  pattern_radius_px=480.0)
    good = (p.margin_limit == "görüntü dairesi"
            and abs(p.margin_px - 23.1) < 2.0)
    print(f"  görüntü dairesi sınırı: pay {p.margin_px:+.1f} px "
          f"(sınır {p.margin_limit!r}, kenar payı olsa +31.5)   {ok(good)}")
    if not good:
        fails.append("görüntü dairesi sınırı")
    return fails


def test_combined():
    """[5] Kaydırma + dönme birlikte — bileşenler karışmamalı."""
    print("\n[5] BİLEŞİK (kaydırma + dönme aynı anda)")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    base = texture()
    h, w = base.shape
    print(f"  {'dx':>5}{'dy':>5}{'roll':>7}"
          f"{'dx_ölç':>10}{'dy_ölç':>10}{'roll_ölç':>10}   sonuç")
    print("  " + "-" * 56)

    fails = []
    for dx, dy, rot in [(30, -20, 2.5), (-40, 25, -3.0)]:
        M = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), rot, 1.0)
        M[0, 2] += dx
        M[1, 2] += dy
        det = cv2.warpAffine(base, M, (w, h))
        H = align(base, det)
        if H is None:
            print(f"  {dx:>5}{dy:>5}{rot:>7.1f}   hizalanamadı   HATA")
            fails.append((dx, dy, rot))
            continue
        p = pointing.measure_pointing(H, base.shape, det.shape, cfg)
        roll = -p.roll_deg
        good = (abs(p.decenter_x_px - dx) < 1.0
                and abs(p.decenter_y_px - dy) < 1.0
                and abs(roll - rot) < TOL_ROLL_DEG)
        print(f"  {dx:>5}{dy:>5}{rot:>7.1f}{p.decenter_x_px:>10.3f}"
              f"{p.decenter_y_px:>10.3f}{roll:>10.3f}   {ok(good)}")
        if not good:
            fails.append((dx, dy, rot))
    return fails


def test_large_rotation():
    """
    [7] BÜYÜK AÇI DÖNMELER — ±90'a katlama hatasının regresyonu.

    Gerçek bir hata: 136° dönme ±90'a katlanınca 44° olarak raporlanıyordu
    ve kullanıcı tamamen farklı bir yönelim okuyordu. Ayrıca dairesel
    simetrik desende (eş merkezli çember) log-polar faz korelasyonu dönmeyi
    hiç çözemeyip 0° veriyordu.

    Bu test iki şeyi birden korur:
      * büyük açılar katlanmadan raporlanıyor mu (`roll_full_deg`)
      * simetrik desende açı taraması gerçek tepeyi buluyor mu
    """
    print("\n[7] BÜYÜK AÇI DÖNMELER (katlama regresyonu)")
    cfg = cfgmod.system_from_preset("Hydra yıldız izleyici")
    fails = []

    # (a) Dokulu desende büyük açılar
    base = texture()
    h, w = base.shape
    print(f"  {'gerçek':>8}{'roll_ölç':>11}{'katlı':>9}   sonuç   (rastgele doku)")
    print("  " + "-" * 48)
    for rot in (30.0, 136.0, 200.0, 315.0):
        M = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), rot, 1.0)
        det = cv2.warpAffine(base, M, (w, h))
        H = align(base, det)
        if H is None:
            print(f"  {rot:>8.1f}   hizalanamadı   HATA")
            fails.append(rot)
            continue
        p = pointing.measure_pointing(H, base.shape, det.shape, cfg)
        # ölçülen ile gerçek arasındaki dairesel fark
        d = abs((p.roll_full_deg - rot + 180.0) % 360.0 - 180.0)
        good = d < 1.0
        print(f"  {rot:>8.1f}{p.roll_full_deg:>11.3f}{p.roll_deg:>9.2f}   {ok(good)}")
        if not good:
            fails.append(rot)

    # (b) Dairesel simetrik desen — asıl zor durum.
    # Saf çember dönme altında değişmez; ayrımı simetri kırıcı işaretler
    # yapar. Bu, gerçek paterndeki F harflerinin karşılığıdır.
    ring = np.zeros((512, 512), np.uint8)
    for r in range(30, 240, 24):
        cv2.circle(ring, (256, 256), r, 255, 2, cv2.LINE_AA)
    cv2.line(ring, (256, 256), (256, 150), 255, 3)          # tek kollu işaret
    cv2.rectangle(ring, (300, 300), (340, 320), 255, -1)    # asimetrik blok
    print(f"  {'gerçek':>8}{'roll_ölç':>11}{'':>9}   sonuç   (simetrik çember)")
    print("  " + "-" * 48)
    for rot in (136.0, 250.0):
        M = cv2.getRotationMatrix2D((255.5, 255.5), rot, 1.0)
        det = cv2.warpAffine(ring, M, (512, 512))
        H = align(ring, det)
        if H is None:
            print(f"  {rot:>8.1f}   hizalanamadı   HATA")
            fails.append(("çember", rot))
            continue
        p = pointing.measure_pointing(H, ring.shape, det.shape, cfg)
        d = abs((p.roll_full_deg - rot + 180.0) % 360.0 - 180.0)
        good = d < 2.0
        print(f"  {rot:>8.1f}{p.roll_full_deg:>11.3f}{'':>9}   {ok(good)}")
        if not good:
            fails.append(("çember", rot))
    return fails


def test_hardware_independence():
    """
    [6] Aynı piksel kaçıklığı, farklı donanım → farklı açı.

    Decenter'ın açıya çevrilmesi IFOV'a bağlıdır; bu testin amacı
    dönüşümün gerçekten parametrik olduğunu (koda gömülü olmadığını)
    göstermek.
    """
    print("\n[6] DONANIM BAĞIMLILIĞI (parametrik olduğunun kanıtı)")
    base = texture()
    det = cv2.warpAffine(base, np.float32([[1, 0, 40], [0, 1, 0]]),
                         base.shape[::-1])
    H = align(base, det)
    fails = []
    print(f"  {'sistem':<26}{'IFOV µrad':>11}{'40px →':>10}")
    print("  " + "-" * 48)
    seen = []
    for key in cfgmod.SYSTEM_PRESETS:
        cfg = cfgmod.system_from_preset(key)
        p = pointing.measure_pointing(H, base.shape, det.shape, cfg)
        print(f"  {key:<26}{p.ifov_urad:>11.2f}{p.decenter_deg:>9.4f}°")
        seen.append(p.decenter_deg)
    good = len(set(round(v, 4) for v in seen)) == len(seen)
    print(f"  farklı donanım → farklı açı   {ok(good)}")
    if not good:
        fails.append("parametrik değil")
    return fails


def main():
    print("=" * 62)
    print("YÖNELİM HATASI (DECENTER / TILT / ROLL) DOĞRULAMASI")
    print("=" * 62)

    all_fails = {
        "decenter": test_decenter(),
        "roll/tilt": test_roll_tilt(),
        "kapsama": test_coverage(),
        "desen payı": test_margin(),
        "bileşik": test_combined(),
        "büyük açı": test_large_rotation(),
        "donanım bağımlılığı": test_hardware_independence(),
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
