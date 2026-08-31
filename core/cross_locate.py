# -*- coding: utf-8 -*-
"""
Merkez cross'unun doğrudan tespiti — decenter'ı hizalamadan bağımsız ölçer.

NEDEN AYRI BİR YOL. Decenter tanım gereği MERKEZ KAÇIKLIĞIDIR: desenin
merkezi sensörün merkezinden ne kadar uzakta. Bu tek bir noktanın
konumudur ve desen tam da bunun için ortasında bir cross taşır.

Buna rağmen `measure_pointing` decenter'ı homografiden türetiyordu; yani
önce TÜM desenin hizalanması gerekiyordu. Eş merkezli halka deseni
dairesel simetriktir, faz korelasyonu dönmeyi çözemez ve hizalama
kırılgandır. Gerçek bir ölçümde (CMV4000 + OLED) bu somut olarak
görüldü:

    tüm deseni hizalama (faz korelasyonu) : NCC 0.09  -> çöktü
    yalnızca cross'u eşleme               : NCC 0.96  -> decenter 0.52°

Hizalama çökünce decenter, roll, tilt ve tüm kapsama satırları birden
"ölçülemedi" oluyordu — oysa decenter ölçülebilir durumdaydı. Bu modül o
bağımlılığı koparır.

NE ÖLÇER, NE ÖLÇMEZ. Cross'tan yalnızca MERKEZ çıkar (2 serbestlik: x, y).
Roll ve tilt çıkmaz:

  * Cross 4 kat simetriktir — 90° döndürünce kendine benzer, dolayısıyla
    okunan dönme 0..90° ile sınırlıdır; 5° ile 95° ayırt edilemez.
  * Tilt (keystone) YAYILMIŞ bir ölçüm ister. Merkezde keystone tanım
    gereği sıfırdır; kadrajın kenarlarındaki farklı büyütmeyi görmek
    gerekir. Tek noktadan çıkmaz.

Roll ve tilt için desenin dört köşesindeki F harfleri vardır: F asimetrik
olduğu için dönmeyi 0..360° tam çözer ve ayna durumunu da belirler.
Onların tespiti bu modülün kapsamı DIŞINDADIR.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class CrossResult:
    """Cross tespiti sonucu — dedektör piksel uzayında."""
    ok: bool = False
    x_px: float = float("nan")        # cross merkezi (dedektör pikseli)
    y_px: float = float("nan")
    score: float = float("nan")       # şablon eşleme NCC'si (0..1)
    scale: float = float("nan")       # GT -> dedektör ölçeği (şablondan)
    template_px: int = 0              # kullanılan şablonun kenarı (dedektörde)
    messages: list = field(default_factory=list)


# Eşleşmenin "bulundu" sayılması için gereken en düşük NCC. Şablon eşleme
# yanlış yerde de bir tepe üretir; eşik olmadan her görüntüde bir "cross"
# bulunurdu. 0.45 deneysel: gerçek ölçümde doğru eşleşme 0.96 verirken,
# deseni olmayan düz alanlarda tepe 0.2'nin altında kalıyor.
MIN_SCORE = 0.45


def extract_template(gt: np.ndarray,
                     center_px: tuple | None = None,
                     radius_px: int = 24) -> np.ndarray:
    """
    Ground truth'un merkezinden cross şablonunu keser.

    `radius_px` ilk halkanın İÇİNDE kalmalıdır; halka şablona girerse
    eşleşme deseni değil halkayı takip eder. Varsayılan 24 px, 1°/halka
    desenlerinde ilk halkanın (r≈37 px) rahatça içinde.
    """
    h, w = gt.shape[:2]
    cx, cy = center_px if center_px is not None else ((w - 1) / 2.0, (h - 1) / 2.0)
    cx, cy = int(round(cx)), int(round(cy))
    r = int(max(4, radius_px))
    x0, y0 = max(0, cx - r), max(0, cy - r)
    x1, y1 = min(w, cx + r), min(h, cy + r)
    return gt[y0:y1, x0:x1]


def locate_cross(det: np.ndarray,
                 gt: np.ndarray,
                 scale_hint: float | None = None,
                 gt_center_px: tuple | None = None,
                 template_radius_px: int = 24) -> CrossResult:
    """
    Dedektör görüntüsünde merkez cross'unu bulur.

    GT'nin merkezinden bir şablon keser, onu bir ÖLÇEK ARALIĞINDA büyütüp
    dedektörde arar ve en yüksek normalize korelasyonu veren konumu döner.
    Ölçek taraması gereklidir: GT ile dedektör arasındaki büyütme önceden
    bilinmez (sistemin ve OLED optiğinin ölçeğine bağlıdır).

    `scale_hint` verilirse tarama onun çevresinde daraltılır — halka
    yarıçaplarından gelen ölçek tahmini iyi bir ipucudur ve taramayı hem
    hızlandırır hem yanlış tepelerden korur.

    Ayna (flip) varyantları da denenir: dedektör görüntüsü çoğu düzenekte
    aynalanmıştır. Cross 4 kat simetrik olduğu için ayna genelde skoru
    değiştirmez, ama şablon tam simetrik olmayabilir (çizgi kalınlığı,
    kırpma) — denemek ucuz.
    """
    res = CrossResult()
    if det is None or gt is None or det.size == 0 or gt.size == 0:
        res.messages.append("Cross aranamadı: görüntü boş.")
        return res

    tmpl0 = extract_template(gt, gt_center_px, template_radius_px)
    if tmpl0.size == 0 or min(tmpl0.shape[:2]) < 6:
        res.messages.append("Cross şablonu çıkarılamadı (GT merkezi çok küçük).")
        return res

    # Ölçek aralığı. İpucu varsa ±%25, yoksa geniş tarama.
    if scale_hint is not None and math.isfinite(scale_hint) and scale_hint > 0:
        lo, hi = scale_hint * 0.75, scale_hint * 1.25
    else:
        lo, hi = 0.3, 4.0
    # Logaritmik adım: ölçek çarpımsal bir büyüklüktür, 0.3->0.4 ile
    # 3.0->4.0 aynı oransal adımdır.
    n_steps = 40
    scales = np.exp(np.linspace(math.log(lo), math.log(hi), n_steps))

    dh, dw = det.shape[:2]
    best = None
    for s in scales:
        th = int(round(tmpl0.shape[0] * s))
        tw = int(round(tmpl0.shape[1] * s))
        if th < 6 or tw < 6 or th >= dh or tw >= dw:
            continue
        T = cv2.resize(tmpl0, (tw, th), interpolation=cv2.INTER_CUBIC)
        for ad, D in (("raw", det),
                      ("flip_h", cv2.flip(det, 1)),
                      ("flip_v", cv2.flip(det, 0))):
            try:
                r = cv2.matchTemplate(D, T, cv2.TM_CCOEFF_NORMED)
            except cv2.error:
                continue
            _, mx, _, ml = cv2.minMaxLoc(r)
            if best is None or mx > best[0]:
                # Ayna varyantında bulunan konumu ORİJİNAL dedektör
                # koordinatına geri çevir; aksi halde decenter'ın işareti
                # ters çıkar.
                cx = ml[0] + tw / 2.0
                cy = ml[1] + th / 2.0
                if ad == "flip_h":
                    cx = dw - 1.0 - cx
                elif ad == "flip_v":
                    cy = dh - 1.0 - cy
                best = (float(mx), float(s), cx, cy, tw)

    if best is None:
        res.messages.append("Cross aranamadı: şablon her ölçekte görüntüden büyük.")
        return res

    score, s, cx, cy, tw = best
    res.score = score
    res.scale = s
    res.x_px, res.y_px = cx, cy
    res.template_px = tw

    if score < MIN_SCORE:
        res.messages.append(
            f"Merkez cross'u güvenle bulunamadı (korelasyon {score:.2f} < "
            f"{MIN_SCORE:.2f}) — decenter cross'tan ölçülemedi.")
        return res

    res.ok = True
    return res


def refine_subpixel(det: np.ndarray, x: float, y: float,
                    win: int = 5) -> tuple:
    """
    Şablon eşlemenin verdiği tamsayı konumu alt-piksele çeker.

    Şablon eşleme piksel ızgarasında çalışır; korelasyon tepesinin
    çevresine parabol oturtmak tipik olarak 0.1 px'e kadar iyileştirir.
    Decenter 100 px mertebesindeyken bu fark önemsizdir, ama küçük
    kaçıklıklarda ölçümün çözünürlüğünü belirler.
    """
    h, w = det.shape[:2]
    xi, yi = int(round(x)), int(round(y))
    r = max(2, win // 2)
    if xi - r < 0 or yi - r < 0 or xi + r + 1 > w or yi + r + 1 > h:
        return float(x), float(y)
    patch = det[yi - r:yi + r + 1, xi - r:xi + r + 1].astype(np.float64)
    patch = patch - patch.min()
    tot = patch.sum()
    if tot <= 0:
        return float(x), float(y)
    yy, xx = np.mgrid[0:patch.shape[0], 0:patch.shape[1]]
    mx = (patch * xx).sum() / tot
    my = (patch * yy).sum() / tot
    return float(xi - r + mx), float(yi - r + my)
