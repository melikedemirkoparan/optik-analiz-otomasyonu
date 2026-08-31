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
from . import (optics, image_analysis, siemens_star, tilt_estimators,
               dense_align, pointing, cross_locate, f_markers)


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

    # Yoğun (desen-agnostik) hizalama — piksel piksel kalıntı/distorsiyon.
    # SIFT yolunun YERİNE GEÇMEZ; yanında koşar ve karşılaştırılabilir.
    dense: dense_align.DenseResult | None = None

    # Yönelim hataları (decenter / roll / tilt) + FOV kapsaması.
    # Normalde yoğun hizalamanın homografisinden türetilir. Hizalama
    # çökerse decenter yine de dolar — merkez cross'undan doğrudan
    # ölçülür (bkz. `cross`); o durumda roll/tilt/kapsama boş kalır.
    pointing: pointing.PointingResult | None = None

    # Merkez cross'unun tespiti. Yalnızca hizalama çöktüğünde denenir;
    # decenter'ı homografiden bağımsız ölçmek için.
    cross: cross_locate.CrossResult | None = None

    # Köşe F işaretleri. Roll'ün mod-90 belirsizliğini ve ayna kararını
    # çözer; hizalamaya ve SIFT'e bağlı değildir.
    f_markers: f_markers.FMatch | None = None

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

    # ---- Yoğun hizalama türevleri ----

    @property
    def dense_ok(self) -> bool:
        return bool(self.dense is not None and self.dense.ok)

    @property
    def dense_rotation_deg(self) -> float:
        """Yoğun yolun ölçtüğü dönme — SIFT'in `rotation_deg`i ile kıyaslanır."""
        return self.dense.rotation_deg if self.dense_ok else float("nan")

    @property
    def distortion_summary(self) -> str:
        """
        Ölçek serbestliğinden arındırılmış distorsiyon özeti.

        DİKKAT: Bu ölçüm homografiye GÖRE kalıntıdır. Ground truth'un ölçeği
        bilinmediği için mutlak `f*tan(theta)` modeline göre değil, veriden
        çözülen en iyi projektif uyuma göre tanımlıdır.
        """
        if not self.dense_ok or self.dense.residual is None:
            return "ölçülemedi"
        return self.dense.residual.distortion_summary()

    @property
    def residual_summary(self) -> str:
        """Piksel piksel kalıntının büyüklük özeti."""
        if not self.dense_ok or self.dense.residual is None:
            return "ölçülemedi"
        return self.dense.residual.summary()

    # ---- Yönelim türevleri ----

    @property
    def pointing_ok(self) -> bool:
        return bool(self.pointing is not None and self.pointing.ok)

    @property
    def decenter_deg(self) -> float:
        """Desen merkezinin sensör merkezinden açısal kaçıklığı."""
        return self.pointing.decenter_deg if self.pointing_ok else float("nan")

    @property
    def roll_deg(self) -> float:
        """Düzlem-içi dönme — `rotation_deg` ile aynı büyüklük, yönelim dilinde."""
        return self.pointing.roll_deg if self.pointing_ok else float("nan")

    @property
    def pointing_summary(self) -> str:
        return self.pointing.summary() if self.pointing_ok else "ölçülemedi"

    @property
    def coverage_summary(self) -> str:
        return self.pointing.coverage_summary() if self.pointing_ok else "ölçülemedi"


def _preview(gray: np.ndarray, fit: siemens_star.EllipseFit | None) -> np.ndarray:
    """Gri görüntüyü, varsa elips çizimiyle BGR önizlemeye çevirir."""
    if fit is not None and fit.ok:
        return siemens_star.draw_ellipse(gray, fit)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def run_analysis(gt_path: str, det_path: str, cfg: SystemConfig,
                 use_sift: bool = True,
                 dense: bool = True,
                 pattern_center_px: tuple | None = None,
                 pattern_radius_px: float | None = None,
                 progress=None) -> AnalysisResult:
    """
    Tam analizi çalıştırır.

    progress: isteğe bağlı callable(yuzde:int, mesaj:str) — GUI ilerleme
              çubuğunu beslemek için.
    dense:    yoğun (desen-agnostik) hizalamayı da koşar. SIFT yolunun yerine
              GEÇMEZ, yanında koşar; piksel piksel kalıntı/distorsiyon
              haritasını yalnızca bu yol üretir. Kapatmak ölçümü hızlandırır.

    pattern_center_px: ground truth'ta desenin merkezi (x, y). Verilmezse
              görüntü merkezi kullanılır — merkezinde artı işareti olan
              paternlerde bu zaten doğrudur.
    pattern_radius_px: ground truth'ta desenin yarıçapı. Verilirse "desen
              sensöre sığıyor mu" ve "ne kadar pay var" hesaplanır.
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

    # --- 3B. Yoğun (desen-agnostik) hizalama ---
    # SIFT'ten ÖNCE koşar. Sebebi: kendine-benzer desenlerde kör SIFT
    # dejenere sonuç üretiyor (ayrıntı: image_analysis._guided_match).
    # Yoğun yolun homografisi SIFT'e ön-bilgi olarak verilince SIFT'in
    # büyük dönme/ölçeği kendi başına bulması gerekmiyor. Yoğun yol yine
    # bağımsız bir ölçümdür; SIFT'in yerine geçmez.
    if dense:
        report(25, "Yoğun hizalama (piksel piksel)…")
        try:
            # Polarite uyumu: beyaz zeminli GT ile koyu zeminli çekim
            # yoğun hizalamada da eşleşmez (ECC yoğunluk korelasyonudur).
            # Tersleme geometriyi değiştirmez.
            gt_dense, inv = image_analysis.match_polarity(gt_gray, det_gray)
            if inv:
                res.messages.append(
                    "Bilgi: ground truth ile dedektörün kontrast polaritesi "
                    "ters — eşleme için ground truth terslendi; geometri ve "
                    "ölçüm etkilenmez.")
            res.dense = dense_align.analyze_dense(gt_dense, det_gray)
            res.messages.extend(res.dense.messages)
        except Exception as e:                              # noqa: BLE001
            res.messages.append(f"Yoğun hizalama hatası: {e}")

    # --- 4. Feature eşleme + homografi ---
    report(45, "Görüntüler eşleniyor (SIFT)…")
    try:
        prior_H = res.dense.homography if res.dense is not None else None
        prior_variant = (res.dense.coarse.variant
                         if res.dense is not None and res.dense.coarse is not None
                         else None)
        res.match = image_analysis.analyze(gt_path, det_path, cfg,
                                           use_sift=use_sift,
                                           prior_H=prior_H,
                                           prior_variant=prior_variant)
        if res.match.homography is None:
            res.messages.append(
                "Görüntüler eşleştirilemedi — dönme/ayna bilgisi homografiden "
                "alınamadı. Tilt yine de yıldız elipsinden ölçülecek.")
        elif res.match.guided:
            res.messages.append(
                f"Bilgi: eşleme, yoğun hizalamanın homografisiyle güdümlü "
                f"yapıldı — kör SIFT bu desende çözemiyor "
                f"({res.match.guided_matches} eşleşme, "
                f"{res.match.num_inliers} inlier, "
                f"{res.match.reproj_error_px:.2f} px).")
    except Exception as e:                              # noqa: BLE001
        res.messages.append(f"Eşleme hatası: {e}")

    # --- 5. Siemens star elips tilt ---
    report(65, "Merkezi yıldız elipsi ölçülüyor…")
    star_missing = False
    try:
        res.star = siemens_star.analyze_pair(gt_gray, det_gray)
        star_missing = not res.star.ok
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

    # "Siemens star bulunamadı" ancak HİÇBİR yöntem tilt ölçemediyse bir
    # eksikliktir. Eş merkezli çember paterninde yıldız zaten yoktur ve tilt
    # halka-fit ile ölçülür; o durumda bu satır ölçüm başarılıyken de uyarı
    # yazıyordu. Karar bu yüzden tilt katmanından SONRA verilir.
    if star_missing:
        if res.tilt is not None and res.tilt.ok:
            res.messages.append(
                f"Bilgi: merkezi Siemens star yok — tilt "
                f"'{res.tilt.primary_method}' yöntemiyle ölçüldü.")
        else:
            res.messages.append(
                "Merkezi Siemens star tespit edilemedi — görüntülerde merkezi "
                "radyal desen net görünmüyor olabilir.")

    # --- 5d. Yönelim hataları (decenter / roll / tilt) + kapsama ---
    # Yoğun hizalamanın homografisinden türetilir. SIFT homografisi de
    # kullanılabilir; yoğun yol tercih edilir çünkü desen-agnostiktir ve
    # kendine-benzer desenlerde sahte sonuç üretmez.
    if res.dense is not None and res.dense.homography is not None:
        report(90, "Yönelim hataları ölçülüyor…")
        try:
            det_v = dense_align.variants(det_gray).get(
                res.dense.coarse.variant, det_gray)
            res.pointing = pointing.measure_pointing(
                res.dense.homography, gt_gray.shape, det_v.shape, cfg,
                tilt=res.dense.tilt,
                pattern_center_px=pattern_center_px,
                pattern_radius_px=pattern_radius_px)
            res.messages.extend(res.pointing.messages)

            # --- Roll ve ayna: F işaretlerinden (ŞİMDİLİK DEVRE DIŞI) ---
            #
            # `f_markers` roll'ün mod-90 belirsizliğini kaldırmayı ve ayna
            # kararını SIFT'ten bağımsız vermeyi hedefler. Yöntem gerçek bir
            # ölçümde doğru sonuç verdi (roll 134.73°, ayna EVET) AMA
            # doğrulama testini geçemedi:
            #
            #   Aynı dedektör görüntüsü bilinen açılarla döndürülüp yöntem
            #   tekrar koşuldu. 8 dönmeden 4'ü yanlış çıktı ve hatalar
            #   90'ın katları civarındaydı (90.1°, 82.5°, 128.6°) — yani
            #   F'ler bulunuyor ama hangi F'nin hangisine karşılık geldiği
            #   yanlış çözülüyor. Ayna kararı da dönmeyle değişiyordu, oysa
            #   dönme aynayı etkilemez.
            #
            # Tek bir koşuda doğru çıkması yöntemin çalıştığını göstermez.
            # Belirsiz bir sayıyı kesin gibi göstermektense homografinin
            # dürüst "mod 90°" değerinde kalıyoruz. Düzeltilip döndürme
            # testinin 8/8'i geçince yeniden bağlanacak.
        except Exception as e:                              # noqa: BLE001
            res.messages.append(f"Yönelim ölçüm hatası: {e}")
    else:
        # Hizalama çöktü. ESKİDEN BURADA HİÇBİR ŞEY YAPILMIYORDU ve
        # decenter dahil bütün yönelim satırları "ölçülemedi" oluyordu.
        #
        # Oysa decenter merkez kaçıklığıdır ve desen tam bunun için
        # ortasında bir cross taşır; tüm deseni hizalamak gerekmez. Eş
        # merkezli halka deseni dairesel simetrik olduğu için faz
        # korelasyonu kırılgandır (gerçek bir ölçümde NCC 0.09), ama aynı
        # görüntüde cross şablonla NCC 0.96 bulunuyor.
        #
        # Bu yol YALNIZCA decenter'ı doldurur. Roll/tilt cross'tan çıkmaz
        # (4 kat simetrik + keystone merkezde sıfır), kapsama da desenin
        # sensöre düşen alanını ister; onlar eksik kalır.
        report(90, "Merkez cross'undan decenter ölçülüyor…")
        try:
            olcek_ipucu = (res.dense.coarse.scale
                           if (res.dense is not None
                               and res.dense.coarse is not None) else None)
            cr = cross_locate.locate_cross(det_gray, gt_gray,
                                           scale_hint=olcek_ipucu,
                                           gt_center_px=pattern_center_px)
            res.cross = cr
            res.messages.extend(cr.messages)
            if cr.ok:
                cx, cy = cross_locate.refine_subpixel(det_gray, cr.x_px, cr.y_px)
                res.pointing = pointing.measure_decenter_from_cross(
                    cx, cy, det_gray.shape, cfg)
                res.messages.extend(res.pointing.messages)
        except Exception as e:                              # noqa: BLE001
            res.messages.append(f"Cross tabanlı decenter hatası: {e}")

    # --- 6. Önizlemeler ---
    report(92, "Önizlemeler hazırlanıyor…")
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
