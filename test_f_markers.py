# -*- coding: utf-8 -*-
"""
F işareti testleri — roll'ün mod-90 belirsizliği ve ayna kararı.

SORUN. Eş merkezli halka deseni 90° dönmelerde kendini tekrar eder. Bu
yüzden homografiden okunan roll ancak `mod 90°` kadar kesindi — panel
"43.644° (mod 90°)" yazıyordu, yani 43.6 / 133.6 / 223.6 / 313.6
arasından hangisi olduğu bilinmiyordu. Aynı simetri ayna kararını da
belirsiz bırakıyordu; SIFT 9 inlier bulup eşiğin (10) altında kalınca
"ölçülemedi" demek zorundaydı.

ÇÖZÜM. Desendeki dört köşe F harfi asimetriktir — ne dönme ne ayna
simetrisi vardır. Her F için İKİ açı ölçülür:

    şekil açısı : şablonu o F'ye oturtmak için gereken dönme
    azimut      : F'nin merkeze göre bulunduğu yön

Görüntü yalnızca döndürülmüşse ikisi AYNI miktarda değişir. Aynalanmışsa
biri artarken diğeri azalır — ayrım buradan çıkar.

NEDEN ŞABLON NCC'Sİ TEK BAŞINA YETMEZ. Şablon her açıda denendiği için
aynalanmış bir F de bir dönmede yüksek NCC verir. Gerçek ölçümde NCC
"düz" diyordu (0.98'e karşı 0.66) ama tutarlılık testi aynayı gösterdi
(12.0°'ye karşı 54.9° tutarsızlık). Karar tutarlılığa dayanmalı.
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np

from core import f_markers as fm

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


# ---------------------------------------------------------------------------
# Sentetik desen üreteci — gerçek dosyaya bağlı kalmadan test edebilmek için.
# ---------------------------------------------------------------------------

def _ciz_f(img, x, y, aci_deg, olcek=1.0, renk=255):
    """Basit bir F harfi çizer (asimetrik: dikey gövde + iki yatay kol)."""
    h = 30.0 * olcek
    w = 18.0 * olcek
    # F'nin yerel koordinatları: gövde solda, kollar sağa
    noktalar = [((-w / 2, -h / 2), (-w / 2, h / 2)),      # gövde
                ((-w / 2, -h / 2), (w / 2, -h / 2)),      # üst kol
                ((-w / 2, 0.0), (w / 4, 0.0))]            # orta kol (kısa)
    th = math.radians(aci_deg)
    c, s = math.cos(th), math.sin(th)
    for (x0, y0), (x1, y1) in noktalar:
        p0 = (int(x + x0 * c - y0 * s), int(y + x0 * s + y0 * c))
        p1 = (int(x + x1 * c - y1 * s), int(y + x1 * s + y1 * c))
        cv2.line(img, p0, p1, renk, max(1, int(2 * olcek)))


def desen_uret(boyut=800, roll_deg=0.0, ayna=False, olcek=1.0,
               merkez=None, n_halka=8):
    """
    Bilinen roll ve ayna ile sentetik halka+F deseni üretir.

    Testin can alıcı noktası: roll ve ayna ÖNCEDEN BİLİNİR, ölçüm onları
    geri bulmalıdır.
    """
    img = np.zeros((boyut, boyut), dtype=np.uint8)
    cx, cy = merkez if merkez else (boyut / 2.0, boyut / 2.0)
    adim = 22.0 * olcek
    for n in range(1, n_halka + 1):
        cv2.circle(img, (int(cx), int(cy)), int(adim * n), 200, 1)
    # Merkez cross
    R = int(9 * olcek)
    cv2.line(img, (int(cx - R), int(cy)), (int(cx + R), int(cy)), 255, 1)
    cv2.line(img, (int(cx), int(cy - R)), (int(cx), int(cy + R)), 255, 1)
    # Dört F, 45/135/225/315 azimutlarında; her biri kendi açısında.
    #
    # F'ler en dış halkanın DIŞINA konur. Gerçek desende de öyledir ve
    # bu şart: F halkaya değerse bağlantılı bileşen analizi ikisini tek
    # parça sayar ve F hiç bulunamaz (bu testi yazarken yaşandı).
    r_f = adim * n_halka + 26.0 * olcek
    # F'lerin KENDİ açıları — DESEN ÜRETECİNDEN alınır, göz kararı değil:
    # generate_circle_pattern_passive.corner_positions() içinde
    #
    #     rots = [0.0, 90.0, 45.0, 270.0]     azimutlar 45/135/225/315
    #
    # Üçüncüsü (azimut 225) bilerek 45° eğiktir; üreticinin kendi yorumu:
    # "Son F 45 derece verilerek simetri kırılır -- hiçbir dönme/aynalama
    # kombinasyonu paterni kendine götürmez." Roll'ü 0..360° tekleştiren
    # bilgi TAM OLARAK burada saklıdır.
    #
    # Eski sürüm burada (0, 270, 316, 90) yazıyordu. O dizilimde iki F'nin
    # imzası (azimut − şekil açısı) 225'te çakışıyor, yani test gerçekte
    # OLMAYAN bir belirsizliği ölçüyordu ve çözücüyü haksız yere
    # suçluyordu. Sentetik desen gerçeğe uymazsa test bir şey kanıtlamaz.
    F_ACILARI = (0.0, 90.0, 45.0, 270.0)
    for k in range(4):
        az = 45.0 + 90.0 * k + roll_deg
        th = math.radians(az)
        fx = cx + r_f * math.cos(th)
        fy = cy + r_f * math.sin(th)
        _ciz_f(img, fx, fy, F_ACILARI[k] + roll_deg, olcek)
    if ayna:
        img = cv2.flip(img, 1)
    return img


# ---------------------------------------------------------------------------
print("\n[1] F işaretleri bulunuyor mu")
gt = desen_uret(800, roll_deg=0.0, ayna=False)
m = fm.find_f_markers(gt)
kontrol("GT'de F bulundu", m.ok, f"{len(m.points)} işaret")
kontrol("tam dört F", len(m.points) == 4, str(len(m.points)))
if len(m.azimuths) == 4:
    az = sorted(m.azimuths)
    farklar = [round(az[i + 1] - az[i]) for i in range(3)]
    kontrol("F'ler ~90° aralıklı", all(80 <= f <= 100 for f in farklar),
            str(farklar))

# Halkalar F sanılmamalı: halka bileşenleri çok geniş çevre kutusuna yayılır.
kontrol("halkalar F olarak sayılmadı", len(m.points) == 4,
        "halka sızsaydı 4'ten fazla olurdu")


# ---------------------------------------------------------------------------
print("\n[2] Roll geri bulunuyor mu — mod 90 belirsizliği YOK")
# Halka deseni 90°'de kendini tekrar eder; F olmadan 43.6 ile 133.6
# ayırt edilemez. Bu testin amacı ikisini ayırabildiğimizi göstermek.
for beklenen in (0.0, 30.0, 134.0, 225.0, 310.0):
    det = desen_uret(800, roll_deg=beklenen, ayna=False)
    r = fm.solve_roll_and_mirror(gt, det)
    if not r.ok:
        kontrol(f"roll {beklenen:.0f}° çözüldü", False,
                "; ".join(r.messages)[:70])
        continue
    hata = abs(((r.roll_deg - beklenen + 180.0) % 360.0) - 180.0)
    kontrol(f"roll {beklenen:.0f}° geri bulundu", hata < 6.0,
            f"ölçülen {r.roll_deg:.1f}°, hata {hata:.1f}°")

# 90'ın katları ayırt edilebilmeli — asıl kazanım bu.
d44 = fm.solve_roll_and_mirror(gt, desen_uret(800, roll_deg=44.0))
d134 = fm.solve_roll_and_mirror(gt, desen_uret(800, roll_deg=134.0))
if d44.ok and d134.ok:
    ayrim = abs(((d134.roll_deg - d44.roll_deg + 180.0) % 360.0) - 180.0)
    kontrol("44° ile 134° AYIRT EDİLİYOR", ayrim > 60.0,
            f"{d44.roll_deg:.1f}° vs {d134.roll_deg:.1f}° (fark {ayrim:.1f}°)")


# ---------------------------------------------------------------------------
print("\n[3] Ayna kararı")
duz = fm.solve_roll_and_mirror(gt, desen_uret(800, roll_deg=20.0, ayna=False))
ayn = fm.solve_roll_and_mirror(gt, desen_uret(800, roll_deg=20.0, ayna=True))
kontrol("aynasız desende ayna=False", duz.ok and not duz.mirrored,
        f"ok={duz.ok} ayna={duz.mirrored}")
kontrol("aynalı desende ayna=True", ayn.ok and ayn.mirrored,
        f"ok={ayn.ok} ayna={ayn.mirrored}")
kontrol("her iki kararda da mirror_known",
        duz.mirror_known and ayn.mirror_known)


# ---------------------------------------------------------------------------
print("\n[4] Ölçek farkı sonucu bozmamalı")
# Dedektörde desen büyür; roll ve ayna bundan etkilenmemeli.
for olc in (1.4, 1.8):
    det = desen_uret(1400, roll_deg=70.0, ayna=False, olcek=olc)
    r = fm.solve_roll_and_mirror(gt, det)
    if r.ok:
        hata = abs(((r.roll_deg - 70.0 + 180.0) % 360.0) - 180.0)
        kontrol(f"ölçek {olc}× ile roll doğru", hata < 8.0,
                f"{r.roll_deg:.1f}° (hata {hata:.1f}°)")
    else:
        kontrol(f"ölçek {olc}× ile çözüldü", False,
                "; ".join(r.messages)[:70])


# ---------------------------------------------------------------------------
print("\n[5] Merkez kaçıksa (decenter) yine çalışmalı")
# Bu projede tekrar eden ders: yanlış merkez sahte sonuç üretir. F ölçümü
# ağırlık merkezini kullanır; desen kaçıksa bile azimutlar tutarlı kalır
# çünkü hepsi AYNI merkeze göre ölçülür.
det = desen_uret(800, roll_deg=100.0, merkez=(430.0, 360.0))
r = fm.solve_roll_and_mirror(gt, det)
if r.ok:
    hata = abs(((r.roll_deg - 100.0 + 180.0) % 360.0) - 180.0)
    kontrol("kaçık merkezde roll doğru", hata < 8.0,
            f"{r.roll_deg:.1f}° (hata {hata:.1f}°)")
else:
    kontrol("kaçık merkezde çözüldü", False, "; ".join(r.messages)[:70])


# ---------------------------------------------------------------------------
print("\n[6] F yoksa dürüstçe 'çözemedim' demeli")
# Sadece halkalar — F yok. Uydurma bir roll DÖNDÜRMEMELİ.
bos = np.zeros((800, 800), np.uint8)
for n in range(1, 9):
    cv2.circle(bos, (400, 400), 22 * n, 200, 1)
r = fm.solve_roll_and_mirror(gt, bos)
kontrol("F'siz görüntüde ok=False", not r.ok)
kontrol("F'siz görüntüde mirror_known=False", not r.mirror_known)
kontrol("neden söyleniyor", len(r.messages) > 0,
        r.messages[0][:60] if r.messages else "mesaj yok")

# Boş görüntü de patlamamalı.
try:
    r = fm.solve_roll_and_mirror(gt, np.zeros((50, 50), np.uint8))
    kontrol("boş görüntüde patlamıyor", not r.ok)
except Exception as e:                                      # noqa: BLE001
    kontrol("boş görüntüde patlamıyor", False, str(e)[:60])


# ---------------------------------------------------------------------------
print("\n[7] Gerçek ölçüm (varsa) — Hydra + OLED")
GT_YOL = "/home/test123/Downloads/patterns1/v6_1deg_inverted (Copy).png"
DET_YOL = ("/home/test123/Downloads/FOV_pattern-captured/"
           "capture_OH2_2026-08-17-14-23-22_T_50_FOVPattern-fullFrame_processed.png")
if os.path.exists(GT_YOL) and os.path.exists(DET_YOL):
    from core.image_analysis import load_image_gray
    g = load_image_gray(GT_YOL)
    d = load_image_gray(DET_YOL)
    r = fm.solve_roll_and_mirror(g, d)
    kontrol("gerçek çiftte çözüldü", r.ok, "; ".join(r.messages)[:70])
    if r.ok:
        # Homografi mod 90 içinde 43.6 diyordu; F'nin çözdüğü 134.7 onunla
        # tutarlı olmalı (134.7 - 90 = 44.7).
        kontrol("roll homografiyle mod 90 tutarlı",
                abs((r.roll_deg % 90.0) - 44.7) < 6.0,
                f"{r.roll_deg:.2f}° -> mod 90 = {r.roll_deg % 90.0:.2f}°")
        # AYNA BEKLENTİSİ DÜZELTİLDİ. Eski sürüm burada "ayna EVET"
        # bekliyordu; o beklenti, F'yi yanlış eşleyen eski çözücünün
        # çıktısına göre yazılmıştı. Kimlik eşlemesi kurulunca ölçüm
        # tersini, hem de tartışmasız biçimde söylüyor:
        #
        #     düz  hipotez : tutarsızlık 0.74°,  NCC 0.977
        #     ayna hipotezi: tutarsızlık 45.36°, NCC 0.659
        #
        # Ayrım 61 kat. Bağımsız bir kontrol de aynı yönü gösteriyor:
        # F merkezlerinin işaretli alanı (chirality) GT'de +430775,
        # dedektörde +681105 — ikisi de pozitif, yani dizilim yönü aynı,
        # ayna yok. Bu ölçüt şablon eşleşmesinden tamamen bağımsızdır.
        kontrol("ayna kararı verildi ve gerekçesi tutarlı",
                (not r.mirrored) and r.mirror_known,
                f"ayna={r.mirrored}, known={r.mirror_known}")
        kontrol("F dağılımı dar", r.rms_px < 5.0, f"±{r.rms_px:.2f}°")
else:
    print("   (gerçek dosyalar yok — atlandı)")


print("\n" + "=" * 72)
print(f"SONUÇ: {GECTI} geçti, {KALDI} kaldı")
print("=" * 72)
sys.exit(1 if KALDI else 0)
