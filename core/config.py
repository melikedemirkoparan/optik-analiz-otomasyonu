"""
Parametrik konfigürasyon sistemi.

Tüm optik sistem bileşenleri (Lens, Dedektör, OLED) burada
parametrik olarak tanımlanır. Hiçbir değer hesap koduna gömülü DEĞİLDİR.
Bir bileşen değişirse (örn. lens), sadece parametreler güncellenir ve
matematik otomatik olarak yeni değerlere göre kurulur.

Preset'ler JSON dosyaları olarak presets/ altında saklanır ve
arayüzden yüklenip düzenlenebilir.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field, fields


@dataclass
class Lens:
    """Görüntüleme lensi parametreleri."""
    name: str = "Rodenstock HR Digaron-W"
    focal_length_mm: float = 70.0        # Odak uzaklığı (f)
    f_number: float = 5.6                # Diyafram (maksimum açıklık)
    pupil_diameter_mm: float = 0.0       # Giriş pupili çapı (0 = f/# ten türet)
    useful_fov_deg: float = 0.0          # Üreticinin verdiği kullanılabilir FOV
    # Lensin AÇI -> GÖRÜNTÜ YÜKSEKLİĞİ haritası. FOV/IFOV matematiğinin
    # altındaki asıl varsayım budur ve lense göre değişir:
    #   rectilinear  r = f·tan θ    (pinhole; 40-60° tasarımların standardı)
    #   equidistant  r = f·θ        (f-theta; ölçüm ve balıkgözü objektifleri)
    #   equisolid / stereographic / orthographic
    # Varsayılan rektilineerdir — projenin bugüne kadarki (ve doğrulanmış)
    # modeli odur. Datasheet açıkça yazmıyorsa TAHMİN ETME:
    # `projection.fit_projection_model` bilinen açı-yarıçap çiftlerinden
    # hangi modelin uyduğunu ölçer.
    projection: str = "rectilinear"
    # Lensin ürettiği GÖRÜNTÜ DAİRESİNİN çapı (mm). Bir lens dairesel bir
    # görüntü üretir; sensör bu dairenin içinde kalan kısmı görür. Daire
    # sensörden küçükse KÖŞELER KARANLIKTIR ve oradan gelen "FOV" gerçek
    # değildir — yalnızca o pikselin geometrik olarak göreceği açıdır.
    #
    # 0 = bilinmiyor/sınırsız (daire sensörü tamamen kapsıyor varsayılır).
    # `useful_fov_deg` verilmişse ondan da türetilebilir; ikisi de varsa
    # doğrudan verilen çap kullanılır.
    image_circle_mm: float = 0.0
    notes: str = ""

    @property
    def effective_pupil_mm(self) -> float:
        """
        Giriş pupili. Doğrudan verilmemişse f/# ten türetilir (D = f / N).
        Datasheet ikisini de veriyorsa doğrudan verilen değer kullanılır —
        gerçek pupil, ince lens yaklaşımından sapabilir.
        """
        if self.pupil_diameter_mm > 0:
            return self.pupil_diameter_mm
        if self.f_number > 0:
            return self.focal_length_mm / self.f_number
        return 0.0

    def image_circle_radius_mm(self) -> float:
        """
        Görüntü dairesinin yarıçapı (mm); bilinmiyorsa NaN.

        Öncelik doğrudan verilen çapta. Verilmemişse üreticinin
        `useful_fov_deg` değerinden türetilir — "kullanılabilir FOV" tam
        olarak lensin makul görüntü verdiği koninin açısıdır, o koninin
        sensör düzlemindeki izdüşümü de görüntü dairesidir.
        """
        import math
        if self.image_circle_mm > 0:
            return self.image_circle_mm / 2.0
        if self.useful_fov_deg > 0 and self.focal_length_mm > 0:
            from . import projection as proj
            r = proj.image_height_mm(self.projection, self.focal_length_mm,
                                     self.useful_fov_deg / 2.0)
            return r if math.isfinite(r) else float("nan")
        return float("nan")

    def validate(self) -> list[str]:
        errs = []
        if self.focal_length_mm <= 0:
            errs.append("Lens odak uzaklığı (focal_length_mm) > 0 olmalı.")
        if self.f_number <= 0:
            errs.append("Lens diyafram sayısı (f_number) > 0 olmalı.")
        from .projection import MODELS
        if self.projection not in MODELS:
            errs.append(f"Bilinmeyen projeksiyon modeli: {self.projection!r} "
                        f"(geçerli: {', '.join(MODELS)}).")
        if self.image_circle_mm < 0:
            errs.append("Görüntü dairesi çapı negatif olamaz.")
        return errs


@dataclass
class Detector:
    """Dedektör (görüntü sensörü) parametreleri."""
    name: str = "CMV4000 (ams/OSRAM)"
    width_px: int = 2048                 # Yatay piksel sayısı
    height_px: int = 2048                # Dikey piksel sayısı
    pixel_pitch_um: float = 5.5          # Piksel pitch (kare piksel varsayımı)
    pixel_pitch_y_um: float = 5.5        # Dikey pitch (dikdörtgen piksel destekli)
    notes: str = ""

    @property
    def sensor_width_mm(self) -> float:
        return self.width_px * self.pixel_pitch_um / 1000.0

    @property
    def sensor_height_mm(self) -> float:
        return self.height_px * self.pixel_pitch_y_um / 1000.0

    @property
    def diagonal_mm(self) -> float:
        return (self.sensor_width_mm ** 2 + self.sensor_height_mm ** 2) ** 0.5

    def validate(self) -> list[str]:
        errs = []
        if self.width_px <= 0 or self.height_px <= 0:
            errs.append("Dedektör çözünürlüğü (width_px/height_px) > 0 olmalı.")
        if self.pixel_pitch_um <= 0 or self.pixel_pitch_y_um <= 0:
            errs.append("Piksel pitch > 0 olmalı.")
        return errs


@dataclass
class RefScreen:
    """
    Referans görüntünün üretildiği ekran (OLED panel ya da STOS gibi bir
    görüntüleme/patern jeneratörü).

    İKİ FARKLI EKRAN TİPİ VAR ve ayrımı `angular_res_deg` yapar:

    * **Pasif panel (OLED):** Ekranın kendisi bir açısal ölçek tanımlamaz;
      desen fiziksel bir yüzeye basılır. `angular_res_deg = 0` bırakılır.

    * **Açısal kaynak (STOS):** Üretici her piksel için bir AÇI verir
      (derece/piksel). Bu, ekranın önünde bir optik olduğu anlamına gelir ve
      ima edilen bir odak uzaklığı doğurur:

          f_implied = pitch / tan(angular_res)

      Bu değer paternin açısal ölçeğini belirler; `generate_circle_pattern.py`
      çemberleri tam olarak buna göre yerleştirir (r = f*tan(theta)/pitch).
      Ölçümde ground truth'un açısal kalibrasyonu gerektiğinde kullanılır.
    """
    name: str = "GL049AMN10A (Guangli 0.49\" Micro-OLED)"
    width_px: int = 1920
    height_px: int = 1080
    pixel_pitch_um: float = 5.616
    active_width_mm: float = 10.783
    active_height_mm: float = 6.065
    angular_res_deg: float = 0.0     # >0 ise açısal kaynak (STOS gibi)
    notes: str = ""

    @property
    def is_angular_source(self) -> bool:
        """Ekran piksel başına bilinen bir açı üretiyor mu."""
        return self.angular_res_deg > 0.0

    @property
    def implied_focal_mm(self) -> float:
        """
        Açısal çözünürlükten ima edilen odak uzaklığı (mm).
        Pasif panelde tanımsızdır (0 döner).
        """
        if not self.is_angular_source:
            return 0.0
        import math
        return (self.pixel_pitch_um / 1000.0) / math.tan(
            math.radians(self.angular_res_deg))

    def half_angle_deg(self, r_px: float) -> float:
        """Ekran merkezinden r_px uzaklığın karşılık geldiği açı (derece)."""
        if not self.is_angular_source:
            return float("nan")
        import math
        f = self.implied_focal_mm
        if f <= 0:
            return float("nan")
        return math.degrees(math.atan(
            abs(r_px) * (self.pixel_pitch_um / 1000.0) / f))

    def radius_px_for_angle(self, theta_deg: float) -> float:
        """Verilen açının ekranda karşılık geldiği yarıçap (piksel)."""
        if not self.is_angular_source:
            return float("nan")
        import math
        f = self.implied_focal_mm
        pitch_mm = self.pixel_pitch_um / 1000.0
        if pitch_mm <= 0:
            return float("nan")
        return f * math.tan(math.radians(abs(theta_deg))) / pitch_mm

    def validate(self) -> list[str]:
        errs = []
        if self.width_px <= 0 or self.height_px <= 0:
            errs.append("Referans ekran çözünürlüğü > 0 olmalı.")
        if self.pixel_pitch_um <= 0:
            errs.append("Referans ekran piksel pitch > 0 olmalı.")
        if self.angular_res_deg < 0:
            errs.append("Açısal çözünürlük negatif olamaz.")
        return errs


# Geriye dönük uyum: eski kod ve preset JSON'ları "OLED" adını kullanıyor.
OLED = RefScreen


def _known_fields(cls, d: dict) -> dict:
    """
    Sözlükten yalnızca `cls`'in tanıdığı alanları süzer.

    Preset JSON'ları farklı sürümlerde yazılmış olabilir: eski dosyada yeni
    alan yoktur (varsayılan devreye girer), yeni dosyada eski koda göre
    fazladan alan olabilir. İkisi de kullanıcının kaydettiği bir dosyayı
    açılmaz hale getirmemeli.
    """
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


@dataclass
class SystemConfig:
    """
    Tüm optik sistemi tanımlayan üst-seviye konfigürasyon.

    setup_type:
      - "direct": Kollimatör yok. Dedektör görüntüsü doğrudan ground truth
                  ile karşılaştırılır. FOV/IFOV pinhole modeliyle lens f'ten
                  hesaplanır. (Bu projenin varsayılan senaryosu.)
      - "collimator": OLED + kollimatör ile sonsuza yansıtma (ileride destek).
    """
    name: str = "CMV4000 + Rodenstock 70mm + GL049 OLED"
    setup_type: str = "direct"
    lens: Lens = field(default_factory=Lens)
    detector: Detector = field(default_factory=Detector)
    oled: OLED = field(default_factory=OLED)
    collimator_focal_length_mm: float = 0.0   # setup_type="collimator" ise kullanılır

    def validate(self) -> list[str]:
        errs = []
        errs += self.lens.validate()
        errs += self.detector.validate()
        errs += self.oled.validate()
        if self.setup_type == "collimator" and self.collimator_focal_length_mm <= 0:
            errs.append("Kollimatör düzeneğinde collimator_focal_length_mm > 0 olmalı.")
        return errs

    # ---- Serileştirme ----
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "setup_type": self.setup_type,
            "collimator_focal_length_mm": self.collimator_focal_length_mm,
            "lens": asdict(self.lens),
            "detector": asdict(self.detector),
            "oled": asdict(self.oled),
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "SystemConfig":
        return cls(
            name=d.get("name", "Sistem"),
            setup_type=d.get("setup_type", "direct"),
            collimator_focal_length_mm=float(d.get("collimator_focal_length_mm", 0.0)),
            lens=Lens(**_known_fields(Lens, d.get("lens", {}))),
            detector=Detector(**_known_fields(Detector, d.get("detector", {}))),
            oled=OLED(**_known_fields(OLED, d.get("oled", {}))),
        )

    @classmethod
    def load(cls, path: str) -> "SystemConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# Yerleşik donanım kataloğu
# ---------------------------------------------------------------------------
#
# Arayüzdeki lens/dedektör açılır listelerini besler. Katalog yalnızca
# KOLAYLIK sağlar — projenin parametrik olma gereksinimini değiştirmez:
# bir kalem seçmek alanları doldurur, ardından her alan elle düzenlenebilir.
# Elle düzenlenen bir sistem "Özel" olarak işaretlenir.

CUSTOM = "Özel (elle girilen)"


LENS_CATALOG: dict[str, Lens] = {
    "Rodenstock HR Digaron-W 70mm": Lens(
        name="Rodenstock HR Digaron-W",
        focal_length_mm=70.0,
        f_number=5.6,
        notes="Kollimatörsüz düzenek, pinhole modeli.",
    ),
    "Hydra yıldız izleyici 47.7mm": Lens(
        name="Hydra star tracker lens",
        focal_length_mm=47.7,
        f_number=1.4,
        pupil_diameter_mm=34.0,
        useful_fov_deg=21.5,
        notes="Yıldız izleyici objektifi; pupil ve kullanılabilir FOV üreticiden. "
              "Görüntü dairesi (18.11 mm) sensör köşegeninden (26.07 mm) küçük — "
              "köşeler karanlık, gerçek FOV her yönde 21.5°.",
    ),
}


DETECTOR_CATALOG: dict[str, Detector] = {
    "CMV4000 (2048², 5.5µm)": Detector(
        name="CMV4000 (ams/OSRAM)",
        width_px=2048,
        height_px=2048,
        pixel_pitch_um=5.5,
        pixel_pitch_y_um=5.5,
    ),
    "Hydra dedektör (1024², 18µm)": Detector(
        name="Hydra star tracker dedektörü",
        width_px=1024,
        height_px=1024,
        pixel_pitch_um=18.0,
        pixel_pitch_y_um=18.0,
        notes="Yıldız izleyici dedektörü; güç 150 (optik hesaba girmez).",
    ),
}


SCREEN_CATALOG: dict[str, RefScreen] = {
    "GL049AMN10A OLED (1920×1080, 5.616µm)": RefScreen(
        name="GL049AMN10A (Guangli 0.49\" Micro-OLED)",
        width_px=1920, height_px=1080,
        pixel_pitch_um=5.616,
        active_width_mm=10.783, active_height_mm=6.065,
        angular_res_deg=0.0,
        notes="Pasif panel — kendi açısal ölçeği yok.",
    ),
    "STOS (1280×1024, 13.62µm, 0.027°/px)": RefScreen(
        name="STOS görüntüleme ekranı",
        width_px=1280, height_px=1024,
        pixel_pitch_um=13.62,
        active_width_mm=1280 * 13.62e-3,
        active_height_mm=1024 * 13.62e-3,
        angular_res_deg=0.027,
        notes="Açısal kaynak — ima edilen f ≈ 28.90 mm. "
              "Patern çemberleri bu ölçeğe göre yerleştirilir.",
    ),
}


# Hazır tam sistemler: (lens anahtarı, dedektör anahtarı, ekran anahtarı)
SYSTEM_PRESETS: dict[str, tuple[str, str, str]] = {
    "CMV4000 + Rodenstock 70mm": (
        "Rodenstock HR Digaron-W 70mm", "CMV4000 (2048², 5.5µm)",
        "GL049AMN10A OLED (1920×1080, 5.616µm)"),
    "Hydra yıldız izleyici": (
        "Hydra yıldız izleyici 47.7mm", "Hydra dedektör (1024², 18µm)",
        "STOS (1280×1024, 13.62µm, 0.027°/px)"),
}


def _clone(obj):
    """Katalog kalemlerinin kopyasını verir — çağıran taraf düzenleyebilsin."""
    return type(obj)(**asdict(obj))


def lens_from_catalog(key: str) -> Lens | None:
    item = LENS_CATALOG.get(key)
    return _clone(item) if item is not None else None


def detector_from_catalog(key: str) -> Detector | None:
    item = DETECTOR_CATALOG.get(key)
    return _clone(item) if item is not None else None


def screen_from_catalog(key: str) -> RefScreen | None:
    item = SCREEN_CATALOG.get(key)
    return _clone(item) if item is not None else None


def system_from_preset(key: str) -> SystemConfig:
    """Hazır sistem kurar. Bilinmeyen anahtarda varsayılana düşer."""
    trio = SYSTEM_PRESETS.get(key)
    if trio is None:
        return SystemConfig()
    lens_key, det_key, scr_key = trio
    return SystemConfig(
        name=key,
        lens=lens_from_catalog(lens_key) or Lens(),
        detector=detector_from_catalog(det_key) or Detector(),
        oled=screen_from_catalog(scr_key) or RefScreen(),
    )


def match_lens_key(lens: Lens) -> str:
    """
    Verilen lens katalogdaki bir kalemle aynı mı — açılır listeyi senkron
    tutmak için. Karşılaştırma ADA değil, optik olarak ANLAMLI alanlara
    dayanır: ad değişse de fizik aynıysa kalem eşleşmiş sayılır.
    """
    for key, item in LENS_CATALOG.items():
        if (abs(item.focal_length_mm - lens.focal_length_mm) < 1e-6
                and abs(item.f_number - lens.f_number) < 1e-6
                and abs(item.pupil_diameter_mm - lens.pupil_diameter_mm) < 1e-6):
            return key
    return CUSTOM


def match_detector_key(det: Detector) -> str:
    """Aynı mantık dedektör için."""
    for key, item in DETECTOR_CATALOG.items():
        if (item.width_px == det.width_px
                and item.height_px == det.height_px
                and abs(item.pixel_pitch_um - det.pixel_pitch_um) < 1e-6
                and abs(item.pixel_pitch_y_um - det.pixel_pitch_y_um) < 1e-6):
            return key
    return CUSTOM


def match_screen_key(scr: RefScreen) -> str:
    """Referans ekranı katalogla eşleştirir (optik olarak anlamlı alanlar)."""
    for key, item in SCREEN_CATALOG.items():
        if (item.width_px == scr.width_px
                and item.height_px == scr.height_px
                and abs(item.pixel_pitch_um - scr.pixel_pitch_um) < 1e-6
                and abs(item.angular_res_deg - scr.angular_res_deg) < 1e-9):
            return key
    return CUSTOM


def default_config() -> SystemConfig:
    """Kullanıcının verdiği donanımla önceden doldurulmuş varsayılan sistem."""
    return SystemConfig()


if __name__ == "__main__":
    cfg = default_config()
    print("Varsayılan sistem:", cfg.name)
    print("  Sensör boyutu: %.2f x %.2f mm (diag %.2f mm)" % (
        cfg.detector.sensor_width_mm, cfg.detector.sensor_height_mm,
        cfg.detector.diagonal_mm))
    print("  Doğrulama:", cfg.validate() or "OK")
