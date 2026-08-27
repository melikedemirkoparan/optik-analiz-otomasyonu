# -*- coding: utf-8 -*-
"""
İlişki çözücü testleri — `core/solver.py`.

Sorulan soru: "bilinenlerden bilinmeyenleri gerçekten türetiyor mu ve
türettiği sayı doğru mu?" Doğrulama ölçütü PROJENİN DOĞRULANMIŞ REFERANS
DEĞERLERİ (DEVAM_YONERGESI §4, §7C, §7E):

    CMV4000 + Rodenstock : FOV 9.200°, IFOV 78.57 µrad/px
    Hydra                : FOV 21.870°, IFOV 377.36 µrad/px
    STOS                 : ima edilen f 28.90 mm, kapsama ±16.78° × ±13.56°
    çapraz doğrulama     : beklenen ölçek 1.2488

En kritik test grubu [2]: ROUND-TRIP. Bir kural ileri yönde doğru olup ters
yönde yanlış yazılabilir (FOV↔IFOV bağıntısında tam olarak bu oldu —
yarım açı katsayısı sadeleşmesi kaçırılınca sonuç iki kat sapıyordu).
Round-trip her ilişkiyi kendi tersiyle karşılaştırır.
"""
from __future__ import annotations

import math
import sys

from core import solver
from core.config import system_from_preset
from core.optics import compute_fov

GECTI = 0
KALDI = 0


def kontrol(ad: str, kosul: bool, ayrinti: str = ""):
    global GECTI, KALDI
    if kosul:
        GECTI += 1
        print(f"   ✓ {ad}" + (f"  ({ayrinti})" if ayrinti else ""))
    else:
        KALDI += 1
        print(f"   ✗ {ad}  {ayrinti}")


def yakin(a: float, b: float, bagil: float = 1e-4) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    if b == 0:
        return abs(a) < bagil
    return abs(a - b) / abs(b) < bagil


# ---------------------------------------------------------------------------
print("\n[1] Katalog sistemleri — çözücü, optics.compute_fov ile aynı sayıyı vermeli")
# İki bağımsız kod yolu: compute_fov doğrudan formülü uygular, çözücü aynı
# ilişkiyi kural grafiğinden geçirir. Ayrışırlarsa biri yanlıştır.
for ad in ("CMV4000 + Rodenstock 70mm", "Hydra yıldız izleyici"):
    cfg = system_from_preset(ad)
    fov = compute_fov(cfg)
    r = solver.solve_config(cfg)
    kontrol(f"{ad}: FOV X", yakin(r.get("fov_x_deg"), fov.fov_x_deg),
            f"{r.get('fov_x_deg'):.4f}° vs {fov.fov_x_deg:.4f}°")
    kontrol(f"{ad}: FOV Y", yakin(r.get("fov_y_deg"), fov.fov_y_deg))
    kontrol(f"{ad}: FOV köşegen", yakin(r.get("fov_diag_deg"), fov.fov_diag_deg),
            f"{r.get('fov_diag_deg'):.4f}°")
    kontrol(f"{ad}: IFOV X", yakin(r.get("ifov_x_urad"), fov.ifov_x_urad),
            f"{r.get('ifov_x_urad'):.3f} µrad/px")
    kontrol(f"{ad}: IFOV arcsec", yakin(r.get("ifov_x_arcsec"), fov.ifov_x_arcsec))
    kontrol(f"{ad}: sensör ölçüsü", yakin(r.get("det_w_mm"), fov.sensor_w_mm))
    kontrol(f"{ad}: çelişkisiz", not r.conflicts,
            "; ".join(c.describe() for c in r.conflicts))

# Katalog değerleri yönergedeki doğrulanmış sayılarla birebir.
r_cmv = solver.solve_config(system_from_preset("CMV4000 + Rodenstock 70mm"))
kontrol("referans: CMV4000 FOV 9.200°", yakin(r_cmv.get("fov_x_deg"), 9.200, 1e-3))
kontrol("referans: CMV4000 IFOV 78.57", yakin(r_cmv.get("ifov_x_urad"), 78.571, 1e-3))
r_hyd = solver.solve_config(system_from_preset("Hydra yıldız izleyici"))
kontrol("referans: Hydra FOV 21.870°", yakin(r_hyd.get("fov_x_deg"), 21.870, 1e-3))
kontrol("referans: Hydra IFOV 377.36", yakin(r_hyd.get("ifov_x_urad"), 377.358, 1e-3))
kontrol("referans: STOS ima edilen f 28.90 mm",
        yakin(r_hyd.get("scr_f_mm"), 28.9025, 1e-3))
kontrol("referans: STOS kapsama ±16.78° × ±13.56°",
        yakin(r_hyd.get("scr_half_x_deg"), 16.783, 1e-3)
        and yakin(r_hyd.get("scr_half_y_deg"), 13.565, 1e-3))
kontrol("referans: beklenen ölçek 1.2488",
        yakin(r_hyd.get("scale_expected"), 1.2488, 1e-3),
        f"{r_hyd.get('scale_expected'):.4f}")


# ---------------------------------------------------------------------------
print("\n[2] ROUND-TRIP — her ilişki kendi tersiyle tutarlı olmalı")
# Yöntem: gerçek bir sistemin TÜM değerlerini çöz, sonra her düğümü tek tek
# "bilinmeyen" yapıp geri kalanlardan yeniden türetilebiliyor mu bak.
# İleri/ters katsayı hatası burada yakalanır.
tam = solver.solve_config(system_from_preset("Hydra yıldız izleyici"))
tum = {n: v.value for n, v in tam.values.items()}

# Lens üçlüsü (f, f/#, pupil) round-trip'ten MUAF — ama testten değil:
# ayrı olarak [2b]'de sınanıyor. Sebebi kod değil VERİ: Hydra datasheet'i
# üçünü de veriyor ve kendi aralarında tam tutarlı değiller
# (34.0 × 1.4 = 47.6, datasheet ise f = 47.7). Bu yüzden "birini sil,
# kalanlardan geri türet" ölçütü bu üçlüde 1e-6 hassasiyetle sağlanamaz —
# sağlansaydı çözücü datasheet'in yuvarlamasını uydurmuş olurdu.
LENS_UCLUSU = {"lens_f_mm", "lens_fnum", "lens_pupil_mm"}

geri_turetilen = 0
for hedef in sorted(tum):
    if hedef in LENS_UCLUSU:
        continue
    kalan = {k: v for k, v in tum.items() if k != hedef}
    r = solver.solve(kalan)
    if hedef not in r.values:
        continue          # tek başına belirlenemiyor olabilir — hata değil
    geri_turetilen += 1
    kontrol(f"geri türetim: {solver.label(hedef)}",
            yakin(r.get(hedef), tum[hedef], 1e-6),
            f"{r.get(hedef):.6g} vs {tum[hedef]:.6g}")
kontrol("round-trip kapsaması anlamlı", geri_turetilen >= 15,
        f"{geri_turetilen} düğüm geri türetildi")

# Lens üçlüsü kendi içinde tutarlı bir veriyle round-trip yapmalı.
tutarli = {"lens_f_mm": 47.6, "lens_fnum": 1.4, "lens_pupil_mm": 34.0}
for hedef in tutarli:
    kalan = {k: v for k, v in tutarli.items() if k != hedef}
    r = solver.solve(kalan)
    kontrol(f"geri türetim (tutarlı lens): {solver.label(hedef)}",
            yakin(r.get(hedef), tutarli[hedef], 1e-9),
            f"{r.get(hedef):.6g} vs {tutarli[hedef]:.6g}")

print("\n[2b] Datasheet'in kendi yuvarlaması — uydurulmuyor, gizlenmiyor")
# Hydra: f=47.7, f/#=1.4, pupil=34.0 üçü de üreticiden. f/# × pupil = 47.6.
# Doğru davranış: verilen f'e DOKUNMA (§4 kuralı), farkı da yut değil ölç.
r = solver.solve({"lens_f_mm": 47.7, "lens_fnum": 1.4, "lens_pupil_mm": 34.0})
kontrol("verilen f korunuyor", r.get("lens_f_mm") == 47.7)
kontrol("verilen pupil korunuyor", r.get("lens_pupil_mm") == 34.0)
fark = abs(47.6 - 47.7) / 47.7
kontrol("fark %1 toleransın altında (çelişki değil)", fark < solver.DEFAULT_TOLERANCE,
        f"%{fark*100:.2f} — üretici yuvarlaması")
kontrol("bu yüzden çelişki raporlanmıyor", not r.conflicts,
        "; ".join(c.describe() for c in r.conflicts))
# Ama dar toleransla sorulursa fark GÖRÜNÜR olmalı — bilgi saklanmıyor.
r_dar = solver.solve({"lens_f_mm": 47.7, "lens_fnum": 1.4,
                      "lens_pupil_mm": 34.0}, tolerance=1e-4)
kontrol("dar toleransta fark görünür hale geliyor", len(r_dar.conflicts) > 0,
        r_dar.conflicts[0].describe() if r_dar.conflicts else "")


# ---------------------------------------------------------------------------
print("\n[3] Bilinmeyeni türetme senaryoları (kullanıcının asıl istediği)")

# a) Datasheet FOV veriyor, odak uzaklığı vermiyor.
r = solver.solve({"fov_x_deg": 9.19989, "det_w_px": 2048, "det_pitch_um": 5.5})
kontrol("FOV + dedektör → lens f", yakin(r.get("lens_f_mm"), 70.0, 1e-4),
        f"{r.get('lens_f_mm'):.4f} mm")
kontrol("FOV + dedektör → IFOV", yakin(r.get("ifov_x_urad"), 78.5714, 1e-4))
kontrol("türetilmiş olarak işaretli", r.is_derived("lens_f_mm"))

# b) Elde yalnız açısal çözünürlük (arcsec) ve piksel sayısı var.
r = solver.solve({"ifov_x_arcsec": 16.20652, "det_w_px": 2048})
kontrol("IFOV(arcsec) + N → FOV", yakin(r.get("fov_x_deg"), 9.19989, 1e-4),
        f"{r.get('fov_x_deg'):.5f}°")
kontrol("IFOV(arcsec) → IFOV(µrad)", yakin(r.get("ifov_x_urad"), 78.5714, 1e-4))
kontrol("pitch yokken f uydurulmuyor", not r.is_known("lens_f_mm"),
        "f ve pitch birlikte belirsiz — çözücü sayı üretmemeli")

# c) IFOV + pitch → f (datasheet "açısal çözünürlük" veriyorsa)
r = solver.solve({"ifov_x_urad": 377.35849, "det_pitch_um": 18.0,
                  "det_w_px": 1024})
kontrol("IFOV + pitch → f", yakin(r.get("lens_f_mm"), 47.7, 1e-4),
        f"{r.get('lens_f_mm'):.4f} mm")
kontrol("oradan FOV", yakin(r.get("fov_x_deg"), 21.87048, 1e-4))

# d) Ekranın °/px'i yok; f ve pitch var.
r = solver.solve({"scr_f_mm": 28.90254, "scr_pitch_um": 13.62,
                  "scr_w_px": 1280, "scr_h_px": 1024})
kontrol("ekran f + pitch → °/px", yakin(r.get("scr_ang_deg"), 0.027, 1e-4),
        f"{r.get('scr_ang_deg'):.5f} °/px")
kontrol("→ kapsama X", yakin(r.get("scr_half_x_deg"), 16.783, 1e-3))

# e) Ekranın kapsaması biliniyor, °/px bilinmiyor.
r = solver.solve({"scr_half_x_deg": 16.78294, "scr_w_px": 1280,
                  "scr_pitch_um": 13.62})
kontrol("kapsama → °/px", yakin(r.get("scr_ang_deg"), 0.027, 1e-4))
kontrol("kapsama → ima edilen f", yakin(r.get("scr_f_mm"), 28.9025, 1e-4))

# f) Görüntüden ölçülen ölçekten lens odak uzaklığı.
# §7E'de ölçülen 1.2458 -> 47.586 mm; gerçek 47.7 (%0.24 fark, yönergedeki
# çapraz doğrulama farkının aynısı). Bu satır o farkın f cinsinden karşılığı.
r = solver.solve({"scale_expected": 1.2458, "det_pitch_um": 18.0,
                  "scr_pitch_um": 13.62, "scr_ang_deg": 0.027})
f_olculen = r.get("lens_f_mm")
kontrol("ölçülen ölçek → lens f", yakin(f_olculen, 47.7, 3e-3),
        f"{f_olculen:.3f} mm (gerçek 47.7, fark %{abs(f_olculen-47.7)/47.7*100:.2f})")

# g) Lens: pupil / f# / f üçlüsü — hangi ikisi verilirse üçüncüsü çıkar.
kontrol("f + f# → pupil",
        yakin(solver.solve({"lens_f_mm": 47.7, "lens_fnum": 1.4}).get("lens_pupil_mm"),
              34.0714, 1e-3))
kontrol("f + pupil → f#",
        yakin(solver.solve({"lens_f_mm": 47.7, "lens_pupil_mm": 34.0}).get("lens_fnum"),
              1.4029, 1e-3))
kontrol("pupil + f# → f",
        yakin(solver.solve({"lens_pupil_mm": 34.0, "lens_fnum": 1.4}).get("lens_f_mm"),
              47.6, 1e-3))


# ---------------------------------------------------------------------------
print("\n[4] Verilen değer korunur, türetilen üzerine YAZILMAZ")
# §5 ve §7B'deki dersin çözücüdeki karşılığı: girdi katmanının söylediği
# sayının üzerine hesaplanan bir yedek yazılmamalı.
r = solver.solve({"lens_f_mm": 70.0, "det_pitch_um": 5.5, "det_w_px": 2048,
                  "fov_x_deg": 9.19989})
kontrol("girilen f değişmedi", r.get("lens_f_mm") == 70.0)
kontrol("girilen f 'given' kaldı", r.values["lens_f_mm"].is_given)
kontrol("girilen FOV değişmedi", r.get("fov_x_deg") == 9.19989)


# ---------------------------------------------------------------------------
print("\n[5] Tutarsız girdi ÇELİŞKİ olarak raporlanır (sessizce yutulmaz)")
# Yanlış f: FOV ve dedektör ölçüsüyle uyuşmuyor.
r = solver.solve({"lens_f_mm": 60.0, "fov_x_deg": 9.19989, "det_w_px": 2048,
                  "det_pitch_um": 5.5})
kontrol("çelişki yakalandı", len(r.conflicts) > 0, f"{len(r.conflicts)} adet")
kontrol("çelişen düğüm lens_f_mm arasında",
        any(c.name == "lens_f_mm" for c in r.conflicts))
f_cel = next((c for c in r.conflicts if c.name == "lens_f_mm"), None)
kontrol("çelişki doğru alternatifi gösteriyor",
        f_cel is not None and yakin(f_cel.derived, 70.0, 1e-3),
        f_cel.describe() if f_cel else "")
kontrol("değer yine de girilen kaldı", r.get("lens_f_mm") == 60.0)

# Tutarlı sistemde çelişki YOK — yanlış alarm üretmemeli.
kontrol("tutarlı sistemde yanlış alarm yok",
        not solver.solve_config(system_from_preset("Hydra yıldız izleyici")).conflicts)

# Yuvarlanmış datasheet değeri çelişki sayılmamalı: STOS'un 0.027 °/px'i
# 3 haneye yuvarlı; toleransın bunu yutması, %16'lık gerçek hatayı yutmaması
# gerekiyor.
r = solver.solve({"scr_pitch_um": 13.62, "scr_ang_deg": 0.027,
                  "scr_f_mm": 28.90})
kontrol("yuvarlama çelişki sayılmıyor", not r.conflicts,
        "; ".join(c.describe() for c in r.conflicts))


# ---------------------------------------------------------------------------
print("\n[6] Bilgisiz girdide sayı UYDURULMAZ")
# §7B'deki "düz gri alanı reddet" ilkesinin cebirsel karşılığı.
r = solver.solve({})
kontrol("boş girdi → boş sonuç", len(r.values) == 0)

r = solver.solve({"det_w_px": 2048, "det_h_px": 2048})
kontrol("yalnız piksel sayısı FOV vermez", not r.is_known("fov_x_deg"))
kontrol("yalnız piksel sayısı IFOV vermez", not r.is_known("ifov_x_urad"))

# Sıfır ve negatif "verilmedi" demek — arayüzdeki boş alanlar 0 gelir.
r = solver.solve({"lens_f_mm": 0.0, "det_pitch_um": 5.5, "det_w_px": 2048})
kontrol("f=0 'verilmedi' sayılıyor", not r.is_known("lens_f_mm"))
kontrol("f=0 iken IFOV uydurulmuyor", not r.is_known("ifov_x_urad"))
r = solver.solve({"lens_f_mm": float("nan"), "det_pitch_um": 5.5})
kontrol("NaN girdi yutuluyor", not r.is_known("lens_f_mm"))

# Pasif panelde (OLED) ekran açısal zinciri hiç kurulmamalı.
r = solver.solve_config(system_from_preset("CMV4000 + Rodenstock 70mm"))
kontrol("pasif panelde ima edilen f üretilmiyor", not r.is_known("scr_f_mm"),
        "OLED açısal kaynak değil — açısal ölçek TANIMSIZ")
kontrol("pasif panelde ölçek öngörüsü yok", not r.is_known("scale_expected"))

# solve_for çözülemeyeni açıkça bildirmeli.
r = solver.solve_for({"det_w_px": 2048}, ["fov_x_deg", "lens_f_mm"])
kontrol("solve_for çözülemeyeni bildiriyor",
        set(r.unresolved) == {"fov_x_deg", "lens_f_mm"})


# ---------------------------------------------------------------------------
print("\n[7] Türetim zinciri (kaynak izleme) okunabilir olmalı")
r = solver.solve({"fov_x_deg": 21.87048, "det_w_px": 1024, "det_pitch_um": 18.0})
iz = r.trace("lens_f_mm")
kontrol("zincir üretiliyor", len(iz) >= 3, f"{len(iz)} satır")
kontrol("zincir kökte girdiyi gösteriyor", any("(girdi)" in s for s in iz))
kontrol("zincir sonda hedefi gösteriyor", "Lens odak uzaklığı f" in iz[-1])
for s in iz:
    print("        " + s)
kontrol("girdinin açıklaması 'datasheet/girdi'",
        r.explain("det_w_px") == "datasheet/girdi")
kontrol("türetilenin açıklaması kuralı içeriyor",
        "türetildi" in r.explain("lens_f_mm"))

# En kısa yol tercih edilmeli — açıklama gereksiz uzamasın.
r = solver.solve_config(system_from_preset("Hydra yıldız izleyici"))
kontrol("türetim derinliği makul",
        max(v.depth for v in r.values.values()) <= 4,
        f"en derin {max(v.depth for v in r.values.values())} adım")


# ---------------------------------------------------------------------------
print("\n[8] Parametriklik — donanım değişince matematik takip etmeli")
# §7C [6] ile aynı ruh: aynı ilişki farklı donanımda farklı sayı vermeli,
# hiçbir değer koda gömülü olmamalı.
a = solver.solve({"lens_f_mm": 70.0, "det_pitch_um": 5.5, "det_w_px": 2048})
b = solver.solve({"lens_f_mm": 47.7, "det_pitch_um": 18.0, "det_w_px": 1024})
kontrol("farklı donanım farklı IFOV",
        not yakin(a.get("ifov_x_urad"), b.get("ifov_x_urad"), 1e-2),
        f"{a.get('ifov_x_urad'):.2f} vs {b.get('ifov_x_urad'):.2f} µrad/px")
kontrol("farklı donanım farklı FOV",
        not yakin(a.get("fov_x_deg"), b.get("fov_x_deg"), 1e-2),
        f"{a.get('fov_x_deg'):.3f}° vs {b.get('fov_x_deg'):.3f}°")

# f iki katına çıkarsa IFOV yarıya iner (küçük açıda), FOV daralır.
c = solver.solve({"lens_f_mm": 140.0, "det_pitch_um": 5.5, "det_w_px": 2048})
kontrol("f iki katı → IFOV yarısı",
        yakin(c.get("ifov_x_urad"), a.get("ifov_x_urad") / 2.0, 1e-3))
kontrol("f iki katı → FOV daralır", c.get("fov_x_deg") < a.get("fov_x_deg"))


# ---------------------------------------------------------------------------
print("\n[9] TAN tabanlı olmak — küçük açı yaklaşımı KULLANILMIYOR")
# §7C: `fov = N × ifov` kenarda %2'den fazla sapar. Geniş FOV'lu Hydra'da
# iki modelin ayrıştığını ve çözücünün doğru (tan tabanlı) olanı verdiğini
# gösteriyoruz.
r = solver.solve({"ifov_x_urad": 377.35849, "det_w_px": 1024})
tan_tabanli = r.get("fov_x_deg")
kucuk_aci = math.degrees(377.35849e-6 * 1024)
kontrol("iki model gerçekten ayrışıyor", abs(tan_tabanli - kucuk_aci) > 0.2,
        f"tan {tan_tabanli:.4f}° vs küçük açı {kucuk_aci:.4f}° "
        f"(%{abs(tan_tabanli-kucuk_aci)/tan_tabanli*100:.2f})")
kontrol("çözücü tan tabanlı olanı veriyor", yakin(tan_tabanli, 21.87048, 1e-4))


# ---------------------------------------------------------------------------
print("\n[10] SystemConfig köprüsü türetilmiş alanı 'datasheet' saymamalı")
# from_config yalnızca gerçekten verilmiş alanları aktarır. Aksi halde
# çözücü kendi türettiği sensör ölçüsünü girdi sanar ve [5]'teki çelişki
# denetimi anlamsızlaşır.
cfg = system_from_preset("CMV4000 + Rodenstock 70mm")
g = solver.from_config(cfg)
kontrol("sensör ölçüsü girdi olarak aktarılmıyor", "det_w_mm" not in g)
kontrol("türetilen pupil girdi olarak aktarılmıyor", "lens_pupil_mm" not in g,
        "CMV lensinde pupil verilmemiş (0) — f/#'ten türetilmeli")
r = solver.solve_config(cfg)
kontrol("pupil yine de türetiliyor", r.is_derived("lens_pupil_mm"),
        f"{r.get('lens_pupil_mm'):.3f} mm")

# Hydra'da pupil datasheet'ten VERİLMİŞ — türetilene tercih edilmeli.
cfg = system_from_preset("Hydra yıldız izleyici")
r = solver.solve_config(cfg)
kontrol("verilen pupil korunuyor",
        r.values["lens_pupil_mm"].is_given and r.get("lens_pupil_mm") == 34.0,
        "datasheet 34.0 mm; f/# ten türetilse 34.07 çıkardı")

# Pasif panelde angular_res=0 aktarılmamalı (0 = 'pasif panel' konvansiyonu).
g = solver.from_config(system_from_preset("CMV4000 + Rodenstock 70mm"))
kontrol("pasif panelde açısal çözünürlük aktarılmıyor", "scr_ang_deg" not in g)


# ---------------------------------------------------------------------------
print("\n[11] Her kural HANGİ FONKSİYONLA hesapladığını söylemeli")
# Arayüzdeki rozet ipucu bunu gösteriyor; formülü olmayan bir kural, panelde
# "türetildi" deyip nasıl türettiğini söyleyemeyen bir satır demek.
for m in ("rectilinear", "equidistant", "equisolid", "stereographic",
          "orthographic"):
    eksik = [r.name for r in solver.rules_for(m) if not r.formula]
    kontrol(f"{m}: tüm kuralların formülü var", not eksik, str(eksik[:3]))

# Formül metni MODELE göre değişmeli — sabit bir metin olsaydı equidistant
# seçildiğinde hâlâ "atan" yazardı ve kullanıcıyı yanıltırdı.
def _formul(model, node):
    cfg = system_from_preset("Hydra yıldız izleyici")
    cfg.lens.projection = model
    return solver.solve_config(cfg).values[node].formula

f_rect = _formul("rectilinear", "ifov_x_urad")
f_equi = _formul("equidistant", "ifov_x_urad")
kontrol("rektilineer formülü atan içeriyor", "atan" in f_rect, f_rect)
kontrol("equidistant formülü atan İÇERMİYOR", "atan" not in f_equi, f_equi)
kontrol("iki model farklı formül metni veriyor", f_rect != f_equi)
kontrol("equisolid formülü asin içeriyor",
        "asin" in _formul("equisolid", "ifov_x_urad"))

# `describe` kullanıcının iki sorusuna da cevap vermeli.
r = solver.solve_config(system_from_preset("Hydra yıldız izleyici"))
d = r.describe("ifov_x_urad")
kontrol("describe: hangi değerlerden", "Şu değerlerden türetildi" in d)
kontrol("describe: girdiler değerleriyle",
        "Dedektör piksel pitch X = 18 µm" in d)
kontrol("describe: hangi fonksiyonla", "Bağıntı:" in d)
kontrol("describe: sonucu da yazıyor", "377.358" in d)

# Verilen değerde bağıntı yazılmamalı — türetilmedi ki.
d_given = r.describe("lens_f_mm")
kontrol("describe: verilen değerde 'Bağıntı' yok", "Bağıntı:" not in d_given)
kontrol("describe: verilen değer datasheet diyor",
        "Datasheet" in d_given, d_given.replace("\n", " ")[:60])
kontrol("describe: bilinmeyen düğüm dürüst",
        "bilinmiyor" in r.describe("scale_measured_xyz"))

# Çok adımlı türetimde tam zincir eklenir, tek adımlıda tekrar edilmez.
kontrol("describe: çok adımlıda tam zincir var",
        "Tam zincir:" in r.describe("ifov_x_arcsec"))
kontrol("describe: tek adımlıda zincir tekrarlanmıyor",
        "Tam zincir:" not in d)



# ---------------------------------------------------------------------------
print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
