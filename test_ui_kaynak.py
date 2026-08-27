# -*- coding: utf-8 -*-
"""
Arayüzdeki KAYNAK ROZETLERİ testi.

Kullanıcının isteği: "birbirini kullanarak hesapladığın şeyleri UI'da da
belirt". Yani panelde bir sayı görüldüğünde, o sayının datasheet'ten mi
okunduğu yoksa başka değerlerden mi türetildiği görünmeli.

Bu dosya üç şeyi sınar:
  [1] Rozetler doğru kaynağı gösteriyor mu
  [2] Rozet ipucu türetim ZİNCİRİNİ taşıyor mu (kullanıcı "bu nereden çıktı"
      diye sorduğunda cevap orada mı)
  [3] Panel ile çözücü AYRIŞMIYOR mu — §5'teki panel↔tablo dersinin bu
      özellikteki karşılığı: kaynak kararı tek yerde (çözücüde) verilmeli,
      panel kendi başına "bu türetilmiş" diye karar vermemeli.
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from core import config as cfgmod, projection as projmod, solver
from core.optics import compute_fov
from core.pipeline import AnalysisResult
from gui.main_window import MainWindow

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


app = QApplication.instance() or QApplication(sys.argv)
w = MainWindow()


def SahteSonuc(fov):
    """
    Yalnızca FOV/IFOV dolu bir analiz sonucu.

    Gerçek `AnalysisResult` kullanılıyor (elde sahte bir sınıf tutmak yerine):
    panelin okuduğu tüm alanlar böylece gerçekten var olan alanlar olur ve
    `_show_results` değişince test de onunla birlikte kırılır — sahte nesne
    sessizce eskir, gerçek olan eskimez.
    """
    return AnalysisResult(fov=fov, ok=True)


def hydra_kur():
    i = w.f_system.findData("Hydra yıldız izleyici")
    w.f_system.setCurrentIndex(i)
    w._apply_system_preset()
    cfg = w._config_from_fields()
    w._show_results(SahteSonuc(compute_fov(cfg)))
    return cfg


# ---------------------------------------------------------------------------
print("\n[1] Rozetler doğru kaynağı gösteriyor")
cfg = hydra_kur()

kontrol("FOV 'türetildi' rozeti taşıyor",
        w.r_fov_xy.source() == "türetildi", w.r_fov_xy.source())
kontrol("IFOV 'türetildi' rozeti taşıyor",
        w.r_ifov.source() == "türetildi", w.r_ifov.source())
kontrol("Açısal çözünürlük 'türetildi' rozeti taşıyor",
        w.r_ang_res.source() == "türetildi", w.r_ang_res.source())
kontrol("Sensör ölçüsü 'türetildi' rozeti taşıyor",
        w.r_sensor.source() == "türetildi", w.r_sensor.source())
kontrol("Projeksiyon 'datasheet' (kullanıcı seçimi) rozeti taşıyor",
        w.r_fov_model.source() == "datasheet", w.r_fov_model.source())

# Değerler doğru mu — rozet doğru ama sayı yanlış olmasın.
fov = compute_fov(cfg)
kontrol("FOV değeri doğru",
        w.r_fov_xy.value() == f"{fov.fov_x_deg:.3f} × {fov.fov_y_deg:.3f}",
        w.r_fov_xy.value())
# Panel 5 haneye yuvarlıyor; karşılaştırma o hassasiyette yapılmalı.
kontrol("açısal çözünürlük IFOV ile tutarlı",
        abs(float(w.r_ang_res.value())
            - math.degrees(fov.ifov_x_urad * 1e-6)) < 5e-6,
        f"{w.r_ang_res.value()} °/px = {math.degrees(fov.ifov_x_urad*1e-6):.8f}")
kontrol("açısal çözünürlük Hydra için 0.02162 °/px",
        abs(float(w.r_ang_res.value()) - 0.021621) < 1e-5,
        w.r_ang_res.value())


# ---------------------------------------------------------------------------
print("\n[2] Rozet ipucu türetim zincirini taşıyor")
ipucu = w.r_ifov._badge.toolTip()
kontrol("ipucu boş değil", len(ipucu) > 20)
# Kullanıcının sorduğu iki soru: HANGİ İKİSİNDEN ve NE FONKSİYONLA.
kontrol("ipucu HANGİ değerlerden türetildiğini sayıyor",
        "Şu değerlerden türetildi" in ipucu)
kontrol("ipucu girdileri DEĞERLERİYLE veriyor",
        "Dedektör piksel pitch X = 18 µm" in ipucu
        and "Lens odak uzaklığı f = 47.7 mm" in ipucu)
kontrol("ipucu her girdinin kendi kaynağını da işaretliyor",
        ipucu.count("(girdi)") >= 2)
kontrol("ipucu NE FONKSİYONLA olduğunu yazıyor", "Bağıntı:" in ipucu)
kontrol("bağıntı gerçek formülü içeriyor",
        "2·atan( (pitch/2) / f )" in ipucu,
        [l for l in ipucu.split("\n") if "Bağıntı" in l])
kontrol("ipucu sonucu da yazıyor", "377.358 µrad/px" in ipucu)
print("      IFOV rozet ipucu:")
for satir in ipucu.split("\n"):
    print("        " + satir if satir.strip() else "")

ipucu_as = w.r_ifov_as._badge.toolTip()
kontrol("arcsec satırı birim dönüşümünü gösteriyor",
        "″/px = µrad" in ipucu_as,
        [l for l in ipucu_as.split("\n") if "Bağıntı" in l])
kontrol("arcsec ipucu ara adımı 'türetilmiş' diye işaretliyor",
        "(türetilmiş)" in ipucu_as,
        "IFOV X'ten geliyor, o da kendisi türetilmiş")
kontrol("çok adımlı türetimde tam zincir de veriliyor",
        "Tam zincir:" in ipucu_as)
kontrol("tek adımlı türetimde zincir tekrarlanmıyor",
        "Tam zincir:" not in ipucu,
        "IFOV doğrudan iki girdiden çıkıyor — zincir zaten yukarıda")

# Projeksiyon seçicisinin her kalemi kendi ipucunu taşımalı.
from PyQt5.QtCore import Qt as _Qt
eksik = [w.f_proj.itemText(i) for i in range(w.f_proj.count())
         if not (w.f_proj.itemData(i, _Qt.ToolTipRole) or "")]
kontrol("projeksiyon listesinde her kalemin ipucu var", not eksik, str(eksik))
rect_help = w.f_proj.itemData(w.f_proj.findData(projmod.RECTILINEAR),
                              _Qt.ToolTipRole)
kontrol("rektilineer ipucu varsayılan olduğunu söylüyor",
        "VARSAYILAN" in rect_help)
equi_help = w.f_proj.itemData(w.f_proj.findData(projmod.EQUIDISTANT),
                              _Qt.ToolTipRole)
kontrol("equidistant ipucu piksel ölçeğinin sabit olduğunu söylüyor",
        "SABİTTİR" in equi_help)

# Kenar pikseli satırı projeksiyon modelini açıklamalı.
ipucu_kenar = w.r_ifov_edge._badge.toolTip()
kontrol("kenar pikseli ipucu modeli adlandırıyor",
        "rectilinear" in ipucu_kenar)
kontrol("kenar pikseli değeri merkezden küçük (rektilineer)",
        fov.ifov_edge_x_urad < fov.ifov_x_urad,
        w.r_ifov_edge.value())

# Projeksiyon rozeti model yayılımını göstermeli — "FOV yanlış mı" sorusunun
# ilk teşhis adımı.
ipucu_model = w.r_fov_model._badge.toolTip()
kontrol("projeksiyon ipucu model yayılımını veriyor",
        "yayılım" in ipucu_model.lower(), ipucu_model.split("\n")[-1][:80])


# ---------------------------------------------------------------------------
print("\n[3] Panel ↔ çözücü AYRIŞMIYOR (tek doğruluk kaynağı)")
# Panelin gösterdiği her kaynak, çözücünün söylediğiyle aynı olmalı.
src = w._solver_sources()
esleme = [(w.r_fov_xy, "fov_x_deg"), (w.r_fov_d, "fov_diag_deg"),
          (w.r_ifov, "ifov_x_urad"), (w.r_ifov_as, "ifov_x_arcsec"),
          (w.r_ang_res, "ifov_x_deg"), (w.r_sensor, "det_w_mm")]
for row, node in esleme:
    beklenen = {"given": "datasheet", "derived": "türetildi"}[src[node][0]]
    kontrol(f"panel ↔ çözücü: {node}", row.source() == beklenen,
            f"panel '{row.source()}' vs çözücü '{beklenen}'")


# ---------------------------------------------------------------------------
print("\n[4] Kaynak DEĞİŞİNCE rozet takip ediyor")
# Kullanıcı f'i silip FOV'u elle girerse f artık türetilmiş olur. Rozetin
# bu tersine dönüşü izlemesi, rozetin gerçekten çözücüye bağlı olduğunun
# kanıtı — sabit bir etiket olsaydı değişmezdi.
g_normal = solver.from_config(cfg)
kontrol("normalde f datasheet",
        solver.solve(g_normal).values["lens_f_mm"].is_given)
g_tersine = {k: v for k, v in g_normal.items() if k != "lens_f_mm"}
g_tersine["fov_x_deg"] = fov.fov_x_deg
r_ters = solver.solve(g_tersine)
kontrol("f silinip FOV verilince f türetilmiş oluyor",
        r_ters.is_derived("lens_f_mm"))

# Hydra'da f'e giden İKİ yol var ve ikisi aynı sayıyı vermez:
#   FOV yolundan   -> 47.700 mm (FOV zaten f=47.7'den üretilmişti)
#   pupil × f/#    -> 47.600 mm (datasheet 34.0 × 1.4)
# Fark üretici yuvarlamasıdır (§7G), kod hatası değil. Çözücü kısa yolu
# seçer; testin sorması gereken "hangi sayı" değil, "her iki yol da makul
# ve aralarındaki fark tolerans içinde mi" olmalı.
kontrol("türetilen f iki yolun ikisiyle de uyumlu",
        abs(r_ters.get("lens_f_mm") - cfg.lens.focal_length_mm)
        / cfg.lens.focal_length_mm < solver.DEFAULT_TOLERANCE,
        f"{r_ters.get('lens_f_mm'):.4f} mm vs datasheet "
        f"{cfg.lens.focal_length_mm} mm (üretici yuvarlaması)")

# Pupil/f-numarası olmadan sorulursa FOV yolu tek başına kalır ve TAM
# doğru f'i vermeli — ters formülün kendisi doğru olduğunun kanıtı.
g_sade = {"fov_x_deg": fov.fov_x_deg,
          "det_w_px": cfg.detector.width_px,
          "det_pitch_um": cfg.detector.pixel_pitch_um}
kontrol("tek yol kalınca f tam doğru",
        abs(solver.solve(g_sade).get("lens_f_mm")
            - cfg.lens.focal_length_mm) < 1e-6,
        f"{solver.solve(g_sade).get('lens_f_mm'):.6f} mm")


# ---------------------------------------------------------------------------
print("\n[5] Projeksiyon modeli değişince panel takip ediyor")
j = w.f_proj.findData(projmod.EQUIDISTANT)
w.f_proj.setCurrentIndex(j)
w._update_projection_label()
cfg2 = w._config_from_fields()
fov2 = compute_fov(cfg2)
w._show_results(SahteSonuc(fov2))

kontrol("FOV değeri modele göre değişti",
        w.r_fov_xy.value() != f"{fov.fov_x_deg:.3f} × {fov.fov_y_deg:.3f}",
        w.r_fov_xy.value())
kontrol("projeksiyon satırı equidistant yazıyor",
        "Equidistant" in w.r_fov_model.value(), w.r_fov_model.value())
kontrol("equidistant'ta kenar IFOV merkeze eşit (%+0.00)",
        "+0.00%" in w.r_ifov_edge.value(), w.r_ifov_edge.value())
kontrol("equidistant'ta kenar satırı yeşil (fark yok)",
        abs(fov2.ifov_edge_x_urad - fov2.ifov_x_urad) < 1e-9)


# ---------------------------------------------------------------------------
print("\n[6] Üretici FOV karşılaştırma satırı")
w.f_proj.setCurrentIndex(w.f_proj.findData(projmod.RECTILINEAR))
cfg3 = w._config_from_fields()
w._show_results(SahteSonuc(compute_fov(cfg3)))
kontrol("üretici FOV satırı dolu", w.r_fov_check.value() != "—",
        w.r_fov_check.value())
# Karşılaştırma GEOMETRİK değil GERÇEK FOV ile yapılır. Hydra'da görüntü
# dairesi zaten üreticinin useful FOV'undan türetildiği için ikisi birebir
# tutar (%0.00) — geometrik değerle karşılaştırılsaydı %+1.72 çıkardı ve
# lensin köşelere hiç görüntü düşürmediği gerçeği gizlenirdi.
# Ayrıntı: test_goruntu_dairesi.py
kontrol("karşılaştırma gerçek FOV ile yapılıyor",
        "+0.00" in w.r_fov_check.value(), w.r_fov_check.value())
kontrol("geometrik değerle yapılsaydı farklı çıkardı",
        abs(compute_fov(cfg3).fov_x_deg - 21.5) / 21.5 * 100 > 1.5,
        f"geometrik {compute_fov(cfg3).fov_x_deg:.4f}° → %+1.72 olurdu")
kontrol("beklenen yön olduğu için ipucu uyarı değil",
        "BEKLENEN" in w.r_fov_check._badge.toolTip())

# Üretici FOV'u olmayan sistemde satır boş kalmalı — uydurma sayı yok.
i = w.f_system.findData("CMV4000 + Rodenstock 70mm")
w.f_system.setCurrentIndex(i)
w._apply_system_preset()
cfg4 = w._config_from_fields()
w._show_results(SahteSonuc(compute_fov(cfg4)))
kontrol("üretici FOV verilmemişse satır boş",
        w.r_fov_check.value() == "—", w.r_fov_check.value())
kontrol("boş satırda rozet de yok", w.r_fov_check.source() == "")

# CMV4000 referans değerleri panelde korunuyor.
kontrol("CMV4000 FOV paneli 9.200 × 9.200",
        w.r_fov_xy.value() == "9.200 × 9.200", w.r_fov_xy.value())
kontrol("CMV4000 IFOV paneli 78.57", w.r_ifov.value() == "78.57",
        w.r_ifov.value())
kontrol("CMV4000 açısal çözünürlük 0.00450 °/px",
        w.r_ang_res.value() == "0.00450", w.r_ang_res.value())


# ---------------------------------------------------------------------------
print("\n[7] clear() rozetleri de temizliyor")
w._clear_results()
kontrol("değer temizlendi", w.r_ifov.value() == "—")
kontrol("rozet temizlendi", w.r_ifov.source() == "")
kontrol("açısal çözünürlük temizlendi", w.r_ang_res.value() == "—")
kontrol("kenar pikseli temizlendi", w.r_ifov_edge.value() == "—")


print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
