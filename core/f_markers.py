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
    rms_px: float = float("nan")        # eşleme tutarsızlığı (DERECE)
    ncc: float = float("nan")           # şablon eşleşme kalitesi (0..1)
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

    # DİKKAT — DÖRT F AYNI ŞEKLİN KOPYALARI DEĞİLDİR.
    #
    # Desen üreteci (generate_circle_pattern_passive.corner_positions) F'leri
    # 0°/90°/45°/270° dönmeleriyle koyar. Üçüncüsü bilerek 45° eğiktir:
    # "Son F 45 derece verilerek simetri kırılır -- hiçbir dönme/aynalama
    # kombinasyonu paterni kendine götürmez." Roll'ü 0..360° tekleştiren
    # BİLGİNİN TAMAMI o eğik F'dedir.
    #
    # Eğik çizim, piksel ızgarasında farklı bir çevre kutusuna oturur.
    # Gerçek desende (v6_1deg_inverted) ölçüldü:
    #
    #     azimut 45.2 / 135.1 / 315.2 : alan 747, kutu 68x46, doluluk 0.239
    #     azimut 226.8 (45° eğik F)   : alan 712, kutu 78x49, doluluk 0.186
    #
    # Bu yüzden "alanı/dolulukları birbirine benzeyenleri tut" biçimindeki
    # her eleme, tam da simetriyi kıran F'yi atar. Önceki sürüm bunu
    # yapıyordu ve 712'yi "halka yayı" sanıyordu; oysa 712 dördüncü F'nin
    # kendisidir (azimutu 226.8 — tam olması gereken yer).
    aday.sort(key=lambda z: -z[0])

    # Fazla aday varsa YERLEŞİMDEN seçilir, ŞEKİLDEN değil.
    #
    # F'ler merkezden eşit uzaklıkta ve ~90° aralıklı dört köşededir. Bu
    # bir DÖNME DEĞİŞMEZİDİR: desen dönse de dördü hep aynı yarıçapta ve
    # aynı açısal aralıkta kalır. Şekle (alan/doluluk) dayanan her ölçüt
    # ise dönmeyle değişir ve 45° eğik F'yi eler.
    #
    # Bu yüzden aday sayısı dördü aşarsa, yarıçapı en tutarlı olan dörtlü
    # seçilir: her aday için "yarıçapı ona en yakın dört aday"ın yarıçap
    # yayılımına bakılır, en dar yayılımlı dörtlü kazanır. Halka yayları
    # F'lerden farklı yarıçaplarda oturduğu için bu elemeden geçemez.
    if len(aday) > 4:
        en_iyi_dortlu, en_dar = None, float("inf")
        for merkez in aday:
            yakin = sorted(aday, key=lambda a: abs(a[3] - merkez[3]))[:4]
            if len(yakin) < 4:
                continue
            rr = [a[3] for a in yakin]
            yayilim = max(rr) - min(rr)
            if yayilim < en_dar:
                en_dar, en_iyi_dortlu = yayilim, yakin
        if en_iyi_dortlu is not None:
            atilan = len(aday) - 4
            aday = en_iyi_dortlu
            res.messages.append(
                f"{atilan} aday yarıçap tutarsızlığından elendi "
                "(F'ler merkezden eşit uzaklıktadır).")
    aday = aday[:4]

    for alan, x, y, r, _bw, _bh in aday:
        res.points.append((x, y))
        res.radii.append(r)
        res.azimuths.append(math.degrees(math.atan2(y - cy, x - cx)) % 360.0)
    res.ok = len(res.points) >= 3
    return res


def _f_templates(img: np.ndarray, marker: FMarkers, half: int = 45):
    """
    DÖRT F'nin HER BİRİNİ ayrı şablon olarak keser.

    NEDEN TEK ŞABLON YETMEZ. Desen üreteci F'leri 0°/90°/45°/270°
    dönmeleriyle koyar; üçüncüsü 45° eğiktir ve simetriyi kıran tek
    işaret odur. Tek şablon dört F'ye de oturtulunca, şablon kendi
    dönme belirsizliğiyle yanlış F'ye kilitlenebiliyor. Ölçüldü — tek
    şablonla GT imzaları 270.1 / 225.3 / 45.1 / 224.8 çıkarken aynı
    harf hem ac=90 hem ac=270 verebiliyordu, yani hangi F'nin hangisi
    olduğu ayırt edilemiyordu.

    Dört ayrı şablonla her F KİMLİĞİYLE eşlenir: i numaralı GT şablonu
    yalnızca ona karşılık gelen dedektör F'sine oturur.

    Dönüş: [(patch, azimut), ...] — GT'deki sırayla.
    """
    if not marker.ok or not marker.points:
        return []
    h, w = img.shape[:2]
    out = []
    for (x, y), az in zip(marker.points, marker.azimuths):
        xi, yi = int(round(x)), int(round(y))
        x0, y0 = max(0, xi - half), max(0, yi - half)
        x1, y1 = min(w, xi + half), min(h, yi + half)
        t = img[y0:y1, x0:x1]
        if t.size == 0 or min(t.shape[:2]) < 10:
            continue
        out.append((t, float(az)))
    return out


def _match_identities(det_img: np.ndarray, det: FMarkers,
                      gt_tmpl: list, olcek: float):
    """
    Dedektör F'lerini GT F'lerine BİREBİR eşler ve roll'ü çözer.

    Her (GT şablonu i, dedektör F'si j) çifti için şablon döndürülerek
    en iyi oturma açısı ve NCC ölçülür. Sonra 4! = 24 permütasyon
    taranır; her permütasyonda dört çiftin verdiği roll okumalarının
    ne kadar tutarlı olduğuna bakılır.

    DOĞRU eşlemede dört okuma da aynı roll'ü gösterir; yanlış eşlemede
    (ör. 90° kaymış atama) eğik F'nin okuması diğerlerinden ayrışır —
    simetriyi kıran şey tam olarak budur.

    Dönüş: (tutarsızlık_derece, roll_derece, ortalama_ncc)
    """
    import itertools

    if not gt_tmpl or not det.ok or len(det.points) < 3:
        return float("inf"), float("nan"), 0.0

    # Her GT şablonunu dedektör ölçeğine getir.
    tmpl_s = []
    for t, az in gt_tmpl:
        T = (cv2.resize(t, None, fx=olcek, fy=olcek,
                        interpolation=cv2.INTER_CUBIC)
             if abs(olcek - 1.0) > 1e-3 else t)
        tmpl_s.append((T, az))

    n_g, n_d = len(tmpl_s), len(det.points)
    # skor[i][j] = (oturma açısı, ncc) — GT şablonu i, dedektör F'si j
    skor = [[None] * n_d for _ in range(n_g)]
    h, w = det_img.shape[:2]
    for j, ((px, py), daz) in enumerate(zip(det.points, det.azimuths)):
        pxi, pyi = int(round(px)), int(round(py))
        for i, (T, gaz) in enumerate(tmpl_s):
            yari = int(max(T.shape[:2]) * 0.5) + 15
            P = det_img[max(0, pyi - yari):min(h, pyi + yari),
                        max(0, pxi - yari):min(w, pxi + yari)]
            if P.size == 0 or min(P.shape[:2]) < min(T.shape[:2]):
                continue
            ncc, aci = _fit_angle(P, T)
            if math.isfinite(aci):
                skor[i][j] = (float(aci), float(ncc))

    en_iyi = (float("inf"), float("nan"), 0.0)
    idx_g = list(range(n_g))
    for perm in itertools.permutations(range(n_d), min(n_g, n_d)):
        okuma, nccler = [], []
        for i, j in zip(idx_g, perm):
            if skor[i][j] is None:
                continue
            aci, ncc = skor[i][j]
            gaz = tmpl_s[i][1]
            daz = det.azimuths[j]
            # İki bağımsız roll okuması; doğru eşlemede ikisi de aynıdır.
            #
            # İŞARET. _fit_angle, ŞABLONU döndürüp patch'e oturtur; yani
            # bulduğu açı görüntünün dönmesinin TERSİDİR. Azimut ise
            # görüntüyle aynı yönde artar. Ölçüldü (roll=30 deseni):
            # doğru çiftlerde ac=330 iken azimut farkı 30 — yani
            # -ac = 30 ile birebir örtüşüyor (hata 0.2°).
            r_sekil = (-aci) % 360.0
            r_azimut = (daz - gaz) % 360.0
            okuma.append((r_sekil, r_azimut))
            nccler.append(ncc)
        if len(okuma) < 3:
            continue
        # Bu permütasyon altında en tutarlı roll'ü ara.
        def _tutarsizlik(roll):
            e = []
            for r_s, r_a in okuma:
                f_s = abs(((r_s - roll + 180.0) % 360.0) - 180.0)
                f_a = abs(((r_a - roll + 180.0) % 360.0) - 180.0)
                e.append(max(f_s, f_a))
            return float(np.median(e))
        kaba = min(np.arange(0.0, 360.0, 1.0), key=_tutarsizlik)
        ince = min(np.arange(kaba - 1.0, kaba + 1.0, 0.1), key=_tutarsizlik)
        t = _tutarsizlik(ince)
        if t < en_iyi[0]:
            en_iyi = (float(t), float(ince % 360.0), float(np.mean(nccler)))
    return en_iyi


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

    # Dört GT F'sinin HER BİRİ ayrı şablon. Tek şablon, 45° eğik F'yi
    # diğerlerinden ayırt edemiyordu (bkz. _f_templates).
    gt_tmpl = _f_templates(gt_img, g)
    if len(gt_tmpl) < 3:
        res.messages.append("GT'de F şablonları kesilemedi.")
        return res

    rg = float(np.mean(g.radii)) if g.radii else 0.0
    rd = float(np.mean(d.radii)) if d.radii else 0.0
    olcek = (float(scale_hint) if (scale_hint and scale_hint > 0)
             else (rd / rg if rg > 1e-6 else 1.0))
    res.scale = olcek

    # AYNA HİPOTEZİ GÖRÜNTÜ ÜZERİNDE denenir, nokta uzayında değil.
    #
    # Eski sürüm ayna için yalnızca azimut ve açı işaretlerini çeviriyordu.
    # Ama ayna F'nin ŞEKLİNİ de çevirir; şablon aynalanmadığı sürece
    # aynalanmış bir F'ye hiçbir dönmede tam oturmaz. Bu yüzden dedektör
    # görüntüsünün kendisi yatay çevrilip aynı kimlik eşlemesi koşulur ve
    # iki hipotezin tutarsızlıkları karşılaştırılır.
    def _hipotez(ayna: bool):
        if not ayna:
            return _match_identities(det_img, d, gt_tmpl, olcek)
        di = cv2.flip(det_img, 1)
        dm = find_f_markers(di, None if det_center is None
                            else (det_img.shape[1] - 1 - det_center[0],
                                  det_center[1]))
        if not dm.ok:
            return float("inf"), float("nan"), 0.0
        return _match_identities(di, dm, gt_tmpl, olcek)

    t_duz, roll_duz, ncc_duz = _hipotez(False)
    t_ayn, roll_ayn, ncc_ayn = _hipotez(True)

    if not (math.isfinite(t_duz) or math.isfinite(t_ayn)):
        res.messages.append("F kimlik eşlemesi kurulamadı.")
        return res

    res.mirrored = t_ayn < t_duz
    kazanan_t = min(t_duz, t_ayn)
    res.roll_deg = (roll_ayn if res.mirrored else roll_duz) % 360.0
    ncc_ort = ncc_ayn if res.mirrored else ncc_duz
    res.n_matched = len(d.points)
    res.rms_px = kazanan_t
    res.ncc = float(ncc_ort)

    # Ayna kararı iki hipotezin AYRIMINA dayanır. Doğru hipotez tutarlı bir
    # roll verir, yanlışı vermez; oran ne kadar büyükse karar o kadar net.
    ayrim = ((max(t_duz, t_ayn) / kazanan_t)
             if kazanan_t > 1e-6 else float("inf"))
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
        f"Roll ve ayna {res.n_matched} F işaretinden çözüldü "
        f"(NCC {ncc_ort:.2f}, tutarsızlık {kazanan_t:.1f}°).")
    return res
