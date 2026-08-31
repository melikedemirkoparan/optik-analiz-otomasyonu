# -*- coding: utf-8 -*-
"""
Köşe F işaretlerinin tespiti — roll ve ayna belirsizliğini kaldırır.

NEDEN F. Eş merkezli halka deseni 90° dönmelerde kendini tekrarlar; bu
yüzden halkalardan okunan roll ancak `mod 90°` kadar kesindir — 43.6° ile
133.6° / 223.6° / 313.6° ayırt edilemez. Aynı simetri ayna kararını da
belirsiz bırakır: aynalanmış bir halka deseni kendine benzer.

F harfi bu simetrilerin İKİSİNİ birden kırar:

  * Dönme simetrisi yok  -> dört F'nin hangisinin hangisi olduğu bellidir,
    dolayısıyla roll 0..360° tam çözülür.
  * Ayna simetrisi yok   -> aynalanmış F, döndürülmüş F'ye benzemez.
    Ayna kararı SIFT'e (ve inlier eşiğine) hiç ihtiyaç duymadan verilir.

Dördü kadraja yayıldığı için ayrıca homografi kurmaya yeter (4 nokta bir
homografiyi tam belirler) — ama bu modül o kadarını yapmaz; yalnızca
işaretleri bulur ve eşler.

YÖNTEM. F'ler görüntüde bağlantılı bileşen olarak aranır: halkalar uzun ve
ince, F'ler kompakt ve orta boydur. Merkezden uzaklık ve alan aralığı ile
elenirler. Sonra GT'deki dördü ile dedektördeki dördü, DÖNME AÇISI TARAMASI
ile eşlenir: her aday dönme için hangi eşlemenin toplam hatayı en aza
indirdiğine bakılır. Ayna varyantı da aynı taramaya dahildir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class FMarkers:
    """Bir görüntüde bulunan F işaretleri."""
    ok: bool = False
    center: tuple = ()              # desenin merkezi (x, y)
    points: list = field(default_factory=list)   # [(x, y), ...] 4 adet
    azimuths: list = field(default_factory=list) # merkeze göre açı (derece)
    radii: list = field(default_factory=list)
    messages: list = field(default_factory=list)


@dataclass
class FMatch:
    """GT ile dedektör F'lerinin eşlemesi — roll ve ayna buradan çıkar."""
    ok: bool = False
    roll_deg: float = float("nan")      # 0..360, TAM çözülmüş
    mirrored: bool = False
    mirror_known: bool = False
    scale: float = float("nan")         # GT -> dedektör ölçeği
    rms_px: float = float("nan")        # eşleme kalıntısı (dedektör pikseli)
    # Kaç F eşleşti. 4 idealdir; 3 ile de roll çözülür ama güven düşer.
    n_matched: int = 0
    pairs: list = field(default_factory=list)   # [(gt_idx, det_idx), ...]
    messages: list = field(default_factory=list)


def find_f_markers(img: np.ndarray,
                   center_px: tuple | None = None,
                   min_radius_frac: float = 0.12) -> FMarkers:
    """
    Görüntüdeki dört köşe F işaretini bulur.

    `center_px` verilmezse desenin ağırlık merkezi kullanılır. DİKKAT:
    ağırlık merkezi desen kırpılmışsa gerçek merkezden kayar (bu projede
    ölçüldü: 30 px). Mümkünse cross'un ölçtüğü merkezi geçin.

    Eleme ölçütleri, F'yi halkalardan ayıran şeye dayanır: halka bileşenleri
    çok geniş bir çevre kutusuna yayılır (görüntünün yarısı kadar), F ise
    kompakttır.
    """
    res = FMarkers()
    if img is None or img.size == 0:
        res.messages.append("F aranamadı: görüntü boş.")
        return res

    t = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    ys, xs = np.nonzero(t)
    if len(xs) < 50:
        res.messages.append("F aranamadı: eşikleme sonrası desen yok.")
        return res
    cx, cy = (center_px if center_px is not None else (xs.mean(), ys.mean()))
    res.center = (float(cx), float(cy))

    h, w = img.shape[:2]
    kisa = min(h, w)
    r_min = kisa * float(min_radius_frac)
    # F'nin çevre kutusu, görüntünün kısa kenarının en çok altıda biri
    # kadardır; halka bileşenleri bunu kat kat aşar.
    kutu_max = kisa / 6.0

    n, lab, st, ce = cv2.connectedComponentsWithStats(t, 8)
    aday = []
    for i in range(1, n):
        x, y, bw, bh, alan = st[i]
        if alan < 60:
            continue
        if bw > kutu_max or bh > kutu_max:
            continue          # halka parçası
        r = math.hypot(ce[i][0] - cx, ce[i][1] - cy)
        if r < r_min:
            continue          # merkeze çok yakın: cross veya iç halka
        aday.append((alan, float(ce[i][0]), float(ce[i][1]), r,
                     int(bw), int(bh)))

    if len(aday) < 3:
        res.messages.append(
            f"F işaretleri bulunamadı ({len(aday)} aday) — roll mod 90° "
            "belirsizliğiyle kalır.")
        return res

    # F'ler AYNI ŞEKLİN dört kopyasıdır: alanları birbirine çok yakın
    # olmalıdır (yalnızca dönme farkı var). Halka yayı parçaları bu
    # elemeden geçemez.
    #
    # Gerçek desende ölçüldü: üç F'nin alanı tam 747, dördüncü aday olan
    # yay parçası 712. Yalnızca "en büyük dördü"nü almak o yayı F sanıyordu
    # ve TÜM eşlemeyi bozuyordu — döndürme testinde 8'de 4 yanlış sonuç
    # bunun sonucuydu.
    #
    # Bu yüzden en kalabalık ALAN KÜMESİ seçilir: her adayı merkez alıp
    # %12 bandına kaç aday düştüğüne bakılır, en kalabalık band kazanır.
    aday.sort(key=lambda z: -z[0])
    if len(aday) > 4:
        en_iyi_kume = None
        for merkez_alan, *_ in aday:
            kume = [a for a in aday
                    if abs(a[0] - merkez_alan) <= 0.12 * merkez_alan]
            if en_iyi_kume is None or len(kume) > len(en_iyi_kume):
                en_iyi_kume = kume
        if en_iyi_kume and len(en_iyi_kume) >= 3:
            aday = en_iyi_kume
    aday = aday[:6]

    # DOLULUK ELEMESİ — F'yi halka parçasından ayıran asıl ölçüt.
    #
    # Alan tek başına yetmiyor: gerçek desende bir halka parçası 712
    # piksellik alanla F'lerin 747'sine %4.7 yakındı ve elemeden geçiyordu.
    # Sonuç, tüm eşlemenin bozulmasıydı — döndürme testinde 8 denemenin
    # 4'ü yanlış çıkıyordu.
    #
    # Ayırt eden şey DOLULUK (alan / çevre kutusu): F kompakt bir harftir
    # (ölçüldü: 0.239), halka parçası ise kutusunun içinde ince bir yaydır
    # (0.186). F'ler aynı şeklin dönmüş kopyaları olduğu için dolulukları
    # da birbirinin aynısıdır; en kalabalık doluluk kümesi F'lerdir.
    if len(aday) > 3:
        dol = [(a[0] / max(1.0, a[4] * a[5]), i) for i, a in enumerate(aday)]
        en_iyi = None
        for d0, _ in dol:
            kume = [i for d, i in dol if abs(d - d0) <= 0.04]
            if en_iyi is None or len(kume) > len(en_iyi):
                en_iyi = kume
        if en_iyi and 3 <= len(en_iyi) < len(aday):
            res.messages.append(
                f"{len(aday) - len(en_iyi)} aday doluluk uyumsuzluğundan "
                "elendi (F değil, muhtemelen halka parçası).")
            aday = [aday[i] for i in en_iyi]
    aday = aday[:4]

    for alan, x, y, r, _bw, _bh in aday:
        res.points.append((x, y))
        res.radii.append(r)
        res.azimuths.append(math.degrees(math.atan2(y - cy, x - cx)) % 360.0)
    res.ok = len(res.points) >= 3
    return res


def _rotate(pts: np.ndarray, deg: float) -> np.ndarray:
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    return pts @ np.array([[c, -s], [s, c]], dtype=float).T


def match_f_markers(gt: FMarkers, det: FMarkers) -> FMatch:
    """
    GT ve dedektör F'lerini eşleyip roll + ayna + ölçek çıkarır.

    Merkeze göre normalize edilmiş nokta bulutları karşılaştırılır. Ölçek,
    ortalama yarıçap oranından; roll, dönme taraması ile bulunur. Ayna
    varyantı ayrı bir aday olarak taranır — F asimetrik olduğu için
    aynalanmış hâli hiçbir dönmede aslına oturmaz, dolayısıyla iki
    varyanttan biri belirgin biçimde daha iyi uyar ve AYNA KARARI BURADAN
    ÇIKAR.

    Dönme taraması 0.25° adımla yapılır: F'ler merkezden ~300-400 px
    uzakta olduğu için 0.25° ≈ 1.5 px'lik bir kaymaya karşılık gelir,
    yani eşleme kararını değiştirmeyecek kadar ince.
    """
    res = FMatch()
    if not gt.ok or not det.ok:
        res.messages.append("F eşlemesi yapılamadı: işaretler eksik.")
        return res
    if len(gt.points) < 3 or len(det.points) < 3:
        res.messages.append("F eşlemesi için en az 3 işaret gerekir.")
        return res

    g = np.array(gt.points, dtype=float) - np.array(gt.center, dtype=float)
    d = np.array(det.points, dtype=float) - np.array(det.center, dtype=float)

    rg = np.hypot(g[:, 0], g[:, 1]).mean()
    rd = np.hypot(d[:, 0], d[:, 1]).mean()
    if rg <= 1e-6:
        res.messages.append("F eşlemesi yapılamadı: GT yarıçapları sıfır.")
        return res
    olcek = rd / rg
    gs = g * olcek                      # GT'yi dedektör ölçeğine getir

    en_iyi = None                        # (hata, roll, ayna, eşleşmeler)
    for ayna in (False, True):
        # Ayna, dedektör görüntüsünün yatay çevrilmiş hâline karşılık gelir;
        # nokta uzayında x işaretini çevirmek aynı şeydir.
        dd = d.copy()
        if ayna:
            dd[:, 0] = -dd[:, 0]
        for roll in np.arange(0.0, 360.0, 0.25):
            gr = _rotate(gs, roll)
            # Her dedektör noktasını en yakın GT noktasına ata (greedy);
            # F'ler ~90° aralıklı olduğu için karışma riski düşüktür.
            kullanildi = set()
            toplam = 0.0
            ciftler = []
            for j, p in enumerate(dd):
                mesafeler = [(np.hypot(*(p - gr[i])), i)
                             for i in range(len(gr)) if i not in kullanildi]
                if not mesafeler:
                    break
                m, i = min(mesafeler)
                kullanildi.add(i)
                toplam += m * m
                ciftler.append((i, j))
            if not ciftler:
                continue
            hata = math.sqrt(toplam / len(ciftler))
            if en_iyi is None or hata < en_iyi[0]:
                en_iyi = (hata, float(roll), bool(ayna), ciftler)

    if en_iyi is None:
        res.messages.append("F eşlemesi çözülemedi.")
        return res

    hata, roll, ayna, ciftler = en_iyi
    res.rms_px = hata
    res.roll_deg = roll % 360.0
    res.mirrored = ayna
    res.scale = olcek
    res.n_matched = len(ciftler)
    res.pairs = ciftler

    # Kalıntı eşiği: F merkezleri ~1-2 px hassasiyetle bulunur; 4 işaret
    # üzerinden RMS'in F'ler arası mesafenin (~2*r) yüzde birkaçını aşmaması
    # beklenir. Aşıyorsa eşleme yanlış dönmeye oturmuş olabilir.
    esik = max(8.0, 0.05 * rd)
    if hata > esik:
        res.messages.append(
            f"F eşlemesi zayıf (RMS {hata:.1f} px > {esik:.1f} px) — roll ve "
            "ayna kararı güvenilmez.")
        return res

    res.ok = True
    res.mirror_known = True
    return res




# ---------------------------------------------------------------------------
# Şekil tabanlı çözüm — asıl kullanılan yol
# ---------------------------------------------------------------------------

def _f_template(img: np.ndarray, marker: FMarkers, half: int = 45):
    """
    F işaretlerinden birini şablon olarak keser (azimutu 45°'ye en yakın olan).

    Şablon F'nin KENDİ ŞEKLİDİR. Yalnızca merkez konumlarını kullanmak
    yetmez: dört F 90°'lik aralıklarla yerleşiktir, yani nokta kümesi 4 kat
    simetriktir ve aynalanınca kendine benzer. Bu projede ölçüldü — konum
    tabanlı ayna ayrımı 8.07 px'e karşı 9.76 px (%17, kararsız).
    """
    if not marker.ok or not marker.points:
        return None
    i = min(range(len(marker.azimuths)),
            key=lambda k: abs(((marker.azimuths[k] - 45.0 + 180) % 360) - 180))
    x, y = int(round(marker.points[i][0])), int(round(marker.points[i][1]))
    h, w = img.shape[:2]
    x0, y0 = max(0, x - half), max(0, y - half)
    x1, y1 = min(w, x + half), min(h, y + half)
    t = img[y0:y1, x0:x1]
    if t.size == 0 or min(t.shape[:2]) < 10:
        return None
    return t, marker.azimuths[i]


def _fit_angle(patch: np.ndarray, tmpl: np.ndarray, step: float = 2.0):
    """Şablonu döndürerek patch'e oturtur; (en iyi NCC, açı) döner."""
    iyi, iyi_aci = -9.0, float("nan")
    cx, cy = tmpl.shape[1] / 2.0, tmpl.shape[0] / 2.0
    for aci in np.arange(0.0, 360.0, step):
        M = cv2.getRotationMatrix2D((cx, cy), float(aci), 1.0)
        Sr = cv2.warpAffine(tmpl, M, (tmpl.shape[1], tmpl.shape[0]))
        if Sr.shape[0] > patch.shape[0] or Sr.shape[1] > patch.shape[1]:
            continue
        v = float(cv2.matchTemplate(patch, Sr, cv2.TM_CCOEFF_NORMED).max())
        if v > iyi:
            iyi, iyi_aci = v, float(aci)
    return iyi, iyi_aci


def _measure_angles(img: np.ndarray, marker: FMarkers,
                    tmpl: np.ndarray) -> list:
    """Her F için (azimut, şablon oturma açısı, NCC) ölçer."""
    yari = int(max(tmpl.shape[:2]) * 0.5) + 15
    h, w = img.shape[:2]
    out = []
    for (px, py), az in zip(marker.points, marker.azimuths):
        px, py = int(round(px)), int(round(py))
        P = img[max(0, py - yari):min(h, py + yari),
                max(0, px - yari):min(w, px + yari)]
        if P.size == 0 or min(P.shape[:2]) < min(tmpl.shape[:2]):
            continue
        ncc, aci = _fit_angle(P, tmpl)
        if math.isfinite(aci):
            out.append((az, aci, ncc))
    return out


def solve_roll_and_mirror(gt_img: np.ndarray, det_img: np.ndarray,
                          gt_center: tuple | None = None,
                          det_center: tuple | None = None,
                          scale_hint: float | None = None) -> FMatch:
    """
    Roll ve aynayı F işaretlerinden çözer. Hizalamaya/SIFT'e bağlı DEĞİLDİR.

    YÖNTEM — iki bağımsız açı ölçüsünün TUTARLILIĞI.
    Her F için iki şey ölçülür:

        şekil açısı : şablonu o F'ye oturtmak için gereken dönme
        azimut      : F'nin merkeze göre bulunduğu yön

    Görüntü yalnızca döndürülmüşse ikisi AYNI miktarda değişir. Aynalanmışsa
    biri artarken diğeri azalır — işte ayrım buradan çıkar, ve bu ayrım
    şablonun kendi NCC'sinden daha güvenilirdir.

    Ölçülen gerçek bir çiftte (Hydra + OLED):
        düz  hipotez : ortalama tutarsızlık 54.9°   -> reddedildi
        ayna hipotezi: ortalama tutarsızlık 12.0°   -> kabul, roll ≈ 134°

    NEDEN ŞABLON NCC'Sİ TEK BAŞINA YETMEZ. Şablon her açıda denendiği için,
    aynalanmış bir F de bir dönmede yüksek NCC verebilir. Aynı çiftte
    NCC "düz" diyordu (0.98 / 0.66) ama tutarlılık testi aynayı gösterdi.
    Karar tutarlılığa dayanır; NCC yalnızca F'nin gerçekten bulunduğunu
    doğrular.

    GT'nin kendi F'leri farklı açılarda çizilmiştir (ölçüldü: 0°, 90°, 270°,
    316°), bu yüzden GT de aynı şekilde ölçülür ve referans olarak kullanılır.
    """
    res = FMatch()
    g = find_f_markers(gt_img, gt_center)
    d = find_f_markers(det_img, det_center)
    res.messages = list(g.messages) + list(d.messages)
    if not g.ok or not d.ok:
        res.messages.append("F işaretleri bulunamadı — roll/ayna çözülemedi.")
        return res

    tg = _f_template(gt_img, g)
    if tg is None:
        res.messages.append("F şablonu kesilemedi.")
        return res
    tmpl, _ = tg

    # GT referansı: şablonun GT'nin KENDİ F'lerine oturma açıları.
    gt_ref = _measure_angles(gt_img, g, tmpl)
    if len(gt_ref) < 3:
        res.messages.append("GT'de F açıları ölçülemedi.")
        return res

    # Ölçek ve dedektör ölçümü.
    rg = float(np.mean(g.radii)) if g.radii else 0.0
    rd = float(np.mean(d.radii)) if d.radii else 0.0
    olcek = (float(scale_hint) if (scale_hint and scale_hint > 0)
             else (rd / rg if rg > 1e-6 else 1.0))
    res.scale = olcek
    T = cv2.resize(tmpl, None, fx=olcek, fy=olcek,
                   interpolation=cv2.INTER_CUBIC)
    det_olcum = _measure_angles(det_img, d, T)
    if len(det_olcum) < 3:
        res.messages.append(
            f"Dedektörde yalnızca {len(det_olcum)} F ölçülebildi.")
        return res

    ncc_ort = float(np.mean([n for _, _, n in det_olcum]))

    def _degerlendir(ayna: bool):
        """
        Bir hipotez altında en iyi roll'ü ve ortalama tutarsızlığını bulur.

        Her F için İKİ bağımsız roll okuması vardır:

            r_şekil  = şablonun oturma açısı farkı  (F'nin kendi dönüşü)
            r_azimut = F'nin merkeze göre yön farkı (F'nin konumu)

        Doğru hipotez ve doğru eşleme altında ikisi AYNI çıkar. Ölçülen
        gerçek çiftte fark çarpıcıydı:

            düz hipotez  : tüm çiftlerde 87-93° tutarsızlık  -> hepsi yanlış
            ayna hipotezi: dört çiftte 1.2-2.8°              -> doğru

        Ayna, azimutu VE şekil açısını birlikte ters çevirir; bu yüzden
        yalnızca biri değil ikisi birden dönüştürülür.

        EŞLEME BİREBİR OLMALI. İki dedektör F'si aynı GT F'sine atanırsa
        "hepsi aynı yere oturdu" gibi sahte bir çözüm doğar. Roll taranır,
        her aday roll için birebir atama kurulur ve toplam hata ölçülür.
        """
        olc = []
        for daz, dac, _ in det_olcum:
            az = (-daz) % 360.0 if ayna else daz
            ac = (-dac) % 360.0 if ayna else dac
            olc.append((az, ac))

        def _hata(roll):
            """
            Bu roll için ortalama tutarsızlık.

            Her dedektör F'si, KENDİSİNE EN İYİ UYAN GT F'sine atanır —
            birebir kısıt YOK. Kısıt koymak burada zarar veriyordu: desende
            F'ler 180°'lik gruplar hâlinde yerleştirilmiş (ölçüldü: az-açı
            değerleri {45, 225} iki kümede toplanıyor), bu yüzden birden çok
            dedektör F'si aynı GT F'sine haklı olarak uyabilir. Zorla farklı
            GT F'lerine dağıtmak doğru eşlemeyi bozuyordu (tutarsızlık elle
            hesaplanan 1.2-2.8° yerine 30.7° çıkıyordu).

            Sahte çözüm riski, ŞEKİL ve AZİMUT'un İKİSİNİ birden zorunlu
            kılmakla önlenir: yanlış bir roll altında biri tutsa bile
            diğeri tutmaz, ve max() ikisinin kötüsünü alır.
            """
            hatalar = []
            for az, ac in olc:
                en = None
                for gaz, gac, _ in gt_ref:
                    f_az = abs(((az - gaz - roll + 180.0) % 360.0) - 180.0)
                    f_ac = abs(((ac - gac - roll + 180.0) % 360.0) - 180.0)
                    f = max(f_az, f_ac)
                    if en is None or f < en:
                        en = f
                hatalar.append(en)
            # MEDYAN, ortalama değil. Bir F kırpılmış, gölgede kalmış ya da
            # halkaya değmiş olabilir; ortalama o tek aykırıyı tüm karara
            # yayar. Ölçülen çiftte hatalar [0.9, 0.8, 89.2] idi —
            # ortalaması 30.3° (kararsız), medyanı 0.9° (net).
            return float(np.median(hatalar))

        kaba = min(np.arange(0.0, 360.0, 1.0), key=_hata)
        ince = min(np.arange(kaba - 1.0, kaba + 1.0, 0.1), key=_hata)
        return float(_hata(ince)), float(ince % 360.0)

    t_duz, roll_duz = _degerlendir(False)
    t_ayn, roll_ayn = _degerlendir(True)

    res.mirrored = t_ayn < t_duz
    kazanan_t = min(t_duz, t_ayn)
    res.roll_deg = (roll_ayn if res.mirrored else roll_duz) % 360.0
    res.n_matched = len(det_olcum)
    res.rms_px = kazanan_t

    ayrim = (max(t_duz, t_ayn) / kazanan_t) if kazanan_t > 1e-6 else float("inf")
    res.mirror_known = bool(ayrim > 1.5 and ncc_ort > 0.5)
    if not res.mirror_known:
        res.messages.append(
            f"Ayna kararı belirsiz (düz {t_duz:.1f}° / ayna {t_ayn:.1f}° "
            f"tutarsızlık, NCC {ncc_ort:.2f}).")

    if ncc_ort < 0.5:
        res.messages.append(
            f"F eşleşmesi zayıf (NCC {ncc_ort:.2f}) — roll güvenilmez.")
        return res
    if kazanan_t > 25.0:
        res.messages.append(
            f"F'ler tutarlı bir roll vermiyor (tutarsızlık {kazanan_t:.1f}°).")
        return res

    res.ok = True
    res.messages.append(
        f"Roll ve ayna {len(det_olcum)} F işaretinden çözüldü "
        f"(NCC {ncc_ort:.2f}, tutarsızlık {kazanan_t:.1f}°, "
        f"dağılım ±{res.rms_px:.1f}°).")
    return res
