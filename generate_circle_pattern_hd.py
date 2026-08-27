#!/usr/bin/env python3
"""
Eş merkezli çember + yönelim (F) test paterni üreteci -- 1920x1080 sürümü.

generate_circle_pattern.py ile aynı çizim mantığı ve aynı açısal kalibrasyon;
fark tuvalin 1920x1080 olması ve çemberlerin cihaz FOV'unda değil panel
sınırında bitmesi.

GROUND TRUTH KORUNUR
--------------------
Piksel pitch'i (13.62 um) ve odak uzaklığı (28.90 mm) DEĞİŞMEZ, dolayısıyla
açı -> piksel dönüşümü referans panelle birebir aynıdır:

    r(theta) = f * tan(theta) / pitch        (0.027 derece/px)

1 derece çemberi burada da 37.04 px'tedir. Tuval büyür, kalibrasyon kaymaz.

EKRANI DOLDURMA
---------------
Çemberler cihazın yarı-FOV'unda (10.75 derece, r = 403 px) KESİLMEZ; panelin
köşegeninin taşıdığı en geniş açıya kadar sürer:

    1920x1080 köşegen  = 1101 px -> 27.42 derece
    1 derece adımla      27 çember (1280x1024'te 10 çemberdi)

Böylece 16:9 tuval kenarlara kadar dolar. Cihazın FOV kenarı yine kesikli
halka ile işaretlenir -- hangi çemberlerin görüntüye gireceği okunabilir kalır,
ama patern orada bitmez. FOV dışındaki çemberler eşleşme (registration) için
ek bağlanma noktası sağlar; ölçüm doğrulaması hydra-stos'tan gelen matematikle
yapılır.

Kullanım:
    python generate_circle_pattern_hd.py --out out.png --invert
"""

import argparse

import numpy as np
import cv2

# --- Referans (orijinal) projektör paneli ---
W_REF, H_REF = 1280, 1024
PITCH_REF_MM = 13.62e-3                 # 13.62 um
ANG_RES_REF_DEG = 0.027                 # derece / px (verilen spec)

# f = pitch / tan(ang_res) -- panel değişse de odak uzaklığı sabit
FOCAL_PROJ_MM = PITCH_REF_MM / np.tan(np.deg2rad(ANG_RES_REF_DEG))   # ~28.90 mm

# --- Hedef panel ---
W, H = 1920, 1080
# Ölçek 1.0: piksel pitch'i ve dolayısıyla açı/piksel ground truth'u DEĞİŞMEZ.
# Tuval büyür, aynı açı yine aynı piksel yarıçapına düşer.
SCALE = 1.0
PIXEL_PITCH_MM = PITCH_REF_MM           # 13.62 um -- referansla birebir
ANG_RES_DEG = np.degrees(np.arctan(PIXEL_PITCH_MM / FOCAL_PROJ_MM))
CX, CY = (W - 1) / 2.0, (H - 1) / 2.0   # 959.5, 539.5 -- gerçek merkez

# --- Görüntü alan cihaz (test edilen) -- değişmedi ---
DEV_FOCAL_MM = 47.7
DEV_APERTURE_MM = 34.0                  # f/1.40
DEV_FOV_DEG = 21.5                      # tam FOV
DEV_HALF_FOV = DEV_FOV_DEG / 2.0        # +-10.75 derece


def ang_to_px(theta_deg, focal_mm=FOCAL_PROJ_MM, pitch_mm=PIXEL_PITCH_MM):
    """Açı -> projektör pikseli (tan tabanlı merkezi izdüşüm)."""
    return focal_mm * np.tan(np.deg2rad(theta_deg)) / pitch_mm


def px_to_ang(r_px, focal_mm=FOCAL_PROJ_MM, pitch_mm=PIXEL_PITCH_MM):
    """Projektör pikseli -> açı."""
    return np.degrees(np.arctan(r_px * pitch_mm / focal_mm))


def circle_radii(step_deg, max_angle_deg, max_radius_px,
                 focal_mm=FOCAL_PROJ_MM, pitch_mm=PIXEL_PITCH_MM):
    """Açısal adımlara karşılık gelen float projektör yarıçapları (px)."""
    angles, radii = [], []
    a = step_deg
    while a <= max_angle_deg + 1e-9:
        r = ang_to_px(a, focal_mm, pitch_mm)
        if r > max_radius_px:
            break
        angles.append(a)
        radii.append(r)
        a += step_deg
    return np.array(angles), np.array(radii)


# --- "F" harfi: birim kare içinde (0..1, 0..1), y aşağı yönde ---
_F_SEGMENTS = [
    (0.22, 0.05, 0.22, 0.95),   # dikey gövde
    (0.22, 0.05, 0.80, 0.05),   # üst kol (uzun)
    (0.22, 0.46, 0.66, 0.46),   # orta kol (kısa)
]


def _seg_distance(px, py, x0, y0, x1, y1):
    """Noktadan doğru parçasına en kısa mesafe (vektörel, alt-piksel doğru)."""
    vx, vy = x1 - x0, y1 - y0
    wx, wy = px - x0, py - y0
    L2 = vx * vx + vy * vy
    t = np.clip((wx * vx + wy * vy) / L2, 0.0, 1.0)
    return np.hypot(wx - t * vx, wy - t * vy)


def draw_F(cover, xx, yy, cx, cy, size, rot_deg=0.0, mirror=False, stroke=3.0):
    """
    (cx, cy) merkezli, 'size' px yüksekliğinde F çizer.
    Mesafe alanı ile -> kenar yumuşatma ve float konum korunur.
    """
    th = np.deg2rad(rot_deg)
    ct, st = np.cos(th), np.sin(th)
    dx = (xx - cx) / size
    dy = (yy - cy) / size
    ux = ct * dx + st * dy
    uy = -st * dx + ct * dy
    if mirror:
        ux = -ux
    ux, uy = ux + 0.5, uy + 0.5

    half = (stroke / size) / 2.0
    for (x0, y0, x1, y1) in _F_SEGMENTS:
        d = _seg_distance(ux, uy, x0, y0, x1, y1)
        c = np.clip((half + 0.5 / size - d) * size, 0.0, 1.0)
        cover = np.maximum(cover, c)
    return cover


def corner_positions(f_radius, f_azimuths=(45.0, 135.0, 225.0, 315.0)):
    """
    F merkezlerini FOV kenarının içinde, verilen azimutlara yerleştirir.

    Döndürmeler 0/90/180/270 DEĞİL: o dizilim paterni 180 derece dönme altında
    kendine eşit yapar (baş aşağı görüntü ayırt edilemez, roll'de 180 derece
    belirsizlik kalır). Son F 45 derece verilerek simetri kırılır -- hiçbir
    dönme/aynalama kombinasyonu paterni kendine götürmez.
    """
    rots = [0.0, 90.0, 45.0, 270.0]
    names = ["A", "B", "C", "D"]
    out = []
    for name, az, rot in zip(names, f_azimuths, rots):
        a = np.deg2rad(az)
        fx = CX + f_radius * np.cos(a)
        fy = CY + f_radius * np.sin(a)
        out.append((name, fx, fy, rot, az))
    return out


def draw_pattern(radii, line_width=3.0, cross_arm=None, cross_width=3.0,
                 f_radius=360.0, f_size=70.0, f_stroke=4.0, f_clear=0.0,
                 fov_marker=True, background=255, ink=0):
    """
    Mesafe alanı tabanlı, kenar yumuşatmalı çizim.
    cv2.circle KULLANILMIYOR: tam sayı yarıçap zorunluluğu 0.5 px'e kadar
    yuvarlama hatası getirirdi.

    f_clear > 0 ise çember yayları F'lerin çevresinde kesilir; böylece
    _boundary_points radyal profilde F'nin kolunu çember kenarı sanmaz.
    fov_marker: cihazın FOV kenarını (+-10.75 derece) kesikli halka ile gösterir.
    """
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = xx - CX, yy - CY
    r = np.hypot(dx, dy)

    if cross_arm is None:
        cross_arm = float(min(radii)) / 2.0 if len(radii) else 40.0

    corners = corner_positions(f_radius)

    # Çemberlerin kesileceği maske (F çevresi)
    keep = np.ones((H, W), np.float32)
    if f_clear > 0:
        for _n, fx, fy, _rot, _az in corners:
            d = np.hypot(xx - fx, yy - fy)
            keep = np.minimum(keep, np.clip((d - f_clear) / 6.0, 0.0, 1.0))

    half = line_width / 2.0
    cover = np.zeros((H, W), np.float32)
    for r0 in radii:
        cover = np.maximum(cover, np.clip(half + 0.5 - np.abs(r - r0), 0.0, 1.0))
    cover *= keep

    # Merkez artısı -- tam (CX, CY) üzerinde
    ch = cross_width / 2.0
    arm_h = (np.clip(ch + 0.5 - np.abs(dy), 0.0, 1.0)
             * np.clip(cross_arm + 0.5 - np.abs(dx), 0.0, 1.0))
    arm_v = (np.clip(ch + 0.5 - np.abs(dx), 0.0, 1.0)
             * np.clip(cross_arm + 0.5 - np.abs(dy), 0.0, 1.0))
    cover = np.maximum(cover, np.maximum(arm_h, arm_v))

    # Cihaz FOV kenarı: kesikli halka (ölçüm çemberleriyle karışmasın diye kesikli)
    # F kesim maskesi buna da uygulanır -- yoksa halka F'lerin içinden geçer.
    if fov_marker:
        r_fov = ang_to_px(DEV_HALF_FOV)
        ring = np.clip(1.0 + 0.5 - np.abs(r - r_fov), 0.0, 1.0)
        theta = np.arctan2(dy, dx)
        dash = (np.sin(theta * 60.0) > 0).astype(np.float32)   # 30 çizgi/halka
        cover = np.maximum(cover, ring * dash * 0.75 * keep)

    for _name, fx, fy, rot, _az in corners:
        cover = draw_F(cover, xx, yy, fx, fy, f_size, rot_deg=rot, stroke=f_stroke)

    img = background + (ink - background) * cover
    return np.clip(np.rint(img), 0, 255).astype(np.uint8), corners


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=1.0, help="açısal adım (derece)")
    ap.add_argument("--max-angle", type=float, default=None,
                    help="en dış çemberin açısı (derece); varsayılan = panel köşegeninin\n                          taşıdığı en geniş açı (ekranı doldurur)")
    ap.add_argument("--line-width", type=float, default=3.0,
                    help="çizgi kalınlığı (projektör px)")
    ap.add_argument("--f-radius", type=float, default=360.0,
                    help="F merkezlerinin optik eksenden uzaklığı (px)")
    ap.add_argument("--f-size", type=float, default=70.0,
                    help="F sembolü yüksekliği (px)")
    ap.add_argument("--f-stroke", type=float, default=4.0,
                    help="F çizgi kalınlığı (px)")
    ap.add_argument("--f-clear", type=float, default=0.0,
                    help="F çevresinde çemberlerin kesileceği yarıçap (px)")
    ap.add_argument("--no-fov-marker", action="store_true",
                    help="cihaz FOV kenarı halkasını çizme")
    ap.add_argument("--invert", action="store_true",
                    help="siyah zemin üzerine beyaz çizgi")
    ap.add_argument("--out", default="circle_pattern_1920x1080.png")
    args = ap.parse_args()

    if args.max_angle is None:
        args.max_angle = px_to_ang(np.hypot(CX, CY))

    r_panel = np.hypot(CX, CY) - 10.0       # panel köşegeni -- çemberler tuvali doldursun
    angles, radii = circle_radii(args.step, args.max_angle, r_panel)
    if len(radii) == 0:
        raise SystemExit("Bu adımda hiç çember sığmıyor -- --step değerini küçült.")

    bg, ink = (0, 255) if args.invert else (255, 0)
    img, corners = draw_pattern(
        radii, line_width=args.line_width, f_radius=args.f_radius,
        f_size=args.f_size, f_stroke=args.f_stroke, f_clear=args.f_clear,
        fov_marker=not args.no_fov_marker, background=bg, ink=ink)
    cv2.imwrite(args.out, img)

    r_fov = ang_to_px(DEV_HALF_FOV)
    print(f"PROJEKTÖR: {W}x{H}, piksel {PIXEL_PITCH_MM * 1e3:.3f} um, "
          f"{ANG_RES_DEG:.4f} derece/px -> f = {FOCAL_PROJ_MM:.2f} mm")
    print(f"  referans {W_REF}x{H_REF} panele göre ölçek s = {SCALE:.6f} "
          f"(pitch {PITCH_REF_MM * 1e3:.2f} -> {PIXEL_PITCH_MM * 1e3:.3f} um)")
    print(f"  panel kapsama: yatay +-{px_to_ang(CX):.2f} derece, "
          f"dikey +-{px_to_ang(CY):.2f} derece, "
          f"köşegen +-{px_to_ang(np.hypot(CX, CY)):.2f} derece")
    print(f"CİHAZ: f = {DEV_FOCAL_MM} mm, f/{DEV_FOCAL_MM / DEV_APERTURE_MM:.2f}, "
          f"FOV {DEV_FOV_DEG} derece (+-{DEV_HALF_FOV})")
    print(f"  cihazın gördüğü daire: r = {r_fov:.1f} px "
          f"(panel köşesi {np.hypot(CX, CY):.0f} px -> köşeler görünmez)")
    print(f"\nAdım {args.step} derece, {len(radii)} çember -> {args.out}\n")

    print(f"{'açı (°)':>9} {'r_proj (px)':>12} {'komşu Δ (px)':>13}  görünür?")
    prev = 0.0
    for a, r0 in zip(angles, radii):
        vis = "evet" if r0 <= r_fov else "HAYIR (FOV dışı)"
        print(f"{a:9.3f} {r0:12.3f} {r0 - prev:13.2f}  {vis}")
        prev = r0

    print(f"\nF sembolleri: r = {args.f_radius:.0f} px "
          f"({px_to_ang(args.f_radius):.2f} derece), boy {args.f_size:.0f} px "
          f"({args.f_size * ANG_RES_DEG:.3f} derece)")
    print("  " + ", ".join(f"{n}: azimut {az:.0f}°, dönme {int(rot)}°"
                           for n, _, _, rot, az in corners))


if __name__ == "__main__":
    main()
