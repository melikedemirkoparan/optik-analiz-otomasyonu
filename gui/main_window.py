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

from core.config import SystemConfig, Lens, Detector, OLED, default_config
from core import pipeline
from gui.widgets import (
    ImageView, ResultRow, hline, STYLESHEET, ACCENT, MUTED, GOOD, WARN, BAD,
)

PRESET_DIR = os.path.join(_ROOT, "presets")


# --------------------------- Arka plan işçisi ------------------------------

class AnalysisWorker(QThread):
    """Analizi ayrı thread'de koşturur — arayüz donmasın."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, gt_path: str, det_path: str, cfg: SystemConfig):
        super().__init__()
        self.gt_path = gt_path
        self.det_path = det_path
        self.cfg = cfg

    def run(self):
        try:
            res = pipeline.run_analysis(
                self.gt_path, self.det_path, self.cfg,
                progress=lambda p, m: self.progress.emit(p, m))
            self.finished_ok.emit(res)
        except Exception as e:                          # noqa: BLE001
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


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

        # --- Lens ---
        gb_lens = QGroupBox("Lens")
        ll = QGridLayout(gb_lens)
        self.f_lens_name = QLineEdit()
        self.f_focal = self._dspin(1.0, 100000.0, 3, " mm")
        self.f_fnum = self._dspin(0.5, 100.0, 2, "")
        self._grid_row(ll, 0, "Model", self.f_lens_name)
        self._grid_row(ll, 1, "Odak uzaklığı f", self.f_focal,
                       "FOV ve IFOV doğrudan bu değere bağlıdır.")
        self._grid_row(ll, 2, "Diyafram f/", self.f_fnum,
                       "Hesabı etkilemez; kayıt amaçlı.")
        lay.addWidget(gb_lens)

        # --- Dedektör ---
        gb_det = QGroupBox("Dedektör")
        dl = QGridLayout(gb_det)
        self.f_det_name = QLineEdit()
        self.f_det_w = self._ispin(1, 100000, " px")
        self.f_det_h = self._ispin(1, 100000, " px")
        self.f_pitch_x = self._dspin(0.01, 1000.0, 4, " µm")
        self.f_pitch_y = self._dspin(0.01, 1000.0, 4, " µm")
        self._grid_row(dl, 0, "Model", self.f_det_name)
        self._grid_row(dl, 1, "Genişlik", self.f_det_w)
        self._grid_row(dl, 2, "Yükseklik", self.f_det_h)
        self._grid_row(dl, 3, "Piksel pitch X", self.f_pitch_x)
        self._grid_row(dl, 4, "Piksel pitch Y", self.f_pitch_y,
                       "Kare piksel için X ile aynı bırakın.")
        self.lbl_sensor = QLabel("—")
        self.lbl_sensor.setStyleSheet(f"color:{MUTED};")
        dl.addWidget(self.lbl_sensor, 5, 0, 1, 2)
        lay.addWidget(gb_det)

        # Sensör boyutu canlı güncellensin
        for wdg in (self.f_det_w, self.f_det_h, self.f_pitch_x, self.f_pitch_y):
            wdg.valueChanged.connect(self._update_sensor_label)

        # --- OLED ---
        gb_oled = QGroupBox("OLED (referans ekran)")
        ol = QGridLayout(gb_oled)
        self.f_oled_name = QLineEdit()
        self.f_oled_w = self._ispin(1, 100000, " px")
        self.f_oled_h = self._ispin(1, 100000, " px")
        self.f_oled_pitch = self._dspin(0.01, 1000.0, 4, " µm")
        self.f_oled_aw = self._dspin(0.01, 10000.0, 3, " mm")
        self.f_oled_ah = self._dspin(0.01, 10000.0, 3, " mm")
        self._grid_row(ol, 0, "Model", self.f_oled_name)
        self._grid_row(ol, 1, "Genişlik", self.f_oled_w)
        self._grid_row(ol, 2, "Yükseklik", self.f_oled_h)
        self._grid_row(ol, 3, "Piksel pitch", self.f_oled_pitch)
        self._grid_row(ol, 4, "Aktif alan G", self.f_oled_aw)
        self._grid_row(ol, 5, "Aktif alan Y", self.f_oled_ah)
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

        # FOV
        gb_fov = QGroupBox("Görüş Alanı (FOV)")
        fl = QVBoxLayout(gb_fov)
        self.r_fov_x = ResultRow("Yatay FOV", "°")
        self.r_fov_y = ResultRow("Dikey FOV", "°")
        self.r_fov_d = ResultRow("Köşegen FOV", "°")
        for r in (self.r_fov_x, self.r_fov_y, self.r_fov_d):
            fl.addWidget(r)
        fl.addWidget(hline())
        self.r_sensor = ResultRow("Sensör", "mm")
        fl.addWidget(self.r_sensor)
        lay.addWidget(gb_fov)

        # IFOV
        gb_ifov = QGroupBox("Anlık Görüş Alanı (IFOV)")
        il = QVBoxLayout(gb_ifov)
        self.r_ifov_x = ResultRow("IFOV yatay", "µrad/px",
                                  "Bir pikselin gördüğü açı.")
        self.r_ifov_y = ResultRow("IFOV dikey", "µrad/px")
        self.r_ifov_as = ResultRow("IFOV", "arcsec/px")
        for r in (self.r_ifov_x, self.r_ifov_y, self.r_ifov_as):
            il.addWidget(r)
        lay.addWidget(gb_ifov)

        # Tilt
        gb_tilt = QGroupBox("Eğiklik (Tilt)")
        tl = QVBoxLayout(gb_tilt)
        self.r_rot = ResultRow("Düzlem-içi dönme", "°",
                               "Görüntünün kendi düzleminde saat yönü dönmesi.")
        self.r_tilt = ResultRow("Düzlem-dışı tilt", "°",
                                "Yıldız elipsinden ölçülür — ölçek ve "
                                "kırpmadan bağımsızdır.")
        self.r_tilt_x = ResultRow("Keystone X", "°")
        self.r_tilt_y = ResultRow("Keystone Y", "°")
        for r in (self.r_rot, self.r_tilt, self.r_tilt_x, self.r_tilt_y):
            tl.addWidget(r)
        lay.addWidget(gb_tilt)

        # Elips ölçümü
        gb_el = QGroupBox("Yıldız Elipsi")
        el = QVBoxLayout(gb_el)
        self.r_gt_ratio = ResultRow("GT eksen oranı", "")
        self.r_det_ratio = ResultRow("Dedektör eksen oranı", "")
        self.r_el_conf = ResultRow("Tespit güveni", "")
        for r in (self.r_gt_ratio, self.r_det_ratio, self.r_el_conf):
            el.addWidget(r)
        lay.addWidget(gb_el)

        # Eşleme kalitesi
        gb_m = QGroupBox("Eşleme Kalitesi")
        ml = QVBoxLayout(gb_m)
        self.r_mirror = ResultRow("Ayna (flip)", "")
        self.r_inliers = ResultRow("Inlier sayısı", "")
        self.r_reproj = ResultRow("Yeniden izdüşüm", "px")
        for r in (self.r_mirror, self.r_inliers, self.r_reproj):
            ml.addWidget(r)
        lay.addWidget(gb_m)

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

    def _load_config_into_fields(self, cfg: SystemConfig):
        self.f_lens_name.setText(cfg.lens.name)
        self.f_focal.setValue(cfg.lens.focal_length_mm)
        self.f_fnum.setValue(cfg.lens.f_number)

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

        idx = self.f_setup.findData(cfg.setup_type)
        self.f_setup.setCurrentIndex(max(0, idx))
        self.f_coll_f.setValue(cfg.collimator_focal_length_mm)
        self._update_sensor_label()

    def _config_from_fields(self) -> SystemConfig:
        return SystemConfig(
            name="Arayüzden düzenlenmiş sistem",
            setup_type=self.f_setup.currentData(),
            collimator_focal_length_mm=self.f_coll_f.value(),
            lens=Lens(
                name=self.f_lens_name.text(),
                focal_length_mm=self.f_focal.value(),
                f_number=self.f_fnum.value(),
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
            ),
        )

    # ---------------------------- eylemler ---------------------------------

    _IMG_FILTER = ("Görüntüler (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
                   "Tüm dosyalar (*)")

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

        self.worker = AnalysisWorker(self.gt_path, self.det_path, cfg)
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

    def _on_finished(self, res):
        self.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        self.result = res
        self._show_results(res)

    # ---------------------------- sonuç gösterimi --------------------------

    def _clear_results(self):
        for r in (self.r_fov_x, self.r_fov_y, self.r_fov_d, self.r_sensor,
                  self.r_ifov_x, self.r_ifov_y, self.r_ifov_as,
                  self.r_rot, self.r_tilt, self.r_tilt_x, self.r_tilt_y,
                  self.r_gt_ratio, self.r_det_ratio, self.r_el_conf,
                  self.r_mirror, self.r_inliers, self.r_reproj):
            r.clear()

    def _show_results(self, res):
        # FOV / IFOV
        if res.fov is not None:
            f = res.fov
            self.r_fov_x.set_value(f"{f.fov_x_deg:.3f}")
            self.r_fov_y.set_value(f"{f.fov_y_deg:.3f}")
            self.r_fov_d.set_value(f"{f.fov_diag_deg:.3f}")
            self.r_sensor.set_value(f"{f.sensor_w_mm:.2f} × {f.sensor_h_mm:.2f}")
            self.r_ifov_x.set_value(f"{f.ifov_x_urad:.2f}")
            self.r_ifov_y.set_value(f"{f.ifov_y_urad:.2f}")
            self.r_ifov_as.set_value(f"{f.ifov_x_arcsec:.3f}")

        # Tilt
        rot = res.rotation_deg
        if rot == rot:                      # NaN değilse
            self.r_rot.set_value(f"{rot:+.3f}")
        tilt = res.tilt_deg
        if tilt == tilt:
            # Küçük tilt iyi, büyük tilt dikkat çekmeli
            color = GOOD if tilt < 1.0 else (WARN if tilt < 5.0 else BAD)
            self.r_tilt.set_value(f"{tilt:.3f}", color)

        if res.match is not None and res.match.tilt is not None:
            t = res.match.tilt
            self.r_tilt_x.set_value(f"{t.tilt_x_deg:+.3f}")
            self.r_tilt_y.set_value(f"{t.tilt_y_deg:+.3f}")

        # Elips
        if res.star is not None and res.star.ok:
            g, d = res.star.gt_ellipse, res.star.det_ellipse
            self.r_gt_ratio.set_value(f"{g.axis_ratio:.4f}")
            self.r_det_ratio.set_value(f"{d.axis_ratio:.4f}")
            conf = min(g.confidence, d.confidence)
            ccol = GOOD if conf > 0.7 else (WARN if conf > 0.4 else BAD)
            self.r_el_conf.set_value(f"{conf:.2f}", ccol)

        # Eşleme
        if res.match is not None:
            m = res.match
            self.r_mirror.set_value("EVET" if m.mirrored else "hayır",
                                    WARN if m.mirrored else GOOD)
            self.r_inliers.set_value(f"{m.num_inliers}")
            if m.reproj_error_px == m.reproj_error_px:
                rcol = GOOD if m.reproj_error_px < 2.0 else WARN
                self.r_reproj.set_value(f"{m.reproj_error_px:.2f}", rcol)

        # Görüntüler
        self.view_gt.set_image(res.gt_preview)
        self.view_det.set_image(res.det_preview)
        if res.overlay is not None:
            self.view_overlay.set_image(res.overlay)
            self.tabs.setCurrentIndex(2)

        # Mesajlar
        if res.messages:
            self.msg_label.setText("⚠ " + "\n⚠ ".join(res.messages))
        self.status_label.setText("Analiz tamamlandı.")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
