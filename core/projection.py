"""
Lens projeksiyon modelleri — FOV/IFOV matematiğinin altındaki asıl varsayım.

NEDEN AYRI BİR MODÜL
--------------------
Proje bugüne kadar TEK bir varsayımla çalıştı: `r = f·tan(θ)` (rektilineer /
pinhole). `optics.compute_fov`, `pointing`, `RefScreen.half_angle_deg` —
hepsi bu formülü ayrı ayrı içinde taşıyordu. Bu iki soruna yol açıyor:

1. **Varsayım görünmez.** Kullanıcı "FOV bazen yanlış çıkıyor" dediğinde
   hangi varsayımın tutmadığı sorulamıyor, çünkü varsayımın adı bile yok.
2. **Her lens rektilineer değil.** Geniş açılı ve balıkgözü objektifler
   equidistant (f-theta), equisolid, stereografik ya da ortografik
   haritalar. Rektilineer formülü onlara uygulamak sistematik hata üretir.

Bu modül beş standart modeli tek yerde toplar; `optics` ve `solver` onu
çağırır. Böylece projeksiyon **parametrik bir alan** olur — tıpkı f ve pitch
gibi — ve projenin "hiçbir değer koda gömülü değil" kuralına uyar.

MODELLER (y' = görüntü yüksekliği, θ = yarı alan açısı)
------------------------------------------------------
    rectilinear    y' = f·tan(θ)        düz çizgileri korur; 40-60° tasarımların
                                        standardı. Pinhole kamera modeli budur.
    equidistant    y' = f·θ             "f-theta". Balıkgözü ve ölçüm
                                        objektiflerinin en yaygın modeli;
                                        OpenCV'nin fisheye modülünün TABANI.
    equisolid      y' = 2f·sin(θ/2)     eşit-alan; ışık ölçümü / gökyüzü.
    stereographic  y' = 2f·tan(θ/2)     açıları yerel olarak korur.
    orthographic   y' = f·sin(θ)        θ<90° ile sınırlı; nadir.

Kaynaklar: Optics for Hire "Types of Projections in Wide Angle Lenses"
(Tablo 1.1), Kannala & Brandt (2006) genelleştirilmiş balıkgözü modeli,
OpenCV `cv::fisheye` (θ_d = θ(1+k1θ²+k2θ⁴+k3θ⁶+k4θ⁸), taban equidistant).
`test_projection.py` bu formülleri OpenCV'nin kendi çıktısıyla karşılaştırır.

HANGİSİNİ SEÇMELİ
-----------------
Datasheet açıkça yazmıyorsa **ölçtür**: `fit_projection_model()` bilinen
açı-yarıçap çiftlerinden (STOS deseni tam olarak bunu sağlar — her çemberin
açısı bilinir) hangi modelin uyduğunu seçer. Tahmin etme, ölç.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

RECTILINEAR = "rectilinear"
EQUIDISTANT = "equidistant"
EQUISOLID = "equisolid"
STEREOGRAPHIC = "stereographic"
ORTHOGRAPHIC = "orthographic"

MODELS = (RECTILINEAR, EQUIDISTANT, EQUISOLID, STEREOGRAPHIC, ORTHOGRAPHIC)

MODEL_LABELS: dict[str, str] = {
    RECTILINEAR: "Rektilineer (pinhole) — r = f·tan θ",
    EQUIDISTANT: "Equidistant (f-theta) — r = f·θ",
    EQUISOLID: "Equisolid (eşit alan) — r = 2f·sin(θ/2)",
    STEREOGRAPHIC: "Stereografik — r = 2f·tan(θ/2)",
    ORTHOGRAPHIC: "Ortografik — r = f·sin θ",
}


# Arayüzdeki seçicinin ipuçları. Kullanıcı listeyi açtığında her kalemin
# NE ANLAMA geldiğini ve NEREDE kullanıldığını görmeli — model seçimi
# FOV/IFOV'un tamamını belirlediği için körlemesine yapılmamalı.
MODEL_HELP: dict[str, str] = {
    RECTILINEAR:
        "r = f · tan θ\n\n"
        "Düz çizgileri düz tutan klasik perspektif; pinhole kamera modeli.\n"
        "40-60° alanlı objektiflerin tasarım standardıdır ve bu projenin\n"
        "VARSAYILANIDIR — doğrulanmış referans değerler bu modelle üretildi.\n\n"
        "Piksel ölçeği alan boyunca SABİT DEĞİLDİR: kenar pikseli merkezden\n"
        "daha küçük bir açı görür. 120-140°'nin ötesine geçemez (tan ıraksar).",
    EQUIDISTANT:
        "r = f · θ\n\n"
        "Yarıçap açıyla doğru orantılı — \"f-theta\" da denir. Ölçüm\n"
        "objektiflerinin ve balıkgözü lenslerin en yaygın modeli;\n"
        "OpenCV'nin fisheye modülünün TABANI budur.\n\n"
        "Piksel ölçeği alan boyunca SABİTTİR: kenar pikseli merkezle aynı\n"
        "açıyı görür. 'FOV = N × IFOV' yaklaşımı yalnız bu modelde TAM doğrudur.",
    EQUISOLID:
        "r = 2f · sin(θ/2)\n\n"
        "Eşit katı açı, sensörde eşit alan kaplar. Işık ölçümü, gökyüzü\n"
        "kapsaması ve tüm-gökyüzü kameralarında kullanılır.\n"
        "180°'nin ötesine geçebilir.",
    STEREOGRAPHIC:
        "r = 2f · tan(θ/2)\n\n"
        "Açıları YEREL olarak korur (konformal): küçük şekiller kenarda da\n"
        "şeklini korur, sadece büyür. Panoramik gösterimde tercih edilir.",
    ORTHOGRAPHIC:
        "r = f · sin θ\n\n"
        "Küresel bir yüzeye dik izdüşüm. θ = 90°'de yarıçap doyuma ulaşır,\n"
        "ötesi tanımsızdır — bu yüzden nadir kullanılır.\n"
        "r > f olan hiçbir noktanın çözümü yoktur.",
}

# Her modelin geçerli olduğu en büyük YARIM alan açısı (derece).
# Rektilineerde tan(90°) ıraksar; ortografikte sin tepe yapar ve ötesi
# tersine dönerek çift değerli olur. Sınır dışı sorgu NaN döner —
# sayı uydurmak yerine "bu model burada tanımsız" demek doğru cevap
# (§7B'deki "ölçemiyorsan yazma" kuralının projeksiyondaki karşılığı).
MODEL_MAX_HALF_ANGLE_DEG: dict[str, float] = {
    RECTILINEAR: 89.0,
    EQUIDISTANT: 180.0,
    EQUISOLID: 180.0,
    STEREOGRAPHIC: 179.0,
    ORTHOGRAPHIC: 90.0,
}


# --------------------------------------------------------------------------
# İleri yön: açı -> görüntü yüksekliği
# --------------------------------------------------------------------------

def image_height_mm(model: str, focal_mm: float, half_angle_deg: float) -> float:
    """
    Yarı alan açısının sensör üzerinde optik eksenden uzaklığı (mm).

    Model bilinmiyorsa ya da açı modelin tanım aralığı dışındaysa NaN döner.
    """
    if focal_mm <= 0 or not math.isfinite(half_angle_deg):
        return float("nan")
    lim = MODEL_MAX_HALF_ANGLE_DEG.get(model)
    if lim is None or abs(half_angle_deg) > lim:
        return float("nan")
    t = math.radians(abs(half_angle_deg))
    if model == RECTILINEAR:
        return focal_mm * math.tan(t)
    if model == EQUIDISTANT:
        return focal_mm * t
    if model == EQUISOLID:
        return 2.0 * focal_mm * math.sin(t / 2.0)
    if model == STEREOGRAPHIC:
        return 2.0 * focal_mm * math.tan(t / 2.0)
    if model == ORTHOGRAPHIC:
        return focal_mm * math.sin(t)
    return float("nan")


# --------------------------------------------------------------------------
# Ters yön: görüntü yüksekliği -> açı
# --------------------------------------------------------------------------

def half_angle_deg(model: str, focal_mm: float, height_mm: float) -> float:
    """
    Optik eksenden `height_mm` uzaklıktaki noktanın yarı alan açısı (derece).

    Her model KAPALI FORMLA tersine çevrilir — sayısal arama yok, çünkü
    kapalı form hem kesin hem de hangi durumda tanımsız olduğunu açıkça
    gösterir. Tanım dışı girdide (örn. ortografikte h > f) NaN döner.
    """
    if focal_mm <= 0 or not math.isfinite(height_mm):
        return float("nan")
    h = abs(height_mm)
    if model == RECTILINEAR:
        return math.degrees(math.atan(h / focal_mm))
    if model == EQUIDISTANT:
        t = h / focal_mm
        return math.degrees(t) if t <= math.pi else float("nan")
    if model == EQUISOLID:
        s = h / (2.0 * focal_mm)
        return math.degrees(2.0 * math.asin(s)) if s <= 1.0 else float("nan")
    if model == STEREOGRAPHIC:
        return math.degrees(2.0 * math.atan(h / (2.0 * focal_mm)))
    if model == ORTHOGRAPHIC:
        s = h / focal_mm
        return math.degrees(math.asin(s)) if s <= 1.0 else float("nan")
    return float("nan")


def full_fov_deg(model: str, focal_mm: float, sensor_mm: float) -> float:
    """Tam sensör boyutunun (mm) gördüğü TAM alan açısı (derece)."""
    return 2.0 * half_angle_deg(model, focal_mm, sensor_mm / 2.0)


def sensor_mm_for_fov(model: str, focal_mm: float, fov_deg: float) -> float:
    """Verilen tam FOV'u kapsamak için gereken sensör boyutu (mm)."""
    return 2.0 * image_height_mm(model, focal_mm, fov_deg / 2.0)


def focal_for_fov_mm(model: str, sensor_mm: float, fov_deg: float) -> float:
    """
    Verilen sensör boyutu ve tam FOV'dan odak uzaklığı (mm).

    Bütün modellerde y' = f · g(θ) biçiminde olduğu için f = y' / g(θ);
    yani birim odakla hesaplanan yüksekliğe bölmek yeterli, ayrı ters
    formül yazmaya gerek yok.
    """
    if sensor_mm <= 0:
        return float("nan")
    g = image_height_mm(model, 1.0, fov_deg / 2.0)
    if not math.isfinite(g) or g <= 0:
        return float("nan")
    return (sensor_mm / 2.0) / g


def ifov_rad(model: str, focal_mm: float, pitch_mm: float,
             field_half_angle_deg: float = 0.0) -> float:
    """
    Tek pikselin gördüğü açı (radyan).

    `field_half_angle_deg` = 0 iken MERKEZ pikselin IFOV'u verilir; bu,
    projenin bugüne kadar raporladığı değerdir (rektilineerde
    `2·atan(pitch/2f)`).

    Sıfırdan farklı verilirse o alan açısındaki YEREL IFOV hesaplanır:
    piksel ölçeği alan boyunca sabit DEĞİLDİR. Rektilineerde kenar pikseli
    merkez pikselinden daha küçük bir açı görür (cos²θ ile daralır);
    equidistant'ta ise tanım gereği sabittir. Bu fark, "FOV = N × IFOV"
    yaklaşımının neden kenarda bozulduğunun asıl sebebidir.
    """
    if focal_mm <= 0 or pitch_mm <= 0:
        return float("nan")
    t0 = abs(field_half_angle_deg)
    r0 = image_height_mm(model, focal_mm, t0)
    if not math.isfinite(r0):
        return float("nan")

    # Piksel, yarıçap ekseninde r0 - pitch/2 ile r0 + pitch/2 arasını kaplar;
    # gördüğü açı bu iki kenarın açı FARKIDIR.
    #
    # Merkezde (r0 = 0) piksel eksenin İKİ YANINA yayılır: alt kenar
    # -pitch/2'dedir, açısı da negatif yönde aynı büyüklüktedir. `half_angle_deg`
    # mutlak yarıçapla çalıştığı (işaret taşımadığı) için oradan -a1 yerine
    # +a1 döner; negatif tarafı elle geri koymazsak sonuç tam YARIYA düşer.
    # Rektilineerde bu, 78.57 yerine 39.29 µrad/px demek olurdu.
    lo_mm = r0 - pitch_mm / 2.0
    a_hi = half_angle_deg(model, focal_mm, r0 + pitch_mm / 2.0)
    a_lo = half_angle_deg(model, focal_mm, abs(lo_mm))
    if not (math.isfinite(a_hi) and math.isfinite(a_lo)):
        return float("nan")
    if lo_mm < 0:
        a_lo = -a_lo
    return math.radians(a_hi - a_lo)


# --------------------------------------------------------------------------
# Model uydurma — "tahmin etme, ölç"
# --------------------------------------------------------------------------

@dataclass
class ProjectionFit:
    """Bilinen açı-yarıçap çiftlerine en iyi uyan model."""
    model: str
    focal_mm: float             # uydurulan odak uzaklığı
    rms_px: float               # yarıçap artığının RMS'i (piksel)
    max_err_px: float
    n_points: int
    ranking: list[tuple[str, float]]   # (model, rms_px) — hepsi, sıralı

    @property
    def label(self) -> str:
        return MODEL_LABELS.get(self.model, self.model)

    def is_conclusive(self, margin: float = 1.5,
                      min_separation_px: float = 0.2) -> bool:
        """
        En iyi model ikinciden belirgin ayrılıyor mu.

        Ayrılmıyorsa veri modelleri AYIRT EDEMİYOR demektir — dar açı
        aralığında bütün modeller birbirine yakınsar (hepsi θ→0'da y'≈fθ).
        Bu durumda "model şudur" demek uydurmadır; çağıran taraf sonucu
        kesin diye sunmamalı.

        İKİ ÖLÇÜT BİRDEN gerekir ve ikincisi kritiktir:

        * **Bağıl** (`margin`): ikinci modelin artığı en iyinin en az
          `margin` katı olmalı.
        * **Mutlak** (`min_separation_px`): ikisi arasındaki artık farkı
          ölçülebilir bir büyüklükte — en az bu kadar piksel — olmalı.

        Mutlak taban olmadan sentetik/temiz veri yanıltır: 0.1-0.4° gibi dar
        bir aralıkta doğru modelin artığı ~1e-15 px, ikincisininki ~8e-6 px
        çıkar. ORAN milyarlarca kat, yani "çok kesin" görünür — ama fark
        pikselin milyonda biri, hiçbir gerçek ölçüm onu göremez. Kesinliği
        orana bakarak ilan etmek, §7F'deki "sıfırın şekli olmaz" hatasının
        aynısı olurdu: önce büyüklük, sonra oran.
        """
        if len(self.ranking) < 2:
            return False
        best, second = self.ranking[0][1], self.ranking[1][1]
        if second - best < min_separation_px:
            return False
        if best <= 0:
            return True
        return second / best >= margin


def fit_projection_model(angles_deg: list[float], radii_px: list[float],
                         pitch_mm: float,
                         focal_mm: float | None = None,
                         models: tuple[str, ...] = MODELS) -> ProjectionFit | None:
    """
    Bilinen (alan açısı, yarıçap) çiftlerinden gerçek projeksiyon modelini
    seçer.

    Veri kaynağı: STOS deseni. `generate_circle_pattern.py` çemberleri
    bilinen açılara yerleştirdiği için her çemberin açısı bilinir; dedektör
    görüntüsünde o çemberin yarıçapı ölçülür. Elde açı-yarıçap tablosu olur.

    `focal_mm` verilmezse her model için EN İYİ f de uydurulur (tek
    parametreli en küçük kareler: f = Σ(r·g) / Σ(g²)). Böylece modeller
    adil karşılaştırılır — biri sırf f'i daha iyi uyduğu için kazanmaz.

    Dönen `ranking` bütün modelleri artıklarıyla sıralar; `is_conclusive()`
    ayrımın anlamlı olup olmadığını söyler.
    """
    if pitch_mm <= 0:
        return None
    pts = [(float(a), float(r)) for a, r in zip(angles_deg, radii_px)
           if math.isfinite(a) and math.isfinite(r) and r > 0]
    if len(pts) < 2:
        return None

    ranking: list[tuple[str, float]] = []
    best: tuple[str, float, float, float] | None = None   # model,f,rms,maxerr

    for m in models:
        # Birim odakla model çekirdeği g(θ); ölçülen yarıçapı mm'ye çevir.
        g, r_mm = [], []
        for a, r_px in pts:
            gi = image_height_mm(m, 1.0, a)
            if not math.isfinite(gi):
                break
            g.append(gi)
            r_mm.append(r_px * pitch_mm)
        if len(g) != len(pts):
            continue                     # model bu açı aralığında tanımsız

        if focal_mm is not None:
            f = focal_mm
        else:
            denom = sum(x * x for x in g)
            if denom <= 0:
                continue
            f = sum(x * y for x, y in zip(g, r_mm)) / denom
        if not math.isfinite(f) or f <= 0:
            continue

        errs_px = [abs(f * gi - rm) / pitch_mm for gi, rm in zip(g, r_mm)]
        rms = math.sqrt(sum(e * e for e in errs_px) / len(errs_px))
        ranking.append((m, rms))
        if best is None or rms < best[2]:
            best = (m, f, rms, max(errs_px))

    if best is None:
        return None
    ranking.sort(key=lambda kv: kv[1])
    return ProjectionFit(model=best[0], focal_mm=best[1], rms_px=best[2],
                         max_err_px=best[3], n_points=len(pts), ranking=ranking)


def compare_models(focal_mm: float, sensor_mm: float) -> list[tuple[str, float]]:
    """
    Aynı donanımda modellerin verdiği FOV'ları karşılaştırır — "FOV yanlış
    çıkıyor" şüphesinde ilk bakılacak tablo. Fark küçükse sorun modelde
    değildir.
    """
    out = []
    for m in MODELS:
        out.append((m, full_fov_deg(m, focal_mm, sensor_mm)))
    return out


if __name__ == "__main__":
    f, pitch, n = 47.7, 0.018, 1024
    sensor = n * pitch
    print(f"f={f} mm, sensör={sensor:.3f} mm")
    for m, fov in compare_models(f, sensor):
        print(f"  {MODEL_LABELS[m]:<42} FOV {fov:8.4f}°")
