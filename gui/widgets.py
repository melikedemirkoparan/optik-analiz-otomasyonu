"""
Arayüz yardımcı bileşenleri: görüntü görüntüleyici, sonuç kartı, tema.
"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QImage, QPixmap, QPainter, QFont
from PyQt5.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
    QScrollArea,
)


# ----------------------------- Tema ---------------------------------------

# Koyu, teknik görünümlü tema. Optik/ölçüm yazılımlarında koyu arka plan
# görüntü önizlemesinin kontrastını korur.
STYLESHEET = """
QWidget {
    background: #1e2229;
    color: #dfe4ec;
    font-family: 'Segoe UI', 'Ubuntu', 'DejaVu Sans', sans-serif;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #333a45;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    background: #232830;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 8px;
    color: #6ec1ff;
    font-weight: 600;
}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background: #1a1e24;
    border: 1px solid #39414e;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: #2d6da8;
}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #4b9fea;
}
QPushButton {
    background: #2b3240;
    border: 1px solid #3d4757;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
}
QPushButton:hover   { background: #343d4d; border-color: #4b9fea; }
QPushButton:pressed { background: #232a36; }
QPushButton:disabled { color: #6b7382; background: #262b33; border-color: #333a45; }
QPushButton#primary {
    background: #2d6da8;
    border: 1px solid #3f8ad0;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primary:hover   { background: #3680c4; }
QPushButton#primary:pressed { background: #255a8c; }
QTabWidget::pane {
    border: 1px solid #333a45;
    border-radius: 8px;
    background: #232830;
    top: -1px;
}
QTabBar::tab {
    background: #262b33;
    border: 1px solid #333a45;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 7px 18px;
    margin-right: 2px;
    color: #9aa5b5;
}
QTabBar::tab:selected { background: #232830; color: #6ec1ff; }
QTabBar::tab:hover:!selected { background: #2d333d; }
QProgressBar {
    border: 1px solid #333a45;
    border-radius: 6px;
    background: #1a1e24;
    text-align: center;
    height: 18px;
}
QProgressBar::chunk { background: #2d6da8; border-radius: 5px; }
QScrollArea { border: none; }
QStatusBar { background: #1a1e24; color: #9aa5b5; }
QSplitter::handle { background: #333a45; }
QToolTip {
    background: #2b3240; color: #dfe4ec;
    border: 1px solid #4b9fea; padding: 4px;
}
"""

ACCENT = "#6ec1ff"
MUTED = "#9aa5b5"
GOOD = "#5fd08a"
WARN = "#ffb454"
BAD = "#ff6b6b"


# --------------------------- Görüntü gösterici -----------------------------

def cv_to_qpixmap(img: np.ndarray) -> QPixmap:
    """OpenCV görüntüsünü (BGR ya da gri) QPixmap'e çevirir."""
    if img is None:
        return QPixmap()
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class ImageView(QLabel):
    """
    Görüntüyü en-boy oranını koruyarak gösteren panel.
    Pencere boyutlandıkça otomatik ölçeklenir.
    """

    def __init__(self, placeholder: str = "Görüntü yüklenmedi"):
        super().__init__()
        self._pix: QPixmap | None = None
        self._placeholder = placeholder
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background:#15181d; border:1px solid #333a45; border-radius:6px;"
            f"color:{MUTED};")
        self.setText(placeholder)

    def set_image(self, img: np.ndarray | None):
        if img is None:
            self._pix = None
            self.setText(self._placeholder)
            return
        self._pix = cv_to_qpixmap(img)
        self._rescale()

    def clear_image(self):
        self.set_image(None)

    def _rescale(self):
        if self._pix is None or self._pix.isNull():
            return
        scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        self.setPixmap(scaled)

    def resizeEvent(self, ev):       # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        self._rescale()


# ----------------------------- Sonuç satırı --------------------------------

class ResultRow(QWidget):
    """Etiket + değer gösteren tek satırlık sonuç bileşeni."""

    def __init__(self, label: str, unit: str = "", tooltip: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 3)

        self._label = QLabel(label)
        self._label.setStyleSheet(f"color:{MUTED};")
        if tooltip:
            self._label.setToolTip(tooltip)
            self.setToolTip(tooltip)

        self._value = QLabel("—")
        f = QFont("monospace")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(12)
        f.setBold(True)
        self._value.setFont(f)
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setStyleSheet(f"color:{ACCENT};")

        self._unit = QLabel(unit)
        self._unit.setStyleSheet(f"color:{MUTED};")
        self._unit.setMinimumWidth(58)

        lay.addWidget(self._label, 1)
        lay.addWidget(self._value, 0)
        lay.addWidget(self._unit, 0)

    def set_value(self, text: str, color: str = ACCENT):
        self._value.setText(text)
        self._value.setStyleSheet(f"color:{color};")

    def clear(self):
        self.set_value("—", MUTED)


def hline() -> QFrame:
    """İnce ayırıcı çizgi."""
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#333a45; background:#333a45; max-height:1px;")
    return f
