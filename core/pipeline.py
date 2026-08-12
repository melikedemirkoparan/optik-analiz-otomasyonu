"""
Tam analiz akışı — arayüzün çağırdığı tek giriş noktası.

Bu modül çekirdek parçaları (config / optics / image_analysis / siemens_star)
tek bir çağrıda birleştirir ve arayüze hazır bir sonuç nesnesi döndürür.
GUI'nin optik matematikle doğrudan uğraşması gerekmez.

Akış:
  1. Sistem parametrelerinden nominal FOV / IFOV hesapla (görüntüden bağımsız).
  2. GT + dedektör görüntülerini eşle -> homografi, ayna durumu, rotasyon.
  3. Merkezi Siemens star'dan elips fit -> güvenilir tilt ölçümü.
  4. Sonuçları tek yapıda topla; önizleme görüntülerini üret.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import SystemConfig
from . import optics, image_analysis, siemens_star, tilt_estimators


@dataclass
class AnalysisResult:
    """Bir analiz koşusunun tüm çıktıları."""
    # Nominal (parametrik) optik değerler
    fov: optics.FovResult | None = None

    # Görüntü eşleme
    match: image_analysis.MatchResult | None = None

    # Siemens star tabanlı tilt (asıl güvenilen kaynak)
    star: siemens_star.StarTiltResult | None = None

    # Çoklu yöntem tilt raporu (belirsizlik + yöntem seçimi)
    tilt: tilt_estimators.TiltReport | None = None

    # Önizleme görüntüleri (BGR, GUI için hazır)
    gt_preview: np.ndarray | None = None
    det_preview: np.ndarray | None = None
    overlay: np.ndarray | None = None

    # Durum
    ok: bool = False
    messages: list[str] = field(default_factory=list)

    # ---- Arayüzün göstereceği türetilmiş değerler ----

    @property
    def rotation_deg(self) -> float:
        """
        Düzlem-içi dönme. Öncelik homografiden (daha hassas, tüm görüntüyü
        kullanır); yoksa yıldız elipsinin eksen açısı farkından.
        """
        if self.match is not None and self.match.tilt is not None:
            return self.match.tilt.in_plane_rotation_deg
        if self.star is not None and self.star.ok:
            return self.star.rotation_deg
        return float("nan")

    @property
    def tilt_deg(self) -> float:
        """
        Düzlem-dışı tilt. Çoklu yöntem raporundan gelir (bkz. tilt_estimators);
        rapor yoksa eski davranışa düşer.

        DİKKAT: Bu sayı tek başına yeterli değildir — `tilt_sigma_deg` ve
        `tilt_resolvable` ile birlikte okunmalıdır. Değer gürültü sınırının
        altındaysa "tilt yok" değil "ayırt edilemiyor" demektir.
        """
        if self.tilt is not None and self.tilt.ok:
            return self.tilt.tilt_deg
        if self.star is not None and self.star.ok:
            return self.star.tilt_deg
        if self.match is not None and self.match.tilt is not None:
            return self.match.tilt.total_tilt_deg
        return float("nan")

    @property
    def tilt_sigma_deg(self) -> float:
        """Düzlem-dışı tilt ölçümünün 1-sigma belirsizliği (derece)."""
        return self.tilt.sigma_deg if self.tilt is not None else float("inf")

    @property
    def tilt_resolvable(self) -> bool:
        """Ölçüm kendi gürültüsünden ayırt edilebiliyor mu."""
        return bool(self.tilt.resolvable) if self.tilt is not None else False

    @property
    def tilt_method(self) -> str:
        """Tilt'i hangi yöntemin verdiği ("circle_ellipse", "grid_vanishing"...)."""
        return self.tilt.primary_method if self.tilt is not None else ""

    @property
    def tilt_summary(self) -> str:
        """Arayüzde gösterilecek dürüst özet: "1.83° ± 0.20°" ya da "< 3.6°"."""
        return self.tilt.summary() if self.tilt is not None else "ölçülemedi"

    @property
    def mirrored(self) -> bool:
        return bool(self.match.mirrored) if self.match is not None else False


def _preview(gray: np.ndarray, fit: siemens_star.EllipseFit | None) -> np.ndarray:
    """Gri görüntüyü, varsa elips çizimiyle BGR önizlemeye çevirir."""
    if fit is not None and fit.ok:
        return siemens_star.draw_ellipse(gray, fit)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def run_analysis(gt_path: str, det_path: str, cfg: SystemConfig,
                 use_sift: bool = True,
                 progress=None) -> AnalysisResult:
    """
    Tam analizi çalıştırır.

    progress: isteğe bağlı callable(yuzde:int, mesaj:str) — GUI ilerleme
              çubuğunu beslemek için.
    """
    def report(pct, msg):
        if progress is not None:
            progress(pct, msg)

    res = AnalysisResult()

    # --- 1. Parametre doğrulama ---
    errs = cfg.validate()
    if errs:
        res.messages.extend(errs)
        return res

    # --- 2. Nominal FOV / IFOV ---
    report(5, "FOV / IFOV hesaplanıyor…")
    res.fov = optics.compute_fov(cfg)

    # --- 3. Görüntüleri yükle ---
    report(15, "Görüntüler yükleniyor…")
    try:
        gt_gray = image_analysis.load_image_gray(gt_path)
        det_gray = image_analysis.load_image_gray(det_path)
    except FileNotFoundError as e:
        res.messages.append(str(e))
        return res

    # --- 4. Feature eşleme + homografi ---
    report(30, "Görüntüler eşleniyor (SIFT)…")
    try:
        res.match = image_analysis.analyze(gt_path, det_path, cfg,
                                           use_sift=use_sift)
        if res.match.homography is None:
            res.messages.append(
                "Görüntüler eşleştirilemedi — dönme/ayna bilgisi homografiden "
                "alınamadı. Tilt yine de yıldız elipsinden ölçülecek.")
    except Exception as e:                              # noqa: BLE001
        res.messages.append(f"Eşleme hatası: {e}")

    # --- 5. Siemens star elips tilt ---
    report(65, "Merkezi yıldız elipsi ölçülüyor…")
    try:
        res.star = siemens_star.analyze_pair(gt_gray, det_gray)
        if not res.star.ok:
            res.messages.append(
                "Merkezi Siemens star tespit edilemedi — görüntülerde merkezi "
                "radyal desen net görünmüyor olabilir.")
    except Exception as e:                              # noqa: BLE001
        res.messages.append(f"Elips tespit hatası: {e}")

    # --- 5b. Çoklu yöntem tilt ölçümü ---
    # Yıldız kadrajda olmasa bile tilt üretilebilsin ve her durumda
    # belirsizlik raporlansın diye ayrı bir katman.
    report(78, "Tilt yöntemleri değerlendiriliyor…")
    try:
        h_tilt = res.match.tilt if res.match is not None else None
        res.tilt = tilt_estimators.measure_tilt(gt_gray, det_gray, cfg,
                                                homography_tilt=h_tilt)
        res.messages.extend(res.tilt.messages)
    except Exception as e:                                  # noqa: BLE001
        res.messages.append(f"Tilt ölçüm katmanı hatası: {e}")

    # --- 6. Önizlemeler ---
    report(85, "Önizlemeler hazırlanıyor…")
    gt_fit = res.star.gt_ellipse if res.star is not None else None
    det_fit = res.star.det_ellipse if res.star is not None else None
    res.gt_preview = _preview(gt_gray, gt_fit)
    res.det_preview = _preview(det_gray, det_fit)

    if res.match is not None and res.match.homography is not None:
        try:
            res.overlay = image_analysis.make_overlay(gt_path, det_path, res.match)
        except Exception as e:                          # noqa: BLE001
            res.messages.append(f"Overlay üretilemedi: {e}")

    report(100, "Tamamlandı.")
    res.ok = res.fov is not None
    return res
