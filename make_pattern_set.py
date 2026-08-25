#!/usr/bin/env python3
"""
Test paterni varyant seti üreteci.

DÜZENEK
-------
Paterni BASAN cihaz  : 1280x1024, 13.62 um piksel, 0.027 derece/px (f ~ 28.90 mm)
Görüntüyü ALAN cihaz : f = 47.7 mm, f/1.4, D = 34 mm, FOV 21.5 derece

Projektör paneli cihazın FOV'undan geniştir; cihaz yalnızca merkezî r = 403 px
dairesini görür. Bu yüzden tüm ölçüm çemberleri ve F sembolleri bu dairenin
içinde tutulur.

Varyantlar patterns/ klasörüne yazılır.
"""

import re
import subprocess
import sys
import os

OUT_DIR = "patterns"
GEN = "generate_circle_pattern.py"

# (dosya adı, açıklama, ekstra argümanlar)
VARIANTS = [
    ("v1_1deg_fov",
     "1.0 derece adım, 10 çember FOV'u dolduruyor, F'ler 336 px — ANA ADAY",
     ["--step", "1.0", "--f-radius", "336", "--f-size", "70",
      "--f-stroke", "5", "--f-clear", "70"]),

    ("v2_2deg_bigF",
     "2.0 derece adım (seyrek), büyük F — düşük kontrastta yönelim doğrulama",
     ["--step", "2.0", "--f-radius", "310", "--f-size", "105",
      "--f-stroke", "7", "--f-clear", "100"]),

    ("v3_0.5deg_dense",
     "0.5 derece adım, 21 çember — distorsiyon haritası için yoğun örnekleme",
     ["--step", "0.5", "--f-radius", "344", "--f-size", "60",
      "--f-stroke", "4", "--f-clear", "60"]),

    ("v4_1deg_beyond",
     "1.0 derece adım, 13 derece'ye kadar — FOV kenarının ötesini de tarar",
     ["--step", "1.0", "--max-angle", "13.0", "--f-radius", "336",
      "--f-size", "70", "--f-stroke", "5", "--f-clear", "70"]),

    ("v5_1deg_clean",
     "1.0 derece adım, FOV halkası yok, F kesimi yok — sade referans",
     ["--step", "1.0", "--f-radius", "340", "--f-size", "65",
      "--f-stroke", "4", "--no-fov-marker"]),

    ("v6_1deg_inverted",
     "1.0 derece adım, siyah zemin/beyaz çizgi — kaçak ışık ve blooming testi",
     ["--step", "1.0", "--f-radius", "336", "--f-size", "70",
      "--f-stroke", "5", "--f-clear", "70", "--invert"]),
]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for name, desc, extra in VARIANTS:
        path = os.path.join(OUT_DIR, name + ".png")
        cmd = [sys.executable, GEN, "--out", path] + extra
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"HATA {name}:\n{res.stderr}")
            continue
        n_circ = 0
        n_hidden = 0
        for line in res.stdout.splitlines():
            m = re.match(r"^Adım .* (\d+) çember", line)
            if m:
                n_circ = int(m.group(1))
            if "HAYIR" in line:
                n_hidden += 1
        rows.append((name, desc, n_circ, n_hidden))
        print(f"[ok] {path}")

    print("\n" + "=" * 104)
    print(f"{'varyant':<20} {'çember':>7} {'FOV dışı':>9}  açıklama")
    print("=" * 104)
    for name, desc, nc, nh in rows:
        print(f"{name:<20} {nc:>7} {nh:>9}  {desc}")


if __name__ == "__main__":
    main()
