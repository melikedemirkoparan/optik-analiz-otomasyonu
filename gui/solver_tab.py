"""
Çözücü sekmesi — bilinenleri gir, bilinmeyeni bul.

NEDEN AYRI BİR SEKME
--------------------
Ana panel TEK YÖNLÜ okunur: sol panele donanım girilir, analiz koşar,
sağ panelde FOV/IFOV/tilt çıkar. Ama kullanıcının elindeki bilgi her
zaman o uçtan gelmez:

    "0.027 °/px değerini bilmiyorum; FOV ve donanımdan onu bulun."

`core.solver` bunu zaten yapabiliyor (kurallar çift yönlü yazılı), ama
ana pencerede yalnızca rozet ipucu olarak kullanılıyordu — kullanıcı bir
alanı BOŞ bırakıp "bunu çöz" diyemiyordu. Bu sekme çözücüyü doğrudan
kullanıcıya açar.

TASARIM KARARI: boş = bilinmiyor
--------------------------------
Her düğüm için bir alan var; doldurulanlar `given`, boş bırakılanlar
çözülecek bilinmeyen. Sıfır yazmak "bilinmiyor" demek DEĞİL — 0 bir
odak uzaklığı olarak anlamsız olduğu için `solver.solve` onu zaten
eler, ama kullanıcı açısından boş alan ile 0 arasındaki fark belirsiz
kalmasın diye alanlar QLineEdit (metin), QDoubleSpinBox değil.
Spinbox boş bırakılamaz; bu ekranda boşluk BİLGİ taşıdığı için
spinbox yanlış araçtır.
"""
from __future__ import annotations

import math

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QScrollArea, QComboBox, QSizePolicy,
)
from PyQt5.QtGui import QFont

from core import solver
from core import projection as projmod
from gui.widgets import hline, ACCENT, MUTED, GOOD, WARN, BAD


# Sekmede gösterilecek düğümler, mantıksal gruplar hâlinde.
# `solver.NODE_LABELS`'ın tamamını dökmek yerine seçiyoruz: ara birim
# dönüşümleri (ifov_x_arcsec gibi) girdi olarak sorulmaz, sonuçta görünür.
GIRDI_GRUPLARI: list[tuple[str, list[str]]] = [
    ("Lens", ["lens_f_mm", "lens_fnum", "lens_pupil_mm",
              "lens_useful_fov_deg", "lens_image_circle_mm"]),
    ("Dedektör", ["det_pitch_um", "det_pitch_y_um", "det_w_px", "det_h_px",
                  "det_w_mm", "det_h_mm", "det_diag_mm"]),
    ("Görüş alanı", ["fov_x_deg", "fov_y_deg", "fov_diag_deg",
                     "ifov_x_urad", "ifov_x_deg", "ifov_x_arcsec",
                     "ifov_y_urad", "ifov_y_deg", "ifov_y_arcsec"]),
    ("Referans ekran", ["scr_pitch_um", "scr_w_px", "scr_h_px",
                        "scr_aw_mm", "scr_ah_mm", "scr_ang_deg", "scr_f_mm",
                        "scr_half_x_deg", "scr_half_y_deg"]),
    ("Zincirler arası", ["scale_expected"]),
]

# Yalnızca DIŞARIDAN gelebilecek büyüklükler: üreticinin verdiği ya da
# ölçülen değerler. Donanım geometrisinden türetilemezler ve boş
# kalmaları bir EKSİKLİK DEĞİLDİR — o sistemde ilgisiz olabilirler.
# Bunları "çözülemedi" diye bildirmek kullanıcıyı olmayan bir sorunu
# aramaya gönderir; ekranda FOV hesaplanmışken "çözülemeyen" uyarısı
# görmek tam olarak buydu.
DISARIDAN_GELEN: frozenset[str] = frozenset({
    "lens_useful_fov_deg",   # üreticinin kullanılabilir FOV'u
    "lens_image_circle_mm",  # datasheet'te doğrudan verilir
    "scale_expected",        # görüntüden ölçülür
})

# YALNIZCA ÇIKTI olan büyüklükler: girdi olarak sorulmazlar, başka
# değerlerden türetilirler. Bunlar için alan açmak yanlış olurdu —
# kullanıcı "kenar pikselinin IFOV'unu" bilmez, o hesaplanır.
# Çözülemediklerinde de eksiklik sayılmazlar: girdiler yetmediği için
# değil, o girdiler henüz verilmediği için yoklar.
TURETILEN_CIKTI: frozenset[str] = frozenset({
    "ifov_edge_urad",        # kenar pikselinin yerel açısı
    "ifov_edge_ratio",       # kenar/merkez oranı
    "fov_eff_diag_deg",      # görüntü dairesiyle kırpılmış gerçek FOV
    "lens_f_measured_mm",    # ölçekten geri hesaplanan f
    "fov_measured_x_deg",    # ölçülen f'ten çıkan FOV
    "focal_error_pct",       # datasheet ↔ ölçüm sapması
})

# Sonuç tablosunda gösterilecek sıra. Girdi olarak sorulmayan türev
# düğümler (arcsec, yarı-kapsama) burada görünür.
SONUC_SIRASI: list[str] = [
    "lens_f_mm", "lens_fnum", "lens_pupil_mm",
    "lens_useful_fov_deg", "lens_image_circle_mm",
    "det_pitch_um", "det_pitch_y_um", "det_w_px", "det_h_px",
    "det_w_mm", "det_h_mm", "det_diag_mm",
    "fov_x_deg", "fov_y_deg", "fov_diag_deg",
    "ifov_x_urad", "ifov_y_urad", "ifov_x_deg", "ifov_y_deg",
    "ifov_x_arcsec", "ifov_y_arcsec",
    "scr_pitch_um", "scr_w_px", "scr_h_px", "scr_aw_mm", "scr_ah_mm",
    "scr_ang_deg", "scr_f_mm", "scr_half_x_deg", "scr_half_y_deg",
    "scale_expected",
    # Ölçüme ve daire kısıtına dayanan türevler — girdi olarak sorulmaz,
    # sonuçta görünür.
    "ifov_edge_urad", "ifov_edge_ratio", "fov_eff_diag_deg",
    "lens_f_measured_mm", "fov_measured_x_deg", "focal_error_pct",
]


def _fmt(v: float, node: str) -> str:
    """Sayıyı düğümün büyüklük mertebesine uygun biçimde yaz."""
    a = abs(v)
    if node.endswith("_px"):
        # Piksel sayısı tam sayıdır; türetildiğinde 2047.9998 çıkabilir.
        return f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.2f}"
    if a >= 100:
        return f"{v:.3f}"
    if a >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


class SolverTab(QWidget):
    """Bilinenleri gir → bilinmeyenleri çöz."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fields: dict[str, QLineEdit] = {}
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        kok = QVBoxLayout(self)
        kok.setContentsMargins(8, 8, 8, 8)
        kok.setSpacing(8)

        bilgi = QLabel(
            "Bildiğiniz değerleri girin, <b>bilmediklerinizi boş bırakın</b>. "
            "Çözücü aradaki optik bağıntıları iki yönde de kullanarak "
            "boş bıraktıklarınızdan türetilebilecek her şeyi hesaplar.<br>"
            "<span style='color:%s'>Buraya girdikleriniz sağ bardaki nominal "
            "değerlere de işler: FOV'u burada girip sol paneldeki odak "
            "uzaklığını silerseniz, FOV/IFOV yine hesaplanır. Aynı büyüklük "
            "her ikisinde de doluysa sol paneldeki kazanır.</span>"
            % MUTED)
        bilgi.setWordWrap(True)
        bilgi.setStyleSheet("font-size:12px;")
        kok.addWidget(bilgi)

        # ---- Projeksiyon modeli ----
        # FOV<->f bağıntısı lensin açı→yükseklik haritasına bağlı; model
        # değişince aynı girdiler farklı sonuç verir. Bu yüzden burada da
        # seçilebilir olmalı, ana panelden bağımsız olarak.
        satir = QHBoxLayout()
        satir.addWidget(QLabel("Projeksiyon modeli:"))
        self.cmb_model = QComboBox()
        for key in projmod.MODELS:
            self.cmb_model.addItem(projmod.MODEL_LABELS.get(key, key), key)
        satir.addWidget(self.cmb_model, 1)
        kok.addLayout(satir)

        # ---- Girdi alanları (kaydırılabilir) ----
        alt = QHBoxLayout()
        alt.setSpacing(10)

        sol_scroll = QScrollArea()
        sol_scroll.setWidgetResizable(True)
        sol_ic = QWidget()
        sol_lay = QVBoxLayout(sol_ic)
        sol_lay.setContentsMargins(2, 2, 2, 2)
        sol_lay.setSpacing(8)

        for baslik, nodes in GIRDI_GRUPLARI:
            gb = QGroupBox(baslik)
            g = QGridLayout(gb)
            g.setContentsMargins(8, 6, 8, 8)
            g.setHorizontalSpacing(6)
            g.setVerticalSpacing(4)
            for i, node in enumerate(nodes):
                lbl = QLabel(solver.label(node))
                lbl.setStyleSheet("font-size:12px;")
                le = QLineEdit()
                le.setPlaceholderText("bilinmiyor")
                le.setFixedWidth(110)
                le.setAlignment(Qt.AlignRight)
                birim = QLabel(solver.unit(node))
                birim.setStyleSheet(f"color:{MUTED}; font-size:11px;")
                birim.setFixedWidth(52)
                g.addWidget(lbl, i, 0)
                g.addWidget(le, i, 1)
                g.addWidget(birim, i, 2)
                self.fields[node] = le
            g.setColumnStretch(0, 1)
            sol_lay.addWidget(gb)
        sol_lay.addStretch(1)
        sol_scroll.setWidget(sol_ic)
        sol_scroll.setMinimumWidth(340)
        alt.addWidget(sol_scroll, 0)

        # ---- Sonuç tablosu ----
        sag = QWidget()
        sag_lay = QVBoxLayout(sag)
        sag_lay.setContentsMargins(0, 0, 0, 0)
        sag_lay.setSpacing(6)

        self.lbl_ozet = QLabel("Değer girip «Çöz» deyin.")
        self.lbl_ozet.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        self.lbl_ozet.setWordWrap(True)
        sag_lay.addWidget(self.lbl_ozet)

        self.sonuc_scroll = QScrollArea()
        self.sonuc_scroll.setWidgetResizable(True)
        self.sonuc_ic = QWidget()
        self.sonuc_lay = QGridLayout(self.sonuc_ic)
        self.sonuc_lay.setContentsMargins(4, 4, 4, 4)
        self.sonuc_lay.setHorizontalSpacing(10)
        self.sonuc_lay.setVerticalSpacing(3)
        self.sonuc_scroll.setWidget(self.sonuc_ic)
        sag_lay.addWidget(self.sonuc_scroll, 1)
        alt.addWidget(sag, 1)
        kok.addLayout(alt, 1)

        # ---- Düğmeler ----
        btns = QHBoxLayout()
        self.btn_coz = QPushButton("Çöz")
        self.btn_coz.setObjectName("primary")
        self.btn_coz.clicked.connect(self.coz)
        self.btn_temizle = QPushButton("Alanları temizle")
        self.btn_temizle.clicked.connect(self.temizle)
        self.btn_panelden = QPushButton("Sol panelden doldur")
        self.btn_panelden.setToolTip(
            "Ana penceredeki lens/dedektör/ekran değerlerini bu alanlara "
            "kopyalar. Sonra bulmak istediğiniz alanı silip «Çöz» deyin.")
        btns.addWidget(self.btn_panelden)
        btns.addWidget(self.btn_temizle)
        btns.addStretch(1)
        btns.addWidget(self.btn_coz)
        kok.addLayout(btns)

    # ------------------------------------------------------------------
    def girdiler(self) -> dict[str, float]:
        """Dolu alanları sayıya çevirir; boş/bozuk olanlar atlanır."""
        g: dict[str, float] = {}
        for node, le in self.fields.items():
            metin = le.text().strip().replace(",", ".")
            if not metin:
                continue
            try:
                val = float(metin)
            except ValueError:
                continue
            if math.isfinite(val) and val > 0:
                g[node] = val
        return g

    def doldur(self, degerler: dict[str, float]):
        """Verilen düğüm→değer sözlüğünü alanlara yazar (ana panelden)."""
        for node, v in degerler.items():
            le = self.fields.get(node)
            if le is None:
                continue
            if v is None or not math.isfinite(v) or v <= 0:
                le.clear()
            else:
                le.setText(_fmt(v, node))

    def temizle(self):
        for le in self.fields.values():
            le.clear()
        self._tabloyu_bosalt()
        self.lbl_ozet.setText("Değer girip «Çöz» deyin.")
        self.lbl_ozet.setStyleSheet(f"color:{MUTED}; font-size:12px;")

    def _tabloyu_bosalt(self):
        while self.sonuc_lay.count():
            it = self.sonuc_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    # ------------------------------------------------------------------
    def coz(self):
        given = self.girdiler()
        self._tabloyu_bosalt()
        if not given:
            self.lbl_ozet.setText(
                "Hiç değer girilmedi — çözücünün başlayacağı bir bilinen yok.")
            self.lbl_ozet.setStyleSheet(f"color:{WARN}; font-size:12px;")
            return

        model = self.cmb_model.currentData()
        res = solver.solve_for(given, SONUC_SIRASI, model=model)

        # Başlık satırı
        basliklar = ["Büyüklük", "Değer", "Birim", "Kaynak"]
        for c, b in enumerate(basliklar):
            lbl = QLabel(b)
            f = QFont(); f.setBold(True)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color:{ACCENT}; font-size:12px;")
            self.sonuc_lay.addWidget(lbl, 0, c)

        satir = 1
        turetilen = 0
        for node in SONUC_SIRASI:
            v = res.values.get(node)
            if v is None:
                continue
            ad = QLabel(solver.label(node))
            ad.setStyleSheet("font-size:12px;")
            deg = QLabel(_fmt(v.value, node))
            deg.setAlignment(Qt.AlignRight)
            brm = QLabel(solver.unit(node))
            brm.setStyleSheet(f"color:{MUTED}; font-size:11px;")

            if v.is_given:
                kaynak = QLabel("girdi")
                kaynak.setStyleSheet(f"color:{MUTED}; font-size:11px;")
                deg.setStyleSheet("font-size:12px;")
            else:
                turetilen += 1
                kaynak = QLabel(v.rule)
                kaynak.setStyleSheet(f"color:{GOOD}; font-size:11px;")
                # Türetilen değer vurgulanır: kullanıcının aradığı sayı bu.
                deg.setStyleSheet(f"color:{GOOD}; font-size:12px; font-weight:600;")

            # İpucu: hangi değerlerden, hangi bağıntıyla, tam zincir.
            aciklama = res.describe(node)
            for w in (ad, deg, brm, kaynak):
                w.setToolTip(aciklama)

            self.sonuc_lay.addWidget(ad, satir, 0)
            self.sonuc_lay.addWidget(deg, satir, 1)
            self.sonuc_lay.addWidget(brm, satir, 2)
            self.sonuc_lay.addWidget(kaynak, satir, 3)
            satir += 1

        self.sonuc_lay.setColumnStretch(3, 1)
        self.sonuc_lay.setRowStretch(satir, 1)

        # ---- Özet ----
        parcalar = [f"<b>{len(given)}</b> girdi → "
                    f"<b style='color:{GOOD}'>{turetilen}</b> değer türetildi."]
        # En çok aranan büyüklükleri özetin ilk satırında göster. Uzun
        # tabloda FOV'u aramak zorunda kalmak, "hesaplanmamış" izlenimi
        # veriyordu — hesaplanmış olduğu hâlde.
        one_cikan = []
        for node in ("fov_x_deg", "ifov_x_urad", "lens_f_mm", "scr_ang_deg"):
            v = res.values.get(node)
            if v is None or v.is_given:
                continue
            one_cikan.append(
                f"{solver.label(node)} = <b>{_fmt(v.value, node)}</b> "
                f"{solver.unit(node)}".rstrip())
        if one_cikan:
            parcalar.append(
                f"<span style='color:{GOOD}'>" + " · ".join(one_cikan[:3])
                + "</span>")
        # Çözülemeyenlerden, DIŞARIDAN gelmesi gereken büyüklükleri ayıkla:
        # onlar türetilemez, boş kalmaları eksiklik değildir. Kalanlar
        # gerçekten "bir girdi daha gerekiyor" diyebileceğimiz olanlardır.
        eksik = [n for n in res.unresolved
                 if n not in DISARIDAN_GELEN and n not in TURETILEN_CIKTI]
        if eksik:
            adlar = ", ".join(solver.label(n) for n in eksik[:6])
            if len(eksik) > 6:
                adlar += f" (+{len(eksik) - 6})"
            parcalar.append(
                f"<span style='color:{WARN}'>Türetilemedi: {adlar}</span> — "
                "bunlar için yeterli bilinen yok; bir girdi daha gerekiyor.")
        # Dışarıdan gelenler ayrı ve YUMUŞAK bir dille bildirilir: bilgi,
        # uyarı değil.
        dis = [n for n in res.unresolved if n in DISARIDAN_GELEN]
        if dis:
            parcalar.append(
                f"<span style='color:{MUTED}'>Girilmedi: "
                + ", ".join(solver.label(n) for n in dis)
                + " — bu değerler datasheet'ten ya da ölçümden gelir, "
                  "donanımdan türetilemez. Boş kalmaları sorun değildir.</span>")
        if res.conflicts:
            # Aynı büyüklük birden çok kuraldan çelişebilir (FOV hem
            # sensör boyutundan hem N×IFOV'dan). Kullanıcı için bunlar TEK
            # bir tutarsızlıktır; hepsini tek tek dökmek okunmaz bir duvar
            # yapıyordu. Büyüklük başına en büyük sapmayı gösteriyoruz ve
            # ham düğüm adı yerine insan-okur etiket kullanıyoruz.
            en_kotu: dict[str, object] = {}
            for c in res.conflicts:
                onceki = en_kotu.get(c.name)
                if onceki is None or c.rel_error > onceki.rel_error:
                    en_kotu[c.name] = c
            satir = []
            for c in sorted(en_kotu.values(), key=lambda x: -x.rel_error)[:3]:
                satir.append(
                    f"{solver.label(c.name)}: girdiniz "
                    f"<b>{_fmt(c.given, c.name)}</b>, diğer değerler "
                    f"<b>{_fmt(c.derived, c.name)}</b> gerektiriyor "
                    f"(%{c.rel_error * 100:.1f} fark)")
            fazla = len(en_kotu) - 3
            ek = f" (+{fazla} büyüklük daha)" if fazla > 0 else ""
            parcalar.append(
                f"<span style='color:{BAD}'><b>ÇELİŞKİ</b> — "
                + "; ".join(satir) + ek
                + ". Girdiğiniz değerler korundu, üzerine yazılmadı; "
                  "hangisinin yanlış olduğuna siz karar verin.</span>")
        self.lbl_ozet.setText("<br>".join(parcalar))
        self.lbl_ozet.setStyleSheet("font-size:12px;")
