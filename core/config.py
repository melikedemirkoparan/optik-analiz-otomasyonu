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
from dataclasses import dataclass, asdict, field


@dataclass
class Lens:
    """Görüntüleme lensi parametreleri."""
    name: str = "Rodenstock HR Digaron-W"
    focal_length_mm: float = 70.0        # Odak uzaklığı (f)
    f_number: float = 5.6                # Diyafram (maksimum açıklık)
    notes: str = ""

    def validate(self) -> list[str]:
        errs = []
        if self.focal_length_mm <= 0:
            errs.append("Lens odak uzaklığı (focal_length_mm) > 0 olmalı.")
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
class OLED:
    """Referans görüntünün yansıtıldığı OLED ekran parametreleri."""
    name: str = "GL049AMN10A (Guangli 0.49\" Micro-OLED)"
    width_px: int = 1920
    height_px: int = 1080
    pixel_pitch_um: float = 5.616
    active_width_mm: float = 10.783
    active_height_mm: float = 6.065
    notes: str = ""

    def validate(self) -> list[str]:
        errs = []
        if self.width_px <= 0 or self.height_px <= 0:
            errs.append("OLED çözünürlüğü > 0 olmalı.")
        return errs


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
            lens=Lens(**d.get("lens", {})),
            detector=Detector(**d.get("detector", {})),
            oled=OLED(**d.get("oled", {})),
        )

    @classmethod
    def load(cls, path: str) -> "SystemConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


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
