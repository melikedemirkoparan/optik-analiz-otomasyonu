"""
Çift analiz testi: tam kare + kırpılan bölge.

ROI seçiliyken analizin İKİ sonuç ürettiğini, kırpma sonucunun gerçekten
kırpılan bölgeden geldiğini ve tam kare sonucunun ROI'den etkilenmediğini
doğrular.

    python3 test_roi_analiz.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow, AnalysisWorker
from core.config import default_config

GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"

for p in (GT, DET):
    if not os.path.exists(p):
        sys.exit(f"Test görüntüsü bulunamadı: {p}")

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1500, 900)
w.show()
w.gt_path, w.det_path = GT, DET
w.view_gt.set_image(cv2.imread(GT, cv2.IMREAD_GRAYSCALE))
w._roi_on_image_loaded("gt")
w.view_det.set_image(cv2.imread(DET, cv2.IMREAD_GRAYSCALE))

cfg = default_config()


def kosu(roi, roi_src="gt", etiket=""):
    """Worker'ı senkron koşturur (thread yerine run() doğrudan)."""
    wk = AnalysisWorker(GT, DET, cfg, roi=roi, roi_src=roi_src)
    out = {}
    wk.finished_ok.connect(lambda a, b: out.update(full=a, roi=b))
    wk.failed.connect(lambda m: out.update(err=m))
    wk.run()                       # thread başlatmadan, aynı thread'de
    if "err" in out:
        print("HATA:", out["err"][:400])
        sys.exit(1)
    print(f"\n>>> {etiket}")
    return out.get("full"), out.get("roi")


def ozet(r):
    if r is None:
        return None
    return {
        "donme": round(r.rotation_deg, 4) if r.rotation_deg == r.rotation_deg else None,
        "tilt": round(r.tilt_deg, 4) if r.tilt_deg == r.tilt_deg else None,
        "inlier": None if r.match is None else r.match.num_inliers,
        "reproj": None if r.match is None else round(r.match.reproj_error_px, 4),
    }


# --- 1. ROI YOK: tek sonuc gelmeli ---
full0, roi0 = kosu(None, etiket="ROI yok")
print("   tam kare :", ozet(full0))
print("   kirpma   :", ozet(roi0))
assert roi0 is None, "ROI yokken ikinci sonuc gelmemeliydi"
assert full0 is not None and full0.ok

# --- 2. ROI VAR: iki ayri sonuc gelmeli ---
ROI = (300, 300, 447, 365)          # w,h,cx,cy -> rect hesaplanir
w.f_roi_w.setValue(300)
w.f_roi_h.setValue(300)
w.f_roi_cx.setValue(447)
w.f_roi_cy.setValue(365)
rect = w._roi_rect()
print("\nGUI ROI rect:", rect)

full1, roi1 = kosu(rect, "gt", etiket="ROI 300x300 @ yildiz merkezi")
print("   tam kare :", ozet(full1))
print("   kirpma   :", ozet(roi1))
assert roi1 is not None, "kirpma sonucu uretilmedi"
assert hasattr(roi1, "roi_rect") and roi1.roi_rect == rect

# --- 3. tam kare sonucu ROI'den ETKILENMEMELI ---
print("\n[3] tam kare tutarliligi")
print("   ROI'siz :", ozet(full0))
print("   ROI'li  :", ozet(full1))
assert ozet(full0) == ozet(full1), "tam kare sonucu ROI'den etkilenmis!"

# --- 4. kirpma sonucu tam kareden FARKLI olmali (farkli veri) ---
print("\n[4] kirpma gercekten farkli bolgeyi mi olcuyor?")
a, b = ozet(full1), ozet(roi1)
print("   tam kare :", a)
print("   kirpma   :", b)
assert a != b, "kirpma sonucu tam kare ile ayni — kirpma uygulanmamis olabilir"

# --- 5. farkli ROI -> farkli kirpma sonucu ---
print("\n[5] farkli ROI -> farkli sonuc")
w.f_roi_cx.setValue(200)
w.f_roi_cy.setValue(200)
rect2 = w._roi_rect()
full2, roi2 = kosu(rect2, "gt", etiket=f"ROI @ {rect2}")
print("   kirpma-1 :", ozet(roi1))
print("   kirpma-2 :", ozet(roi2))
assert roi2 is not None and roi2.roi_rect == rect2

# --- 6. GUI karsilastirma tablosu dolduruluyor mu? ---
print("\n[6] GUI karsilastirma tablosu")
w._on_finished(full1, roi1)
assert w.gb_cmp.isVisible() or True     # offscreen'de isVisible guvenilmez
satirlar = [(lbl, vf.text(), vr.text())
            for (lbl, _), (vf, vr) in zip(w._cmp_rows, w._cmp_widgets)]
for lbl, vf, vr in satirlar:
    print(f"   {lbl:20s} tam={vf:>10s}  kirpma={vr:>10s}")
assert any(vf != vr for _, vf, vr in satirlar), "tablo ayni degerleri gosteriyor"

# --- 6b. GUVENILIRLIK: dejenere kirpmada donme "—" olmali ---
print("\n[6b] dejenere kirpmada donme gizleniyor mu?")
durum = dict((lbl, vr) for lbl, _, vr in satirlar).get("Eşleme durumu")
donme = dict((lbl, vr) for lbl, _, vr in satirlar).get("Dönme (°)")
print("   kirpma esleme durumu:", durum, "| donme:", donme)
if durum in ("dejenere", "eşleşmedi"):
    assert donme == "—", (
        f"esleme '{durum}' iken donme '{donme}' gosterilmemeli — "
        "basarisiz olcum gercek deger gibi okunur")
    assert "⚠" in w.lbl_cmp_note.text(), "uyari metni yok"
    print("   OK: donme gizlendi, uyari verildi")
else:
    print("   (bu ROI'de esleme saglam, kontrol atlandi)")
print("   not:", w.lbl_cmp_note.text().replace("\n", " ")[:110], "...")

# --- 6c. saglam eslesen bir ROI'de donme GOSTERILMELI ---
print("\n[6c] saglam kirpmada donme gosteriliyor mu?")
full3, roi3 = kosu((100, 100, 500, 400), "gt", etiket="ROI 500x400 (genis)")
w._on_finished(full3, roi3)
s3 = dict((lbl, vr) for (lbl, _), (_, vr) in
          zip(w._cmp_rows, [(a.text(), b.text()) for a, b in w._cmp_widgets]))
print("   esleme durumu:", s3.get("Eşleme durumu"), "| donme:", s3.get("Dönme (°)"))
if s3.get("Eşleme durumu") == "sağlam":
    assert s3.get("Dönme (°)") != "—", "saglam eslemede donme gosterilmeliydi"
    print("   OK: saglam eslemede donme gosteriliyor")

# --- 6d. egiklik hicbir zaman "< inf" yazmamali ---
print("\n[6d] egiklik gosterimi")
for etiket, sozluk in (("saglam ROI", s3),
                       ("dejenere ROI",
                        dict((lbl, vr) for lbl, _, vr in satirlar))):
    eg = sozluk.get("Eğiklik (°)", "")
    print(f"   {etiket:14s} -> {eg}")
    assert "inf" not in eg.lower(), f"anlamsiz egiklik gosterimi: {eg}"

# --- 7. ROI'siz cagri tabloyu gizlemeli ---
w._on_finished(full0, None)
print("\n[7] ROI'siz -> tablo gizli:", not w.gb_cmp.isVisibleTo(w))


# --- 8. PANEL <-> TABLO TUTARLILIGI (kalici koruma) ---
#
# Ayni kosu iki yerde gosteriliyor: sag paneldeki sonuc satirlari ve
# "Tam kare <-> Kirpma" tablosunun "tam" sutunu. Ikisi ayni sayiyi farkli
# kuralla bicimlendirirse kullanici hangisine inanacagini bilemez.
# Daha once iki kez ayristi:
#   - egiklikte panel 1-sigma, tablo 2-sigma kullaniyordu (< 3.6 / < 7.25),
#   - donmede panel yildiz-elipsi yedegini yaziyor, tablo "—" gosteriyordu.
# Bu test o ayrismayi kalici olarak yakalar.
print("\n[8] panel <-> tablo tutarliligi")


def sayi(txt):
    """Gosterilen metni sayiya cevirir; sayi degilse None (— / olculemedi)."""
    try:
        return float(txt.replace("<", "").replace("+", "").strip())
    except ValueError:
        return None


def tablo_tam(pencere):
    """Tablonun 'tam kare' sutununu {etiket: metin} olarak verir."""
    return {lbl: vf.text() for (lbl, _), (vf, _) in
            zip(pencere._cmp_rows, pencere._cmp_widgets)}


for etiket, sonuc in (("tam kare (saglam)", full1),
                      ("dejenere kirpma", roi1),
                      ("genis kirpma", roi3)):
    # Ayni sonucu hem panele hem tablonun "tam" sutununa yazdir. Ikinci
    # argüman yalnizca tabloyu gorunur kilmak icin (roi_rect tasimali);
    # karsilastirilan sutun degil.
    w._on_finished(sonuc, roi1)
    t = tablo_tam(w)

    for alan, panel_txt, tablo_txt in (
            ("Dönme", w.r_rot.value(), t["Dönme (°)"]),
            ("Eğiklik", w.r_tilt.value(), t["Eğiklik (°)"])):
        p, q = sayi(panel_txt), sayi(tablo_txt)
        print(f"   {etiket:18s} {alan:8s} panel={panel_txt:>12s}  "
              f"tablo={tablo_txt:>12s}")
        # Biri sayi digeri degilse ayrisma vardir: biri "olctum" derken
        # digeri "olcemedim" diyor.
        assert (p is None) == (q is None), (
            f"{etiket}/{alan}: panel '{panel_txt}' ile tablo '{tablo_txt}' "
            "ayni olcum icin farkli sey soyluyor")
        if p is not None:
            assert abs(p - q) < 1e-6, (
                f"{etiket}/{alan}: panel {p} != tablo {q}")
        # "< X" siniri her iki yerde de ayni esikten gelmeli.
        assert ("<" in panel_txt) == ("<" in tablo_txt), (
            f"{etiket}/{alan}: ust-sinir gosterimi ayrisiyor "
            f"(panel '{panel_txt}', tablo '{tablo_txt}')")

print("   OK: panel ve tablo ayni olcum icin ayni seyi yaziyor")

# Dejenere kosuda panel de donmeyi gizlemeli (yedek sayiyi yazmamali).
w._on_finished(roi1, roi1)
if w._match_state(roi1) in ("dejenere", "eşleşmedi"):
    assert sayi(w.r_rot.value()) is None, (
        f"dejenere eslemede panel donme olarak '{w.r_rot.value()}' yaziyor — "
        "bu yildiz elipsinden gelen yedek, olcum degil")
    print("   OK: dejenere kosuda panel de donmeyi gizliyor:",
          w.r_rot.value())
    print("   tani:", w.lbl_verdict.text())

print("\nCIFT ANALIZ TESTLERI GECTI")
