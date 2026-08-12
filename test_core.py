"""Çekirdek hızlı test — GUI olmadan matematiği doğrular."""
import sys
from core.config import default_config
from core import optics, image_analysis

GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"

cfg = default_config()
print("=" * 60)
print("SİSTEM:", cfg.name)
print("  Lens f = %.1f mm | Sensör %.2f x %.2f mm" % (
    cfg.lens.focal_length_mm, cfg.detector.sensor_width_mm,
    cfg.detector.sensor_height_mm))

fov = optics.compute_fov(cfg)
print("-" * 60)
print("NOMINAL FOV / IFOV (parametrelerden):")
print("  FOV  yatay = %.3f°  dikey = %.3f°  köşegen = %.3f°" % (
    fov.fov_x_deg, fov.fov_y_deg, fov.fov_diag_deg))
print("  IFOV yatay = %.2f µrad/px (%.3f arcsec/px)" % (
    fov.ifov_x_urad, fov.ifov_x_arcsec))
print("  IFOV dikey = %.2f µrad/px (%.3f arcsec/px)" % (
    fov.ifov_y_urad, fov.ifov_y_arcsec))

print("-" * 60)
print("GÖRÜNTÜ EŞLEME (tilt/rotasyon):")
try:
    res = image_analysis.analyze(GT, DET, cfg, use_sift=True)
    if res.homography is None:
        print("  Eşleşme bulunamadı!")
    else:
        print("  Varyant: %s | eşleşme=%d inlier=%d reproj=%.2fpx" % (
            res.detector_variant, res.num_matches, res.num_inliers,
            res.reproj_error_px))
        print("  Aynalanmış (flip): %s" % res.mirrored)
        t = res.tilt
        print("  Düzlem-içi dönme : %+.3f°" % t.in_plane_rotation_deg)
        print("  Tilt X (dikey keystone) : %+.3f°" % t.tilt_x_deg)
        print("  Tilt Y (yatay keystone) : %+.3f°" % t.tilt_y_deg)
        print("  Toplam düzlem-dışı tilt : %.3f°" % t.total_tilt_deg)
        print("  Ölçek x=%.4f y=%.4f" % (t.scale_x, t.scale_y))
except Exception as e:
    print("  HATA:", e)
    import traceback; traceback.print_exc()
print("=" * 60)
