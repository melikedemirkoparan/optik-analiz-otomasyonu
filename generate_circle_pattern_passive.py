#!/usr/bin/env python3
"""
Eş merkezli çember + yönelim (F) test paterni üreteci -- PASİF PANEL sürümü.

REFERANS EKRAN
--------------
GL049AMN10A (Guangli 0.49" Micro-OLED)
    1920 x 1080 px, piksel pitch 5.616 um
    aktif alan 10.783 x 6.065 mm, köşegen 12.372 mm

NEDEN AÇI DEĞİL MİLİMETRE
-------------------------
Bu panel PASİF: kendi açısal ölçeği yoktur (açısal çözünürlük 0 °/px).
generate_circle_pattern.py'deki projektör modeli burada GEÇERSİZDİR --
paterni açılara bağlayan bir projeksiyon optiği yok, yalnızca fiziksel
bir yüzey var. Dolayısıyla f = pitch / tan(ang_res) türetmesi yapılamaz.

Çemberler bu yüzden sabit MİLİMETRE adımlarıyla konumlanır:

    r_px(k) = k * step_mm / pitch_mm

Ground truth panelin ölçülmüş piksel pitch'idir (5.616 um) -- kaybolmaz,
yalnızca açısal yorum ertelenir. Ekran-cihaz mesafesi d (veya panel bir
kollimatörün odak düzlemindeyse kollimatör odak uzaklığı) öğrenildiğinde
her çemberin açısı tek adımda çıkar:

    theta(k) = atan(k * step_mm / d)

--distance verilirse bu dönüşüm rapor tablosunda gösterilir; patern
geometrisi değişmez, sadece açısal etiketler eklenir.

VARSAYILAN ADIM
---------------
0.25 mm -> 24 çember, komşu aralık 44.5 px.
Panelin köşegen yarı-çapı 6.182 mm olduğundan patern tam 6.000 mm'de biter
ve 16:9 tuvali kenarlara kadar doldurur.

Kullanım:
    python generate_circle_pattern_passive.py --invert
    python generate_circle_pattern_passive.py --step-mm 0.2 --distance 30
"""

import argparse

import numpy as np
import cv2

# --- Referans ekran: GL049AMN10A (pasif Micro-OLED) ---
W, H = 1920, 1080
PIXEL_PITCH_MM = 5.616e-3               # 5.616 um
ACTIVE_W_MM = W * PIXEL_PITCH_MM        # 10.783 mm
ACTIVE_H_MM = H * PIXEL_PITCH_MM        # 6.065 mm
CX, CY = (W - 1) / 2.0, (H - 1) / 2.0   # 959.5, 539.5 -- gerçek merkez

# --- Görüntü alan cihaz (test edilen) ---
DEV_FOCAL_MM = 47.7
DEV_APERTURE_MM = 34.0                  # f/1.40
DEV_FOV_DEG = 21.5                      # tam FOV
DEV_HALF_FOV = DEV_FOV_DEG / 2.0        # +-10.75 derece


def mm_to_px(r_mm, pitch_mm=PIXEL_PITCH_MM):
    """Panel üzerinde milimetre -> piksel."""
    return r_mm / pitch_mm


def px_to_mm(r_px, pitch_mm=PIXEL_PITCH_MM):
    """Panel üzerinde piksel -> milimetre."""
    return r_px * pitch_mm


def mm_to_ang(r_mm, distance_mm):
    """
    Milimetre -> açı. YALNIZCA mesafe bilindiğinde anlamlıdır.
    distance_mm, panel ile cihaz giriş pupili arasındaki mesafe; panel bir
    kollimatörün odak düzlemindeyse kollimatörün odak uzaklığı verilir.
    """
    return np.degrees(np.arctan(r_mm / distance_mm))


def circle_radii_mm(step_mm, max_radius_px, max_r_mm=None):
    """Sabit mm adımlarına karşılık gelen float piksel yarıçapları."""
    radii_mm, radii_px = [], []
    k = 1
    while True:
        r_mm = k * step_mm
        if max_r_mm is not None and r_mm > max_r_mm + 1e-9:
            break
        r_px = mm_to_px(r_mm)
        if r_px > max_radius_px:
            break
        radii_mm.append(r_mm)
        radii_px.append(r_px)
        k += 1
    return np.array(radii_mm), np.array(radii_px)


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
    F merkezlerini verilen azimutlara, optik eksenden f_radius px uzağa koyar.

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


def draw_pattern(radii_px, line_width=3.0, cross_arm=None, cross_width=3.0,
                 f_radius=620.0, f_size=105.0, f_stroke=7.0, f_clear=0.0,
                 ref_ring_px=None, background=255, ink=0):
    """
    Mesafe alanı tabanlı, kenar yumuşatmalı çizim.
    cv2.circle KULLANILMIYOR: tam sayı yarıçap zorunluluğu 0.5 px'e kadar
    yuvarlama hatası getirirdi.

    f_clear > 0 ise çember yayları F'lerin çevresinde kesilir.
    ref_ring_px verilirse o yarıçapta kesikli referans halkası çizilir
    (pasif panelde bu FOV değil, kullanıcının seçtiği bir mm referansıdır).
    """
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx, dy = xx - CX, yy - CY
    r = np.hypot(dx, dy)

    if cross_arm is None:
        cross_arm = float(min(radii_px)) / 2.0 if len(radii_px) else 40.0

    corners = corner_positions(f_radius)

    # Çemberlerin kesileceği maske (F çevresi)
    keep = np.ones((H, W), np.float32)
    if f_clear > 0:
        for _n, fx, fy, _rot, _az in corners:
            d = np.hypot(xx - fx, yy - fy)
            keep = np.minimum(keep, np.clip((d - f_clear) / 6.0, 0.0, 1.0))

    half = line_width / 2.0
    cover = np.zeros((H, W), np.float32)
    for r0 in radii_px:
        cover = np.maximum(cover, np.clip(half + 0.5 - np.abs(r - r0), 0.0, 1.0))
    cover *= keep

    # Merkez artısı -- tam (CX, CY) üzerinde
    ch = cross_width / 2.0
    arm_h = (np.clip(ch + 0.5 - np.abs(dy), 0.0, 1.0)
             * np.clip(cross_arm + 0.5 - np.abs(dx), 0.0, 1.0))
    arm_v = (np.clip(ch + 0.5 - np.abs(dx), 0.0, 1.0)
             * np.clip(cross_arm + 0.5 - np.abs(dy), 0.0, 1.0))
    cover = np.maximum(cover, np.maximum(arm_h, arm_v))

    # Kesikli referans halkası (ölçüm çemberleriyle karışmasın diye kesikli)
    if ref_ring_px is not None:
        ring = np.clip(1.0 + 0.5 - np.abs(r - ref_ring_px), 0.0, 1.0)
        theta = np.arctan2(dy, dx)
        dash = (np.sin(theta * 60.0) > 0).astype(np.float32)   # 30 çizgi/halka
        cover = np.maximum(cover, ring * dash * 0.75 * keep)

    for _name, fx, fy, rot, _az in corners:
        cover = draw_F(cover, xx, yy, fx, fy, f_size, rot_deg=rot, stroke=f_stroke)

    img = background + (ink - background) * cover
    return np.clip(np.rint(img), 0, 255).astype(np.uint8), corners


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step-mm", type=float, default=0.25,
                    help="çemberler arası mesafe (mm)")
    ap.add_argument("--max-mm", type=float, default=None,
                    help="en dış çemberin yarıçapı (mm); varsayılan = panel köşegeni")
    ap.add_argument("--line-width", type=float, default=3.0,
                    help="çizgi kalınlığı (px)")
    ap.add_argument("--f-radius", type=float, default=620.0,
                    help="F merkezlerinin merkezden uzaklığı (px)")
    ap.add_argument("--f-size", type=float, default=105.0,
                    help="F sembolü yüksekliği (px)")
    ap.add_argument("--f-stroke", type=float, default=7.0,
                    help="F çizgi kalınlığı (px)")
    ap.add_argument("--f-clear", type=float, default=78.0,
                    help="F çevresinde çemberlerin kesileceği yarıçap (px)")
    ap.add_argument("--ref-ring-mm", type=float, default=3.0,
                    help="kesikli referans halkasının yarıçapı (mm); 0 = çizme")
    ap.add_argument("--distance", type=float, default=None,
                    help="panel-cihaz mesafesi veya kollimatör odak uzaklığı (mm); "
                         "verilirse rapora açısal sütunlar eklenir")
    ap.add_argument("--invert", action="store_true",
                    help="siyah zemin üzerine beyaz çizgi")
    ap.add_argument("--out", default="circle_pattern_passive.png")
    args = ap.parse_args()

    r_panel = np.hypot(CX, CY) - 10.0       # panel köşegeni -- tuvali doldur
    radii_mm, radii_px = circle_radii_mm(args.step_mm, r_panel, args.max_mm)
    if len(radii_px) == 0:
        raise SystemExit("Bu adımda hiç çember sığmıyor -- --step-mm değerini küçült.")

    ref_ring = (mm_to_px(args.ref_ring_mm) if args.ref_ring_mm > 0 else None)

    bg, ink = (0, 255) if args.invert else (255, 0)
    img, corners = draw_pattern(
        radii_px, line_width=args.line_width, f_radius=args.f_radius,
        f_size=args.f_size, f_stroke=args.f_stroke, f_clear=args.f_clear,
        ref_ring_px=ref_ring, background=bg, ink=ink)
    cv2.imwrite(args.out, img)

    diag_mm = px_to_mm(np.hypot(CX, CY))
    print(f"REFERANS EKRAN: GL049AMN10A, {W}x{H}, "
          f"piksel pitch {PIXEL_PITCH_MM * 1e3:.4f} um")
    print(f"  aktif alan: {ACTIVE_W_MM:.3f} x {ACTIVE_H_MM:.3f} mm, "
          f"köşegen {np.hypot(ACTIVE_W_MM, ACTIVE_H_MM):.3f} mm")
    print(f"  PASİF PANEL -- kendi açısal ölçeği yok; çemberler mm tabanlı")
    print(f"  yarı-eksenler: yatay {px_to_mm(CX):.3f} mm, "
          f"dikey {px_to_mm(CY):.3f} mm, köşegen {diag_mm:.3f} mm")

    if args.distance:
        d = args.distance
        print(f"\nMESAFE d = {d:.2f} mm verildi -> açısal yorum:")
        print(f"  panel kapsama: yatay +-{mm_to_ang(px_to_mm(CX), d):.2f}°, "
              f"dikey +-{mm_to_ang(px_to_mm(CY), d):.2f}°, "
              f"köşegen +-{mm_to_ang(diag_mm, d):.2f}°")
        print(f"  CİHAZ: f = {DEV_FOCAL_MM} mm, "
              f"f/{DEV_FOCAL_MM / DEV_APERTURE_MM:.2f}, "
              f"FOV {DEV_FOV_DEG}° (+-{DEV_HALF_FOV})")
        r_fov_mm = d * np.tan(np.deg2rad(DEV_HALF_FOV))
        print(f"  cihazın gördüğü daire: r = {r_fov_mm:.3f} mm "
              f"({mm_to_px(r_fov_mm):.0f} px)")
    else:
        print(f"\nMesafe verilmedi -- açısal yorum ERTELENDİ.")
        print(f"  theta(r) = atan(r_mm / d); d öğrenilince tabloya uygulanır.")

    print(f"\nAdım {args.step_mm} mm, {len(radii_px)} çember -> {args.out}\n")

    hdr = f"{'#':>3} {'r (mm)':>9} {'r (px)':>10} {'komşu Δ (px)':>13}"
    if args.distance:
        hdr += f" {'açı (°)':>9}"
    print(hdr)
    prev = 0.0
    for i, (rm, rp) in enumerate(zip(radii_mm, radii_px), 1):
        line = f"{i:3d} {rm:9.4f} {rp:10.3f} {rp - prev:13.2f}"
        if args.distance:
            line += f" {mm_to_ang(rm, args.distance):9.4f}"
        print(line)
        prev = rp

    print(f"\nF sembolleri: r = {args.f_radius:.0f} px "
          f"({px_to_mm(args.f_radius):.3f} mm), boy {args.f_size:.0f} px "
          f"({px_to_mm(args.f_size):.3f} mm)")
    print("  " + ", ".join(f"{n}: azimut {az:.0f}°, dönme {int(rot)}°"
                           for n, _, _, rot, az in corners))
    if ref_ring is not None:
        print(f"Kesikli referans halkası: r = {args.ref_ring_mm:.3f} mm "
              f"({ref_ring:.0f} px)")


if __name__ == "__main__":
    main()
