# -*- coding: utf-8 -*-
"""
GÖRÜNTÜ DAİRESİ testleri — "köşegen FOV neden 30° çıkıyordu".

SORUN. Hydra'da panel köşegen FOV için **30.565°** yazıyordu. Sayı
matematiksel olarak doğruydu ama YANLIŞ ŞEYİ temsil ediyordu.

Kök neden: `compute_fov` sensörün GEOMETRİSİNDEN hesap yapıyordu —
"şu piksel eksenden şu kadar uzakta, demek ki şu açıyı görür". Bu,
lensin oraya ışık düşürdüğünü VARSAYAR.

Hydra'da varsayım tutmuyor:

    lensin görüntü dairesi çapı : 18.112 mm  (useful FOV 21.5°'den)
    sensörün köşegeni           : 26.067 mm

Sensörün KENARI dairenin içinde ama KÖŞELERİ dışında. Köşeler karanlık;
oradan "30.565°" diye bir görüntü gelmiyor. Gerçekte görülen alan
dairenin kestiği kısım: **her yönde 21.50°**, köşegen dahil — çünkü
kırpan şey daire, ve daire her yönde aynı.

Bu, projenin tekrar eden dersinin bir başka hâli: ölçüm/fizik katmanının
"orada görüntü yok" dediği yere geometrik bir sayı yazmak.
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import projection as proj
from core.config import Lens, Detector, SystemConfig, system_from_preset
from core.optics import compute_fov

GECTI = 0
KALDI = 0


def kontrol(ad, kosul, ayrinti=""):
    global GECTI, KALDI
    if kosul:
        GECTI += 1
        print(f"   ✓ {ad}" + (f"  ({ayrinti})" if ayrinti else ""))
    else:
        KALDI += 1
        print(f"   ✗ {ad}  {ayrinti}")


def yakin(a, b, tol=1e-6):
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tol


# ---------------------------------------------------------------------------
print("\n[1] Hydra — asıl şikâyet edilen durum")
cfg = system_from_preset("Hydra yıldız izleyici")
f = compute_fov(cfg)

kontrol("geometrik köşegen hâlâ 30.565°", yakin(f.fov_diag_deg, 30.5646, 1e-3),
        f"{f.fov_diag_deg:.4f}° — sayı doğru, ama sensör geometrisi")
kontrol("GERÇEK köşegen 21.500°", yakin(f.eff_fov_diag_deg, 21.5, 1e-3),
        f"{f.eff_fov_diag_deg:.4f}° — görüntü dairesiyle kırpılmış")
kontrol("gerçek yatay da 21.500°", yakin(f.eff_fov_x_deg, 21.5, 1e-3))
kontrol("daire sensörü kapsamıyor diye işaretli", not f.covers_sensor)
kontrol("daire çapı raporlanıyor", yakin(f.image_circle_mm, 18.1123, 1e-3),
        f"{f.image_circle_mm:.4f} mm")

# Dairesel görüntüde her yön AYNI açıyı görür — kırpan şey daire.
kontrol("kırpılmış FOV her yönde eşit",
        yakin(f.eff_fov_x_deg, f.eff_fov_diag_deg, 1e-9),
        "daire yönden bağımsız kırpar")

# Ve üreticinin verdiği değerle birebir tutuyor.
kontrol("üreticinin useful FOV'u ile birebir",
        yakin(f.eff_fov_diag_deg, cfg.lens.useful_fov_deg, 1e-6),
        f"{f.eff_fov_diag_deg:.4f}° vs {cfg.lens.useful_fov_deg}°")

print(f"\n      sensör  {f.sensor_w_mm:.3f} × {f.sensor_h_mm:.3f} mm, "
      f"köşegen {math.hypot(f.sensor_w_mm, f.sensor_h_mm):.3f} mm")
print(f"      daire   {f.image_circle_mm:.3f} mm "
      f"→ köşeler {math.hypot(f.sensor_w_mm, f.sensor_h_mm) - f.image_circle_mm:.3f} mm taşıyor")


# ---------------------------------------------------------------------------
print("\n[2] Kırpma GERÇEKTEN gerektiğinde yapılmalı, keyfi değil")
# Sensörün kenarı dairenin içinde, köşeleri dışında — yani yatay FOV
# kırpılmamalıydı diye düşünülebilir. ÖLÇELİM:
r_daire = cfg.lens.image_circle_radius_mm()
kontrol("sensör yarı-genişliği daireyi aşıyor",
        cfg.detector.sensor_width_mm / 2 > r_daire,
        f"{cfg.detector.sensor_width_mm/2:.3f} mm > {r_daire:.3f} mm")
kontrol("bu yüzden yatay FOV da kırpılıyor",
        f.eff_fov_x_deg < f.fov_x_deg,
        f"{f.eff_fov_x_deg:.4f}° < {f.fov_x_deg:.4f}°")

# Daire sensörden BÜYÜK olsaydı hiç kırpılmamalıydı.
buyuk = system_from_preset("Hydra yıldız izleyici")
buyuk.lens.image_circle_mm = 40.0        # sensör köşegeni 26.07 mm
fb = compute_fov(buyuk)
kontrol("daire sensörü kapsıyorsa kırpma YOK", fb.covers_sensor)
kontrol("kapsıyorsa gerçek = geometrik",
        yakin(fb.eff_fov_diag_deg, fb.fov_diag_deg, 1e-9),
        f"{fb.eff_fov_diag_deg:.4f}° = {fb.fov_diag_deg:.4f}°")

# Tam sınırda: daire = sensör köşegeni.
sinir = system_from_preset("Hydra yıldız izleyici")
sinir.lens.image_circle_mm = sinir.detector.diagonal_mm
fs = compute_fov(sinir)
kontrol("daire tam köşegene eşitse kapsıyor sayılır", fs.covers_sensor,
        f"daire {fs.image_circle_mm:.3f} = köşegen "
        f"{sinir.detector.diagonal_mm:.3f} mm")
kontrol("sınırda gerçek = geometrik",
        yakin(fs.eff_fov_diag_deg, fs.fov_diag_deg, 1e-9))

# Bir tık küçüğü kapsamamalı — sınır testinin ters yönü.
kil = system_from_preset("Hydra yıldız izleyici")
kil.lens.image_circle_mm = kil.detector.diagonal_mm - 0.01
kontrol("bir tık küçük daire kapsamıyor",
        not compute_fov(kil).covers_sensor)


# ---------------------------------------------------------------------------
print("\n[3] Daire bilinmiyorsa sayı UYDURULMAZ")
# CMV4000 lensinin useful FOV'u ve daire çapı verilmemiş. Doğru davranış:
# "kapsıyor" varsaymak ve geometrik değeri olduğu gibi vermek — yoksa
# her sistemde uydurma bir kırpma uygulanırdı.
cmv = system_from_preset("CMV4000 + Rodenstock 70mm")
fc = compute_fov(cmv)
kontrol("daire bilinmiyorsa kapsıyor sayılır", fc.covers_sensor)
kontrol("daire çapı NaN (bilinmiyor)", math.isnan(fc.image_circle_mm))
kontrol("gerçek = geometrik", yakin(fc.eff_fov_diag_deg, fc.fov_diag_deg, 1e-9))
kontrol("CMV4000 referansları korunuyor",
        yakin(fc.fov_x_deg, 9.19989, 1e-4) and yakin(fc.fov_diag_deg, 12.9828, 1e-3),
        f"{fc.fov_x_deg:.4f}° / {fc.fov_diag_deg:.4f}°")

kontrol("Lens.image_circle_radius_mm daire yokken NaN",
        math.isnan(Lens().image_circle_radius_mm()))


# ---------------------------------------------------------------------------
print("\n[4] Daire iki kaynaktan gelebilir; doğrudan verilen kazanır")
# useful_fov_deg'den türetim, doğrudan verilen çapa göre YEDEKTİR.
l1 = Lens(focal_length_mm=47.7, useful_fov_deg=21.5)
kontrol("useful FOV'dan türetiliyor",
        yakin(l1.image_circle_radius_mm(), 9.0561, 1e-3),
        f"{l1.image_circle_radius_mm():.4f} mm")

l2 = Lens(focal_length_mm=47.7, useful_fov_deg=21.5, image_circle_mm=30.0)
kontrol("doğrudan verilen çap önceliklidir",
        yakin(l2.image_circle_radius_mm(), 15.0, 1e-9),
        "datasheet ikisini de veriyorsa çap daha doğrudan bir ölçüdür")

# Türetim projeksiyon modeline uymalı.
l3 = Lens(focal_length_mm=47.7, useful_fov_deg=21.5,
          projection=proj.EQUIDISTANT)
r_rect = l1.image_circle_radius_mm()
r_equi = l3.image_circle_radius_mm()
kontrol("türetim projeksiyon modeline uyuyor", not yakin(r_rect, r_equi, 1e-6),
        f"rect {r_rect:.4f} vs equi {r_equi:.4f} mm")
kontrol("equidistant yarıçapı f·θ ile tutuyor",
        yakin(r_equi, 47.7 * math.radians(21.5 / 2), 1e-9))


# ---------------------------------------------------------------------------
print("\n[5] Kırpma projeksiyon modelinden BAĞIMSIZ olmamalı")
# Aynı daire, farklı modelde farklı açı kırpar.
for m in (proj.RECTILINEAR, proj.EQUIDISTANT):
    c = system_from_preset("Hydra yıldız izleyici")
    c.lens.projection = m
    c.lens.image_circle_mm = 18.0        # modelden bağımsız FİZİKSEL ölçü
    fm = compute_fov(c)
    beklenen = 2 * proj.half_angle_deg(m, c.lens.focal_length_mm, 9.0)
    kontrol(f"{m}: kırpılmış FOV modele uyuyor",
            yakin(fm.eff_fov_diag_deg, beklenen, 1e-9),
            f"{fm.eff_fov_diag_deg:.4f}°")


# ---------------------------------------------------------------------------
print("\n[6] Panelde geometrik ile gerçek AYRIŞTIRILIYOR")
from PyQt5.QtWidgets import QApplication
from core.pipeline import AnalysisResult
from gui.main_window import MainWindow

app = QApplication.instance() or QApplication(sys.argv)
w = MainWindow()


def goster(sysname):
    i = w.f_system.findData(sysname)
    w.f_system.setCurrentIndex(i)
    w._apply_system_preset()
    w._show_results(AnalysisResult(fov=compute_fov(w._config_from_fields()),
                                   ok=True))


goster("Hydra yıldız izleyici")
kontrol("Hydra'da 'Gerçekte görülen' satırı dolu",
        "21.500" in w.r_fov_eff.value(), w.r_fov_eff.value())
kontrol("gerçek satırı köşegeni de veriyor",
        "köş 21.500" in w.r_fov_eff.value())
kontrol("görüntü dairesi satırı dolu",
        w.r_fov_circle.value() == "18.112", w.r_fov_circle.value())
kontrol("geometrik satırlar 'Geometrik' diye adlandırılıyor",
        w.r_fov_xy._label.text().startswith("Geometrik")
        and w.r_fov_d._label.text().startswith("Geometrik"),
        f"{w.r_fov_xy._label.text()} / {w.r_fov_d._label.text()}")
kontrol("geometrik köşegen hâlâ görünüyor (gizlenmiyor)",
        w.r_fov_d.value() == "30.565", w.r_fov_d.value())

ipucu = w.r_fov_eff._badge.toolTip()
kontrol("ipucu köşelerin karanlık olduğunu söylüyor",
        "karanlık" in ipucu)
kontrol("ipucu iki ölçüyü karşılaştırıyor",
        "18.112" in ipucu and "26.067" in ipucu)
kontrol("ipucu geometrik değerin neden gerçek olmadığını yazıyor",
        "GEOMETRİK" in ipucu and "30.565" in ipucu)

# Üretici karşılaştırması artık GERÇEK değerle yapılmalı.
kontrol("üretici FOV karşılaştırması gerçek değeri kullanıyor",
        "+0.00" in w.r_fov_check.value(), w.r_fov_check.value())

# Daire kapsıyorsa satırlar gizlenmeli VE temizlenmeli.
goster("CMV4000 + Rodenstock 70mm")
kontrol("kapsayan sistemde gerçek satırı temizleniyor",
        w.r_fov_eff.value() == "—", w.r_fov_eff.value())
kontrol("kapsayan sistemde daire satırı temizleniyor",
        w.r_fov_circle.value() == "—", w.r_fov_circle.value())
kontrol("etiketler normale dönüyor",
        w.r_fov_xy._label.text() == "Yatay × Dikey"
        and w.r_fov_d._label.text() == "Köşegen")
kontrol("CMV4000 değerleri doğru", w.r_fov_d.value() == "12.983",
        w.r_fov_d.value())

# Sisteme geri dönünce yine görünmeli — durum sızmamalı.
goster("Hydra yıldız izleyici")
kontrol("geri dönüşte gerçek satırı yine dolu",
        "21.500" in w.r_fov_eff.value(), w.r_fov_eff.value())


print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
