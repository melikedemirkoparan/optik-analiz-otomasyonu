"""Tam akış testi — GUI olmadan pipeline.run_analysis'i uçtan uca koşturur."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2

from core.config import default_config
from core import pipeline

GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"

cfg = default_config()
print("=" * 66)
print("SİSTEM:", cfg.name)


def prog(p, m):
    print(f"  [{p:3d}%] {m}")


res = pipeline.run_analysis(GT, DET, cfg, progress=prog)

print("-" * 66)
if res.fov:
    f = res.fov
    print("FOV  : yatay %.3f°  dikey %.3f°  köşegen %.3f°" % (
        f.fov_x_deg, f.fov_y_deg, f.fov_diag_deg))
    print("IFOV : %.2f µrad/px  (%.3f arcsec/px)" % (
        f.ifov_x_urad, f.ifov_x_arcsec))
    print("Sensör: %.2f × %.2f mm" % (f.sensor_w_mm, f.sensor_h_mm))

print("-" * 66)
print("Düzlem-içi dönme : %+.3f°" % res.rotation_deg)
print("Düzlem-dışı tilt : %.3f°" % res.tilt_deg)
print("Ayna (flip)      : %s" % ("EVET" if res.mirrored else "hayır"))

if res.star and res.star.ok:
    g, d = res.star.gt_ellipse, res.star.det_ellipse
    print("Elips GT  : oran=%.4f açı=%+.1f° güven=%.2f  (r=%.0f px)" % (
        g.axis_ratio, g.angle_deg, g.confidence, g.major_axis / 2))
    print("Elips DET : oran=%.4f açı=%+.1f° güven=%.2f  (r=%.0f px)" % (
        d.axis_ratio, d.angle_deg, d.confidence, d.major_axis / 2))

if res.match:
    m = res.match
    print("Eşleme    : varyant=%s inlier=%d reproj=%.2f px" % (
        m.detector_variant, m.num_inliers, m.reproj_error_px))

if res.messages:
    print("-" * 66)
    for msg in res.messages:
        print("UYARI:", msg)

# Önizlemeleri kaydet — gözle doğrulama için
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(out, exist_ok=True)
for name, img in (("pipe_gt.png", res.gt_preview),
                  ("pipe_det.png", res.det_preview),
                  ("pipe_overlay.png", res.overlay)):
    if img is not None:
        cv2.imwrite(os.path.join(out, name), img)
        print("kaydedildi:", name)

print("=" * 66)
print("SONUÇ:", "OK" if res.ok else "BAŞARISIZ")
