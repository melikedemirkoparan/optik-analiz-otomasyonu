"""
Arayüz yardımcı bileşenleri: görüntü görüntüleyici, sonuç kartı, tema.
"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPainter, QFont, QPen, QColor
from PyQt5.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
    QScrollArea, QDoubleSpinBox,
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

    Ayrıca bir ROI (ilgi alanı) dikdörtgeni çizebilir. ROI *görüntü piksel*
    koordinatlarında tutulur; ekrana çizerken o anki ölçek çarpanıyla
    dönüştürülür. Böylece pencere boyutu değişse de ROI aynı piksel alanını
    gösterir — ölçü girişi anlamını korur.
    """

    #: Kullanıcı görüntü üzerine tıkladığında (x, y) piksel koordinatı.
    clicked_at = pyqtSignal(int, int)

    def __init__(self, placeholder: str = "Görüntü yüklenmedi"):
        super().__init__()
        self._pix: QPixmap | None = None
        self._placeholder = placeholder
        self._roi: tuple[int, int, int, int] | None = None   # x, y, w, h
        self._draw_off = QPoint(0, 0)   # görüntünün panel içindeki sol-üst köşesi
        self._scale = 1.0               # ekran px / görüntü px
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background:#15181d; border:1px solid #333a45; border-radius:6px;"
            f"color:{MUTED};")
        self.setText(placeholder)

    # ------------------------------ görüntü -------------------------------

    def set_image(self, img: np.ndarray | None):
        if img is None:
            self._pix = None
            self.setPixmap(QPixmap())
            self.setText(self._placeholder)
            return
        self._pix = cv_to_qpixmap(img)
        self._rescale()

    def clear_image(self):
        self.set_image(None)

    def image_size(self) -> tuple[int, int]:
        """Yüklü görüntünün (genişlik, yükseklik) değeri; yoksa (0, 0)."""
        if self._pix is None or self._pix.isNull():
            return (0, 0)
        return (self._pix.width(), self._pix.height())

    # -------------------------------- ROI ---------------------------------

    def set_roi(self, roi: tuple[int, int, int, int] | None):
        """ROI'yi görüntü piksel koordinatlarında ayarlar (x, y, w, h)."""
        self._roi = roi
        self.update()

    def _rescale(self):
        if self._pix is None or self._pix.isNull():
            return
        scaled = self._pix.scaled(self.size(), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation)
        self.setPixmap(scaled)
        # Ölçek ve yerleşim offsetini sakla — ROI çizimi ve tıklama eşlemesi
        # bu ikisine dayanıyor.
        self._scale = (scaled.width() / self._pix.width()
                       if self._pix.width() else 1.0)
        self._draw_off = QPoint((self.width() - scaled.width()) // 2,
                                (self.height() - scaled.height()) // 2)

    def resizeEvent(self, ev):       # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        self._rescale()

    def paintEvent(self, ev):        # noqa: N802 (Qt API)
        super().paintEvent(ev)
        if self._roi is None or self._pix is None or self._pix.isNull():
            return
        x, y, w, h = self._roi
        s = self._scale
        r = QRect(self._draw_off.x() + int(round(x * s)),
                  self._draw_off.y() + int(round(y * s)),
                  max(1, int(round(w * s))), max(1, int(round(h * s))))

        p = QPainter(self)
        # Dış kontur koyu, iç kontur parlak: hem açık hem koyu desende görünür.
        p.setPen(QPen(QColor(0, 0, 0, 180), 3))
        p.drawRect(r)
        p.setPen(QPen(QColor(ACCENT), 1.5))
        p.drawRect(r)
        # Merkez artı işareti — ROI'nin nereye oturduğunu okumayı kolaylaştırır.
        cx, cy = r.center().x(), r.center().y()
        p.drawLine(cx - 6, cy, cx + 6, cy)
        p.drawLine(cx, cy - 6, cx, cy + 6)
        p.end()

    def mousePressEvent(self, ev):   # noqa: N802 (Qt API)
        """Görüntü üzerine tıklamayı piksel koordinatına çevirip yayınlar."""
        if self._pix is None or self._pix.isNull() or self._scale <= 0:
            return
        px = (ev.pos().x() - self._draw_off.x()) / self._scale
        py = (ev.pos().y() - self._draw_off.y()) / self._scale
        w, h = self.image_size()
        if 0 <= px < w and 0 <= py < h:
            self.clicked_at.emit(int(px), int(py))


# ----------------------------- Sonuç satırı --------------------------------

# Kaynak rozeti renkleri. Bir sayının NEREDEN geldiği, sayının kendisi kadar
# önemlidir: datasheet'ten okunan bir değerle başka değerlerden türetilen bir
# değer aynı güvene sahip değildir ve kullanıcı ikisini ayırt edebilmelidir.
class BlankableDoubleSpin(QDoubleSpinBox):
    """
    Boş bırakılabilen sayı alanı — boş = BİLİNMİYOR.

    NEDEN VAR
    ---------
    Normal `QDoubleSpinBox` boş bırakılamaz: alanı silseniz bile minimuma
    döner. Ama bu projede boşluk BİLGİ taşıyor — "odak uzaklığını bilmiyorum,
    sen hesapla" demenin tek yolu o alanı boş bırakmaktır.

    Alt sınır 0'dır ve 0 "verilmedi" anlamına gelir (config.py'deki
    konvansiyonun aynısı). 0 iken kutuda sayı değil `placeholder` görünür,
    böylece "0 mm'lik bir lens" ile "bilinmeyen lens" karışmaz.
    """

    def __init__(self, hi: float, dec: int, suffix: str,
                 placeholder: str = "bilinmiyor", parent=None):
        super().__init__(parent)
        self._suffix = suffix
        self._ph = placeholder
        self.setRange(0.0, hi)
        self.setDecimals(dec)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setSpecialValueText(placeholder)   # 0 iken bunu göster
        self.setSuffix(suffix)

    # 0 (= special value) iken Qt suffix'i de gizler; bu doğru davranış.
    def bos_mu(self) -> bool:
        return self.value() <= 0.0

    def temizle(self):
        self.setValue(0.0)


SRC_GIVEN = "#7c8798"      # datasheet / kullanıcı girdisi — nötr
SRC_DERIVED = "#c9a0ff"    # türetildi — dikkat çeksin ama alarm olmasın


class ResultRow(QWidget):
    """
    Etiket + değer gösteren tek satırlık sonuç bileşeni.

    Değerin yanında isteğe bağlı bir **kaynak rozeti** taşır: sayının
    datasheet'ten mi okunduğu yoksa başka değerlerden mi türetildiği.
    Türetilmiş bir değerde rozetin üstüne gelince türetim zinciri görünür
    ("IFOV ve piksel pitch'inden türetildi" gibi).
    """

    def __init__(self, label: str, unit: str = "", tooltip: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 3)

        self._label = QLabel(label)
        self._label.setStyleSheet(f"color:{MUTED};")
        if tooltip:
            self._label.setToolTip(tooltip)
            self.setToolTip(tooltip)

        # Kaynak rozeti — varsayılan olarak gizli, `set_source` ile açılır.
        self._badge = QLabel("")
        self._badge.setVisible(False)
        self._badge_on = False
        bf = QFont()
        bf.setPointSize(9)
        self._badge.setFont(bf)

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

        # Yerleşim: etiket esner, SAYI ESNEMEZ ama kırpılmasına da izin
        # verilmez. Eskiden etiket tek başına stretch alıyordu; dar panelde
        # sayının yerini yiyip "0 × 9.200" ya da yalnızca "(-0.64%)" gibi
        # yarım değerler görünüyordu — sonuç panelinde en kritik hata bu,
        # çünkü yanlış okunan sayı sessizce yanlış karara götürür.
        self._label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._value.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self._value.setMinimumWidth(96)
        lay.addWidget(self._label, 1)
        lay.addWidget(self._badge, 0)
        lay.addWidget(self._value, 0)
        lay.addWidget(self._unit, 0)

    def set_value(self, text: str, color: str = ACCENT):
        self._value.setText(text)
        self._value.setStyleSheet(f"color:{color};")

    def set_source(self, kind: str | None, detail: str = ""):
        """
        Değerin kaynağını rozet olarak gösterir.

        `kind`:
          * ``"given"``   — datasheet ya da kullanıcı girdisi
          * ``"unit"``    — aynı değerin başka birimde yazılışı
          * ``"derived"`` — gerçek bir bağıntıyla hesaplandı
          * ``None``      — rozet gizlenir (kaynak bilinmiyor/anlamsız)

        `detail` rozetin ipucu metnidir; türetilmiş değerlerde türetim
        zinciri buraya yazılır. Rozet metnini KISA tutmak şart — satırın
        asıl işi sayıyı göstermek, rozet yalnızca ona bir güven etiketi
        iliştirmek.
        """
        if kind is None:
            self._badge.setVisible(False)
            self._badge_on = False
            self._badge.setToolTip("")
            return
        if kind == "given":
            metin, renk = "datasheet", SRC_GIVEN
        elif kind == "unit":
            # Birim çevrimi yeni bilgi değil — aynı sayının başka yazılışı.
            # "türetildi" demek hesap yapılmış izlenimi verirdi.
            metin, renk = "birim", SRC_GIVEN
        else:
            metin, renk = "türetildi", SRC_DERIVED
        self._badge.setText(metin)
        self._badge.setStyleSheet(
            f"color:{renk}; border:1px solid {renk}; border-radius:6px; "
            f"padding:0px 5px; font-size:9px;")
        self._badge.setToolTip(detail or metin)
        self._badge.setVisible(True)
        self._badge_on = True

    def source(self) -> str:
        """
        Rozet metni; rozet kapalıysa boş.

        `isVisible()` KULLANILMAZ: Qt'de gizli bir pencerenin çocukları da
        görünmez sayılır, dolayısıyla henüz `show()` edilmemiş bir panelde
        (testlerin koştuğu hâl) her rozet boş görünürdü. Rozetin AÇIK olup
        olmadığı ayrı bir bayrakta tutulur.
        """
        return self._badge.text() if self._badge_on else ""

    def value(self) -> str:
        """
        Gösterilen değer metni.

        Testler panel ile karşılaştırma tablosunun aynı ölçüm için aynı şeyi
        yazdığını buradan doğrular; olmazsa iç `_value` etiketine uzanmak
        gerekirdi.
        """
        return self._value.text()

    def clear(self):
        self.set_value("—", MUTED)
        self.set_source(None)


def hline() -> QFrame:
    """İnce ayırıcı çizgi."""
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#333a45; background:#333a45; max-height:1px;")
    return f
