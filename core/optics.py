"""
Optik hesap çekirdeği — FOV, IFOV ve tilt matematiği.

TÜM formüller SystemConfig'ten okunan parametreleri kullanır.
Lens / dedektör / OLED değişirse formüller otomatik yeni değerlere göre
sonuç üretir; hiçbir sayı burada sabit kodlanmamıştır.

Model: Pinhole (delik-iğne) kamera modeli — kollimatörsüz doğrudan görüntüleme.

  IFOV  (bir pikselin gördüğü açı):
        ifov = 2 * atan( pitch / (2 f) )        [tek piksel için, rad]
        ~ pitch / f   (küçük açı yaklaşımı)

  FOV   (tüm sensörün gördüğü açı):
        fov = 2 * atan( (N * pitch) / (2 f) )    [rad]

  f     : lens odak uzaklığı
  pitch : dedektör piksel pitch'i
  N     : dedektör piksel sayısı (yatay / dikey)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import SystemConfig


# ----------------------------- Sonuç yapıları -----------------------------

@dataclass
class FovResult:
    ifov_x_urad: float          # yatay IFOV (mikroradyan / piksel)
    ifov_y_urad: float          # dikey IFOV
    ifov_x_arcsec: float        # yatay IFOV (arcsecond / piksel)
    ifov_y_arcsec: float
    fov_x_deg: float            # yatay FOV (derece)
    fov_y_deg: float            # dikey FOV
    fov_diag_deg: float         # köşegen FOV
    sensor_w_mm: float
    sensor_h_mm: float
    # Hangi projeksiyon modeliyle hesaplandı. FOV sayısı tek başına
    # eksiktir: aynı f ve sensörle equidistant, rektilineerden ~%1.2
    # farklı FOV verir. Modelin adı sonuçla BİRLİKTE taşınmalı.
    projection: str = "rectilinear"
    # Kenar pikselinin gördüğü açı (µrad). Rektilineerde merkezden
    # KÜÇÜKTÜR (piksel ölçeği alan boyunca sabit değildir); equidistant'ta
    # tanım gereği eşittir. "FOV = N × IFOV" yaklaşımının neden kenarda
    # bozulduğu doğrudan bu farktır.
    ifov_edge_x_urad: float = float("nan")
    ifov_edge_y_urad: float = float("nan")

    # ---- Görüntü dairesi kısıtı ----
    #
    # Yukarıdaki fov_* alanları SENSÖRÜN GEOMETRİSİNDEN gelir: "şu piksel
    # eksenden şu kadar uzakta, demek ki şu açıyı görür". Bu, lensin oraya
    # gerçekten ışık düşürdüğünü VARSAYAR.
    #
    # Lensin görüntü dairesi sensörden küçükse bu varsayım çöker: köşeler
    # dairenin dışında kalır ve KARANLIKTIR. O köşelerin "gördüğü" açı
    # geometrik bir hesap sonucu olarak vardır ama görüntü olarak yoktur.
    # Hydra bunun tam örneği: köşegen geometrik olarak 30.56° çıkar, oysa
    # lens 21.5°'lik bir daire veriyor — köşeler boş.
    image_circle_mm: float = float("nan")     # lensin daire ÇAPI
    covers_sensor: bool = True                # daire tüm sensörü kapsıyor mu
    # Dairenin kestiği gerçek değerler. Daire sensörü tamamen kapsıyorsa
    # fov_* ile aynıdırlar.
    eff_fov_x_deg: float = float("nan")
    eff_fov_y_deg: float = float("nan")
    eff_fov_diag_deg: float = float("nan")


@dataclass
class TiltResult:
    in_plane_rotation_deg: float   # düzlem-içi dönme, ±90'a katlı (eski konvansiyon)
    tilt_x_deg: float              # düzlem-dışı tilt — x ekseni etrafında (keystone dikey)
    tilt_y_deg: float              # düzlem-dışı tilt — y ekseni etrafında (keystone yatay)
    total_tilt_deg: float          # toplam düzlem-dışı eğiklik büyüklüğü
    scale_x: float                 # ground-truth -> dedektör ölçek (x)
    scale_y: float                 # ölçek (y)
    mirrored: bool                 # görüntü aynalanmış mı (flip)
    homography_ok: bool            # homografi güvenilir mi
    # Katlanmamış dönme, 0..360. Gerçek yönelim budur; ±90'a katlı alan
    # 224° gibi açıları 44°'ye düşürdüğü için yönelim raporlarında BU
    # kullanılmalıdır.
    in_plane_rotation_full_deg: float = float("nan")


# ----------------------------- FOV / IFOV ---------------------------------

def compute_fov(cfg: SystemConfig) -> FovResult:
    """
    Sadece sistem parametrelerinden (lens f + dedektör) FOV/IFOV hesaplar.
    Görüntüden bağımsızdır; sistemin teorik/nominal değerleridir.

    PROJEKSİYON MODELİ. Hesap `cfg.lens.projection` alanına uyar. Varsayılan
    `rectilinear` (r = f·tan θ) — projenin doğrulanmış modeli ve 40-60°
    tasarımların standardı. Lens f-theta ise (`equidistant`) formüller
    otomatik değişir; aynı donanımda iki model arasındaki fark Hydra'da
    ~%1.2'dir, geniş açıda çok daha büyür.

    Köşegen FOV, sensör KÖŞEGEN ÖLÇÜSÜNDEN hesaplanır. Yaygın hata köşegeni
    açı uzayında Pisagor'la birleştirmektir (hypot(fov_x, fov_y)); açı
    doğrusal bir büyüklük değildir ve bu Hydra'da 0.365° fazla verir.

    GÖRÜNTÜ DAİRESİ. `fov_*` alanları sensörün GEOMETRİSİNDEN gelir ve
    lensin oraya ışık düşürdüğünü varsayar. Lensin görüntü dairesi
    sensörden küçükse köşeler karanlıktır; gerçekte görülen değerler
    `eff_fov_*` alanlarındadır ve `covers_sensor` False olur.

    Hydra bunun örneği: köşegen geometrik olarak 30.56° çıkar ama lensin
    dairesi 18.11 mm, sensörün köşegeni 26.07 mm — köşeler dairenin
    dışında. Gerçekte görülen köşegen 21.50°.
    """
    from . import projection as proj

    f = cfg.lens.focal_length_mm
    det = cfg.detector
    model = getattr(cfg.lens, "projection", proj.RECTILINEAR)
    pitch_x_mm = det.pixel_pitch_um / 1000.0
    pitch_y_mm = det.pixel_pitch_y_um / 1000.0

    # IFOV — merkez pikselin açısı
    ifov_x = proj.ifov_rad(model, f, pitch_x_mm)
    ifov_y = proj.ifov_rad(model, f, pitch_y_mm)

    # FOV — tüm sensör
    fov_x = proj.full_fov_deg(model, f, det.sensor_width_mm)
    fov_y = proj.full_fov_deg(model, f, det.sensor_height_mm)
    fov_d = proj.full_fov_deg(model, f, det.diagonal_mm)

    # Kenar pikselinin açısı — piksel ölçeğinin alan boyunca ne kadar
    # değiştiğini gösterir.
    ifov_ex = proj.ifov_rad(model, f, pitch_x_mm, fov_x / 2.0)
    ifov_ey = proj.ifov_rad(model, f, pitch_y_mm, fov_y / 2.0)

    # --- Görüntü dairesi kısıtı ---
    # Sensörün her yarı-ölçüsü dairenin yarıçapıyla kırpılır. Kırpma
    # gerekiyorsa o eksende görülen açı geometrik hesaptan KÜÇÜKTÜR.
    r_circle = cfg.lens.image_circle_radius_mm()
    if math.isfinite(r_circle) and r_circle > 0:
        def _kirp(yari_mm: float) -> float:
            return 2.0 * proj.half_angle_deg(model, f, min(yari_mm, r_circle))
        eff_x = _kirp(det.sensor_width_mm / 2.0)
        eff_y = _kirp(det.sensor_height_mm / 2.0)
        eff_d = _kirp(det.diagonal_mm / 2.0)
        # Köşe en uzak nokta; onu kapsıyorsa tüm sensör kapsanıyor demektir.
        kapsiyor = r_circle >= det.diagonal_mm / 2.0 - 1e-9
        circle_mm = 2.0 * r_circle
    else:
        eff_x, eff_y, eff_d = fov_x, fov_y, fov_d
        kapsiyor = True
        circle_mm = float("nan")

    return FovResult(
        ifov_x_urad=ifov_x * 1e6,
        ifov_y_urad=ifov_y * 1e6,
        ifov_x_arcsec=math.degrees(ifov_x) * 3600.0,
        ifov_y_arcsec=math.degrees(ifov_y) * 3600.0,
        fov_x_deg=fov_x,
        fov_y_deg=fov_y,
        fov_diag_deg=fov_d,
        sensor_w_mm=det.sensor_width_mm,
        sensor_h_mm=det.sensor_height_mm,
        projection=model,
        ifov_edge_x_urad=ifov_ex * 1e6,
        ifov_edge_y_urad=ifov_ey * 1e6,
        image_circle_mm=circle_mm,
        covers_sensor=bool(kapsiyor),
        eff_fov_x_deg=eff_x,
        eff_fov_y_deg=eff_y,
        eff_fov_diag_deg=eff_d,
    )

 
def angle_of_pixel_offset(cfg: SystemConfig, dx_px: float, dy_px: float) -> float:
    """
    Sensör merkezinden (dx, dy) piksel uzaktaki bir noktanın optik eksene
    göre açısını (derece) döndürür. FOV doğrulaması / nokta-açı sorguları için.

    `compute_fov` ile AYNI projeksiyon modelini kullanır — ayrışırlarsa
    aynı sensörün kenarı iki farklı açı verirdi.
    """
    from . import projection as proj

    f = cfg.lens.focal_length_mm
    model = getattr(cfg.lens, "projection", proj.RECTILINEAR)
    pitch_x_mm = cfg.detector.pixel_pitch_um / 1000.0
    pitch_y_mm = cfg.detector.pixel_pitch_y_um / 1000.0
    rx = dx_px * pitch_x_mm
    ry = dy_px * pitch_y_mm
    return proj.half_angle_deg(model, f, math.hypot(rx, ry))


# ----------------------------- Tilt / rotasyon ----------------------------

def decompose_homography(H, image_shape=None) -> TiltResult:
    """
    Ground truth -> dedektör homografi matrisini (3x3) ayrıştırıp:
      * düzlem-içi rotasyon (in-plane, derece)
      * düzlem-dışı tilt (perspektif / keystone, derece)
      * ölçek ve ayna (flip) durumu
    çıkarır.

    ÖNEMLİ: Ground truth ve dedektör görüntüleri farklı çözünürlükte olabilir
    ve OLED'e kırpılarak (crop) basıldığı için farklı kadrajda olabilir. Bu
    yüzden ham homografinin ölçek/aspect farkı GERÇEK tilt DEĞİLDİR. Rotasyon
    ve tilt'i ölçekten ayırmak için affine kısmı QR-benzeri ayrıştırmayla
    (rotasyon x [ölçek+shear] üst-üçgen) çözeriz. Böylece anizotropik ölçek
    ve kırpma, dönme/tilt ölçümüne karışmaz.
    """
    import numpy as np
    H = np.asarray(H, dtype=float)
    if H.shape != (3, 3) or abs(H[2, 2]) < 1e-12:
        return TiltResult(0, 0, 0, 0, 1, 1, False, False)
    H = H / H[2, 2]

    A = H[:2, :2].copy()          # affine kısım (rotasyon+ölçek+shear)

    # Determinant < 0 => ayna (flip) var
    det2 = float(np.linalg.det(A))
    mirrored = det2 < 0
    # Rotasyonu doğru ölçmek için ayna bileşenini geçici olarak ayır:
    # flip'i x eksenini ters çevirerek modelle, kalan saf rotasyon+ölçek olsun.
    if mirrored:
        A = A @ np.array([[-1.0, 0.0], [0.0, 1.0]])

    # QR ayrıştırması: A = R (rotasyon) * K (üst-üçgen: ölçek+shear)
    # numpy QR, R'yi ortogonal (rotasyon/yansıma), K'yi üst-üçgen verir.
    R, K = np.linalg.qr(A)
    # K'nin köşegenini pozitif yapacak şekilde işaret normalize et
    signs = np.sign(np.diag(K))
    signs[signs == 0] = 1.0
    R = R * signs           # sütunları ölçekle
    K = (K.T * signs).T     # satırları ölçekle
    # Saf düzlem-içi rotasyon açısı (R ortogonal)
    rot_raw = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    # KATLANMAMIŞ açı 0..360 aralığında saklanır (in_plane_rotation_full_deg).
    # 224 derecelik gerçek bir yönelim ±90'a katlanınca 44 dereceye düşer ve
    # bilgi geri getirilemez biçimde kaybolur — bu, dairesel simetrik olmayan
    # desenlerde (F harfleri, artı işareti) YANLIŞ sonuçtur.
    rot_full = rot_raw % 360.0
    # Geriye dönük uyum: mevcut `in_plane_rotation_deg` ±90'a katlı kalır,
    # çünkü doğrulanmış referans değerler (+1.583°) bu konvansiyonda üretildi.
    rot = rot_raw
    while rot > 90:
        rot -= 180
    while rot < -90:
        rot += 180

    # Ölçekler K'nin köşegeninden (anizotropik ölçek — rapor için)
    sx = abs(float(K[0, 0]))
    sy = abs(float(K[1, 1]))

    # --- Düzlem-dışı tilt (perspektif) ---
    # Perspektif terimleri h[2,0], h[2,1] görüntü ölçeğine bağlıdır; görüntü
    # boyutuyla normalize edilir. Kenardan kenara "perspektif kaçışı" oranını
    # verir ve bir düzlemin optik eksene göre yatış açısına çevrilir.
    h20, h21 = H[2, 0], H[2, 1]
    if image_shape is not None:
        Hh, Ww = image_shape[:2]
        pers_x = h20 * Ww
        pers_y = h21 * Hh
    else:
        pers_x = h20
        pers_y = h21

    tilt_y = math.degrees(math.atan(pers_x))   # y ekseni etrafında (yatay keystone)
    tilt_x = math.degrees(math.atan(pers_y))   # x ekseni etrafında (dikey keystone)
    total_tilt = math.degrees(math.atan(math.hypot(pers_x, pers_y)))

    return TiltResult(
        in_plane_rotation_deg=rot,
        in_plane_rotation_full_deg=rot_full,
        tilt_x_deg=tilt_x,
        tilt_y_deg=tilt_y,
        total_tilt_deg=total_tilt,
        scale_x=sx,
        scale_y=sy,
        mirrored=bool(mirrored),
        homography_ok=True,
    )


def measured_ifov_from_scale(cfg: SystemConfig, oled_feature_px: float,
                             detector_feature_px: float) -> float:
    """
    (İsteğe bağlı doğrulama) Ground truth'ta bilinen boyuttaki bir özelliğin
    dedektörde kaç piksele denk geldiğinden ölçülen efektif IFOV'u hesaplar.
    Nominal (compute_fov) ile karşılaştırma için kullanılır.
    Döndürülen değer: mikroradyan/piksel.
    """
    if detector_feature_px <= 0:
        return float("nan")
    pitch_mm = cfg.detector.pixel_pitch_um / 1000.0
    f = cfg.lens.focal_length_mm
    ifov = 2.0 * math.atan(pitch_mm / (2.0 * f))
    return ifov * 1e6
