"""
Optik Analiz — ana pencere.

Sol panel : sistem parametreleri (lens / dedektör / OLED) + preset kaydet-yükle
Orta panel: görüntü sekmeleri (ground truth / dedektör / overlay)
Sağ panel : hesaplanan sonuçlar (FOV, IFOV, tilt, ayna, eşleme kalitesi)

Parametrelerin hepsi düzenlenebilir; bir değer değişince analiz yeniden
çalıştırıldığında matematik otomatik yeni değerlere göre kurulur
(config.py parametrik olduğu için hiçbir sayı GUI'ye gömülü değildir).
"""
from __future__ import annotations

import math
import os
import sys

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox, QComboBox,
    QFileDialog, QTabWidget, QProgressBar, QMessageBox, QSplitter,
    QScrollArea, QSizePolicy, QApplication,
)

# Proje kökünü import yoluna ekle (doğrudan çalıştırma senaryosu)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2
import numpy as np

from core.config import SystemConfig, Lens, Detector, OLED, default_config
from core import config as cfgmod
from core import projection as projmod
from core import solver
from core import pipeline, image_analysis
from core.pointing import fmt_px, fmt_shape
from gui.widgets import (
    ImageView, ResultRow, hline, STYLESHEET, ACCENT, MUTED, GOOD, WARN, BAD,
)

PRESET_DIR = os.path.join(_ROOT, "presets")


# --------------------------- Arka plan işçisi ------------------------------

class AnalysisWorker(QThread):
    """
    Analizi ayrı thread'de koşturur — arayüz donmasın.

    ROI verilmişse analiz İKİ KEZ çalışır:
      1. Tam kare  — her iki görüntünün tamamı (asıl/referans sonuç)
      2. Kırpılmış — GT ve dedektörden aynı bölge kesilip aynı akış tekrar

    İkinci koşu, kırpılan bölgeler geçici dosyaya yazılıp aynı
    `pipeline.run_analysis` çağrılarak yapılır; böylece iki sonuç birebir
    aynı kodu kullanır ve karşılaştırılabilir olur.
    """
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object, object)     # (tam_kare, roi | None)
    failed = pyqtSignal(str)

    def __init__(self, gt_path: str, det_path: str, cfg: SystemConfig,
                 roi: tuple[int, int, int, int] | None = None,
                 roi_src: str = "gt"):
        super().__init__()
        self.gt_path = gt_path
        self.det_path = det_path
        self.cfg = cfg
        self.roi = roi
        self.roi_src = roi_src

    def run(self):
        try:
            # Her iki koşu ortak bir ilerleme çubuğunu paylaşır: ROI varsa
            # tam kare 0-50, kırpılmış 50-100 aralığına sıkıştırılır.
            span = 50 if self.roi else 100
            full = pipeline.run_analysis(
                self.gt_path, self.det_path, self.cfg,
                progress=lambda p, m: self.progress.emit(
                    int(p * span / 100), m))

            roi_res = None
            if self.roi:
                roi_res = self._run_roi(full)

            self.finished_ok.emit(full, roi_res)
        except Exception as e:                          # noqa: BLE001
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")

    def _run_roi(self, full):
        """
        Kırpılmış bölge için ikinci analizi koşar.

        ROI kullanıcının seçtiği kaynağın (GT ya da dedektör) piksel
        koordinatlarında. Diğer görüntüde karşılık gelen bölgeyi bulmak için
        tam kare koşusunun homografisi kullanılır: ROI köşeleri o dönüşümle
        diğer düzleme taşınır. Homografi yoksa (eşleme başarısızsa) iki
        görüntü ölçek olarak orantılı kabul edilip ROI oransal eşlenir —
        kaba ama kadraj benzerse kullanışlı bir yedek.
        """
        import tempfile
        x, y, w, h = self.roi
        gt_gray = image_analysis.load_image_gray(self.gt_path)
        det_gray = image_analysis.load_image_gray(self.det_path)

        src_gray = gt_gray if self.roi_src == "gt" else det_gray
        dst_gray = det_gray if self.roi_src == "gt" else gt_gray

        dst_rect = self._map_rect(full, (x, y, w, h), src_gray, dst_gray)

        src_crop = self._safe_crop(src_gray, (x, y, w, h))
        dst_crop = self._safe_crop(dst_gray, dst_rect)
        if src_crop is None or dst_crop is None:
            return None

        gt_crop = src_crop if self.roi_src == "gt" else dst_crop
        det_crop = dst_crop if self.roi_src == "gt" else src_crop

        tmp = tempfile.mkdtemp(prefix="optik_roi_")
        gt_p = os.path.join(tmp, "gt_roi.png")
        det_p = os.path.join(tmp, "det_roi.png")
        cv2.imwrite(gt_p, gt_crop)
        cv2.imwrite(det_p, det_crop)

        res = pipeline.run_analysis(
            gt_p, det_p, self.cfg,
            progress=lambda p, m: self.progress.emit(
                50 + int(p * 0.5), f"[kırpma] {m}"))
        # Sonuca hangi bölgenin ölçüldüğü iliştirilir (GUI etiketlemek için).
        res.roi_rect = (x, y, w, h)
        res.roi_dst_rect = dst_rect
        res.roi_src = self.roi_src
        return res

    def _map_rect(self, full, rect, src_gray, dst_gray):
        """
        ROI'yi diğer görüntünün koordinatlarına taşır.

        Homografi GT -> dedektör yönünde üretiliyor. ROI dedektörden
        seçildiyse ters yöne gitmek gerekir, o yüzden matris tersleniyor.
        """
        x, y, w, h = rect
        corners = np.float32([[x, y], [x + w, y],
                              [x + w, y + h], [x, y + h]]).reshape(-1, 1, 2)
        H = full.match.homography if full.match is not None else None
        if H is not None and not getattr(full.match, "degenerate", False):
            try:
                M = H if self.roi_src == "gt" else np.linalg.inv(H)
                proj = cv2.perspectiveTransform(corners, M).reshape(-1, 2)
                xs, ys = proj[:, 0], proj[:, 1]
                return (int(xs.min()), int(ys.min()),
                        int(xs.max() - xs.min()), int(ys.max() - ys.min()))
            except Exception:                            # noqa: BLE001
                pass
        # Yedek: iki görüntü orantılı kabul edilip oransal eşlenir.
        sh, sw = src_gray.shape[:2]
        dh, dw = dst_gray.shape[:2]
        fx, fy = dw / sw, dh / sh
        return (int(x * fx), int(y * fy), int(w * fx), int(h * fy))

    @staticmethod
    def _safe_crop(img, rect):
        """Dikdörtgeni görüntü sınırlarına kısarak keser."""
        x, y, w, h = rect
        ih, iw = img.shape[:2]
        x, y = max(0, x), max(0, y)
        x2, y2 = min(x + w, iw), min(y + h, ih)
        if x2 - x < 8 or y2 - y < 8:
            return None
        return img[y:y2, x:x2]


# ------------------------------ Ana pencere --------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Optik Analiz — FOV / IFOV / Tilt Ölçümü")
        self.resize(1500, 900)

        self.gt_path: str | None = None
        self.det_path: str | None = None
        self.worker: AnalysisWorker | None = None
        self.result = None
        self.roi_result = None

        self._build_ui()
        self._load_config_into_fields(default_config())

    # ---------------------------- UI kurulum -------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([380, 720, 360])
        root.addWidget(splitter, 1)

        # Alt: ilerleme + durum
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.status_label = QLabel("Hazır — ground truth ve dedektör görüntüsünü seçin.")
        self.status_label.setStyleSheet(f"color:{MUTED};")
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.progress, 1)
        root.addLayout(bottom)

    def _build_header(self) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(2, 0, 2, 0)

        title = QLabel("Optik Analiz")
        tf = QFont()
        tf.setPointSize(17)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color:{ACCENT};")

        sub = QLabel("Ground truth ↔ dedektör karşılaştırmasıyla "
                     "FOV · IFOV · tilt ölçümü")
        sub.setStyleSheet(f"color:{MUTED};")

        lay.addWidget(title)
        lay.addSpacing(14)
        lay.addWidget(sub)
        lay.addStretch(1)
        return box

    # ---- Sol panel: parametreler ----

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 8, 2)
        lay.setSpacing(10)

        # --- Görüntüler ---
        gb_img = QGroupBox("Görüntüler")
        gl = QGridLayout(gb_img)
        gl.setColumnStretch(1, 1)

        self.gt_label = QLabel("seçilmedi")
        self.gt_label.setStyleSheet(f"color:{MUTED};")
        self.gt_label.setWordWrap(True)
        btn_gt = QPushButton("Ground truth seç…")
        btn_gt.clicked.connect(self._pick_gt)

        self.det_label = QLabel("seçilmedi")
        self.det_label.setStyleSheet(f"color:{MUTED};")
        self.det_label.setWordWrap(True)
        btn_det = QPushButton("Dedektör görüntüsü seç…")
        btn_det.clicked.connect(self._pick_det)

        gl.addWidget(btn_gt, 0, 0, 1, 2)
        gl.addWidget(self.gt_label, 1, 0, 1, 2)
        gl.addWidget(btn_det, 2, 0, 1, 2)
        gl.addWidget(self.det_label, 3, 0, 1, 2)
        lay.addWidget(gb_img)

        lay.addWidget(self._build_roi_group())

        # --- Hazır sistem ---
        # Katalog yalnızca kolaylık: bir kalem seçmek alanları doldurur,
        # ardından HER alan elle düzenlenebilir. Elle düzenlenince seçici
        # kendiliğinden "Özel"e döner — böylece gösterilen seçim ile
        # alanlardaki değerler asla ayrışmaz.
        gb_sys = QGroupBox("Hazır sistem")
        syl = QGridLayout(gb_sys)
        self.f_system = QComboBox()
        for key in cfgmod.SYSTEM_PRESETS:
            self.f_system.addItem(key, key)
        self.f_system.addItem(cfgmod.CUSTOM, cfgmod.CUSTOM)
        self._grid_row(syl, 0, "Sistem", self.f_system,
                       "Lens + dedektörü birlikte doldurur.")
        self.f_system.activated.connect(self._apply_system_preset)
        lay.addWidget(gb_sys)

        # --- Lens ---
        gb_lens = QGroupBox("Lens")
        ll = QGridLayout(gb_lens)
        self.f_lens_sel = QComboBox()
        for key in cfgmod.LENS_CATALOG:
            self.f_lens_sel.addItem(key, key)
        self.f_lens_sel.addItem(cfgmod.CUSTOM, cfgmod.CUSTOM)
        self.f_lens_sel.activated.connect(self._apply_lens_preset)

        self.f_lens_name = QLineEdit()
        self.f_focal = self._dspin(1.0, 100000.0, 3, " mm")
        self.f_fnum = self._dspin(0.5, 100.0, 2, "")
        self.f_pupil = self._dspin(0.0, 10000.0, 2, " mm")
        self.f_ufov = self._dspin(0.0, 360.0, 2, " °")
        self._grid_row(ll, 0, "Hazır lens", self.f_lens_sel)
        self._grid_row(ll, 1, "Model", self.f_lens_name)
        self._grid_row(ll, 2, "Odak uzaklığı f", self.f_focal,
                       "FOV ve IFOV doğrudan bu değere bağlıdır.")
        self._grid_row(ll, 3, "Diyafram f/", self.f_fnum,
                       "Hesabı etkilemez; kayıt amaçlı.")
        self._grid_row(ll, 4, "Giriş pupili", self.f_pupil,
                       "0 = f/# ten türet (D = f / N).")
        self._grid_row(ll, 5, "Üretici FOV", self.f_ufov,
                       "Üreticinin verdiği kullanılabilir FOV; "
                       "hesaplanan FOV ile karşılaştırma için.")

        # Projeksiyon modeli — FOV/IFOV matematiğinin altındaki asıl varsayım.
        # Görünür bir alan olması önemli: "FOV yanlış çıkıyor" şüphesinde ilk
        # bakılacak yer burasıdır.
        self.f_proj = QComboBox()
        for key in projmod.MODELS:
            self.f_proj.addItem(projmod.MODEL_LABELS[key], key)
            # Her kalemin kendi ipucu: ne anlama geldiği ve nerede
            # kullanıldığı. Model seçimi FOV/IFOV'un tamamını belirlediği
            # için listede körlemesine seçim yapılmamalı.
            self.f_proj.setItemData(self.f_proj.count() - 1,
                                    projmod.MODEL_HELP.get(key, ""),
                                    Qt.ToolTipRole)
        self.f_proj.activated.connect(self._update_projection_label)
        self._grid_row(ll, 6, "Projeksiyon", self.f_proj,
                       "Lensin açı → görüntü yüksekliği haritası. "
                       "Rektilineer (r = f·tan θ) 40-60° tasarımların "
                       "standardıdır; balıkgözü ve ölçüm objektifleri "
                       "genelde equidistant (r = f·θ) haritalar.")
        self.lbl_proj = QLabel("—")
        self.lbl_proj.setWordWrap(True)
        self.lbl_proj.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        ll.addWidget(self.lbl_proj, 7, 0, 1, 2)
        for wdg in (self.f_focal, self.f_ufov):
            wdg.valueChanged.connect(self._update_projection_label)
        lay.addWidget(gb_lens)

        # --- Dedektör ---
        gb_det = QGroupBox("Dedektör")
        dl = QGridLayout(gb_det)
        self.f_det_sel = QComboBox()
        for key in cfgmod.DETECTOR_CATALOG:
            self.f_det_sel.addItem(key, key)
        self.f_det_sel.addItem(cfgmod.CUSTOM, cfgmod.CUSTOM)
        self.f_det_sel.activated.connect(self._apply_detector_preset)

        self.f_det_name = QLineEdit()
        self.f_det_w = self._ispin(1, 100000, " px")
        self.f_det_h = self._ispin(1, 100000, " px")
        self.f_pitch_x = self._dspin(0.01, 1000.0, 4, " µm")
        self.f_pitch_y = self._dspin(0.01, 1000.0, 4, " µm")
        self._grid_row(dl, 0, "Hazır dedektör", self.f_det_sel)
        self._grid_row(dl, 1, "Model", self.f_det_name)
        self._grid_row(dl, 2, "Genişlik", self.f_det_w)
        self._grid_row(dl, 3, "Yükseklik", self.f_det_h)
        self._grid_row(dl, 4, "Piksel pitch X", self.f_pitch_x)
        self._grid_row(dl, 5, "Piksel pitch Y", self.f_pitch_y,
                       "Kare piksel için X ile aynı bırakın.")
        self.lbl_sensor = QLabel("—")
        self.lbl_sensor.setStyleSheet(f"color:{MUTED};")
        dl.addWidget(self.lbl_sensor, 6, 0, 1, 2)
        lay.addWidget(gb_det)

        # Sensör boyutu canlı güncellensin
        for wdg in (self.f_det_w, self.f_det_h, self.f_pitch_x, self.f_pitch_y):
            wdg.valueChanged.connect(self._update_sensor_label)
            # FOV dedektör ölçüsüne de bağlı — projeksiyon satırı takip etsin.
            wdg.valueChanged.connect(self._update_projection_label)

        # Elle düzenleme seçiciyi "Özel"e düşürür (tek doğruluk kaynağı).
        for wdg in (self.f_focal, self.f_fnum, self.f_pupil):
            wdg.valueChanged.connect(self._sync_catalog_selectors)
        for wdg in (self.f_det_w, self.f_det_h, self.f_pitch_x, self.f_pitch_y):
            wdg.valueChanged.connect(self._sync_catalog_selectors)

        # --- Referans ekran (OLED panel ya da STOS gibi açısal kaynak) ---
        gb_oled = QGroupBox("Referans ekran")
        ol = QGridLayout(gb_oled)
        self.f_scr_sel = QComboBox()
        for key in cfgmod.SCREEN_CATALOG:
            self.f_scr_sel.addItem(key, key)
        self.f_scr_sel.addItem(cfgmod.CUSTOM, cfgmod.CUSTOM)
        self.f_scr_sel.activated.connect(self._apply_screen_preset)

        self.f_oled_name = QLineEdit()
        self.f_oled_w = self._ispin(1, 100000, " px")
        self.f_oled_h = self._ispin(1, 100000, " px")
        self.f_oled_pitch = self._dspin(0.01, 1000.0, 4, " µm")
        self.f_oled_aw = self._dspin(0.01, 10000.0, 3, " mm")
        self.f_oled_ah = self._dspin(0.01, 10000.0, 3, " mm")
        self.f_scr_ang = self._dspin(0.0, 90.0, 5, " °/px")
        self._grid_row(ol, 0, "Hazır ekran", self.f_scr_sel)
        self._grid_row(ol, 1, "Model", self.f_oled_name)
        self._grid_row(ol, 2, "Genişlik", self.f_oled_w)
        self._grid_row(ol, 3, "Yükseklik", self.f_oled_h)
        self._grid_row(ol, 4, "Piksel pitch", self.f_oled_pitch)
        self._grid_row(ol, 5, "Aktif alan G", self.f_oled_aw)
        self._grid_row(ol, 6, "Aktif alan Y", self.f_oled_ah)
        self._grid_row(ol, 7, "Açısal çözünürlük", self.f_scr_ang,
                       "STOS gibi açısal kaynaklarda üreticinin verdiği "
                       "derece/piksel. 0 = pasif panel (OLED).")
        # Açısal kaynakta ima edilen odak uzaklığı ve kapsama canlı gösterilir.
        self.lbl_screen = QLabel("—")
        self.lbl_screen.setWordWrap(True)
        self.lbl_screen.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        ol.addWidget(self.lbl_screen, 8, 0, 1, 2)
        for wdg in (self.f_oled_w, self.f_oled_h, self.f_oled_pitch,
                    self.f_scr_ang):
            wdg.valueChanged.connect(self._update_screen_label)
            # Elle düzenleme seçiciyi "Özel"e düşürmeli — aksi halde listede
            # "STOS" yazarken alanlarda başka bir ekran durur (tek doğruluk
            # kaynağı kuralı, bkz. lens/dedektör seçicileri).
            wdg.valueChanged.connect(self._sync_catalog_selectors)
        lay.addWidget(gb_oled)

        # --- Düzenek ---
        gb_setup = QGroupBox("Düzenek")
        sl = QGridLayout(gb_setup)
        self.f_setup = QComboBox()
        self.f_setup.addItem("Doğrudan (kollimatörsüz)", "direct")
        self.f_setup.addItem("Kollimatörlü", "collimator")
        self.f_coll_f = self._dspin(0.0, 100000.0, 3, " mm")
        self._grid_row(sl, 0, "Tip", self.f_setup)
        self._grid_row(sl, 1, "Kollimatör f", self.f_coll_f,
                       "Sadece kollimatörlü düzenekte kullanılır.")
        self.f_setup.currentIndexChanged.connect(
            lambda: self.f_coll_f.setEnabled(
                self.f_setup.currentData() == "collimator"))
        self.f_coll_f.setEnabled(False)
        lay.addWidget(gb_setup)

        # --- Preset ---
        gb_pre = QGroupBox("Preset")
        pl = QHBoxLayout(gb_pre)
        btn_save = QPushButton("Kaydet…")
        btn_save.clicked.connect(self._save_preset)
        btn_load = QPushButton("Yükle…")
        btn_load.clicked.connect(self._load_preset)
        btn_reset = QPushButton("Varsayılan")
        btn_reset.clicked.connect(
            lambda: self._load_config_into_fields(default_config()))
        pl.addWidget(btn_save)
        pl.addWidget(btn_load)
        pl.addWidget(btn_reset)
        lay.addWidget(gb_pre)

        # --- Analiz butonu ---
        self.btn_run = QPushButton("ANALİZ ET")
        self.btn_run.setObjectName("primary")
        self.btn_run.setMinimumHeight(42)
        self.btn_run.clicked.connect(self._run_analysis)
        lay.addWidget(self.btn_run)

        lay.addStretch(1)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(340)
        return scroll

    # ---- Sol panel: kırpma / zoom (ROI) ----

    def _build_roi_group(self) -> QGroupBox:
        """
        Ölçü girilebilen kırpma penceresi.

        Amaç: görüntünün belirli bir bölgesini verilen piksel ölçüsünde kesip
        büyütmek. Bu bir *inceleme* aracı — girilen ölçü FOV/IFOV/tilt
        hesabını değiştirmez (o hesaplar tüm kareyi ve dedektörün gerçek
        piksel sayısını kullanır). Kırpma yalnızca "şu bölgeye yakından
        bakayım" ihtiyacını karşılar; ölçüm matematiği parametrik kalır.
        """
        gb = QGroupBox("Kırpma (ROI)")
        g = QGridLayout(gb)
        g.setColumnStretch(1, 1)

        self.f_roi_src = QComboBox()
        self.f_roi_src.addItem("Ground truth", "gt")
        self.f_roi_src.addItem("Dedektör", "det")
        self._grid_row(g, 0, "Kaynak", self.f_roi_src)

        # Varsayılan 0 = kırpma kapalı. Boyut girilene kadar hiçbir alan
        # seçilmez; kullanıcı ölçüyü kendisi belirler.
        self.f_roi_w = self._ispin(0, 100000, " px")
        self.f_roi_h = self._ispin(0, 100000, " px")
        for wdg in (self.f_roi_w, self.f_roi_h):
            wdg.setSpecialValueText("—")     # 0 iken boş görünsün
            wdg.setValue(0)
        self._grid_row(g, 1, "Genişlik", self.f_roi_w)
        self._grid_row(g, 2, "Yükseklik", self.f_roi_h)

        # Konum: hem elle yazılabilir hem görüntüye tıklayarak doldurulur.
        # İkisi çift yönlü bağlı — tıklayınca alanlar güncellenir, alana
        # yazınca dikdörtgen taşınır.
        self.f_roi_cx = self._ispin(0, 100000, " px")
        self.f_roi_cy = self._ispin(0, 100000, " px")
        self._grid_row(g, 3, "Merkez X", self.f_roi_cx,
                       "Görüntüye tıklayarak da seçebilirsiniz.")
        self._grid_row(g, 4, "Merkez Y", self.f_roi_cy,
                       "Görüntüye tıklayarak da seçebilirsiniz.")

        btn_center = QPushButton("Ortala")
        btn_center.clicked.connect(self._roi_center)
        g.addWidget(btn_center, 5, 0, 1, 2)

        self.lbl_roi_info = QLabel("Ölçü girin, sonra konumu seçin.")
        self.lbl_roi_info.setStyleSheet(f"color:{MUTED};")
        self.lbl_roi_info.setWordWrap(True)
        g.addWidget(self.lbl_roi_info, 6, 0, 1, 2)

        # Merkez ayrıca iç durumda tutuluyor: None = henüz konum seçilmedi
        # (spinbox 0'ı geçerli bir konum olduğu için ayırt edilemez).
        self._roi_cx: int | None = None
        self._roi_cy: int | None = None

        for wdg in (self.f_roi_w, self.f_roi_h):
            wdg.valueChanged.connect(self._roi_changed)
        for wdg in (self.f_roi_cx, self.f_roi_cy):
            wdg.valueChanged.connect(self._roi_center_edited)
        self.f_roi_src.currentIndexChanged.connect(self._roi_source_changed)
        return gb

    # ---- Orta panel: görüntüler ----

    def _build_center_panel(self) -> QWidget:
        self.tabs = QTabWidget()
        self.view_gt = ImageView("Ground truth görüntüsü seçilmedi")
        self.view_det = ImageView("Dedektör görüntüsü seçilmedi")
        self.view_overlay = ImageView("Analizden sonra hizalama görünecek")

        self.tabs.addTab(self._wrap_view(
            self.view_gt,
            "Yeşil elips: merkezi Siemens star'ın tespit edilen dış sınırı."),
            "Ground truth")
        self.tabs.addTab(self._wrap_view(
            self.view_det,
            "Yeşil elips: dedektör görüntüsündeki yıldız sınırı. "
            "Elipsleşme = düzlem-dışı tilt."),
            "Dedektör")
        self.tabs.addTab(self._wrap_view(
            self.view_overlay,
            "Kırmızı: dedektör · Yeşil: hizalanmış ground truth. "
            "Sarı bölgeler iyi örtüşmeyi gösterir."),
            "Hizalama (overlay)")

        self.view_crop = ImageView(
            "Sol panelden ölçü ve konum girin.")
        self.tabs.addTab(self._wrap_view(
            self.view_crop,
            "Seçilen alan. Analiz koşturulduğunda bu bölge için ayrı bir "
            "ölçüm daha yapılır; sonuçlar sağdaki karşılaştırma tablosunda."),
            "Kırpma")

        # Görüntüye tıklayınca ROI merkezi oraya taşınsın.
        self.view_gt.clicked_at.connect(
            lambda x, y: self._roi_click("gt", x, y))
        self.view_det.clicked_at.connect(
            lambda x, y: self._roi_click("det", x, y))
        return self.tabs

    def _wrap_view(self, view: ImageView, hint: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(view, 1)
        lbl = QLabel(hint)
        lbl.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        return w

    # ---- Sağ panel: sonuçlar ----

    def _build_right_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(10)

        # ------------------------------------------------------------------
        # Panel, kullanıcının sorduğu ÜÇ soruya göre düzenlenmiştir:
        #   1. Sensör ne kadar geniş görüyor?      -> FOV
        #   2. Tek piksel ne kadar açı görüyor?    -> IFOV (açısal FOV)
        #   3. Görüntüde ne kadar eğiklik var?     -> Tilt
        # Ara veriler (eksen oranı, inlier, yeniden izdüşüm, keystone) bu
        # soruların cevabı değil, cevabın türetildiği ham veridir; kullanıcı
        # onlarla karar vermez. Bu yüzden "Ayrıntılar" katlanır bölümüne
        # alındı — sorun teşhisi için duruyorlar ama ekranı meşgul etmiyorlar.
        # ------------------------------------------------------------------

        # 1) FOV
        gb_fov = QGroupBox("Görüş Alanı (FOV)")
        fl = QVBoxLayout(gb_fov)
        self.r_fov_xy = ResultRow("Yatay × Dikey", "°",
                                  "Sensörün gördüğü toplam açı.")
        self.r_fov_d = ResultRow("Köşegen", "°")
        # FOV sayısı TEK BAŞINA eksiktir: aynı f ve sensörle farklı
        # projeksiyon modelleri farklı FOV verir (Hydra'da yayılım 0.41°).
        # Model, sonucun ayrılmaz parçası olduğu için panelde görünür.
        # Lensin görüntü dairesi sensörden küçükse köşeler karanlıktır ve
        # geometrik FOV gerçeği yansıtmaz. O durumda GERÇEK satırı devreye
        # girer; kapsıyorsa satır gizlenir (aynı sayıyı iki kez yazmamak için).
        self.r_fov_eff = ResultRow("Gerçekte görülen", "°",
                                   "Lensin görüntü dairesiyle kırpıldıktan "
                                   "sonra sensörde gerçekten görüntü olan "
                                   "alan. Daire sensörü tamamen kapsıyorsa "
                                   "bu satır gizlenir.")
        self.r_fov_circle = ResultRow("Görüntü dairesi", "mm",
                                      "Lensin ürettiği dairesel görüntünün "
                                      "çapı. Sensör köşegeninden küçükse "
                                      "köşeler karanlıktır (vignetting).")
        self.r_fov_model = ResultRow("Projeksiyon", "",
                                     "FOV'un hangi açı→yükseklik haritasıyla "
                                     "hesaplandığı. Sol panelden değiştirilir.")
        # Üreticinin verdiği FOV ile karşılaştırma — bağımsız bir sağlık
        # göstergesi (§7C'deki useful FOV tutarlılığı).
        self.r_fov_check = ResultRow("Üretici FOV ile", "",
                                     "Hesaplanan tam-sensör FOV'unun üreticinin "
                                     "kullanılabilir FOV'undan BÜYÜK çıkması "
                                     "beklenen yöndür: useful FOV köşe kalitesi "
                                     "düştüğü için dar tanımlanır.")
        for r in (self.r_fov_xy, self.r_fov_d, self.r_fov_eff,
                  self.r_fov_circle, self.r_fov_model, self.r_fov_check):
            fl.addWidget(r)
        lay.addWidget(gb_fov)

        # 2) IFOV — "açısal FOV", tek pikselin gördüğü açı
        gb_ifov = QGroupBox("Piksel Açısı (IFOV)")
        il = QVBoxLayout(gb_ifov)
        self.r_ifov = ResultRow("Bir piksel", "µrad",
                                "Tek bir pikselin gördüğü açı — sistemin "
                                "ayırt etme gücü.")
        self.r_ifov_as = ResultRow("", "arcsec")
        # Açısal çözünürlük = IFOV'un derece/piksel cinsinden yazılışı.
        # Ayrı bir satır olmasının sebebi datasheet dili: üreticiler bu
        # büyüklüğü genelde °/px olarak verir (STOS'un 0.027 °/px'i gibi),
        # µrad değil. Aynı sayının iki dilde yazılışı — hangi birimde
        # arıyorsa kullanıcı onu bulsun.
        self.r_ang_res = ResultRow("Açısal çözünürlük", "°/px",
                                   "IFOV'un derece/piksel cinsinden karşılığı. "
                                   "Datasheet'ler açısal çözünürlüğü genelde "
                                   "bu birimde verir.")
        # Kenar pikselinin açısı — merkezdekinden farklıdır (rektilineerde
        # daha küçük). Tek bir IFOV sayısının tüm alan için geçerli
        # OLMADIĞINI gösterir; "FOV = N × IFOV" yaklaşımının neden kenarda
        # bozulduğu doğrudan budur.
        self.r_ifov_edge = ResultRow("Kenar pikseli", "µrad",
                                     "Sensör kenarındaki pikselin gördüğü açı. "
                                     "Rektilineer projeksiyonda merkezden "
                                     "küçüktür; equidistant (f-theta) lenste "
                                     "tanım gereği eşittir.")
        for r in (self.r_ifov, self.r_ifov_as, self.r_ang_res,
                  self.r_ifov_edge):
            il.addWidget(r)
        lay.addWidget(gb_ifov)

        # 3) Tilt
        gb_tilt = QGroupBox("Eğiklik (Tilt)")
        tl = QVBoxLayout(gb_tilt)
        # "Dönme (SIFT)" satırı PANELDEN KALDIRILDI.
        #
        # Aynı büyüklük iki kez gösteriliyordu: bu satır özellik eşleme
        # (SIFT) yolundan, "Yönelim hataları"ndaki Roll satırı ise yoğun
        # hizalamadan. Kendine-benzer desenlerde (eş merkezli çember) SIFT
        # çalışamadığı için burası "ölçülemedi" derken hemen altında Roll
        # gerçek değeri gösteriyordu — kullanıcı haklı olarak "neden
        # ölçülemedi?" diye soruyordu. Tek dönme satırı kalsın diye bu
        # gizlendi.
        #
        # Widget YOK EDİLMEDİ: karşılaştırma tablosu ve `_clear_results`
        # ona başvuruyor, ayrıca SIFT arka planda çalışmaya devam ediyor
        # (ROI kırpma eşlemesi, eşleşen nokta ve hizalama hatası ondan
        # geliyor). İleride karşılaştırma istenirse layout'a geri eklemek
        # yeterli.
        self.r_rot = ResultRow("Dönme (SIFT)", "°",
                               "Özellik eşleme (SIFT) yolunun dönme ölçümü. "
                               "Panelde gösterilmiyor; yoğun hizalamanın "
                               "ölçümü Roll satırındadır.")
        self.r_tilt = ResultRow("Eğiklik", "°",
                                "Dedektör düzleminin hedefe göre eğikliği.")
        tl.addWidget(self.r_tilt)
        # Ölçüm belirsizliği / sınır durumu için açıklama satırı
        self.lbl_tilt_note = QLabel("")
        self.lbl_tilt_note.setWordWrap(True)
        self.lbl_tilt_note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        tl.addWidget(self.lbl_tilt_note)
        lay.addWidget(gb_tilt)

        # 3B) Yönelim hataları — decenter / roll / tilt
        # Kaynağı yoğun hizalamanın homografisidir (core/pointing.py); ayrı
        # bir ölçüm değil, aynı homografinin yönelim dilindeki okunuşudur.
        gb_point = QGroupBox("Yönelim hataları")
        pl = QVBoxLayout(gb_point)
        self.r_decenter = ResultRow(
            "Decenter", "°",
            "Desen merkezinin sensör merkezinden kaçıklığı (bore-sight hatası). "
            "Açıya çevirme lens f'i ve piksel pitch'e bağlıdır.")
        self.r_decenter_px = ResultRow(
            "Decenter (piksel)", "px",
            "Aynı kaçıklığın dedektör pikseli cinsinden karşılığı.")
        self.r_roll = ResultRow(
            "Roll (düzlem-içi dönme)", "°",
            "Görüntünün kendi düzleminde dönmesi, 0..360°. Yoğun (desen-agnostik) "
            "hizalamadan gelir; kendine-benzer desenlerde de çalışır. "
            "Perspektif bozulması yaratmaz — o 'Eğiklik' satırıdır.")
        self.r_ptilt = ResultRow(
            "Tilt (x / y)", "°",
            "Düzlem-dışı yatışın iki bileşeni: dikey ve yatay keystone.")
        for r in (self.r_decenter, self.r_decenter_px, self.r_roll, self.r_ptilt):
            pl.addWidget(r)
        self.lbl_point_note = QLabel("")
        self.lbl_point_note.setWordWrap(True)
        self.lbl_point_note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        pl.addWidget(self.lbl_point_note)
        lay.addWidget(gb_point)

        # 3C) FOV kapsaması — "ekranda en fazla ne kadar alan görünüyor"
        gb_cov = QGroupBox("FOV kapsaması")
        cl = QVBoxLayout(gb_cov)
        self.r_cov_pattern = ResultRow(
            "Desenden kullanılan", "px",
            "Ground truth deseninin kaç pikseli sensöre düşüyor "
            "(kullanılan / GT'nin toplam pikseli). Sayım GT'nin KENDİ "
            "çözünürlüğünde yapılır; dedektörde desen büyümüş görünse de "
            "toplam, yüklenen ground truth görüntüsünün piksel sayısıdır.")
        self.r_cov_sensor = ResultRow(
            "Sensörden kullanılan", "px",
            "Sensörün kaç pikseli desenle kaplı (dolu / dedektör "
            "görüntüsünün toplam pikseli — yüklenen görüntünün boyutu, "
            "soldaki dedektör ayarı değil).")
        self.r_cov_maxang = ResultRow(
            "Ulaşılan en büyük açı", "°",
            "Sensör köşesinin optik eksene göre açısı — pratikte görülen yarı-FOV.")
        self.r_cov_edges = ResultRow(
            "Kenar açıları", "°",
            "Sol / sağ / üst / alt kenarların açısı. Kırpılmış görüntüde asimetriktir.")
        self.r_cov_margin = ResultRow(
            "Desen payı", "px",
            "Deseni tamamen görmek için kalan pay. Negatifse desen taşıyor.")
        for r in (self.r_cov_pattern, self.r_cov_sensor, self.r_cov_maxang,
                  self.r_cov_edges, self.r_cov_margin):
            cl.addWidget(r)
        lay.addWidget(gb_cov)

        # 4) Tek satır durum — sonuca güvenilir mi
        gb_st = QGroupBox("Durum")
        sl = QVBoxLayout(gb_st)
        self.lbl_verdict = QLabel("—")
        self.lbl_verdict.setWordWrap(True)
        self.lbl_verdict.setStyleSheet(f"color:{MUTED}; font-size:13px;")
        sl.addWidget(self.lbl_verdict)

        self.btn_details = QPushButton("▸ Ayrıntılar")
        self.btn_details.setCheckable(True)
        self.btn_details.setStyleSheet(
            f"QPushButton{{border:none; color:{MUTED}; text-align:left; "
            f"padding:2px; font-size:11px;}}")
        self.btn_details.toggled.connect(self._toggle_details)
        sl.addWidget(self.btn_details)

        # Katlanan teknik ayrıntılar
        self.details_box = QWidget()
        # Gizliyken yer kaplamasın (aksi halde Durum kutusu boş boşluk bırakır)
        sp = self.details_box.sizePolicy()
        sp.setRetainSizeWhenHidden(False)
        self.details_box.setSizePolicy(sp)
        dl = QVBoxLayout(self.details_box)
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(2)
        self.r_sensor = ResultRow("Sensör", "mm")
        self.r_tilt_method = ResultRow("Tilt yöntemi", "")
        self.r_el_conf = ResultRow("Desen tespit güveni", "")
        self.r_mirror = ResultRow("Ayna (flip)", "")
        self.r_inliers = ResultRow("Eşleşen nokta", "")
        self.r_reproj = ResultRow("Hizalama hatası", "px")
        for r in (self.r_sensor, self.r_tilt_method, self.r_el_conf,
                  self.r_mirror, self.r_inliers, self.r_reproj):
            dl.addWidget(r)
        self.details_box.setVisible(False)
        sl.addWidget(self.details_box)
        lay.addWidget(gb_st)

        # 5) Tam kare ↔ kırpılan bölge karşılaştırması
        # Yalnızca ROI ile analiz koşulduğunda görünür.
        self.gb_cmp = QGroupBox("Tam kare ↔ Kırpma")
        cl = QGridLayout(self.gb_cmp)
        cl.setSpacing(4)
        for col, txt in ((1, "Tam kare"), (2, "Kırpma")):
            h = QLabel(txt)
            h.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:600;")
            h.setAlignment(Qt.AlignRight)
            cl.addWidget(h, 0, col)
        cl.setColumnStretch(0, 1)

        # (etiket, sonuçtan değeri üreten fonksiyon)
        # DİKKAT: Dönme, homografi reddedilirse sessizce yıldız elipsine
        # düşer. Bunu ayırt etmeden göstermek yanıltıcı olur — başarısız
        # ölçüm "0.000" diye gerçek bir değermiş gibi okunur. Bu yüzden
        # eşleme güvenilir değilse dönme "—" yazılır ve durum satırında
        # nedeni belirtilir.
        self._cmp_rows = [
            ("Dönme (°)",        lambda r: self._fmt_rotation(r)),
            # Tilt ayırt edilemiyorsa sayı yerine "< sınır" yazılır —
            # gürültünün altındaki değeri ölçüm gibi göstermemek için.
            ("Eğiklik (°)",      lambda r: self._fmt_tilt(r)),
            ("Eşleşen nokta",    lambda r: ("—" if r.match is None
                                            else str(r.match.num_inliers))),
            ("Hizalama h. (px)", lambda r: ("—" if r.match is None else
                                            self._fmt(r.match.reproj_error_px, 2))),
            ("Desen güveni",     lambda r: ("—" if r.star is None else
                                            self._fmt(r.star.det_ellipse.confidence, 2))),
            ("Eşleme durumu",    lambda r: self._match_state(r)),
        ]
        self._cmp_widgets = []
        for i, (label, _) in enumerate(self._cmp_rows, start=1):
            lb = QLabel(label)
            lb.setStyleSheet(f"color:{MUTED}; font-size:11px;")
            v_full, v_roi = QLabel("—"), QLabel("—")
            for v in (v_full, v_roi):
                v.setAlignment(Qt.AlignRight)
                v.setStyleSheet("font-family:monospace; font-size:11px;")
            cl.addWidget(lb, i, 0)
            cl.addWidget(v_full, i, 1)
            cl.addWidget(v_roi, i, 2)
            self._cmp_widgets.append((v_full, v_roi))

        self.lbl_cmp_note = QLabel("")
        self.lbl_cmp_note.setWordWrap(True)
        self.lbl_cmp_note.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        cl.addWidget(self.lbl_cmp_note, len(self._cmp_rows) + 1, 0, 1, 3)

        self.gb_cmp.setVisible(False)
        lay.addWidget(self.gb_cmp)

        # Uyarılar
        self.msg_label = QLabel("")
        self.msg_label.setWordWrap(True)
        self.msg_label.setStyleSheet(f"color:{WARN}; font-size:12px;")
        lay.addWidget(self.msg_label)

        lay.addStretch(1)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(320)
        return scroll

    # --------------------------- yardımcılar -------------------------------

    def _toggle_details(self, checked: bool):
        """'Ayrıntılar' bölümünü açar/kapatır."""
        self.details_box.setVisible(checked)
        self.btn_details.setText("▾ Ayrıntılar" if checked else "▸ Ayrıntılar")

    def _dspin(self, lo, hi, dec, suffix) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSuffix(suffix)
        s.setButtonSymbols(QDoubleSpinBox.NoButtons)
        return s

    def _ispin(self, lo, hi, suffix) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSuffix(suffix)
        s.setButtonSymbols(QSpinBox.NoButtons)
        return s

    def _grid_row(self, grid: QGridLayout, row: int, label: str,
                  widget: QWidget, tip: str = ""):
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{MUTED};")
        if tip:
            lbl.setToolTip(tip)
            widget.setToolTip(tip)
        grid.addWidget(lbl, row, 0)
        grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)

    def _update_sensor_label(self):
        w = self.f_det_w.value() * self.f_pitch_x.value() / 1000.0
        h = self.f_det_h.value() * self.f_pitch_y.value() / 1000.0
        diag = (w * w + h * h) ** 0.5
        self.lbl_sensor.setText(
            f"Sensör alanı: {w:.2f} × {h:.2f} mm  (köşegen {diag:.2f} mm)")

    # ------------------------ config <-> alanlar ---------------------------

    # --------------------- donanım kataloğu seçicileri ---------------------

    def _apply_lens_preset(self):
        """Açılır listeden lens seçildi — alanları doldur."""
        key = self.f_lens_sel.currentData()
        item = cfgmod.lens_from_catalog(key)
        if item is None:            # "Özel" seçildi: alanlara dokunma
            return
        self.f_lens_name.setText(item.name)
        self.f_focal.setValue(item.focal_length_mm)
        self.f_fnum.setValue(item.f_number)
        self.f_pupil.setValue(item.pupil_diameter_mm)
        self.f_ufov.setValue(item.useful_fov_deg)
        self._set_projection(item.projection)
        self._sync_catalog_selectors()

    def _apply_detector_preset(self):
        """Açılır listeden dedektör seçildi — alanları doldur."""
        key = self.f_det_sel.currentData()
        item = cfgmod.detector_from_catalog(key)
        if item is None:
            return
        self.f_det_name.setText(item.name)
        self.f_det_w.setValue(item.width_px)
        self.f_det_h.setValue(item.height_px)
        self.f_pitch_x.setValue(item.pixel_pitch_um)
        self.f_pitch_y.setValue(item.pixel_pitch_y_um)
        self._sync_catalog_selectors()

    def _apply_screen_preset(self):
        """Açılır listeden referans ekran seçildi — alanları doldur."""
        key = self.f_scr_sel.currentData()
        item = cfgmod.screen_from_catalog(key)
        if item is None:            # "Özel": alanlara dokunma
            return
        self.f_oled_name.setText(item.name)
        self.f_oled_w.setValue(item.width_px)
        self.f_oled_h.setValue(item.height_px)
        self.f_oled_pitch.setValue(item.pixel_pitch_um)
        self.f_oled_aw.setValue(item.active_width_mm)
        self.f_oled_ah.setValue(item.active_height_mm)
        self.f_scr_ang.setValue(item.angular_res_deg)
        self._update_screen_label()
        self._sync_catalog_selectors()

    def _set_projection(self, model: str):
        """
        Projeksiyon seçicisini ayarlar ve bilgi satırını tazeler.

        Bilinmeyen bir model gelirse (elle düzenlenmiş preset) rektilineere
        düşülür — seçicide boş/geçersiz bir kalem bırakmaktansa projenin
        doğrulanmış varsayılanını göstermek doğru davranış.
        """
        idx = self.f_proj.findData(model)
        if idx < 0:
            idx = max(0, self.f_proj.findData(projmod.RECTILINEAR))
        self.f_proj.blockSignals(True)
        self.f_proj.setCurrentIndex(idx)
        self.f_proj.blockSignals(False)
        self._update_projection_label()

    def _update_projection_label(self):
        """
        Seçili projeksiyon modelinin verdiği FOV'u ve diğer modellerle
        farkını canlı gösterir.

        Neden diğer modeller de yazılıyor: "FOV yanlış çıkıyor" şüphesinde
        ilk soru "fark modelden mi gelebilir" olmalı. Yayılım küçükse sorun
        modelde DEĞİLDİR ve başka yere bakmak gerekir — bu satır o ayrımı
        tek bakışta yaptırır.
        """
        model = self.f_proj.currentData()
        f = self.f_focal.value()
        w_mm = self.f_det_w.value() * self.f_pitch_x.value() / 1000.0
        h_mm = self.f_det_h.value() * self.f_pitch_y.value() / 1000.0
        if f <= 0 or w_mm <= 0:
            self.lbl_proj.setText("—")
            return
        fov_x = projmod.full_fov_deg(model, f, w_mm)
        fov_y = projmod.full_fov_deg(model, f, h_mm)
        diag = projmod.full_fov_deg(model, f, math.hypot(w_mm, h_mm))
        if not math.isfinite(fov_x):
            self.lbl_proj.setText(
                "Bu sensör ölçüsü seçili modelin tanım aralığı dışında — "
                "FOV hesaplanamıyor.")
            return
        txt = (f"FOV {fov_x:.3f}° × {fov_y:.3f}°  ·  köşegen {diag:.3f}°")

        # Model yayılımı: aynı donanımda diğer modeller ne verirdi.
        hepsi = [v for _, v in projmod.compare_models(f, w_mm)
                 if math.isfinite(v)]
        if len(hepsi) >= 2:
            yayilim = max(hepsi) - min(hepsi)
            txt += (f"  ·  model yayılımı {min(hepsi):.3f}–{max(hepsi):.3f}° "
                    f"({yayilim:.3f}°)")

        # Üretici FOV'u ile karşılaştırma: hesaplanan tam-sensör FOV'unun
        # üreticinin "useful FOV"undan büyük çıkması BEKLENEN yöndür (köşe
        # kalitesi düştüğü için useful dar tanımlanır). Ters yön şüphelidir.
        ufov = self.f_ufov.value()
        if ufov > 0:
            fark = (fov_x - ufov) / ufov * 100.0
            yon = "hesaplanan büyük (beklenen yön)" if fark > 0 \
                else "DİKKAT: hesaplanan üretici FOV'undan DAR"
            txt += f"  ·  üretici {ufov:.2f}° → %{abs(fark):.2f} {yon}"
        self.lbl_proj.setText(txt)

    def _update_screen_label(self):
        """
        Referans ekranın açısal kapsamasını canlı gösterir.

        Açısal kaynakta (STOS) üreticinin verdiği derece/piksel bir ODAK
        UZAKLIĞI ima eder: f = pitch / tan(açısal_çözünürlük). Paternin
        açısal ölçeği buna dayanır, o yüzden değer ekranda görünmeli.
        """
        scr = cfgmod.RefScreen(
            width_px=self.f_oled_w.value(),
            height_px=self.f_oled_h.value(),
            pixel_pitch_um=self.f_oled_pitch.value(),
            angular_res_deg=self.f_scr_ang.value())
        if not scr.is_angular_source:
            self.lbl_screen.setText(
                "Pasif panel — kendi açısal ölçeği yok "
                "(açısal çözünürlük 0).")
            return
        hx = scr.half_angle_deg(scr.width_px / 2.0)
        hy = scr.half_angle_deg(scr.height_px / 2.0)
        txt = (f"Açısal kaynak: ima edilen f = {scr.implied_focal_mm:.2f} mm  ·  "
               f"panel kapsaması ±{hx:.2f}° × ±{hy:.2f}°")
        # Cihazın FOV'u biliniyorsa panelin onu taşıyıp taşımadığı da yazılır —
        # taşımıyorsa desenin kenarları hiç görüntülenemez.
        half_fov = self.f_ufov.value() / 2.0
        if half_fov > 0:
            r = scr.radius_px_for_angle(half_fov)
            durum = "panel FOV'u taşıyor" if min(hx, hy) >= half_fov \
                else "DİKKAT: panel cihaz FOV'undan dar"
            txt += f"  ·  cihaz yarı-FOV {half_fov:.2f}° → r={r:.0f} px ({durum})"
        self.lbl_screen.setText(txt)

    def _apply_system_preset(self):
        """Hazır sistem seçildi — lens + dedektörü birlikte doldur."""
        key = self.f_system.currentData()
        if key not in cfgmod.SYSTEM_PRESETS:
            return
        cfg = cfgmod.system_from_preset(key)
        # OLED ve düzenek korunur; hazır sistem yalnızca optik zinciri tanımlar.
        self.f_lens_name.setText(cfg.lens.name)
        self.f_focal.setValue(cfg.lens.focal_length_mm)
        self.f_fnum.setValue(cfg.lens.f_number)
        self.f_pupil.setValue(cfg.lens.pupil_diameter_mm)
        self.f_ufov.setValue(cfg.lens.useful_fov_deg)
        self._set_projection(cfg.lens.projection)
        self.f_det_name.setText(cfg.detector.name)
        self.f_det_w.setValue(cfg.detector.width_px)
        self.f_det_h.setValue(cfg.detector.height_px)
        self.f_pitch_x.setValue(cfg.detector.pixel_pitch_um)
        self.f_pitch_y.setValue(cfg.detector.pixel_pitch_y_um)
        # Referans ekran da sistemin parçası: STOS ile OLED farklı açısal
        # ölçek tanımlar, sistem değişince ekran da değişmeli.
        self.f_oled_name.setText(cfg.oled.name)
        self.f_oled_w.setValue(cfg.oled.width_px)
        self.f_oled_h.setValue(cfg.oled.height_px)
        self.f_oled_pitch.setValue(cfg.oled.pixel_pitch_um)
        self.f_oled_aw.setValue(cfg.oled.active_width_mm)
        self.f_oled_ah.setValue(cfg.oled.active_height_mm)
        self.f_scr_ang.setValue(cfg.oled.angular_res_deg)
        self._update_screen_label()
        self._sync_catalog_selectors()

    def _sync_catalog_selectors(self):
        """
        Seçicileri alanlardaki GERÇEK değerlere göre günceller.

        Tek doğruluk kaynağı alanlardır: kullanıcı bir değeri elle
        değiştirdiğinde seçici kendiliğinden "Özel"e döner. Aksi halde
        açılır listede "Hydra" yazarken alanlarda başka bir sistem
        durabilir — panel ile tablo arasındaki eski ayrışmanın aynısı.
        """
        lens = Lens(focal_length_mm=self.f_focal.value(),
                    f_number=self.f_fnum.value(),
                    pupil_diameter_mm=self.f_pupil.value())
        det = Detector(width_px=self.f_det_w.value(),
                       height_px=self.f_det_h.value(),
                       pixel_pitch_um=self.f_pitch_x.value(),
                       pixel_pitch_y_um=self.f_pitch_y.value())
        scr = cfgmod.RefScreen(width_px=self.f_oled_w.value(),
                               height_px=self.f_oled_h.value(),
                               pixel_pitch_um=self.f_oled_pitch.value(),
                               angular_res_deg=self.f_scr_ang.value())
        lkey = cfgmod.match_lens_key(lens)
        dkey = cfgmod.match_detector_key(det)
        skey = cfgmod.match_screen_key(scr)

        for combo, key in ((self.f_lens_sel, lkey), (self.f_det_sel, dkey),
                           (self.f_scr_sel, skey)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(key)))
            combo.blockSignals(False)

        # Hazır sistem yalnızca İKİSİ de eşleşiyorsa o sistemi gösterir.
        syskey = cfgmod.CUSTOM
        for name, (lk, dk, sk) in cfgmod.SYSTEM_PRESETS.items():
            if lk == lkey and dk == dkey and sk == skey:
                syskey = name
                break
        self.f_system.blockSignals(True)
        self.f_system.setCurrentIndex(max(0, self.f_system.findData(syskey)))
        self.f_system.blockSignals(False)

    # ------------------------ config <-> alanlar ---------------------------

    def _load_config_into_fields(self, cfg: SystemConfig):
        self.f_lens_name.setText(cfg.lens.name)
        self.f_focal.setValue(cfg.lens.focal_length_mm)
        self.f_fnum.setValue(cfg.lens.f_number)
        self.f_pupil.setValue(cfg.lens.pupil_diameter_mm)
        self.f_ufov.setValue(cfg.lens.useful_fov_deg)
        self._set_projection(getattr(cfg.lens, "projection",
                                     projmod.RECTILINEAR))

        self.f_det_name.setText(cfg.detector.name)
        self.f_det_w.setValue(cfg.detector.width_px)
        self.f_det_h.setValue(cfg.detector.height_px)
        self.f_pitch_x.setValue(cfg.detector.pixel_pitch_um)
        self.f_pitch_y.setValue(cfg.detector.pixel_pitch_y_um)

        self.f_oled_name.setText(cfg.oled.name)
        self.f_oled_w.setValue(cfg.oled.width_px)
        self.f_oled_h.setValue(cfg.oled.height_px)
        self.f_oled_pitch.setValue(cfg.oled.pixel_pitch_um)
        self.f_oled_aw.setValue(cfg.oled.active_width_mm)
        self.f_oled_ah.setValue(cfg.oled.active_height_mm)
        self.f_scr_ang.setValue(getattr(cfg.oled, "angular_res_deg", 0.0))
        self._update_screen_label()

        idx = self.f_setup.findData(cfg.setup_type)
        self.f_setup.setCurrentIndex(max(0, idx))
        self.f_coll_f.setValue(cfg.collimator_focal_length_mm)
        self._update_sensor_label()
        self._sync_catalog_selectors()

    def _config_from_fields(self) -> SystemConfig:
        return SystemConfig(
            name="Arayüzden düzenlenmiş sistem",
            setup_type=self.f_setup.currentData(),
            collimator_focal_length_mm=self.f_coll_f.value(),
            lens=Lens(
                name=self.f_lens_name.text(),
                focal_length_mm=self.f_focal.value(),
                f_number=self.f_fnum.value(),
                pupil_diameter_mm=self.f_pupil.value(),
                useful_fov_deg=self.f_ufov.value(),
                projection=self.f_proj.currentData(),
            ),
            detector=Detector(
                name=self.f_det_name.text(),
                width_px=self.f_det_w.value(),
                height_px=self.f_det_h.value(),
                pixel_pitch_um=self.f_pitch_x.value(),
                pixel_pitch_y_um=self.f_pitch_y.value(),
            ),
            oled=OLED(
                name=self.f_oled_name.text(),
                width_px=self.f_oled_w.value(),
                height_px=self.f_oled_h.value(),
                pixel_pitch_um=self.f_oled_pitch.value(),
                active_width_mm=self.f_oled_aw.value(),
                active_height_mm=self.f_oled_ah.value(),
                angular_res_deg=self.f_scr_ang.value(),
            ),
        )

    # ---------------------------- eylemler ---------------------------------

    _IMG_FILTER = ("Görüntüler (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
                   "Tüm dosyalar (*)")

    # ------------------------- kırpma / zoom (ROI) -------------------------

    def _roi_view(self, src: str | None = None) -> ImageView:
        """Seçili ROI kaynağına karşılık gelen görüntü paneli."""
        if src is None:
            src = self.f_roi_src.currentData()
        return self.view_gt if src == "gt" else self.view_det

    def _roi_rect(self) -> tuple[int, int, int, int] | None:
        """
        Girilen ölçü ve merkezden ROI dikdörtgenini üretir (x, y, w, h).

        Ölçü 0 (girilmemiş) ya da merkez henüz seçilmemişse None döner —
        kırpma kapalıdır. Ölçü görüntüden büyükse görüntüye kısılır, merkez
        kenara dayanınca içeri kaydırılır.
        """
        iw, ih = self._roi_view().image_size()
        if iw == 0 or ih == 0:
            return None
        w, h = self.f_roi_w.value(), self.f_roi_h.value()
        if w <= 0 or h <= 0 or self._roi_cx is None or self._roi_cy is None:
            return None
        w, h = min(w, iw), min(h, ih)
        x = max(0, min(int(self._roi_cx - w / 2), iw - w))
        y = max(0, min(int(self._roi_cy - h / 2), ih - h))
        return (x, y, w, h)

    def _roi_changed(self):
        """Ölçü/merkez değişti — dikdörtgeni ve kırpma önizlemesini tazele."""
        src = self.f_roi_src.currentData()
        # Dikdörtgen yalnızca seçili kaynakta görünsün.
        self._roi_view("gt" if src == "det" else "det").set_roi(None)

        rect = self._roi_rect()
        self._roi_view().set_roi(rect)

        if rect is None:
            self.view_crop.clear_image()
            if self._roi_view().image_size()[0] == 0:
                self.lbl_roi_info.setText("Önce bu kaynağın görüntüsünü seçin.")
            else:
                self.lbl_roi_info.setText("Ölçü girin, sonra konumu seçin.")
            return

        crop = self._roi_crop(src, rect)
        if crop is None:
            self.lbl_roi_info.setText("Görüntü okunamadı.")
            self.view_crop.clear_image()
            return

        self.view_crop.set_image(crop)
        x, y, w, h = rect
        self.lbl_roi_info.setText(f"{w}×{h} px · konum ({x}, {y})")

    def _roi_crop(self, src: str, rect: tuple[int, int, int, int]):
        """
        ROI'yi *ham* dosyadan keser.

        Panelde gösterilen görüntü analiz sonrası elips çizimi içerebiliyor;
        kırpma incelemesinde o çizimi değil gerçek piksel verisini görmek
        gerekir. Bu yüzden önizleme değil, dosya yeniden okunuyor.
        """
        import cv2
        path = self.gt_path if src == "gt" else self.det_path
        if not path:
            return None
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        x, y, w, h = rect
        ih, iw = img.shape[:2]
        # Panel görüntüsü ile dosya boyutu birebir; yine de sınırları kırp.
        x2, y2 = min(x + w, iw), min(y + h, ih)
        if x >= x2 or y >= y2:
            return None
        return img[y:y2, x:x2]

    def _roi_click(self, src: str, x: int, y: int):
        """Görüntüye tıklanınca ROI merkezini oraya taşı ve alanları doldur."""
        if self.f_roi_src.currentData() != src:
            return
        self._roi_cx, self._roi_cy = x, y
        # Alanları sinyal tetiklemeden güncelle — yoksa _roi_center_edited
        # geri çağrılıp aynı işi tekrarlar.
        for wdg, val in ((self.f_roi_cx, x), (self.f_roi_cy, y)):
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)
        self._roi_changed()

    def _roi_center_edited(self):
        """Merkez alanına elle yazıldı — dikdörtgeni oraya taşı."""
        self._roi_cx = self.f_roi_cx.value()
        self._roi_cy = self.f_roi_cy.value()
        self._roi_changed()

    def _roi_center(self):
        """ROI'yi görüntünün ortasına taşır."""
        iw, ih = self._roi_view().image_size()
        if iw == 0:
            return
        self._roi_click(self.f_roi_src.currentData(), iw // 2, ih // 2)

    def _roi_reset_for(self, src: str):
        """
        Ölçü ve konum alanlarının üst sınırlarını `src` görüntüsüne göre kurar
        ve seçili konumu sıfırlar.

        Üst sınır görüntü boyutuna çekilince kullanıcı görüntü dışında bir
        alan ya da konum giremez. Konum sıfırlanır: kırpma, ölçü ve konum
        girilene kadar kapalı kalır.
        """
        iw, ih = self._roi_view(src).image_size()
        if iw and ih:
            self.f_roi_w.setMaximum(iw)
            self.f_roi_h.setMaximum(ih)
            self.f_roi_cx.setMaximum(iw - 1)
            self.f_roi_cy.setMaximum(ih - 1)
        self._roi_cx = self._roi_cy = None
        for wdg in (self.f_roi_cx, self.f_roi_cy):
            wdg.blockSignals(True)
            wdg.setValue(0)
            wdg.blockSignals(False)
        self._roi_changed()

    def _roi_on_image_loaded(self, src: str):
        """Yeni görüntü yüklendi — o kaynak seçiliyse ROI'yi ona göre kur."""
        if self.f_roi_src.currentData() == src:
            self._roi_reset_for(src)

    def _roi_source_changed(self):
        """Kaynak değişti — ROI'yi yeni kaynağa göre kur."""
        self._roi_reset_for(self.f_roi_src.currentData())

    def _pick_gt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ground truth görüntüsünü seçin", os.path.expanduser("~"),
            self._IMG_FILTER)
        if path:
            self.gt_path = path
            self.gt_label.setText(os.path.basename(path))
            self.gt_label.setToolTip(path)
            import cv2
            self.view_gt.set_image(cv2.imread(path, cv2.IMREAD_GRAYSCALE))
            self.tabs.setCurrentIndex(0)
            self._roi_on_image_loaded("gt")

    def _pick_det(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Dedektör görüntüsünü seçin", os.path.expanduser("~"),
            self._IMG_FILTER)
        if path:
            self.det_path = path
            self.det_label.setText(os.path.basename(path))
            self.det_label.setToolTip(path)
            import cv2
            self.view_det.set_image(cv2.imread(path, cv2.IMREAD_GRAYSCALE))
            self.tabs.setCurrentIndex(1)
            self._roi_on_image_loaded("det")

    def _save_preset(self):
        os.makedirs(PRESET_DIR, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "Preset kaydet", os.path.join(PRESET_DIR, "preset.json"),
            "JSON (*.json)")
        if not path:
            return
        try:
            self._config_from_fields().save(path)
            self.status_label.setText(f"Preset kaydedildi: {os.path.basename(path)}")
        except Exception as e:                          # noqa: BLE001
            QMessageBox.critical(self, "Preset kaydedilemedi", str(e))

    def _load_preset(self):
        os.makedirs(PRESET_DIR, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Preset yükle", PRESET_DIR, "JSON (*.json)")
        if not path:
            return
        try:
            self._load_config_into_fields(SystemConfig.load(path))
            self.status_label.setText(f"Preset yüklendi: {os.path.basename(path)}")
        except Exception as e:                          # noqa: BLE001
            QMessageBox.critical(self, "Preset yüklenemedi", str(e))

    def _run_analysis(self):
        if not self.gt_path or not self.det_path:
            QMessageBox.warning(
                self, "Görüntü eksik",
                "Lütfen hem ground truth hem de dedektör görüntüsünü seçin.")
            return

        cfg = self._config_from_fields()
        errs = cfg.validate()
        if errs:
            QMessageBox.warning(self, "Parametre hatası", "\n".join(errs))
            return

        self.btn_run.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.msg_label.setText("")
        self._clear_results()

        # ROI seçiliyse analiz iki kez koşar: tam kare + kırpılan bölge.
        roi = self._roi_rect()
        self.worker = AnalysisWorker(self.gt_path, self.det_path, cfg,
                                     roi=roi,
                                     roi_src=self.f_roi_src.currentData())
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress.setValue(pct)
        self.status_label.setText(msg)

    def _on_failed(self, msg: str):
        self.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        self.status_label.setText("Analiz başarısız.")
        QMessageBox.critical(self, "Analiz hatası", msg)

    def _on_finished(self, res, roi_res=None):
        self.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        self.result = res
        self.roi_result = roi_res
        self._show_results(res)
        self._show_comparison(res, roi_res)

    # ---------------------------- sonuç gösterimi --------------------------

    @staticmethod
    def _match_ok(res) -> bool:
        """
        Bu koşunun homografisi kullanılabilir mi?

        Dejenere homografi reddedildiğinde `rotation_deg` sessizce yıldız
        elipsine düşer; o sayı bir ölçüm değil yedektir. Karşılaştırmada
        ayırt edilmesi gerekir.
        """
        m = getattr(res, "match", None)
        return bool(m is not None and m.homography is not None
                    and not getattr(m, "degenerate", False))

    @staticmethod
    def _match_state(res) -> str:
        """
        Eşlemenin durumunu tek kelimeyle özetler.

        Dejenere kontrolü önce yapılır: dejenere durumda `homography` zaten
        None'a çekilir (bkz. image_analysis.analyze), o yüzden None kontrolü
        önce gelirse gerçek neden gizlenir ve "eşleşmedi" gibi yanlış bir
        teşhis görünür — oysa eşleşme bulunmuş, güvenilmez olduğu için
        reddedilmiştir.
        """
        m = getattr(res, "match", None)
        if m is None:
            return "eşleşmedi"
        if getattr(m, "degenerate", False):
            return "dejenere"
        if m.homography is None:
            return "eşleşmedi"
        return "sağlam"

    @classmethod
    def _fmt_rotation(cls, res, missing: str = "—") -> str:
        """
        Düzlem-içi dönmeyi panel ve tablo için AYNI kuralla yazar.

        Homografi reddedildiğinde `rotation_deg` sessizce yıldız elipsinin
        eksen açısı farkına düşer (bkz. pipeline.AnalysisResult.rotation_deg).
        Yıldız neredeyse daire olduğunda o eksen açısı tanımsıza yakındır —
        çıkan sayı ölçüm değil gürültüdür ve dejenere kırpmada "0.000" gibi
        inandırıcı görünür.

        Karar tek yerde verilir: hem sonuç paneli hem karşılaştırma tablosu
        bunu çağırır. Aksi halde aynı ölçüm iki yerde farklı görünür — panel
        yedek sayıyı yazarken tablo "—" gösterir. Yalnızca eksik değerin
        yazılışı çağırana bırakılır (`missing`), kararın kendisi değil.
        """
        if not cls._match_ok(res):
            return missing
        return cls._fmt(res.rotation_deg, 3)

    @classmethod
    def _fmt_tilt(cls, res) -> str:
        """
        Eğikliği "Eğiklik (Tilt)" paneliyle AYNI kuralla yazar.

        Sınır değeri `sigma_deg`'in kendisidir (1-sigma) — panelde
        `< {sigma:.1f}` biçiminde gösteriliyor. Burada başka bir çarpan
        kullanmak (örn. 2σ) aynı ölçümün iki yerde farklı görünmesine yol
        açar; tek doğruluk kaynağı `res.tilt` raporudur.
        """
        rep = getattr(res, "tilt", None)
        if rep is None or not rep.ok:
            return "ölçülemedi"
        if not rep.resolvable:
            return f"< {rep.sigma_deg:.1f}"
        return f"{rep.tilt_deg:.3f}"

    @staticmethod
    def _fmt(val, dec: int) -> str:
        """Sayıyı sabit ondalıkla yazar; NaN/None ise '—'."""
        try:
            f = float(val)
        except (TypeError, ValueError):
            return "—"
        if f != f:                                  # NaN
            return "—"
        return f"{f:.{dec}f}"

    def _show_comparison(self, full, roi_res):
        """Tam kare ve kırpılan bölge sonuçlarını yan yana yazar."""
        if roi_res is None:
            self.gb_cmp.setVisible(False)
            return

        for (v_full, v_roi), (_, getter) in zip(self._cmp_widgets,
                                                self._cmp_rows):
            try:
                a = getter(full)
            except Exception:                       # noqa: BLE001
                a = "—"
            try:
                b = getter(roi_res)
            except Exception:                       # noqa: BLE001
                b = "—"
            v_full.setText(a)
            v_roi.setText(b)
            # Değerler ayrışıyorsa kırpma sütununu vurgula — bölgesel fark
            # tam da bakılmak istenen şey.
            farkli = a != b and "—" not in (a, b)
            v_roi.setStyleSheet(
                "font-family:monospace; font-size:11px;"
                + (f"color:{WARN};" if farkli else ""))

        x, y, w, h = roi_res.roi_rect
        kaynak = "ground truth" if roi_res.roi_src == "gt" else "dedektör"
        satirlar = [f"Kırpma: {kaynak} üzerinde {w}×{h} px @ ({x}, {y})."]

        # Kırpma ölçümü güvenilir değilse bunu açıkça söyle. Aksi halde
        # yedeğe düşmüş bir sayı gerçek bölgesel farkmış gibi okunur.
        if not self._match_ok(roi_res):
            durum = self._match_state(roi_res)
            if durum == "dejenere":
                satirlar.append(
                    "⚠ Kırpılan bölgede eşleme DEJENERE — bu alan kendine "
                    "benzer (Siemens star'ın radyal deseni) olduğu için "
                    "sahte eşleşmeler üretiyor. Dönme ölçülemedi. Daha geniş "
                    "ya da desen çeşitliliği olan bir bölge seçin.")
            else:
                satirlar.append(
                    "⚠ Kırpılan bölgede yeterli eşleşme bulunamadı — bölge "
                    "çok küçük ya da ayırt edici desen içermiyor olabilir.")
        satirlar.append(
            "FOV ve IFOV karşılaştırmaya dahil değil — onlar görüntüden "
            "değil lens/dedektör parametrelerinden hesaplanır, kırpmayla "
            "değişmez.")

        self.lbl_cmp_note.setText("\n".join(satirlar))
        self.lbl_cmp_note.setStyleSheet(
            f"color:{WARN if not self._match_ok(roi_res) else MUTED}; "
            f"font-size:11px;")
        self.gb_cmp.setVisible(True)

    def _clear_results(self):
        for r in (self.r_fov_xy, self.r_fov_d,
                  self.r_ifov, self.r_ifov_as,
                  self.r_rot, self.r_tilt,
                  self.r_sensor, self.r_tilt_method, self.r_el_conf,
                  self.r_mirror, self.r_inliers, self.r_reproj,
                  self.r_decenter, self.r_decenter_px, self.r_roll, self.r_ptilt,
                  self.r_cov_pattern, self.r_cov_sensor, self.r_cov_maxang,
                  self.r_cov_edges, self.r_cov_margin,
                  self.r_ang_res, self.r_ifov_edge,
                  self.r_fov_model, self.r_fov_check,
                  self.r_fov_eff, self.r_fov_circle):
            r.clear()
        self.lbl_tilt_note.setText("")
        self.lbl_point_note.setText("")
        self.lbl_verdict.setText("—")
        self.lbl_verdict.setStyleSheet(f"color:{MUTED}; font-size:13px;")
        self.gb_cmp.setVisible(False)
        for v_full, v_roi in self._cmp_widgets:
            v_full.setText("—")
            v_roi.setText("—")

    def _solver_sources(self):
        """
        Panelde gösterilen büyüklüklerin kaynağını çözücüden alır.

        Dönen: {düğüm_adı: (kind, açıklama)} — `kind` "given" ya da
        "derived", açıklama da rozetin ipucu metni (türetim zinciri).

        Neden çözücüden: hangi sayının datasheet'ten okunduğu, hangisinin
        hesaplandığı TEK YERDE bilinmeli. Panel kendi başına "bu türetilmiş"
        diye karar verseydi, çözücüyle ayrışan ikinci bir doğruluk kaynağı
        doğardı — §5'teki panel↔tablo ayrışmasının aynısı.
        """
        try:
            cfg = self._config_from_fields()
            r = solver.solve_config(cfg)
        except Exception:
            return {}
        out = {}
        for node, v in r.values.items():
            kind = "given" if v.is_given else "derived"
            # İpucu metni çözücünün `describe`'ından gelir: hangi
            # değerlerden, hangi bağıntıyla, ve gerekiyorsa tam zincir.
            out[node] = (kind, r.describe(node))
        return out

    def _show_results(self, res):
        # Kaynak rozetleri: hangi sayı datasheet'ten, hangisi türetildi.
        src = self._solver_sources()

        def rozet(row, node):
            kind_detail = src.get(node)
            row.set_source(*kind_detail) if kind_detail else row.set_source(None)

        # ---- 1) FOV — sensörün gördüğü toplam açı ----
        if res.fov is not None:
            f = res.fov
            self.r_fov_xy.set_value(f"{f.fov_x_deg:.3f} × {f.fov_y_deg:.3f}")
            self.r_fov_d.set_value(f"{f.fov_diag_deg:.3f}")
            self.r_sensor.set_value(f"{f.sensor_w_mm:.2f} × {f.sensor_h_mm:.2f}")
            rozet(self.r_fov_xy, "fov_x_deg")
            rozet(self.r_fov_d, "fov_diag_deg")
            rozet(self.r_sensor, "det_w_mm")

            # --- Görüntü dairesi kısıtı ---
            # Daire sensörü kapsamıyorsa yukarıdaki iki satır GEOMETRİK
            # değerdir: "bu piksel eksenden şu kadar uzakta, demek ki şu
            # açıyı görür". Lens oraya ışık düşürmüyorsa o açıdan görüntü
            # GELMEZ. Ayrımı göstermezsek panel köşegen için 30.56° yazar
            # ve kullanıcı bunu gerçek FOV sanar.
            kapsiyor = getattr(f, "covers_sensor", True)
            daire = getattr(f, "image_circle_mm", float("nan"))
            if not kapsiyor and math.isfinite(f.eff_fov_diag_deg):
                self.r_fov_eff.setVisible(True)
                self.r_fov_circle.setVisible(True)
                self.r_fov_eff.set_value(
                    f"{f.eff_fov_x_deg:.3f} × {f.eff_fov_y_deg:.3f}"
                    f"  ·  köş {f.eff_fov_diag_deg:.3f}", GOOD)
                self.r_fov_eff.set_source(
                    "derived",
                    "Lensin görüntü dairesiyle kırpıldıktan sonra sensörde "
                    "gerçekten görüntü olan alan.\n\n"
                    f"Daire çapı {daire:.3f} mm, sensör köşegeni "
                    f"{math.hypot(f.sensor_w_mm, f.sensor_h_mm):.3f} mm — "
                    "köşeler dairenin DIŞINDA, orası karanlık.\n\n"
                    f"Yukarıdaki {f.fov_diag_deg:.3f}° köşegen, o köşe "
                    "pikselinin GEOMETRİK olarak göreceği açıdır; lens "
                    "oraya görüntü düşürmediği için gerçek değildir.")
                self.r_fov_circle.set_value(f"{daire:.3f}", WARN)
                self.r_fov_circle.set_source(
                    "derived",
                    "Lensin ürettiği dairesel görüntünün çapı.\n\n"
                    "Üreticinin kullanılabilir FOV değerinden türetildi:\n"
                    "   çap = 2 · f · tan(useful_FOV / 2)\n\n"
                    "Sensör köşegeninden küçük olduğu için köşeler "
                    "karanlıktır (vignetting).")
                # Geometrik satırların gerçek olmadığı görünsün.
                self.r_fov_xy.set_value(
                    f"{f.fov_x_deg:.3f} × {f.fov_y_deg:.3f}", MUTED)
                self.r_fov_d.set_value(f"{f.fov_diag_deg:.3f}", MUTED)
                self.r_fov_xy._label.setText("Geometrik Y × D")
                self.r_fov_d._label.setText("Geometrik köşegen")
            else:
                # Gizlemek YETMEZ: satır eski koşunun değerini tutmaya devam
                # eder ve bir sonraki sistemde yanlış sayı taşır. Gizlerken
                # temizle.
                self.r_fov_eff.clear()
                self.r_fov_circle.clear()
                self.r_fov_eff.setVisible(False)
                self.r_fov_circle.setVisible(False)
                self.r_fov_xy._label.setText("Yatay × Dikey")
                self.r_fov_d._label.setText("Köşegen")

            # Projeksiyon modeli — sonucun ayrılmaz parçası.
            model = getattr(f, "projection", projmod.RECTILINEAR)
            self.r_fov_model.set_value(
                projmod.MODEL_LABELS.get(model, model).split(" —")[0])
            yayilim = [v for _, v in projmod.compare_models(
                self.f_focal.value(), f.sensor_w_mm) if math.isfinite(v)]
            if len(yayilim) >= 2:
                self.r_fov_model.set_source(
                    "given",
                    "Sol panelden seçilen lens projeksiyon modeli.\n\n"
                    f"Aynı donanımda diğer modeller {min(yayilim):.3f}° – "
                    f"{max(yayilim):.3f}° arası verirdi "
                    f"(yayılım {max(yayilim)-min(yayilim):.3f}°).\n"
                    "Yayılım küçükse 'FOV yanlış' şüphesinin sebebi model "
                    "DEĞİLDİR.")

            # Üretici FOV karşılaştırması — bağımsız doğrulama.
            ufov = self.f_ufov.value()
            if ufov > 0:
                # Karşılaştırma GERÇEKTE GÖRÜLEN değerle yapılır. Görüntü
                # dairesi üreticinin useful FOV'undan türetildiyse ikisi
                # zaten birebir tutar — anlamlı olan, kırpma öncesi
                # geometrik değerin ne kadar taştığıdır.
                kars = (f.eff_fov_x_deg if math.isfinite(f.eff_fov_x_deg)
                        else f.fov_x_deg)
                fark = (kars - ufov) / ufov * 100.0
                if fark >= 0:
                    self.r_fov_check.set_value(
                        f"{ufov:.2f}° → %{fark:+.2f}", GOOD)
                    aciklama = ("Hesaplanan FOV üreticinin useful FOV'undan "
                                "büyük — BEKLENEN yön.")
                else:
                    self.r_fov_check.set_value(
                        f"{ufov:.2f}° → %{fark:+.2f}", WARN)
                    aciklama = ("DİKKAT: hesaplanan FOV üreticinin verdiğinden "
                                "DAR. Odak uzaklığı, piksel pitch'i ya da "
                                "projeksiyon modeli gözden geçirilmeli.")
                self.r_fov_check.set_source("derived", aciklama)
            else:
                self.r_fov_check.clear()

            # ---- 2) IFOV — tek pikselin gördüğü açı ----
            # Piksel kare değilse iki eksen ayrı gösterilir.
            if abs(f.ifov_x_urad - f.ifov_y_urad) < 0.01:
                self.r_ifov.set_value(f"{f.ifov_x_urad:.2f}")
            else:
                self.r_ifov.set_value(
                    f"{f.ifov_x_urad:.2f} × {f.ifov_y_urad:.2f}")
            self.r_ifov_as.set_value(f"{f.ifov_x_arcsec:.3f}")
            rozet(self.r_ifov, "ifov_x_urad")
            rozet(self.r_ifov_as, "ifov_x_arcsec")

            # Açısal çözünürlük — aynı IFOV, datasheet'lerin kullandığı birimde.
            self.r_ang_res.set_value(f"{math.degrees(f.ifov_x_urad * 1e-6):.5f}")
            rozet(self.r_ang_res, "ifov_x_deg")

            # Kenar pikseli. Merkezden farkı yüzde olarak da yazılır: fark
            # büyükse tek bir IFOV sayısıyla tüm alanı temsil etmek yanıltıcıdır.
            if math.isfinite(f.ifov_edge_x_urad) and f.ifov_x_urad > 0:
                sapma = (f.ifov_edge_x_urad / f.ifov_x_urad - 1.0) * 100.0
                self.r_ifov_edge.set_value(
                    f"{f.ifov_edge_x_urad:.2f}  ({sapma:+.2f}%)",
                    GOOD if abs(sapma) < 1.0 else WARN)
                self.r_ifov_edge.set_source(
                    "derived",
                    "Kenar pikselinin gördüğü açı — merkez IFOV'undan "
                    f"%{abs(sapma):.2f} farklı.\n\n"
                    f"Projeksiyon modeli: {f.projection}.\n"
                    "Rektilineerde piksel ölçeği alan boyunca sabit değildir; "
                    "kenara doğru daralır.")
            else:
                self.r_ifov_edge.clear()

        # ---- 3) Tilt ----
        # Dönme, tabloyla AYNI süzgeçten geçer (_fmt_rotation): eşleme
        # dejenereyse gösterilen sayı ölçüm değil yıldız elipsinden gelen
        # yedektir. Eskiden panel bu yedeği "+0.000" diye gerçek bir değer
        # gibi yazarken tablo "—" gösteriyordu — aynı koşu iki yerde farklı
        # okunuyordu.
        rot = self._fmt_rotation(res, missing="ölçülemedi")
        if rot == "ölçülemedi":
            # SIFT bu desende çalışamadıysa satır boş kalır — ama aynı
            # büyüklük yoğun hizalamayla ÖLÇÜLMÜŞ olabilir. Kullanıcı
            # panelde iki dönme satırı görüp "neden ölçülemedi?" diye
            # sormasın diye, boş satır nereye bakılacağını söyler.
            p = getattr(res, "pointing", None)
            if p is not None and p.ok and p.roll_full_deg == p.roll_full_deg:
                self.r_rot.set_value("SIFT yok → Roll satırına bakın", WARN)
            else:
                self.r_rot.set_value(rot, BAD)
        elif rot != "—":                    # NaN değilse
            self.r_rot.set_value(f"{float(rot):+.3f}")

        self._show_tilt(res)

        # ---- Ayrıntılar (katlanır) ----
        if res.star is not None and res.star.ok:
            g, d = res.star.gt_ellipse, res.star.det_ellipse
            conf = min(g.confidence, d.confidence)
            ccol = GOOD if conf > 0.7 else (WARN if conf > 0.4 else BAD)
            self.r_el_conf.set_value(f"{conf:.2f}", ccol)

        if res.match is not None:
            m = res.match
            self.r_mirror.set_value("EVET" if m.mirrored else "hayır",
                                    WARN if m.mirrored else GOOD)
            self.r_inliers.set_value(f"{m.num_inliers}")
            if m.reproj_error_px == m.reproj_error_px:
                rcol = GOOD if m.reproj_error_px < 2.0 else WARN
                self.r_reproj.set_value(f"{m.reproj_error_px:.2f}", rcol)

        self._show_pointing(res)
        self._show_verdict(res)

    def _show_pointing(self, res):
        """
        Yönelim hatalarını ve FOV kapsamasını gösterir.

        Kaynak `core/pointing.py`; yoğun hizalamanın homografisinden türetilir.
        Homografi yoksa ya da yoğun yol koşmadıysa satırlar BOŞ bırakılır —
        yedek bir sayı yazılmaz (bkz. DEVAM_YONERGESI §5 "tek doğruluk kaynağı").
        """
        p = getattr(res, "pointing", None)
        if p is None or not p.ok:
            for r in (self.r_decenter, self.r_decenter_px, self.r_roll,
                      self.r_ptilt, self.r_cov_pattern, self.r_cov_sensor,
                      self.r_cov_maxang, self.r_cov_edges, self.r_cov_margin):
                r.set_value("ölçülemedi", MUTED)
            self.lbl_point_note.setText(
                "Yönelim hataları yoğun hizalamanın homografisinden türetilir; "
                "hizalama başarısız olduğu için hesaplanamadı.")
            return

        # --- Yönelim ---
        # Decenter için renk eşiği FOV'un kendisinden türetilir; sabit bir
        # derece değeri farklı donanımda anlamsız olurdu.
        half_fov = p.fov_x_deg / 2.0 if p.fov_x_deg == p.fov_x_deg else 0.0
        if half_fov > 0:
            frac = p.decenter_deg / half_fov
            dcol = GOOD if frac < 0.02 else (WARN if frac < 0.10 else BAD)
        else:
            dcol = MUTED
        self.r_decenter.set_value(f"{p.decenter_deg:.4f}", dcol)
        # Yönü YAZIYLA da söyle. Görüntü koordinatlarında y ekseni AŞAĞI
        # bakar; "y -9.6" tek başına okunduğunda yukarı mı aşağı mı olduğu
        # belli olmaz. Kullanıcı deseni gözle "sağa ve yukarı kaymış" diye
        # görüyorsa, panel de aynı dili konuşmalı.
        yon = []
        if abs(p.decenter_x_px) >= 0.5:
            yon.append("sağa" if p.decenter_x_px > 0 else "sola")
        if abs(p.decenter_y_px) >= 0.5:
            yon.append("aşağı" if p.decenter_y_px > 0 else "yukarı")
        yon_txt = (" · " + " ve ".join(yon)) if yon else ""
        self.r_decenter_px.set_value(
            f"{p.decenter_px:.2f}  (x {p.decenter_x_px:+.1f}, "
            f"y {p.decenter_y_px:+.1f}){yon_txt}")

        # GERÇEK yönelim gösterilir (0..360), ±90'a katlı değer değil.
        # Katlama 136°'yi 44°'ye düşürüyordu — kullanıcı tamamen farklı bir
        # yönelim okuyordu. Katlı değer parantez içinde referans olarak kalır.
        if p.roll_full_deg == p.roll_full_deg:
            self.r_roll.set_value(f"{p.roll_full_deg:.3f}")
        else:
            self.r_roll.set_value(f"{-p.roll_deg:+.3f}")
        self.r_ptilt.set_value(f"{p.tilt_x_deg:+.3f} / {p.tilt_y_deg:+.3f}")

        # --- Kapsama ---
        # Kapsama ORAN değil MİKTAR olarak yazılır: "kullanılan / toplam px".
        # Yüzde, bir bölgenin kaç piksel veri taşıdığını söylemiyordu; iki
        # farklı çözünürlükte aynı "%61.7" tamamen farklı ölçüm gücü demek.
        # Desen satırı GT'nin KENDİ pikselleriyle sayılır; dedektör uzayındaki
        # alan homografinin büyütmesini taşır ve toplam, GT görüntüsünden
        # büyük çıkardı (1280×1024 → 2.07 Mpx gibi).
        ccol = GOOD if p.pattern_fully_visible else WARN
        # Toplamın YANINDA kaynağın çözünürlüğü de yazılır — "1.310.720"
        # tek başına hangi görüntüden geldiğini söylemiyor; "(1280×1024)"
        # söylüyor ve satırın hangi uzayda sayıldığı tartışmasız oluyor.
        if p.visible_area_gt_px == p.visible_area_gt_px:
            self.r_cov_pattern.set_value(
                f"{fmt_px(p.visible_area_gt_px)} / "
                f"{fmt_px(p.pattern_area_gt_px)}  ({fmt_shape(p.gt_shape)})",
                ccol)
        if p.visible_area_px == p.visible_area_px:
            self.r_cov_sensor.set_value(
                f"{fmt_px(p.visible_area_px)} / {fmt_px(p.sensor_area_px)}"
                f"  ({fmt_shape(p.detector_shape)})")
        if p.max_angle_deg == p.max_angle_deg:
            self.r_cov_maxang.set_value(f"{p.max_angle_deg:.3f}")
        if p.edge_angles_deg:
            e = p.edge_angles_deg
            self.r_cov_edges.set_value(
                f"{e['sol']:.2f} / {e['sağ']:.2f} / "
                f"{e['üst']:.2f} / {e['alt']:.2f}")
        if p.margin_px == p.margin_px:
            mcol = GOOD if p.margin_px >= 0 else BAD
            self.r_cov_margin.set_value(
                f"{p.margin_px:+.0f}  ({p.margin_deg:+.2f}°)", mcol)
        else:
            self.r_cov_margin.set_value("desen yarıçapı girilmedi", MUTED)

        # --- Açıklama satırı ---
        notes = []
        if not p.pattern_fully_visible:
            notes.append("Desen sensöre sığmıyor — kenarlardan kırpılıyor.")
        e = p.edge_angles_deg or {}
        if e:
            yatay = (e.get("sol", 0) + e.get("sağ", 0)) / 2.0
            dikey = (e.get("üst", 0) + e.get("alt", 0)) / 2.0
            if max(yatay, dikey) > 0 and min(yatay, dikey) / max(yatay, dikey) < 0.5:
                notes.append(
                    "Kenar açıları asimetrik — dedektör görüntüsü kırpılmış "
                    "olabilir; kapsama tam sensör değil bu kadraj için geçerli.")
        # Ayna belirsizliği roll'ü etkiler; kullanıcı sayıya güvenmeden önce bilmeli.
        d = getattr(res, "dense", None)
        if d is not None and getattr(d, "mirror_ambiguous", False):
            notes.append(
                "Ayna ekseni belirsiz — roll değeri bu belirsizlikten "
                "etkilenebilir; decenter ve kapsama etkilenmez.")
        self.lbl_point_note.setText("  ".join(notes))

    def _show_tilt(self, res):
        """
        Tilt'i belirsizliğiyle birlikte gösterir.

        Eski davranış küçük tilt'i "0.000°" diye kesin bir sayı gibi
        gösteriyordu; oysa elips yöntemi ~3.6° altını çözemez ve o sıfır
        "ölçemedim"in kılık değiştirmiş haliydi. Artık ölçüm gürültünün
        altındaysa üst sınır olarak ("< 3.6°") gösterilir.
        """
        rep = getattr(res, "tilt", None)
        tilt = res.tilt_deg

        if rep is not None and rep.ok:
            self.r_tilt_method.set_value(rep.primary_method or "—")
            if not rep.resolvable:
                # Ölçülemedi değil — "bu değerden küçük" bilgisi de sonuçtur.
                self.r_tilt.set_value(f"< {rep.sigma_deg:.1f}", GOOD)
                self.lbl_tilt_note.setText(
                    "Eğiklik ölçüm sınırının altında — bu yöntemle ayırt "
                    "edilemiyor, sıfır olduğu anlamına gelmez.")
            else:
                color = (GOOD if rep.tilt_deg < 1.0
                         else (WARN if rep.tilt_deg < 5.0 else BAD))
                self.r_tilt.set_value(f"{rep.tilt_deg:.3f}", color)
                self.lbl_tilt_note.setText(f"belirsizlik ± {rep.sigma_deg:.2f}°")
            return

        if rep is not None:
            # Rapor VAR ama ok=False: bu bir eksiklik değil, bilinçli bir
            # REDDİR — ya hiçbir yöntemin önkoşulu sağlanmadı ya da yalnızca
            # doğrulanmamış (deneysel) yöntem sonuç verdi
            # (bkz. tilt_estimators.measure_tilt).
            #
            # Buradan eski yedek yola düşüp `res.tilt_deg` yazmak, o katmanın
            # engellemek için var olduğu şeyin ta kendisidir: belirsizliği
            # bilinmeyen bir sayıyı ölçüm gibi göstermek. Tablo zaten
            # "ölçülemedi" yazıyordu; panel yedeği yazınca aynı koşu iki
            # yerde farklı okunuyordu (panel 5.859 / tablo ölçülemedi).
            self.r_tilt_method.set_value("—")
            self.r_tilt.set_value("ölçülemedi", BAD)
            self.lbl_tilt_note.setText(
                rep.messages[0] if rep.messages else
                "Eğiklik bu görüntüden güvenilir biçimde ölçülemedi.")
            return

        # Rapor hiç yoksa (eski sonuç nesnesi) eski davranış
        self.lbl_tilt_note.setText("")
        if tilt == tilt:
            color = GOOD if tilt < 1.0 else (WARN if tilt < 5.0 else BAD)
            self.r_tilt.set_value(f"{tilt:.3f}", color)
        else:
            self.r_tilt.set_value("ölçülemedi", BAD)
            self.lbl_tilt_note.setText(
                "Görüntüde bilinen bir geometri (dairesel desen) "
                "bulunamadı — eğiklik ölçülemez.")

    def _show_verdict(self, res):
        """
        Tek satırda "bu sonuca güvenebilir miyim" cevabı.

        Kullanıcı inlier sayısı veya yeniden izdüşüm hatasıyla karar vermez;
        onun sorusu "sonuç sağlam mı". Bu satır o soruyu cevaplar, ham
        sayılar ayrıntılarda kalır.
        """
        problems, warnings = [], []

        if res.match is not None:
            m = res.match
            # Dejenere ve "hiç eşleşmedi" farklı teşhislerdir: dejenerede
            # eşleşme bulunmuş ama güvenilmez olduğu için reddedilmiştir.
            # İkisini "eşleştirilemedi" diye birleştirmek kullanıcıyı yanlış
            # yere bakmaya yollar (bkz. _match_state).
            state = self._match_state(res)
            if state == "dejenere":
                warnings.append("eşleme dejenere — bu bölgede desen "
                                "kendine benzer, dönme ölçülemez")
            elif state == "eşleşmedi":
                warnings.append("görüntüler eşleştirilemedi")
            elif getattr(m, "guided", False):
                # Güdümlü eşlemede nokta sayısı TASARIM GEREĞİ azdır: GT
                # yoğun hizalamanın homografisiyle ön-warp edilir ve yalnızca
                # 20 px'lik kapıdan geçen eşleşmeler kullanılır. Onlarca
                # nokta beklemek burada yanlış alarm üretir; ölçümün sağlığı
                # yeniden-izdüşüm hatasından okunur.
                if m.num_inliers < 6:
                    warnings.append(f"güdümlü eşlemede çok az nokta "
                                    f"({m.num_inliers})")
            elif m.num_inliers < 20:
                warnings.append(f"az sayıda ortak nokta ({m.num_inliers})")
            if m.reproj_error_px == m.reproj_error_px and m.reproj_error_px > 2.0:
                warnings.append(f"hizalama hatası yüksek "
                                f"({m.reproj_error_px:.1f} px)")

        if res.star is not None and res.star.ok:
            conf = min(res.star.gt_ellipse.confidence,
                       res.star.det_ellipse.confidence)
            if conf < 0.7:
                problems.append(f"desen net seçilemedi (güven {conf:.2f})")
        elif not (res.tilt is not None and res.tilt.ok):
            # "Dairesel desen bulunamadı" yalnızca HİÇBİR yöntem tilt
            # ölçemediyse bir uyarıdır. Siemens star bulunamasa da halka-fit
            # (tilt_estimators) ölçüyorsa ortada eksik bir şey yok; eski hâli
            # ölçüm başarılıyken de uyarı yazıyordu.
            warnings.append("dairesel desen bulunamadı")

        if problems:
            self.lbl_verdict.setText("⛔  Sonuca güvenmeyin — " +
                                     "; ".join(problems))
            self.lbl_verdict.setStyleSheet(f"color:{BAD}; font-size:13px;")
        elif warnings:
            self.lbl_verdict.setText("⚠  Dikkat — " + "; ".join(warnings))
            self.lbl_verdict.setStyleSheet(f"color:{WARN}; font-size:13px;")
        else:
            self.lbl_verdict.setText("✓  Ölçüm güvenilir")
            self.lbl_verdict.setStyleSheet(f"color:{GOOD}; font-size:13px;")

        # Görüntüler
        self.view_gt.set_image(res.gt_preview)
        self.view_det.set_image(res.det_preview)
        if res.overlay is not None:
            self.view_overlay.set_image(res.overlay)
            self.tabs.setCurrentIndex(2)
        # set_image ROI dikdörtgenini silmez ama önizleme boyutu değişmiş
        # olabilir; ölçüyü yeni görüntüye göre yeniden kıs.
        self._roi_changed()

        # Mesajlar — tilt belirsizliği zaten Tilt bölümünde ve Durum
        # satırında gösteriliyor; burada tekrar etmesin.
        msgs = [m for m in res.messages
                if "gürültüsünün altında" not in m
                and not m.lstrip().startswith("·")]
        if msgs:
            # "Bilgi:" ile başlayanlar uyarı değildir — ölçüm başarılıyken de
            # yazılan açıklamalardır (polarite terslendi, güdümlü eşleme
            # kullanıldı gibi). Hepsini ⚠ ile göstermek sağlam bir sonucu
            # sorunluymuş gibi okutuyordu.
            self.msg_label.setText("\n".join(
                ("ℹ " + m[len("Bilgi:"):].strip()) if m.startswith("Bilgi:")
                else ("⚠ " + m) for m in msgs))
        else:
            self.msg_label.setText("")
        self.status_label.setText("Analiz tamamlandı.")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
