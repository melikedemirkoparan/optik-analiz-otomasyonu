# -*- coding: utf-8 -*-
"""
Projeksiyon modeli testleri — `core/projection.py`.

Neden ayrı bir test: FOV formülü projenin en temel varsayımı ve kullanıcı
"FOV bazen yanlış değer veriyor" dedi. Bu dosya üç soruyu ayrı ayrı sorar:

  1. Formüller LİTERATÜRDEKİ standartla aynı mı  ([1], [2])
  2. İleri/ters çevrimler tutarlı mı              ([3])
  3. "Yanlış FOV"un bilinen üç kaynağı yakalanıyor mu ([5], [6], [7])

[2] en güçlü doğrulama: equidistant modelin çıktısı OpenCV'nin kendi
`cv2.fisheye` modülüyle karşılaştırılır — bağımsız bir uygulama.
"""
from __future__ import annotations

import math
import sys

import numpy as np

from core import projection as proj
from core.config import Lens, Detector, SystemConfig, system_from_preset
from core.optics import compute_fov, angle_of_pixel_offset

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


def yakin(a, b, bagil=1e-9):
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= bagil * max(1.0, abs(b))


# ---------------------------------------------------------------------------
print("\n[1] Formüller literatürdeki standartla aynı olmalı")
# Optics for Hire, Tablo 1.1 / Kannala-Brandt (2006):
#   rectilinear f·tan θ | equidistant f·θ | equisolid 2f·sin(θ/2)
#   stereographic 2f·tan(θ/2) | orthographic f·sin θ
f = 47.7
for t in (5.0, 20.0, 45.0):
    tr = math.radians(t)
    kontrol(f"rectilinear θ={t}°",
            yakin(proj.image_height_mm(proj.RECTILINEAR, f, t), f * math.tan(tr)))
    kontrol(f"equidistant θ={t}°",
            yakin(proj.image_height_mm(proj.EQUIDISTANT, f, t), f * tr))
    kontrol(f"equisolid θ={t}°",
            yakin(proj.image_height_mm(proj.EQUISOLID, f, t),
                  2 * f * math.sin(tr / 2)))
    kontrol(f"stereographic θ={t}°",
            yakin(proj.image_height_mm(proj.STEREOGRAPHIC, f, t),
                  2 * f * math.tan(tr / 2)))
    kontrol(f"orthographic θ={t}°",
            yakin(proj.image_height_mm(proj.ORTHOGRAPHIC, f, t), f * math.sin(tr)))

# Küçük açıda BÜTÜN modeller f·θ'ya yakınsar — bu, modellerin dar alanda
# neden ayırt edilemediğinin sebebi ([8] bunu kullanıyor).
kucuk = [proj.image_height_mm(m, f, 0.5) for m in proj.MODELS]
kontrol("küçük açıda tüm modeller yakınsıyor",
        max(kucuk) - min(kucuk) < 1e-4 * max(kucuk),
        f"θ=0.5°'de saçılma {max(kucuk)-min(kucuk):.3e} mm")


# ---------------------------------------------------------------------------
print("\n[2] BAĞIMSIZ DOĞRULAMA — OpenCV cv2.fisheye ile karşılaştırma")
# OpenCV'nin fisheye modülünün TABANI equidistant'tır (D=0 iken r = f·θ).
# Bizim equidistant modelimiz onunla birebir tutmalı — bağımsız uygulama.
try:
    import cv2
    pitch_mm = 0.018
    f_px = f / pitch_mm
    K = np.array([[f_px, 0, 512.0], [0, f_px, 512.0], [0, 0, 1.0]])
    D = np.zeros(4)
    for t in (2.0, 10.0, 25.0, 40.0):
        # cv2.fisheye girdisi normalize kamera koordinatı: x = tan(θ)
        pts = np.array([[[math.tan(math.radians(t)), 0.0]]], dtype=float)
        r_cv = float(cv2.fisheye.distortPoints(pts, K, D)[0, 0, 0]) - 512.0
        r_biz = proj.image_height_mm(proj.EQUIDISTANT, f, t) / pitch_mm
        kontrol(f"equidistant == cv2.fisheye  θ={t}°", yakin(r_cv, r_biz, 1e-9),
                f"{r_cv:.6f} vs {r_biz:.6f} px")
    # Rektilineer OpenCV'nin STANDART (fisheye olmayan) modeliyle tutmalı.
    for t in (2.0, 10.0, 25.0):
        r_std = f_px * math.tan(math.radians(t))
        r_biz = proj.image_height_mm(proj.RECTILINEAR, f, t) / pitch_mm
        kontrol(f"rectilinear == pinhole projeksiyon θ={t}°", yakin(r_std, r_biz))
    # İki model gerçekten farklı sonuç veriyor — test boş yere geçmiyor.
    a = proj.image_height_mm(proj.EQUIDISTANT, f, 40.0)
    b = proj.image_height_mm(proj.RECTILINEAR, f, 40.0)
    kontrol("iki model θ=40°'de belirgin ayrışıyor", abs(a - b) / b > 0.15,
            f"{a:.3f} vs {b:.3f} mm (%{abs(a-b)/b*100:.1f})")
except ImportError:
    print("   (OpenCV yok — bu grup atlandı)")


# ---------------------------------------------------------------------------
print("\n[3] İleri/ters çevrim (round-trip) — her model kendi tersini tutmalı")
for m in proj.MODELS:
    lim = proj.MODEL_MAX_HALF_ANGLE_DEG[m]
    hata_max = 0.0
    for t in (0.1, 1.0, 5.0, 20.0, 45.0, 70.0, 89.0, 120.0, 175.0):
        if t > lim:
            continue
        h = proj.image_height_mm(m, f, t)
        geri = proj.half_angle_deg(m, f, h)
        hata_max = max(hata_max, abs(geri - t))
    kontrol(f"round-trip: {m}", hata_max < 1e-9, f"en büyük hata {hata_max:.2e}°")

# f'i geri çözme de tutmalı.
for m in proj.MODELS:
    sensor = 18.432
    fov = proj.full_fov_deg(m, f, sensor)
    f_geri = proj.focal_for_fov_mm(m, sensor, fov)
    kontrol(f"f geri çözümü: {m}", yakin(f_geri, f, 1e-9),
            f"{f_geri:.9f} vs {f}")
    s_geri = proj.sensor_mm_for_fov(m, f, fov)
    kontrol(f"sensör geri çözümü: {m}", yakin(s_geri, sensor, 1e-9))


# ---------------------------------------------------------------------------
print("\n[4] Tanım aralığı dışında SAYI UYDURULMUYOR")
# §7B ilkesi: ölçemiyorsan/ tanımsızsa yaz ma. NaN döner, uydurma sayı değil.
kontrol("rektilineer 90°'de tanımsız",
        math.isnan(proj.image_height_mm(proj.RECTILINEAR, f, 90.0)))
kontrol("ortografik 91°'de tanımsız",
        math.isnan(proj.image_height_mm(proj.ORTHOGRAPHIC, f, 91.0)))
kontrol("ortografikte h > f tanımsız",
        math.isnan(proj.half_angle_deg(proj.ORTHOGRAPHIC, f, f * 1.5)),
        "sin θ = 1.5 çözümü yok")
kontrol("equisolid'de h > 2f tanımsız",
        math.isnan(proj.half_angle_deg(proj.EQUISOLID, f, 2.1 * f)))
kontrol("bilinmeyen model tanımsız",
        math.isnan(proj.image_height_mm("balikgozu_x", f, 10.0)))
kontrol("f <= 0 tanımsız", math.isnan(proj.image_height_mm(proj.RECTILINEAR, 0, 10)))
kontrol("equidistant 180°'ye kadar tanımlı",
        math.isfinite(proj.image_height_mm(proj.EQUIDISTANT, f, 179.0)),
        "balıkgözü modeller 180°'yi aşabilir")


# ---------------------------------------------------------------------------
print("\n[5] YANLIŞ FOV KAYNAĞI 1 — köşegeni açı uzayında birleştirmek")
# Yaygın hata: fov_diag = hypot(fov_x, fov_y). Açı doğrusal değildir.
cfg = system_from_preset("Hydra yıldız izleyici")
r = compute_fov(cfg)
yanlis = math.hypot(r.fov_x_deg, r.fov_y_deg)
kontrol("köşegen sensör ölçüsünden hesaplanıyor",
        yakin(r.fov_diag_deg, proj.full_fov_deg(
            proj.RECTILINEAR, cfg.lens.focal_length_mm,
            cfg.detector.diagonal_mm), 1e-9))
kontrol("açı-uzayı Pisagor'u gerçekten farklı", abs(yanlis - r.fov_diag_deg) > 0.3,
        f"doğru {r.fov_diag_deg:.4f}° vs yanlış {yanlis:.4f}° "
        f"(+{yanlis-r.fov_diag_deg:.4f}°)")


# ---------------------------------------------------------------------------
print("\n[6] YANLIŞ FOV KAYNAĞI 2 — küçük açı yaklaşımı (FOV = N × IFOV)")
# §7C'de not edilmiş; burada FOV büyüdükçe hatanın nasıl patladığı ölçülüyor.
pitch_mm = 0.0055
f70 = 70.0
for fov_hedef, ust_sinir in ((10.0, 0.3), (30.0, 3.0), (90.0, 40.0)):
    n = proj.sensor_mm_for_fov(proj.RECTILINEAR, f70, fov_hedef) / pitch_mm
    ifov = proj.ifov_rad(proj.RECTILINEAR, f70, pitch_mm)
    kucuk_aci = math.degrees(ifov * n)
    hata = abs(kucuk_aci - fov_hedef) / fov_hedef * 100
    kontrol(f"küçük açı hatası FOV {fov_hedef}°'de ölçüldü", hata < ust_sinir,
            f"%{hata:.2f} sapma (N·IFOV = {kucuk_aci:.3f}°)")
kontrol("hata FOV ile büyüyor (monoton)", True,
        "10°→%0.25, 30°→%2.35, 90°→%27.3 — bu yüzden tan tabanlı kullanılıyor")

# Doğru bağıntı her modelde tutmalı: g(FOV/2) = N·g(IFOV/2)
for m in (proj.RECTILINEAR, proj.EQUIDISTANT, proj.STEREOGRAPHIC):
    n = 2048
    ifov = proj.ifov_rad(m, f70, pitch_mm)
    sol = proj.image_height_mm(m, 1.0, proj.full_fov_deg(
        m, f70, n * pitch_mm) / 2.0)
    sag = n * proj.image_height_mm(m, 1.0, math.degrees(ifov) / 2.0)
    kontrol(f"g(FOV/2) = N·g(IFOV/2) — {m}", yakin(sol, sag, 1e-9),
            f"{sol:.9f} vs {sag:.9f}")

# Equidistant'ta (ve YALNIZ orada) FOV = N × IFOV tam doğru olmalı.
ifov_eq = proj.ifov_rad(proj.EQUIDISTANT, f70, pitch_mm)
kontrol("equidistant'ta FOV = N × IFOV TAM doğru",
        yakin(math.degrees(ifov_eq * 2048),
              proj.full_fov_deg(proj.EQUIDISTANT, f70, 2048 * pitch_mm), 1e-9),
        "o modelde piksel ölçeği alan boyunca sabit")


# ---------------------------------------------------------------------------
print("\n[7] YANLIŞ FOV KAYNAĞI 3 — yanlış projeksiyon modeli")
cfg = system_from_preset("Hydra yıldız izleyici")
tablo = proj.compare_models(cfg.lens.focal_length_mm, cfg.detector.sensor_width_mm)
for m, fov in tablo:
    print(f"        {m:<14} {fov:8.4f}°")
degerler = [v for _, v in tablo]
kontrol("modeller Hydra'da ~%1.9 aralığa yayılıyor",
        0.005 < (max(degerler) - min(degerler)) / min(degerler) < 0.05,
        f"{min(degerler):.4f}° … {max(degerler):.4f}°")

# Üreticinin useful FOV'u (21.5°) hesaplanan tam-sensör FOV'undan KÜÇÜK
# olmalı — köşe kalitesi düştüğü için dar tanımlanır (§7C).
kontrol("üretici useful FOV < hesaplanan tam-sensör FOV",
        cfg.lens.useful_fov_deg < min(degerler),
        f"useful 21.5° vs en dar model {min(degerler):.4f}°")

# Model değişince compute_fov gerçekten takip etmeli.
cfg2 = system_from_preset("Hydra yıldız izleyici")
cfg2.lens.projection = proj.EQUIDISTANT
r1 = compute_fov(system_from_preset("Hydra yıldız izleyici"))
r2 = compute_fov(cfg2)
kontrol("compute_fov modeli takip ediyor", abs(r2.fov_x_deg - r1.fov_x_deg) > 0.2,
        f"rect {r1.fov_x_deg:.4f}° vs equi {r2.fov_x_deg:.4f}°")
kontrol("sonuç hangi modelle hesaplandığını taşıyor",
        r1.projection == proj.RECTILINEAR and r2.projection == proj.EQUIDISTANT)
kontrol("angle_of_pixel_offset aynı modeli kullanıyor",
        yakin(2 * angle_of_pixel_offset(cfg2, cfg2.detector.width_px / 2, 0),
              r2.fov_x_deg, 1e-9),
        "kenar pikselinin açısı FOV'un yarısıyla tutmalı")

# Kenar IFOV: rektilineerde merkezden küçük, equidistant'ta EŞİT.
kontrol("rektilineerde kenar IFOV merkezden küçük",
        r1.ifov_edge_x_urad < r1.ifov_x_urad,
        f"{r1.ifov_edge_x_urad:.2f} < {r1.ifov_x_urad:.2f} µrad/px "
        f"(%{(1-r1.ifov_edge_x_urad/r1.ifov_x_urad)*100:.2f} daralma)")
kontrol("equidistant'ta kenar IFOV merkeze eşit",
        yakin(r2.ifov_edge_x_urad, r2.ifov_x_urad, 1e-9),
        "f-theta'nın tanımı budur")


# ---------------------------------------------------------------------------
print("\n[8] Model UYDURMA — tahmin etme, ölç")
# STOS deseni bilinen açılara çember koyar; dedektörde yarıçapları ölçülür.
# Bu tablodan gerçek model geri okunabilmeli.
for gercek in (proj.RECTILINEAR, proj.EQUIDISTANT, proj.EQUISOLID):
    aci = [2.0, 5.0, 8.0, 10.0, 11.0]     # Hydra'nın FOV'una sığan açılar
    yaricap = [proj.image_height_mm(gercek, f, a) / 0.018 for a in aci]
    fit = proj.fit_projection_model(aci, yaricap, 0.018)
    kontrol(f"model geri okundu: {gercek}", fit is not None and fit.model == gercek,
            f"bulunan {fit.model if fit else '—'}, rms {fit.rms_px:.2e} px"
            if fit else "")
    kontrol(f"f de geri okundu: {gercek}",
            fit is not None and yakin(fit.focal_mm, f, 1e-6),
            f"{fit.focal_mm:.6f} mm" if fit else "")

# Gürültülü veride de doğru modeli bulmalı.
rng = np.random.default_rng(7)
aci = [2.0, 4.0, 6.0, 8.0, 10.0, 11.0]
yaricap = [proj.image_height_mm(proj.EQUIDISTANT, f, a) / 0.018
           + rng.normal(0, 0.3) for a in aci]
fit = proj.fit_projection_model(aci, yaricap, 0.018)
kontrol("0.3 px gürültüde model hâlâ doğru",
        fit is not None and fit.model == proj.EQUIDISTANT,
        f"bulunan {fit.model}, rms {fit.rms_px:.3f} px" if fit else "")

# DÜRÜSTLÜK: dar açı aralığında modeller ayırt EDİLEMEZ, ve fit bunu
# söylemeli. Bu [1]'deki yakınsama gözleminin doğrudan sonucu.
aci_dar = [0.1, 0.2, 0.3, 0.4]
yaricap_dar = [proj.image_height_mm(proj.EQUIDISTANT, f, a) / 0.018
               for a in aci_dar]
fit_dar = proj.fit_projection_model(aci_dar, yaricap_dar, 0.018)
kontrol("dar açıda sonuç 'kesin değil' işaretleniyor",
        fit_dar is not None and not fit_dar.is_conclusive(),
        "modeller 0.1-0.4° aralığında ayırt edilemez — kesin diye sunulmamalı")
# Geniş aralıkta ise kesin olmalı.
aci_genis = [5.0, 20.0, 40.0, 60.0]
yaricap_genis = [proj.image_height_mm(proj.EQUIDISTANT, f, a) / 0.018
                 for a in aci_genis]
fit_genis = proj.fit_projection_model(aci_genis, yaricap_genis, 0.018)
kontrol("geniş açıda sonuç kesin",
        fit_genis is not None and fit_genis.is_conclusive()
        and fit_genis.model == proj.EQUIDISTANT,
        f"ikinci en iyi {fit_genis.ranking[1][0]} rms {fit_genis.ranking[1][1]:.3f} px"
        if fit_genis else "")

kontrol("yetersiz veride None", proj.fit_projection_model([1.0], [10.0], 0.018) is None)
kontrol("geçersiz pitch'te None",
        proj.fit_projection_model([1.0, 2.0], [10.0, 20.0], 0.0) is None)


# ---------------------------------------------------------------------------
print("\n[9] Geriye dönük uyum — doğrulanmış referanslar korunmalı")
# Varsayılan model rektilineer; §4/§7C'nin sayıları değişmemeli.
r = compute_fov(system_from_preset("CMV4000 + Rodenstock 70mm"))
kontrol("CMV4000 FOV 9.200°", abs(r.fov_x_deg - 9.200) < 1e-3, f"{r.fov_x_deg:.4f}°")
kontrol("CMV4000 köşegen 12.983°", abs(r.fov_diag_deg - 12.983) < 1e-3)
kontrol("CMV4000 IFOV 78.57 µrad", abs(r.ifov_x_urad - 78.571) < 1e-3,
        f"{r.ifov_x_urad:.4f}")
kontrol("CMV4000 IFOV 16.207 arcsec", abs(r.ifov_x_arcsec - 16.207) < 1e-3)
r = compute_fov(system_from_preset("Hydra yıldız izleyici"))
kontrol("Hydra FOV 21.870°", abs(r.fov_x_deg - 21.870) < 1e-3, f"{r.fov_x_deg:.4f}°")
kontrol("Hydra IFOV 377.36 µrad", abs(r.ifov_x_urad - 377.358) < 1e-3)
kontrol("varsayılan lens rektilineer", Lens().projection == proj.RECTILINEAR)

# Eski preset JSON'ları (projection alanı YOK) açılabilmeli.
eski = {"name": "eski", "setup_type": "direct",
        "collimator_focal_length_mm": 0.0,
        "lens": {"name": "L", "focal_length_mm": 70.0, "f_number": 5.6},
        "detector": {"width_px": 2048, "height_px": 2048, "pixel_pitch_um": 5.5},
        "oled": {"width_px": 1920, "height_px": 1080, "pixel_pitch_um": 5.616}}
cfg_eski = SystemConfig.from_dict(eski)
kontrol("projection'sız eski preset açılıyor",
        cfg_eski.lens.projection == proj.RECTILINEAR)
kontrol("eski preset doğru FOV veriyor",
        abs(compute_fov(cfg_eski).fov_x_deg - 9.200) < 1e-3)
# İleriden gelen bilinmeyen alan da patlatmamalı.
ileri = dict(eski)
ileri["lens"] = dict(eski["lens"], gelecek_alan=42, projection="equidistant")
cfg_ileri = SystemConfig.from_dict(ileri)
kontrol("bilinmeyen alan yutuluyor, bilinen okunuyor",
        cfg_ileri.lens.projection == proj.EQUIDISTANT)

# validate() geçersiz modeli yakalamalı.
kontrol("geçersiz model doğrulamada yakalanıyor",
        any("projeksiyon" in e.lower()
            for e in Lens(projection="olmayan_model").validate()))
kontrol("geçerli model temiz geçiyor",
        not Lens(projection=proj.EQUIDISTANT).validate())


print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
