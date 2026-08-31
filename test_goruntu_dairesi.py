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
# Hydra'da daire sensörün KENARINI da kesiyor, dolayısıyla efektif FOV
# yatay/dikey/köşegen için aynı sayıdır. Dairesel bir görüntüde yön ayrımı
# yoktur; üç sayıyı ayrı yazmak olmayan bir ayrımı varmış gibi gösterirdi.
# Bu yüzden satır tek değere iner ve "her yönde" diye işaretlenir.
kontrol("gerçek FOV yönsüz tek sayı olarak yazılıyor",
        "her yönde" in w.r_fov_eff.value()
        and "köş" not in w.r_fov_eff.value(), w.r_fov_eff.value())
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


# ======================================================================
# SATIR SIRASI — cevap en üstte
#
# Panelde hangi satırın önce geldiği kozmetik değil: kullanıcı ilk
# gördüğü sayıyı "FOV" diye okur. Daire kısıtlıyken geometrik köşegen
# (30.565°) üstte kalırsa gerçek FOV'dan (21.500°) %42 sapan bir değer
# okunur. Sıra bu yüzden duruma göre değişir ve test edilir.
# ======================================================================
print("\n--- FOV satır sırası ---")


def sira():
    lay = w._fov_layout
    return [lay.itemAt(i).widget()._label.text() for i in range(lay.count())]


goster("Hydra yıldız izleyici")
s = sira()
kontrol("daire kısıtlıyken gerçek FOV en üstte", s[0] == "Gerçekte görülen FOV", s[0])
kontrol("kısıtı belirleyen daire hemen altında", s[1] == "Görüntü dairesi", s[1])
kontrol("kullanılabilir alan üçüncü", s[2] == "Kullanılabilir alan", s[2])
kontrol("geometrik satırlar cevabın ALTINA düşüyor",
        s.index("Geometrik Y × D") > s.index("Gerçekte görülen FOV")
        and s.index("Geometrik köşegen") > s.index("Gerçekte görülen FOV"), s)
kontrol("üretici kontrolü en sonda", s[-1] == "Üretici FOV ile", s[-1])
# "Projeksiyon" satırı panelden kaldırıldı: bir GİRDİdir (sol panelde
# seçilir), sonuç değil — ve rozeti "datasheet" diyordu, oysa model
# datasheet'ten okunmuyor.
kontrol("projeksiyon satırı panelde YOK", "Projeksiyon" not in s, s)

goster("CMV4000 + Rodenstock 70mm")
s = sira()
kontrol("daire kapsıyorsa cevap geometrik FOV'dur ve en üsttedir",
        s[0] == "Yatay × Dikey" and s[1] == "Köşegen", s[:2])

# Sıra ileri geri geçişte bozulmamalı.
goster("Hydra yıldız izleyici")
kontrol("geri dönüşte sıra yine cevap-önce",
        sira()[0] == "Gerçekte görülen FOV", sira()[0])


# ======================================================================
# KULLANILABILIR ALAN — dairenin sensöre oranı
#
# FOV'un tek sayıya inmesinin bedeli. Hydra'da daire (Ø18.112 mm) sensörün
# kenarından (18.432 mm) küçük olduğu için tam bir disktir:
#     π/4 · (18.112/0.018)² ≈ 795.000 px  /  1024² = %75.8
# Kalan %24 karanlıktır; yıldız arama maskesi bununla sınırlanmalıdır.
# ======================================================================
print("\n--- Kullanılabilir alan ---")

goster("Hydra yıldız izleyici")
deger = w.r_fov_fill.value()
kontrol("Hydra'da kullanılabilir alan satırı dolu", deger != "—", deger)
kontrol("doluluk oranı %75.8", "%75.8" in deger, deger)
kontrol("piksel sayısı ~795k", "795," in deger, deger)

ipucu = w.r_fov_fill._badge.toolTip()
kontrol("alan ipucu maskelemeyi söylüyor", "maske" in ipucu.lower(), ipucu[:60])
kontrol("alan ipucu karanlık oranını veriyor", "%24.2" in ipucu, ipucu[:80])

goster("CMV4000 + Rodenstock 70mm")
kontrol("daire bilinmiyorsa alan satırı temizleniyor",
        w.r_fov_fill.value() == "—", w.r_fov_fill.value())


# ======================================================================
# KENAR IFOV'U — hangi kenar?
#
# FOV panelindeki hatanın IFOV'daki eşi. Kenar IFOV'u sensörün fiziksel
# kenarındaki (10.935° yarı-açı) pikselden hesaplanıyordu; daire kısıtlıysa
# o piksel KARANLIKTIR. Aydınlık alanın gerçek kenarı dairenin sınırıdır
# (10.750°) ve sayı oradan okunmalıdır.
#
# Hydra'da fark küçüktür (363.78 -> 364.23 µrad) ama sınıf olarak aynı
# hatadır ve daire/sensör uyumsuzluğu büyüdükçe büyür.
# ======================================================================
print("\n--- Kenar IFOV'u: sensör kenarı mı, daire kenarı mı ---")

goster("Hydra yıldız izleyici")
_f = compute_fov(w._config_from_fields())

kontrol("efektif kenar IFOV alanı dolu",
        math.isfinite(_f.ifov_eff_edge_x_urad), _f.ifov_eff_edge_x_urad)
kontrol("efektif kenar, geometrik kenardan FARKLI",
        abs(_f.ifov_eff_edge_x_urad - _f.ifov_edge_x_urad) > 1e-6,
        f"{_f.ifov_eff_edge_x_urad:.4f} vs {_f.ifov_edge_x_urad:.4f}")
kontrol("efektif kenar, dairenin yarı-açısından hesaplanıyor",
        abs(_f.ifov_eff_edge_x_urad
            - proj.ifov_rad(_f.projection, 47.7, 0.018,
                             _f.eff_fov_x_deg / 2.0) * 1e6) < 1e-6)
# Daire içeride kaldığı için açı küçülür, IFOV daralması AZALIR:
# efektif kenar merkeze geometrik kenardan daha yakındır.
kontrol("efektif kenar merkeze daha yakın (daralma daha az)",
        _f.ifov_eff_edge_x_urad > _f.ifov_edge_x_urad,
        f"{_f.ifov_eff_edge_x_urad:.2f} > {_f.ifov_edge_x_urad:.2f}")
kontrol("efektif kenar hâlâ merkezden küçük (rektilineer daralma)",
        _f.ifov_eff_edge_x_urad < _f.ifov_x_urad,
        f"{_f.ifov_eff_edge_x_urad:.2f} < {_f.ifov_x_urad:.2f}")

kontrol("panel efektif değeri gösteriyor",
        "364.2" in w.r_ifov_edge.value(), w.r_ifov_edge.value())
kontrol("panel geometrik değeri GÖSTERMİYOR",
        "363.7" not in w.r_ifov_edge.value(), w.r_ifov_edge.value())
kontrol("satır 'Görüntü kenarı' diye adlandırılıyor",
        w.r_ifov_edge._label.text() == "Görüntü kenarı",
        w.r_ifov_edge._label.text())
_ip = w.r_ifov_edge._badge.toolTip()
kontrol("ipucu hangi kenardan okunduğunu söylüyor",
        "DAİRENİN KENARINDAN" in _ip, _ip[-160:])
kontrol("ipucu sensör kenarının karanlık olduğunu yazıyor",
        "karanlık" in _ip, _ip[-120:])

# Daire kapsıyorsa iki değer aynı olmalı ve etiket normale dönmeli.
goster("CMV4000 + Rodenstock 70mm")
_f2 = compute_fov(w._config_from_fields())
kontrol("daire yokken efektif kenar = geometrik kenar",
        abs(_f2.ifov_eff_edge_x_urad - _f2.ifov_edge_x_urad) < 1e-9,
        f"{_f2.ifov_eff_edge_x_urad:.4f} / {_f2.ifov_edge_x_urad:.4f}")
kontrol("etiket 'Kenar pikseli'ne dönüyor",
        w.r_ifov_edge._label.text() == "Kenar pikseli",
        w.r_ifov_edge._label.text())

# Merkez IFOV daireden ETKİLENMEZ — merkez her hâlde aydınlıktır.
goster("Hydra yıldız izleyici")
kontrol("merkez IFOV daire kısıtından etkilenmiyor",
        abs(_f.ifov_x_urad - (0.018 / 47.7) * 1e6) < 0.5,
        f"{_f.ifov_x_urad:.2f} µrad")


# ======================================================================
# PANELDEN ÇIKARILAN SATIRLAR
#
# Üçü de "doğru ama gereksiz ya da yanıltıcı" olduğu için kaldırıldı.
# Widget'lar yaşıyor (sonuç nesnesi ve karşılaştırma tablosu onlara
# başvuruyor) — yalnızca layout'ta yer almıyorlar. Test, geri sızmalarını
# ve ebeveynsiz kalıp C++ tarafında silinmelerini engeller.
# ======================================================================
print("\n--- Panelden çıkarılanlar ---")

from PyQt5.QtWidgets import QGroupBox

goster("Hydra yıldız izleyici")


def grup(baslik):
    for gb in w.findChildren(QGroupBox):
        if gb.title() == baslik:
            return gb
    return None


def satirlar(gb):
    lay, ad = gb.layout(), []
    for k in range(lay.count()):
        wd = lay.itemAt(k).widget()
        if wd is not None and hasattr(wd, "_label"):
            ad.append(wd._label.text())
    return ad


# 1) Eğiklik (Tilt) grubu — tilt artık yalnızca "Yönelim hataları"nda.
gb_t = grup("Eğiklik (Tilt)")
kontrol("Eğiklik grubu gizli", gb_t is not None and not gb_t.isVisibleTo(w))
kontrol("tilt yalnızca Yönelim hatalarında",
        "Tilt (x / y)" in satirlar(grup("Yönelim hataları")))

# 2) Ulaşılan en büyük açı — FOV ile karışıyordu (yarı-açı vs tam açı).
kaps = satirlar(grup("FOV kapsaması"))
kontrol("'Ulaşılan en büyük açı' panelde YOK",
        "Ulaşılan en büyük açı" not in kaps, kaps)
kontrol("kenar açıları duruyor (aynı bilgi, karışmayan biçimde)",
        "Kenar açıları" in kaps, kaps)

# 3) Projeksiyon — girdi, sonuç değil; rozeti de yanlış beyandı.
kontrol("'Projeksiyon' FOV panelinde YOK",
        "Projeksiyon" not in satirlar(grup("Görüş Alanı (FOV)")))

# Kaldırılan widget'lar ebeveynsiz kalmamalı: sahipsiz QWidget C++
# tarafında silinir ve bir sonraki set_value/setText çağrısı
# "wrapped C/C++ object has been deleted" ile patlar.
for ad, wd in (("r_fov_model", w.r_fov_model),
               ("r_cov_maxang", w.r_cov_maxang),
               ("lbl_tilt_note", w.lbl_tilt_note)):
    try:
        wd.setVisible(False)          # C++ nesnesine dokunur
        canli = True
    except RuntimeError:
        canli = False
    kontrol(f"{ad} hâlâ canlı (ebeveyni var)", canli)

# Ve sonuç gösterimi bu widget'ları doldurmaya devam edebilmeli.
try:
    goster("Hydra yıldız izleyici")
    goster("CMV4000 + Rodenstock 70mm")
    goster("Hydra yıldız izleyici")
    kontrol("kaldırılan satırlarla sonuç gösterimi patlamıyor", True)
except RuntimeError as e:
    kontrol("kaldırılan satırlarla sonuç gösterimi patlamıyor", False, str(e))


print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
