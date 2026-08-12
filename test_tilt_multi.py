#!/usr/bin/env python3
"""
Çoklu tilt ölçüm katmanının doğrulaması (core/tilt_estimators.py).

Bu test iki şeyi sınar:

  1. ÖLÇÜM DOĞRULUĞU — bilinen açılarla üretilmiş sentetik veriden tilt geri
     okunabiliyor mu.
  2. DÜRÜSTLÜK — ölçülemeyen durumlarda sistem sayı UYDURUYOR mu. Bu en az
     birincisi kadar önemlidir: eski akış, ölçemediği tilt'i 0.000° diye
     gösteriyordu.

Kullanım:
    python3 test_tilt_multi.py
"""
import math

import cv2
import numpy as np

from core import tilt_estimators as te
from core.config import SystemConfig


def _synth_star(size=700, wedges=55, tilt_deg=0.0, radius_frac=0.30):
    """Bilinen tilt ile eğilmiş sentetik Siemens star üretir."""
    img = np.full((size, size), 255, np.uint8)
    cx = cy = size / 2.0
    r = size * radius_frac
    ratio = math.cos(math.radians(tilt_deg))      # eğiklik -> elips oranı
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    dx = xx - cx
    dy = (yy - cy) / max(1e-6, ratio)             # y ekseninde basıklaştır
    rr = np.hypot(dx, dy)
    th = np.arctan2(dy, dx)
    wedge = (np.sin(th * wedges) > 0)
    img[(rr < r) & wedge] = 0
    return img


def _synth_grid(size=800, step=60):
    img = np.full((size, size), 255, np.uint8)
    for i in range(0, size, step):
        cv2.line(img, (i, 0), (i, size), 0, 3)
        cv2.line(img, (0, i), (size, i), 0, 3)
    return img


def test_circle_accuracy():
    """Dairesel desen yöntemi bilinen açıları geri okuyabilmeli."""
    print("\n1) DAİRE/ELİPS YÖNTEMİ — bilinen açıların geri okunması")
    print(f"   {'gerçek':>7} {'ölçülen':>9} {'±sigma':>8} {'hata':>7}  durum")
    gt = _synth_star(tilt_deg=0.0)
    worst = 0.0
    for t in (0.0, 5.0, 10.0, 20.0, 30.0, 40.0):
        det = _synth_star(tilt_deg=t)
        e = te.estimate_from_circle(gt, det)
        if not e.ok:
            print(f"   {t:7.1f} {'ölçüm yok':>9} {'--':>8} {'--':>7}  {e.detail[:34]}")
            continue
        err = abs(e.tilt_deg - t)
        worst = max(worst, err if t > 4 else 0.0)   # <4°: yöntem zaten duyarsız
        durum = "çözülebilir" if e.resolvable else "gürültü altında"
        print(f"   {t:7.1f} {e.tilt_deg:9.2f} {e.sigma_deg:8.2f} {err:7.2f}  {durum}")
    print(f"   -> çözülebilir aralıkta en büyük hata: {worst:.2f}°")
    return worst


def test_resolution_limit():
    """Küçük açılarda 'ölçemiyorum' demeli — sıfır uydurmamalı."""
    print("\n2) DÜRÜSTLÜK — küçük açılar gürültü sınırının altında mı")
    gt = _synth_star(tilt_deg=0.0)
    ok = True
    for t in (0.0, 1.0, 2.0):
        det = _synth_star(tilt_deg=t)
        e = te.estimate_from_circle(gt, det)
        if e.ok and e.resolvable:
            print(f"   {t:5.1f}° -> HATA: çözülebilir sayıldı "
                  f"({e.tilt_deg:.2f}° ± {e.sigma_deg:.2f}°)")
            ok = False
        else:
            print(f"   {t:5.1f}° -> doğru: '< {e.sigma_deg:.2f}°' olarak raporlandı")
    print(f"   -> {'GEÇTİ' if ok else 'KALDI'}")
    return ok


def test_no_pattern_refuses():
    """Bilinen geometri yoksa sistem sayı üretmemeli."""
    print("\n3) DÜRÜSTLÜK — desensiz görüntüde ölçüm reddediliyor mu")
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, (600, 600), dtype=np.uint8)
    rep = te.measure_tilt(noise, noise, SystemConfig())
    circle = next(e for e in rep.estimates if e.method == "circle_ellipse")
    ok = not circle.ok
    print(f"   dairesel desen ölçümü: {'reddedildi (doğru)' if ok else 'ÜRETİLDİ (hata)'}")
    print(f"   detay: {circle.detail[:70]}")

    # Asıl sınav: NİHAİ rapor da sayı üretmemeli. Deneysel bir yöntem
    # gürültüden tilt "bulsa" bile bu kullanıcıya ölçüm diye sunulmamalı.
    if not rep.ok:
        print("   nihai rapor: ölçülemedi -> doğru davranış")
    else:
        print(f"   nihai rapor: {rep.summary()} (yöntem: {rep.primary_method})"
              "  <- HATA: desensiz görüntüden ölçüm üretildi")
        ok = False
    print(f"   -> {'GEÇTİ' if ok else 'KALDI'}")
    return ok


def test_grid_experimental():
    """Izgara yöntemi deneysel işaretli olmalı ve birincil seçilmemeli."""
    print("\n4) IZGARA YÖNTEMİ — deneysel statü korunuyor mu")
    f_px = 70.0 / 0.0055
    e = te.estimate_from_grid(_synth_grid(), focal_px=f_px)
    if e.ok:
        print(f"   ölçüm üretildi: {e.tilt_deg:.2f}° · deneysel={e.experimental}")
        ok = e.experimental
    else:
        print(f"   ölçüm yok: {e.detail[:60]}")
        ok = True
    print(f"   -> {'GEÇTİ' if ok else 'KALDI'}")
    return ok


def test_real_pair():
    """Gerçek görüntü çiftiyle uçtan uca rapor."""
    print("\n5) GERÇEK GÖRÜNTÜ ÇİFTİ")
    GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
    DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"
    gt = cv2.imread(GT, 0)
    det = cv2.imread(DET, 0)
    if gt is None or det is None:
        print("   (görüntüler bulunamadı — atlandı)")
        return True
    rep = te.measure_tilt(gt, det, SystemConfig())
    for e in rep.estimates:
        flag = " [deneysel]" if e.experimental else ""
        if e.ok:
            print(f"   {e.method:16s} {e.tilt_deg:7.3f}° ± {e.sigma_deg:.2f}°{flag}")
        else:
            print(f"   {e.method:16s} ölçüm yok — {e.detail[:44]}")
    print(f"   NİHAİ: {rep.summary()}  (yöntem: {rep.primary_method or 'yok'})")
    for m in rep.messages:
        print(f"     · {m[:88]}")
    return True


if __name__ == "__main__":
    print("=" * 72)
    print("ÇOKLU TİLT ÖLÇÜM KATMANI — DOĞRULAMA")
    print("=" * 72)
    worst = test_circle_accuracy()
    r2 = test_resolution_limit()
    r3 = test_no_pattern_refuses()
    r4 = test_grid_experimental()
    test_real_pair()

    print("\n" + "=" * 72)
    passed = (worst < 1.0) and r2 and r3 and r4
    print("SONUÇ:", "TÜM TESTLER GEÇTİ" if passed else "BAŞARISIZ TEST VAR")
    print("=" * 72)
