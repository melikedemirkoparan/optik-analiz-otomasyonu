"""
Kırpma (ROI) kontrolünün testi.

Arayüzü offscreen açıp ROI'yi program üzerinden sürer: varsayılan kapalı
durum, ölçü girişi, tıklamayla konumlandırma, kenar clamp'i, kaynak değişimi
ve görüntü yokken çökmeme davranışı doğrulanır.

    python3 test_roi.py
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow

GT = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg"
DET = "/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg"

for p in (GT, DET):
    if not os.path.exists(p):
        sys.exit(f"Test görüntüsü bulunamadı: {p}\n"
                 "Bu test örnek görüntü çiftini gerektirir.")

app = QApplication(sys.argv)
w = MainWindow()
w.resize(1500, 900)
w.show()

w.gt_path = GT
w.view_gt.set_image(cv2.imread(GT, cv2.IMREAD_GRAYSCALE))
w._roi_on_image_loaded("gt")
w.det_path = DET
w.view_det.set_image(cv2.imread(DET, cv2.IMREAD_GRAYSCALE))
w._roi_on_image_loaded("det")

print("GT boyut :", w.view_gt.image_size())
print("DET boyut:", w.view_det.image_size())

# --- 1. varsayilan: kirpma KAPALI, olcu bos ---
print("\n[1] varsayilan (bos)")
print("  G/Y  :", w.f_roi_w.value(), w.f_roi_h.value())
print("  rect :", w._roi_rect())
print("  info :", w.lbl_roi_info.text())
assert w.f_roi_w.value() == 0 and w.f_roi_h.value() == 0, "varsayilan bos degil"
assert w._roi_rect() is None, "olcu girilmeden ROI olusmamali"
assert w.view_gt._roi is None, "olcu yokken dikdortgen cizilmemeli"

# --- 2. sadece olcu girildi, henuz tiklanmadi ---
print("\n[2] olcu girildi, tiklanmadi")
w.f_roi_w.setValue(200)
w.f_roi_h.setValue(200)
print("  rect :", w._roi_rect())
print("  info :", w.lbl_roi_info.text())
assert w._roi_rect() is None, "merkez secilmeden ROI olusmamali"

# --- 3. ELLE konum girisi -> kirpma olusur ---
print("\n[3] elle konum girisi (hassas)")
w.f_roi_cx.setValue(447)
w.f_roi_cy.setValue(365)
print("  rect :", w._roi_rect())
print("  info :", w.lbl_roi_info.text())
assert w._roi_rect() == (347, 265, 200, 200), w._roi_rect()
crop = w._roi_crop("gt", w._roi_rect())
print("  crop shape:", crop.shape)
assert crop.shape == (200, 200), crop.shape

# --- 3b. tek piksel kaydirma elle giriste calisiyor mu? ---
print("\n[3b] 1 px hassasiyet")
w.f_roi_cx.setValue(448)
print("  rect :", w._roi_rect())
assert w._roi_rect() == (348, 265, 200, 200), w._roi_rect()
w.f_roi_cx.setValue(447)

# --- 3c. tiklama alanlari da doldurmali (cift yonlu bag) ---
print("\n[3c] tiklama -> alanlar guncellenir")
w._roi_click("gt", 500, 400)
print("  alanlar:", w.f_roi_cx.value(), w.f_roi_cy.value())
assert (w.f_roi_cx.value(), w.f_roi_cy.value()) == (500, 400)
assert w._roi_rect() == (400, 300, 200, 200), w._roi_rect()

# --- 4. serbest en-boy ---
print("\n[4] serbest en-boy 400x150")
w.f_roi_w.setValue(400)
w.f_roi_h.setValue(150)
crop = w._roi_crop("gt", w._roi_rect())
print("  rect :", w._roi_rect(), "crop:", crop.shape)
assert crop.shape == (150, 400), crop.shape

# --- 5. kose clamp (elle giris ile) ---
print("\n[5] kose clamp")
w.f_roi_cx.setValue(0)
w.f_roi_cy.setValue(0)
assert w._roi_rect()[:2] == (0, 0), w._roi_rect()
iw, ih = w.view_gt.image_size()
w.f_roi_cx.setValue(w.f_roi_cx.maximum())
w.f_roi_cy.setValue(w.f_roi_cy.maximum())
x, y, cw, ch = w._roi_rect()
print("  konum ust siniri:", w.f_roi_cx.maximum(), w.f_roi_cy.maximum())
print("  rect :", (x, y, cw, ch))
assert x + cw == iw and y + ch == ih, "sag-alt clamp hatali"

# --- 6. Ortala ---
print("\n[6] ortala")
w._roi_center()
x, y, cw, ch = w._roi_rect()
print("  rect :", (x, y, cw, ch))
assert abs((x + cw / 2) - iw / 2) <= 1 and abs((y + ch / 2) - ih / 2) <= 1

# --- 7. kaynak degisimi: konum sifirlanir, sinirlar yeni goruntuye gore ---
print("\n[7] kaynak = dedektor")
w.f_roi_src.setCurrentIndex(1)
print("  rect :", w._roi_rect())
print("  info :", w.lbl_roi_info.text())
assert w._roi_rect() is None, "kaynak degisince konum sifirlanmali"
assert w.view_gt._roi is None, "GT dikdortgeni temizlenmedi"
iw2, ih2 = w.view_det.image_size()
assert w.f_roi_cx.maximum() == iw2 - 1, "konum ust siniri guncellenmedi"
w.f_roi_cx.setValue(789)
w.f_roi_cy.setValue(773)
crop = w._roi_crop("det", w._roi_rect())
print("  elle konum sonrasi rect:", w._roi_rect(), "crop:", crop.shape)
assert w.view_det._roi is not None, "DET dikdortgeni cizilmedi"

# --- 8. olcu goruntuden buyuk olamaz (maximum sinirli) ---
print("\n[8] olcu ust siniri")
w.f_roi_w.setValue(10**5)
w.f_roi_h.setValue(10**5)
print("  G/Y  :", w.f_roi_w.value(), w.f_roi_h.value(), "goruntu:", (iw2, ih2))
assert (w.f_roi_w.value(), w.f_roi_h.value()) == (iw2, ih2)
x, y, cw, ch = w._roi_rect()
assert (cw, ch) == (iw2, ih2), "tam kare kirpma beklenirdi"

# --- 9. goruntusuz pencere cokmemeli ---
print("\n[9] goruntusuz pencere")
w2 = MainWindow()
print("  rect :", w2._roi_rect())
w2._roi_changed()
w2._roi_center()
w2.f_roi_w.setValue(50)
w2.f_roi_cx.setValue(20)
print("  info :", w2.lbl_roi_info.text())
assert w2._roi_rect() is None

print("\nTUM ROI TESTLERI GECTI")
