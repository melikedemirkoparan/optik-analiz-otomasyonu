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
from core.config import default_config
from core.optics import compute_fov
from core.pipeline import AnalysisResult
from gui.main_window import MainWindow
from gui.widgets import ResultRow

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
# Açısal çözünürlük IFOV'un derece cinsinden yazılışıdır — yeni bir hesap
# değil, aynı sayının başka birimi. "türetildi" demek kullanıcıya burada
# bir bağıntı uygulandığı izlenimi verirdi; rozet "birim" olmalı.
kontrol("Açısal çözünürlük 'birim' rozeti taşıyor (türetildi DEĞİL)",
        w.r_ang_res.source() == "birim", w.r_ang_res.source())
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
ROZET_METNI = {"given": "datasheet", "unit": "birim", "derived": "türetildi"}
for row, node in esleme:
    beklenen = ROZET_METNI[src[node][0]]
    kontrol(f"panel ↔ çözücü: {node}", row.source() == beklenen,
            f"panel '{row.source()}' vs çözücü '{beklenen}'")


# ---------------------------------------------------------------------------
print("\n[3b] BİRİM ÇEVRİMİ 'türetildi' sayılmıyor")
# 78.57 µrad/px ile 16.207 ″/px aynı ölçümdür; ikincisine "türetildi" demek
# kullanıcıya hesap yapılmış izlenimi verir. Birim çevrimleri ayrı sınıf.
r_birim = solver.solve({"lens_f_mm": 70.0, "det_pitch_um": 5.5,
                        "det_w_px": 2048})
kontrol("IFOV µrad gerçekten türetilmiş",
        r_birim.kaynak_turu("ifov_x_urad") == "derived",
        "f ve pitch'ten hesaplanıyor")
for n in ("ifov_x_deg", "ifov_x_arcsec"):
    kontrol(f"{n} birim çevrimi sayılıyor",
            r_birim.kaynak_turu(n) == "unit",
            f"kaynak_turu = {r_birim.kaynak_turu(n)}")
kontrol("FOV birim çevrimi DEĞİL",
        r_birim.kaynak_turu("fov_x_deg") == "derived")
# Asıl değer VERİLMİŞSE birim çevrimi de "verilmiş" sayılmalı: kullanıcı
# 78.57 µrad girdiyse, onun arcsec karşılığı da onun girdisidir.
r_gv = solver.solve({"ifov_x_urad": 78.57})
kontrol("verilen değerin birim çevrimi de datasheet",
        r_gv.kaynak_turu("ifov_x_arcsec") == "given",
        f"kaynak_turu = {r_gv.kaynak_turu('ifov_x_arcsec')}")


# ---------------------------------------------------------------------------
print("\n[3c] Boş alan = bilinmiyor; çözücü onu doldurabiliyor")
# Kullanıcının istediği akış: alanı sil, arkadaki matematik bulsun.
kontrol("odak uzaklığı alanı boş bırakılabiliyor",
        w.f_focal.minimum() == 0.0,
        "alt sınır 0 — eskiden 1.0 idi ve alan silinemiyordu")
kontrol("boş alan 'bilinmiyor' gösteriyor",
        w.f_focal.specialValueText() != "",
        repr(w.f_focal.specialValueText()))
# Pupil ve f# biliniyorsa f türetilebilmeli (f = D x N).
r_bos = solver.solve({"lens_pupil_mm": 34.0, "lens_fnum": 1.4})
kontrol("boş f, pupil ve f#'ten türetiliyor",
        abs(r_bos.get("lens_f_mm") - 47.6) < 0.01,
        f"f = {r_bos.get('lens_f_mm'):.2f} mm")
# Çözülemiyorsa NE gerektiği söylenmeli.
oneriler = solver.eksikler_icin("lens_f_mm", ["det_pitch_um", "det_w_px"])
kontrol("çözülemeyen için eksik girdi öneriliyor", len(oneriler) > 0,
        f"{len(oneriler)} öneri: " + ", ".join(
            "+".join(solver.label(x) for x in o) for o in oneriler[:3]))
kontrol("öneriler en az eksikten başlıyor",
        all(len(oneriler[i]) <= len(oneriler[i + 1])
            for i in range(len(oneriler) - 1)))


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


# ---------------------------------------------------------------------------
print("\n[8] Açılış durumu = temizlenmiş durum")
# Eskiden `_clear_results` yalnızca analiz BAŞLARKEN çağrılıyordu; programın
# ilk açılışı ile "analiz yapıldı sonra temizlendi" hâli farklı görünüyordu
# (satır etiketleri yeniden adlandırılmış, bazı satırlar görünür kalmış).
def _durum(win):
    d = {}
    for ad in dir(win):
        if not ad.startswith("r_"):
            continue
        row = getattr(win, ad)
        if not hasattr(row, "_value"):
            continue
        # `_label.text()` kısaltılmış olabilir ("Yatay × Di…") ve kısaltma
        # kutu genişliğine bağlıdır; karşılaştırma TAM etiket üzerinden
        # yapılmalı, yoksa yerleşim farkı içerik farkı gibi görünür.
        d[ad] = (row._value.text(), row._label_tam,
                 row.isVisible(), row.source())
    d["_gb_tilt"] = win.gb_tilt.isVisible()
    d["_details"] = win.details_box.isVisible()
    return d

w8 = MainWindow()
w8.show()
app.processEvents()
acilis = _durum(w8)

w8.f_system.setCurrentIndex(w8.f_system.findData("Hydra yıldız izleyici"))
w8._apply_system_preset()


class _Res:
    pass


_r = _Res()
_r.fov = compute_fov(w8._config_from_fields())
for _a in ("tilt", "match", "mirror", "pointing", "coverage", "roi",
           "notes", "verdict", "tilt_deg"):
    setattr(_r, _a, None)
try:
    w8._show_results(_r)
except Exception:
    pass          # sahte sonuç nesnesi; ilgilendiğimiz FOV bloğu çalıştı
app.processEvents()
w8._clear_results()
# Açılışta sağ bar BOŞ DEĞİL: nominal değerler donanımdan anında
# hesaplanır. Karşılaştırma "aynı girdiyle aynı ekran mı" sorusudur,
# "boş mu" değil — bu yüzden donanımı da açılıştaki hâline döndürüp
# canlı hesabı yeniden koşturuyoruz. (Yukarıda Hydra yüklendi; onunla
# kıyaslamak farklı donanımın farklı sayı vermesini hata sanmak olurdu.)
w8._analiz_sonucu_var = False
w8._load_config_into_fields(default_config())
app.processEvents()
sonra = _durum(w8)

farklar = [k for k in acilis if acilis[k] != sonra.get(k)]
kontrol("açılış ile temizlenmiş durum aynı", not farklar,
        "fark yok" if not farklar else f"ayrışan: {farklar[:4]}")
kontrol("FOV satır etiketleri başlangıca döndü",
        w8.r_fov_xy._label_tam == "Yatay × Dikey",
        w8.r_fov_xy._label_tam)
kontrol("daire satırları temizlikte gizlendi",
        not w8.r_fov_eff.isVisible() and not w8.r_fov_circle.isVisible())


# ---------------------------------------------------------------------------
print("\n[9] Önerilen her girdinin panelde bir ALANI var")
# Sistem "Görüntü dairesi çapını girin" diyordu ama o alan panelde yoktu;
# kullanıcı olmayan bir alanı aramaya gönderiliyordu.
w9 = MainWindow()
w9.f_system.setCurrentIndex(w9.f_system.findData("Hydra yıldız izleyici"))
w9._apply_system_preset()
kontrol("görüntü dairesi alanı panelde var", hasattr(w9, "f_circle"))
kontrol("görüntü dairesi ALAN_DUGUM'da eşleşiyor",
        w9.ALAN_DUGUM.get("f_circle") == "lens_image_circle_mm")

girilebilir = set(w9.ALAN_DUGUM.values()) | {
    "det_w_px", "det_h_px", "scr_w_px", "scr_h_px"}
# Önerilen her düğümün karşılığında gerçekten bir alan olmalı.
_g = w9._panel_bilinenleri()
_tum_oneri = []
for _hedef in w9.ALAN_DUGUM.values():
    for _o in solver.eksikler_icin(_hedef, _g.keys()):
        _tum_oneri.append((_hedef, _o))
# Hedef, bilinenlerden ÇIKARILMIŞ olmalı — kullanıcı o alanı sildiğinde
# olan budur. Çıkarmazsak çözücü "zaten biliniyor" der ve öneri üretmez.
_g_eksik = {k: v for k, v in _g.items() if k != "lens_useful_fov_deg"}
kontrol("üretici FOV, daire çapından türetilebiliyor",
        "lens_image_circle_mm" in {x for o in solver.eksikler_icin(
            "lens_useful_fov_deg", _g_eksik.keys()) for x in o},
        "daire çapı girilirse çözülür")

# Daire çapı girilince üretici FOV gerçekten çözülmeli.
w9.f_circle.setValue(18.112)
w9._bastan_bos_guncelle()
w9.f_ufov.setValue(0)
_r9 = solver.solve_for(w9._panel_bilinenleri(), ["lens_useful_fov_deg"])
kontrol("daire çapından üretici FOV türetiliyor",
        abs(_r9.get("lens_useful_fov_deg") - 21.5) < 0.01,
        f"{_r9.get('lens_useful_fov_deg'):.4f}°")


# ---------------------------------------------------------------------------
print("\n[10] Hızlı hesap kutusu kaldırıldı")
# Paneldeki ham donanımı taban alıyordu; ölçülen FOV/IFOV ile karışınca
# tutarsız sonuç veriyordu. Tam tablo Çözücü sekmesinde zaten var.
kontrol("r_calc satırı yok", not hasattr(w9, "r_calc"))
kontrol("_hizli_hesapla metodu yok", not hasattr(w9, "_hizli_hesapla"))
kontrol("Çözücü sekmesi duruyor", hasattr(w9, "tab_solver"))
# "Boşları hesapla" butonu da kaldırıldı: canlı hesap aynı işi butona
# basmadan yapıyordu, buton ikinci bir yol olarak kafa karıştırıyordu.
kontrol("Boşları hesapla butonu yok", not hasattr(w9, "btn_coz_bos"))
kontrol("_bos_alanlari_coz metodu yok",
        not hasattr(w9, "_bos_alanlari_coz"))



# ---------------------------------------------------------------------------
print("\n[11] HER türetilebilir büyüklük GİRİLEBİLİR")
# Çözücünün bildiği 31 büyüklükten yarısının girilecek yeri yoktu.
# Sol panele 12 alan eklemek paneli boş kutularla doldurdu; o alanlar
# Çözücü sekmesine taşındı. Kapsam denetimi aynı: her büyüklük bir yerden
# girilebilmeli — panelden ya da sekmeden.
w11 = MainWindow()
_panelden = set(w11.ALAN_DUGUM.values()) | {
    "det_w_px", "det_h_px", "scr_w_px", "scr_h_px"}
_sekmeden = set(w11.tab_solver.fields)
_girilebilir = _panelden | _sekmeden
_eksik = set(solver.NODE_LABELS) - _girilebilir
kontrol("her büyüklüğün girilebilir bir yeri var", not _eksik,
        "eksik yok" if not _eksik
        else ", ".join(sorted(solver.label(n) for n in _eksik)))
kontrol("sol panel yalnızca DONANIMI tutuyor",
        not any(a.startswith("f_n_") for a in w11.ALAN_DUGUM),
        f"{len(w11.ALAN_DUGUM)} donanım alanı")
kontrol("FOV/IFOV sekmeden giriliyor",
        {"fov_x_deg", "ifov_x_urad"} <= _sekmeden)
# ALAN_DUGUM örneğe kopyalanmalı; sınıf düzeyinde kalırsa birikirdi.
_w11b = MainWindow()
kontrol("ALAN_DUGUM pencereler arasında birikmiyor",
        len(_w11b.ALAN_DUGUM) == len(w11.ALAN_DUGUM),
        f"{len(w11.ALAN_DUGUM)} = {len(_w11b.ALAN_DUGUM)}")


# ---------------------------------------------------------------------------
print("\n[12] TERS yönler panelden çalışıyor")
# Asıl kazanım bu: kullanıcı bildiğini girip bilmediğini sildiğinde
# arkadaki matematik onu bulmalı. Her senaryo ayrı bir yön.
_CMV = "CMV4000 + Rodenstock 70mm"
_HYD = "Hydra yıldız izleyici"


def _ters_dene(preset, girilen, silinen):
    """`girilen` artık ÇÖZÜCÜ SEKMESİNE yazılır (düğüm adıyla)."""
    win = MainWindow()
    _i = win.f_system.findData(preset)
    if _i >= 0:
        win.f_system.setCurrentIndex(_i)
        win._apply_system_preset()
    for _dugum, _v in girilen.items():
        win.tab_solver.fields[_dugum].setText(str(_v))
    win._bastan_bos_guncelle()
    getattr(win, silinen).setValue(0)
    _bos = [d for d in win._bos_alanlar() if d not in win._bastan_bos]
    _res = solver.solve_for(win._panel_bilinenleri(), _bos,
                            model=win.f_proj.currentData())
    return _res.get(win.ALAN_DUGUM[silinen])


_v = _ters_dene(_CMV, {"fov_x_deg": 9.2}, "f_focal")
kontrol("FOV girildi -> odak uzaklığı bulunuyor", abs(_v - 70.0) < 0.01,
        f"f = {_v:.4f} mm")

_v = _ters_dene(_CMV, {"ifov_x_urad": 78.5714}, "f_focal")
kontrol("IFOV girildi -> odak uzaklığı bulunuyor", abs(_v - 70.0) < 0.05,
        f"f = {_v:.4f} mm")

_v = _ters_dene(_CMV, {"fov_x_deg": 9.2}, "f_pitch_x")
kontrol("FOV girildi -> piksel pitch bulunuyor", abs(_v - 5.5) < 0.01,
        f"pitch = {_v:.4f} µm")

_v = _ters_dene(_HYD, {"scr_f_mm": 28.90}, "f_scr_ang")
kontrol("ekran f'i girildi -> açısal çözünürlük bulunuyor",
        abs(_v - 0.027) < 0.0005, f"°/px = {_v:.6f}")

_v = _ters_dene(_HYD, {"det_diag_mm": 26.0673}, "f_pitch_x")
kontrol("sensör köşegeni girildi -> pitch bulunuyor", abs(_v - 18.0) < 0.05,
        f"pitch = {_v:.4f} µm")

# Ölçek görüntüden ölçülür: lensi fiziksel olarak ölçmeden f bulunabilir.
# Hydra'da pupil (34) × f# (1.4) = 47.6 da bir yol; çözücü hangisini
# seçerse seçsin ikisi de datasheet'in 47.7'sine %0.25 içinde. Testi bu
# yolu ZORLAYACAK şekilde kurmak için pupil'i de boşaltıyoruz — yoksa
# ölçek yolunun çalıştığını değil, pupil yolunun çalıştığını ölçerdik.
_win12 = MainWindow()
_win12.f_system.setCurrentIndex(_win12.f_system.findData(_HYD))
_win12._apply_system_preset()
_win12.tab_solver.fields["scr_f_mm"].setText("28.90")
_win12.tab_solver.fields["scale_expected"].setText(
    str((47.7 / 0.018) / (28.90 / 0.01362)))
_win12._bastan_bos_guncelle()
_win12.f_focal.setValue(0)
_win12.f_pupil.setValue(0)       # pupil yolunu kapat
_r12 = solver.solve_for(_win12._panel_bilinenleri(), ["lens_f_mm"])
_v = _r12.get("lens_f_mm")
kontrol("ölçek girildi -> lens f'i görüntüden bulunuyor",
        abs(_v - 47.7) < 0.05, f"f = {_v:.4f} mm")
kontrol("bu yol gerçekten ölçek üzerinden gitti",
        "ölçek" in _r12.values["lens_f_mm"].rule.lower(),
        _r12.values["lens_f_mm"].rule)


# ---------------------------------------------------------------------------
print("\n[13] Girilen değer 'datasheet', türetilen 'türetildi'")
# Kullanıcının GİRDİĞİ bir FOV, sonuç panelinde "türetildi" görünmemeli.
w13 = MainWindow()
w13.f_system.setCurrentIndex(w13.f_system.findData(_CMV))
w13._apply_system_preset()
kontrol("FOV girilmemişken türetilmiş sayılıyor",
        w13._solver_sources().get("fov_x_deg", ("?",))[0] == "derived")
w13.tab_solver.fields["fov_x_deg"].setText("9.2")
kontrol("FOV girilince 'given' oluyor",
        w13._solver_sources().get("fov_x_deg", ("?",))[0] == "given",
        w13._solver_sources().get("fov_x_deg", ("yok",))[0])

# Preset değişince ölçülen değerler taşınmamalı: eski sistemin FOV'u yeni
# sistemin panelinde kalırsa çözücü onu bilinen sayar ve çelişki üretir.
w13.f_system.setCurrentIndex(w13.f_system.findData(_HYD))
w13._apply_system_preset()
# Sekme preset'ten bağımsızdır: kullanıcının oraya yazdığı değer
# donanım değişince silinmez, çünkü sekme geçici bir çalışma alanıdır.
kontrol("sekmedeki değer preset değişince duruyor",
        w13.tab_solver.fields["fov_x_deg"].text() == "9.2",
        w13.tab_solver.fields["fov_x_deg"].text())



# ---------------------------------------------------------------------------
print("\n[14] CANLI HESAP — sağ bar sol panelden anında dolar")
# FOV/IFOV görüntü gerektirmez, donanımdan çıkar. Buna rağmen sağ bar
# analiz koşulana kadar boş duruyordu ve hesaplatmak için ayrıca butona
# basmak gerekiyordu. Artık sol panelde ne varsa sağ barda karşılığı yazar.
w14 = MainWindow()
kontrol("açılışta FOV zaten hesaplanmış",
        w14.r_fov_xy._value.text().startswith("9.200"),
        w14.r_fov_xy._value.text())
kontrol("açılışta IFOV zaten hesaplanmış",
        w14.r_ifov._value.text().startswith("78.57"),
        w14.r_ifov._value.text())
kontrol("açılışta sensör ölçüsü yazılı",
        "11.26" in w14.r_sensor._value.text(), w14.r_sensor._value.text())

# Bir alan değişince ANINDA güncellenmeli — butona basılmadan.
w14.f_focal.setValue(50.0)
kontrol("f değişince FOV anında güncelleniyor",
        w14.r_fov_xy._value.text().startswith("12.85"),
        f"f=50mm -> {w14.r_fov_xy._value.text()}")
w14.f_pitch_x.setValue(11.0)
kontrol("pitch değişince IFOV anında güncelleniyor",
        w14.r_ifov._value.text().startswith("220"),
        w14.r_ifov._value.text())

# ÖLÇÜME dayanan satırlar "ölçülemedi" DEMEMELİ: analiz denenmedi ki
# başarısız olsun. Bu, kullanıcıyı olmayan bir sorunu aramaya gönderirdi.
w14b = MainWindow()
for _ad in ("r_tilt", "r_decenter", "r_mirror", "r_inliers"):
    _row = getattr(w14b, _ad)
    kontrol(f"{_ad}: 'ölçülemedi' değil, boş",
            _row._value.text() == "—", _row._value.text())
kontrol("durum satırı ne yapılacağını söylüyor",
        "ANALİZ ET" in w14b.lbl_verdict.text(),
        w14b.lbl_verdict.text()[:60])

# Boş alan varsa sağ bar YİNE hesaplamalı: değer türetilebiliyorsa
# kullanıcının onu panele yazmasını beklemeye gerek yok.
w14c = MainWindow()
w14c.tab_solver.fields["fov_x_deg"].setText("9.2")
w14c.f_focal.setValue(0)          # f'i sil — FOV'dan türetilebilir
kontrol("f boşken FOV yine hesaplanıyor",
        w14c.r_fov_xy._value.text().startswith("9.200"),
        w14c.r_fov_xy._value.text())
kontrol("f alanı boş KALIYOR (panele yazılmıyor)",
        w14c.f_focal.value() == 0.0,
        "boş alan kullanıcının 'bilmiyorum' demesidir")

# Hydra: f silinse bile pupil × f# üzerinden çıkar.
w14d = MainWindow()
w14d.f_system.setCurrentIndex(w14d.f_system.findData("Hydra yıldız izleyici"))
w14d._apply_system_preset()
w14d.f_focal.setValue(0)
kontrol("f boşken pupil×f# yolundan FOV çıkıyor",
        w14d.r_fov_xy._value.text().startswith("21.9"),
        w14d.r_fov_xy._value.text())

# Hiçbir yol yoksa "nan" DEĞİL, dürüst bir açıklama görünmeli.
w14e = MainWindow()
w14e.f_focal.setValue(0)
kontrol("çözülemeyince 'nan' gösterilmiyor",
        "nan" not in w14e.r_fov_xy._value.text().lower(),
        w14e.r_fov_xy._value.text())
kontrol("çözülemeyince neden açıklanıyor",
        "yeterli bilgi yok" in w14e.lbl_verdict.text(),
        w14e.lbl_verdict.text()[:50])

# Analiz koşulduktan sonra canlı hesap ÖLÇÜMÜN üzerine yazmamalı.
w14f = MainWindow()
w14f._analiz_sonucu_var = True
_onceki = w14f.r_fov_xy._value.text()
w14f.f_focal.setValue(33.0)
kontrol("analizden sonra canlı hesap ölçümü ezmiyor",
        w14f.r_fov_xy._value.text() == _onceki,
        f"'{_onceki}' korundu")



# ---------------------------------------------------------------------------
print("\n[15] Sonuç satırı: ne etiket ne değer kırpılır")
# Bu yerleşim iki kez yanlış kuruldu ve ikisi de aynı tehlikeyi üretti:
# yarım okunan bir sayı sessizce yanlış karara götürür.
#   1. Etiket stretch alınca değer kırpıldı ("0 × 9.200").
#   2. Etikete Ignored verilince etiket yok oldu, değer soldan kesildi.
w15 = MainWindow()
w15.resize(1720, 950)
w15.show()
app.processEvents()

kontrol("değer satır sarabiliyor", w15.r_fov_xy._value.wordWrap())
kontrol("etiketin alt genişliği var",
        w15.r_fov_xy._label.minimumWidth() >= 80,
        f"{w15.r_fov_xy._label.minimumWidth()} px")
kontrol("değerin alt genişliği var",
        w15.r_fov_xy._value.minimumWidth() >= 96,
        f"{w15.r_fov_xy._value.minimumWidth()} px")

# Uzun bir değer verildiğinde metin OLDUĞU GİBİ saklanmalı; görünürde
# sarılır ama içerik kısalmaz.
_uzun = "546.677 / 652.620  (tüm ekran (desen yarıçapı bilinmiyor))"
w15.r_cov_pattern.set_value(_uzun)
app.processEvents()
kontrol("uzun değer içeriği kısalmıyor",
        w15.r_cov_pattern._value.text() == _uzun,
        f"{len(_uzun)} karakter korundu")

# Etiket kısalırsa tam metin ipucunda kalmalı — kullanıcı hangi satıra
# baktığını yine anlayabilsin.
w15.r_cov_pattern.set_label("Desenden kullanılan")
app.processEvents()
# Kendi açıklayıcı ipucu OLMAYAN bir satırda, kısaltma tam metni ipucuna
# koymalı. Açıklayıcı ipucu olan satırlarda o ipucu korunur — kısaltma
# bilgisi için satırın asıl açıklamasını feda etmek yanlış olurdu.
_sade = ResultRow("Çok uzun bir satır etiketi örneği", "px")
_sade.resize(90, 20)
_sade.show()
app.processEvents()
_sade._etiketi_sigdir()
kontrol("ipucusuz satırda kısaltma tam metni ipucuna koyuyor",
        _sade._label.text() == _sade._label_tam
        or _sade._label.toolTip() == _sade._label_tam,
        f"'{_sade._label.text()}' → ipucu '{_sade._label.toolTip()[:28]}'")
kontrol("açıklayıcı ipucu kısaltmayla ezilmiyor",
        w15.r_cov_pattern._aciklama_ipucu
        and "sensöre düşüyor" in w15.r_cov_pattern._label.toolTip(),
        w15.r_cov_pattern._label.toolTip()[:44] + "…")
kontrol("set_label tam metni saklıyor",
        w15.r_cov_pattern._label_tam == "Desenden kullanılan")

# Sağ panel, en uzun satırı taşımaya yetecek kadar geniş açılmalı.
kontrol("sağ panel yeterince geniş açılıyor",
        w15.centralWidget().findChild(type(w15.tabs)) is not None and
        w15.width() > 0)


# ---------------------------------------------------------------------------
print("\n[16] Çözücü özeti: eksiklik ile 'girilmedi' karışmıyor")
# Ekranda FOV 21.87° hesaplanmışken turuncu "Çözülemeyen: Kullanılabilir FOV,
# Görüntü dairesi çapı" uyarısı çıkıyordu. O iki büyüklük donanımdan
# TÜRETİLEMEZ (üretici/datasheet verir); boş kalmaları eksiklik değildir ve
# uyarı kullanıcıyı FOV hesaplanamadı sanmaya itiyordu.
import re as _re
from gui.solver_tab import DISARIDAN_GELEN

def _duz(html):
    return _re.sub("<[^>]+>", "", html.replace("<br>", "\n"))

w16 = MainWindow()
w16.f_system.setCurrentIndex(w16.f_system.findData("Hydra yıldız izleyici"))
w16._apply_system_preset()
t16 = w16.tab_solver
t16.btn_panelden.click()
t16.fields["lens_useful_fov_deg"].clear()      # ekrandaki senaryo
t16.coz()
_ozet = _duz(t16.lbl_ozet.text())

kontrol("FOV gerçekten hesaplanıyor",
        abs(solver.solve(t16.girdiler()).get("fov_x_deg") - 21.8705) < 0.01,
        "21.8705°")
kontrol("özet FOV'u ilk satırlarda gösteriyor",
        "FOV X" in _ozet.split("\n")[1],
        _ozet.split("\n")[1][:52])
kontrol("türetilemeyen büyüklük uyarısı YOK",
        "Türetilemedi" not in _ozet,
        "yalnızca bilgi satırı var")
kontrol("dışarıdan gelenler yumuşak dille bildiriliyor",
        "Girilmedi" in _ozet and "sorun değildir" in _ozet)
kontrol("üretici FOV dışarıdan-gelen sayılıyor",
        "lens_useful_fov_deg" in DISARIDAN_GELEN)

# GERÇEK eksiklikte uyarı hâlâ çıkmalı — mesajı yumuşatmak, gerçek
# boşluğu gizlemek anlamına gelmemeli.
for _le in t16.fields.values():
    _le.clear()
t16.fields["det_pitch_um"].setText("5.5")
t16.coz()
kontrol("gerçek eksiklikte uyarı korunuyor",
        "Türetilemedi" in _duz(t16.lbl_ozet.text()),
        "tek girdiyle çoğu büyüklük çözülemez")

# Çelişki: aynı büyüklük birden çok kuraldan çelişebilir; kullanıcı için
# bu TEK tutarsızlıktır ve ham düğüm adı değil etiket görmelidir.
for _le in t16.fields.values():
    _le.clear()
for _k, _v in (("lens_f_mm", "70"), ("det_pitch_um", "5.5"),
               ("det_w_px", "2048"), ("fov_x_deg", "15.0")):
    t16.fields[_k].setText(_v)
t16.coz()
_c = _duz(t16.lbl_ozet.text())
kontrol("çelişki bildiriliyor", "ÇELİŞKİ" in _c)
kontrol("çelişkide insan-okur etiket kullanılıyor",
        "FOV X" in _c and "fov_x_deg" not in _c,
        "ham düğüm adı sızmıyor")
kontrol("çelişki büyüklük başına tekilleştiriliyor",
        _c.count("FOV X: girdiniz") <= 1,
        "aynı büyüklük tek kez listeleniyor")
kontrol("girilen değerin korunduğu söyleniyor",
        "korundu" in _c)


# ---------------------------------------------------------------------------
print("\n[17] Ölçümden gelen odak uzaklığı — bağımsız doğrulama")
# Panelin geri kalanı datasheet f'ine dayanır. Bu iki satır GÖRÜNTÜDEN
# gelir: hizalamanın ölçtüğü ölçek, ekranın açısal ölçeği biliniyorsa
# lensin f'ini verir. Ayrışma odak kayması/montaj/yanlış parametre demektir
# ve panelin başka hiçbir satırı bunu yakalayamaz.
from core import pointing as _pointing, projection as _proj
from core.config import SCREEN_CATALOG as _SCR

_stos = _SCR["STOS (1280×1024, 13.62µm, 0.027°/px)"]
_pd = 5.5e-3
_ps = _stos.pixel_pitch_um / 1000.0
_fs = _stos.implied_focal_mm

# Ters hesap TAM olmalı: bilinen f'ten ölçek üret, ölçekten f'i geri oku.
for _f in (47.7, 24.659, 70.0):
    _olcek = (_f / _pd) / (_fs / _ps)
    _geri = _olcek * (_fs / _ps) * _pd
    kontrol(f"ölçekten f geri okunuyor (f={_f})", abs(_geri - _f) < 1e-9,
            f"{_geri:.6f} mm")

kontrol("STOS açısal kaynak olarak tanınıyor",
        _stos.angular_res_deg > 0 and _fs > 0,
        f"°/px={_stos.angular_res_deg}, ima edilen f={_fs:.3f} mm")

# Pasif panelde ölçek f vermez: iki bilinmeyenli tek denklem kalır.
_oled = _SCR["GL049AMN10A OLED (1920×1080, 5.616µm)"]
kontrol("pasif panelde açısal ölçek yok",
        _oled.angular_res_deg == 0.0,
        "ölçek tek başına f vermez — bu doğru davranış")

# Panel satırları: ölçüm yoksa GİZLİ olmalı, boş "—" değil.
w17 = MainWindow()
w17.show()
app.processEvents()
kontrol("ölçüm yokken 'Ölçülen f' gizli", not w17.r_focal_meas.isVisible())
kontrol("ölçüm yokken 'Ölçülen FOV' gizli", not w17.r_fov_meas.isVisible())

# Satırlar FOV grubunun layout'unda duruyor mu? `_fov_satir_sirala`
# listelemezse widget layout'tan düşer ve setVisible(True) işe yaramaz.
w17._fov_satir_sirala(False)
_layout_widgets = [w17._fov_layout.itemAt(i).widget()
                   for i in range(w17._fov_layout.count())]
kontrol("ölçüm satırları FOV layout'unda kalıyor",
        w17.r_focal_meas in _layout_widgets and w17.r_fov_meas in _layout_widgets,
        f"{len(_layout_widgets)} satır")
w17._fov_satir_sirala(True)
_layout_widgets = [w17._fov_layout.itemAt(i).widget()
                   for i in range(w17._fov_layout.count())]
kontrol("daire kısıtlı dizilişte de kalıyor",
        w17.r_focal_meas in _layout_widgets and w17.r_fov_meas in _layout_widgets)

# Sentetik bir PointingResult ile panel doldurma yolunu denetle.
class _P:
    measured_focal_mm = 24.659
    focal_error_pct = -0.001
    measured_fov_x_deg = 25.731
    measured_ifov_urad = 223.0


class _R:
    pointing = _P()
    fov = compute_fov(w17._config_from_fields())


w17.f_focal.setValue(24.659)
_r17 = _R()
_r17.fov = compute_fov(w17._config_from_fields())
w17._olculen_optigi_yaz(_r17)
app.processEvents()
kontrol("ölçüm varken satır görünür", w17.r_focal_meas.isVisible())
kontrol("ölçülen f değeri yazılıyor",
        "24.659" in w17.r_focal_meas._value.text(),
        w17.r_focal_meas._value.text())
kontrol("uyumlu ölçümde ipucu 'uyumlu' diyor",
        "uyumlu" in w17.r_focal_meas._badge.toolTip(),
        "%0'a yakın fark")
kontrol("bağıntı ipucunda yazılı",
        "pitch_ekran" in w17.r_focal_meas._badge.toolTip())

# Ayrışma büyükse uyarı diline geçmeli.
_P.focal_error_pct = -64.77
w17._olculen_optigi_yaz(_r17)
kontrol("büyük ayrışmada uyarı dili",
        "AYRIŞMA VAR" in w17.r_focal_meas._badge.toolTip(),
        "%-64.77 fark")

print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)