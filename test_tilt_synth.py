"""
Sentetik doğrulama: BİLİNEN tilt açısıyla eğilmiş Siemens star üretip
ölçümün bu değeri geri verip vermediğini kontrol eder.

Bir düzlem, optik eksene göre theta kadar eğildiğinde dairesel desen
elipse döner ve eksen oranı cos(theta) olur. Biz de tam bunu simüle
edip acos(oran) ile theta'yı geri okuyabiliyor muyuz diye bakıyoruz.
"""
import sys, math
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import cv2
from core import siemens_star


def make_star(size=900, n_spokes=72, r_frac=0.32):
    """Merkezi Siemens star üretir (dışında düz zemin)."""
    img = np.full((size, size), 235, np.uint8)
    cx = cy = size / 2.0
    R = size * r_frac
    ys, xs = np.mgrid[0:size, 0:size]
    dx, dy = xs - cx, ys - cy
    r = np.hypot(dx, dy)
    th = np.arctan2(dy, dx)
    wedge = ((th + np.pi) / (2 * np.pi) * n_spokes).astype(int) % 2
    inside = r <= R
    img[inside & (wedge == 0)] = 20
    # merkezde küçük beyaz göbek (gerçek chart'ta da var)
    img[r < size * 0.012] = 235
    return img


def tilt_image(img, theta_deg, axis="y"):
    """
    Görüntüyü theta kadar eğilmiş bir düzlemmiş gibi ölçekler.
    Eğik düzlemin izdüşümünde bir eksen cos(theta) kadar kısalır.
    """
    h, w = img.shape
    c = math.cos(math.radians(theta_deg))
    if axis == "y":                       # yatay eksen kısalır
        new = cv2.resize(img, (max(2, int(round(w * c))), h),
                         interpolation=cv2.INTER_AREA)
        out = np.full((h, w), 235, np.uint8)
        x0 = (w - new.shape[1]) // 2
        out[:, x0:x0 + new.shape[1]] = new
    else:                                 # dikey eksen kısalır
        new = cv2.resize(img, (w, max(2, int(round(h * c)))),
                         interpolation=cv2.INTER_AREA)
        out = np.full((h, w), 235, np.uint8)
        y0 = (h - new.shape[0]) // 2
        out[y0:y0 + new.shape[0], :] = new
    return out


base = make_star()
ref = siemens_star.detect_center_ellipse(base)
print("referans (tilt=0): oran=%.4f  ->  ölçülen tilt=%.2f°  güven=%.2f"
      % (ref.axis_ratio, ref.tilt_deg, ref.confidence))
print()
print("%8s %10s %12s %10s %9s" % ("gerçek°", "beklenen", "ölçülen°", "hata°", "güven"))
print("-" * 54)

max_err = 0.0
for theta in (0, 5, 10, 15, 20, 25, 30, 40):
    timg = tilt_image(base, theta, axis="y")
    fit = siemens_star.detect_center_ellipse(timg)
    if not fit.ok:
        print("%8.1f  TESPİT EDİLEMEDİ" % theta)
        continue
    # GT referansına göre normalize et (analyze_pair ile aynı mantık)
    ratio = min(1.0, fit.axis_ratio / max(1e-6, ref.axis_ratio))
    measured = math.degrees(math.acos(ratio))
    err = measured - theta
    max_err = max(max_err, abs(err))
    print("%8.1f %10.4f %12.2f %+10.2f %9.2f"
          % (theta, math.cos(math.radians(theta)), measured, err, fit.confidence))

print("-" * 54)
print("En büyük hata: %.2f°" % max_err)
print("SONUÇ:", "OK — ölçüm tilt'e duyarlı ve doğru" if max_err < 2.0
      else "DİKKAT — sapma büyük")

# Dönme (düzlem-içi) duyarlılığı: elips açısı dönmeyi izliyor mu?
print()
print("Düzlem-içi dönme testi (20° tiltli deseni döndür):")
t20 = tilt_image(base, 25, axis="y")
for rot in (0, 15, 30, 60):
    M = cv2.getRotationMatrix2D((t20.shape[1] / 2, t20.shape[0] / 2), rot, 1.0)
    rimg = cv2.warpAffine(t20, M, (t20.shape[1], t20.shape[0]),
                          borderValue=235)
    fit = siemens_star.detect_center_ellipse(rimg)
    print("   döndürme %3d° -> elips açısı %+7.2f°  (oran %.4f)"
          % (rot, fit.angle_deg, fit.axis_ratio))
