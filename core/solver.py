"""
İlişki çözücü — bilinenlerden bilinmeyenleri türeten optik matematik katmanı.

NEDEN VAR
---------
Projenin geri kalanı TEK YÖNLÜ hesaplar: `compute_fov` lens f + dedektör
pitch'ten FOV/IFOV üretir, `RefScreen.implied_focal_mm` açısal çözünürlükten
odak uzaklığı üretir. Ama gerçek kullanımda elde olan bilgi her zaman aynı
uçtan gelmiyor:

  * Datasheet FOV veriyor ama f vermiyor      -> f'i FOV'dan türet
  * Datasheet açısal çözünürlük veriyor       -> IFOV zaten odur
  * Ekranın °/px'i yok ama f ve pitch'i var   -> °/px'i türet
  * Elde yalnız IFOV ve piksel sayısı var     -> FOV'u türet

Yani ilişki tek yönlü bir formül değil, bir DENKLEM. Bu modül denklemleri
"kural" olarak tanımlar; her kural hangi girdilerden hangi çıktıyı
üretebileceğini bilir ve aynı fiziksel bağıntı BİRDEN ÇOK yönde yazılır.

Çözüm yöntemi bilinen değerlerin tekrarlı YAYILIMIDIR (constraint
propagation): elde ne varsa uygulanabilir kurallar koşulur, yeni değerler
doğar, hiçbir yeni değer doğmayana kadar tekrarlanır.

KAYNAK İZLEME (kullanıcının asıl istediği)
------------------------------------------
Her düğüm değerinin nereden geldiği saklanır:

  * `given`   — kullanıcı/datasheet girdisi
  * `derived` — türetildi; hangi kuraldan ve hangi girdilerden

Böylece arayüzde "Açısal çözünürlük: 0.027 °/px (datasheet)" ile
"Açısal çözünürlük: 0.0270 °/px (IFOV ve pitch'ten türetildi)" ayırt
edilebilir. Türetilmiş değer asla verilmiş değerin üzerine yazılmaz.

TUTARLILIK DENETİMİ
-------------------
Bir büyüklük hem verilmiş hem de başka yoldan türetilebiliyorsa iki sayı
karşılaştırılır. Ayrışma `Conflict` olarak raporlanır — bu, §7E'deki
"beklenen vs ölçülen ölçek" çapraz doğrulamasının genelleştirilmiş hâlidir:
donanım parametreleri kendi içinde tutarlı mı sorusuna cevap verir.

BİRİMLER (düğüm adlarında açıkça yazılıdır, karışıklık olmasın diye)
  mm, um (mikrometre), px, deg, urad, arcsec
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable


# ---------------------------------------------------------------------------
# Değer + kaynak
# ---------------------------------------------------------------------------

GIVEN = "given"
DERIVED = "derived"


@dataclass
class Value:
    """Bir büyüklüğün sayısal değeri ve nereden geldiği."""
    name: str
    value: float
    origin: str                     # GIVEN | DERIVED
    rule: str = ""                  # DERIVED ise üreten kuralın adı
    inputs: tuple[str, ...] = ()    # DERIVED ise kullanılan düğümler
    depth: int = 0                  # kaç adım türetmeyle ulaşıldı (given = 0)
    formula: str = ""               # DERIVED ise uygulanan bağıntının yazılışı

    @property
    def is_given(self) -> bool:
        return self.origin == GIVEN

    def explain(self) -> str:
        """
        Tek satırlık insan-okur açıklama (arayüzde ipucu olarak kullanılır).

        Girdiler HAM DÜĞÜM ADIYLA değil insan-okur etiketleriyle yazılır:
        kullanıcı panelde "det_pitch_um" değil "Dedektör piksel pitch X"
        görür, ipucunda da aynı dili görmeli.
        """
        if self.is_given:
            return "datasheet/girdi"
        if not self.inputs:
            return f"{self.rule} ile türetildi"
        girdiler = " ve ".join(label(i) for i in self.inputs)
        return f"{girdiler} değerlerinden türetildi"


@dataclass
class Conflict:
    """Aynı büyüklüğün iki farklı yoldan farklı çıkması."""
    name: str
    given: float
    derived: float
    rule: str
    rel_error: float                # bağıl fark (|d-g| / |g|)

    def describe(self) -> str:
        return (f"{self.name}: girilen {self.given:.6g}, "
                f"{self.rule} ile türetilen {self.derived:.6g} "
                f"(fark %{self.rel_error * 100:.2f})")


# ---------------------------------------------------------------------------
# Kural
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """
    Tek yönlü bir üretim adımı: `inputs` biliniyorsa `output` hesaplanır.

    Aynı fiziksel bağıntı birden çok Rule ile yazılır (her bilinmeyen için
    bir tane). Bu kasıtlıdır: çözücü genel bir cebir motoru değil, açıkça
    yazılmış ve tek tek doğrulanabilir tersine formüllerden oluşur.
    Böyle olması, bir formülün yanlış tersi alınırsa testin yakalayabilmesini
    sağlar — gizli sembolik manipülasyon yok.
    """
    name: str
    inputs: tuple[str, ...]
    output: str
    fn: Callable[..., float]
    # Girdiler geçerli mi (sıfıra bölme, negatif açı, tanım aralığı).
    guard: Callable[..., bool] | None = None
    # Uygulanan bağıntının matematiksel yazılışı ("IFOV = 2·atan(pitch / 2f)").
    # Arayüzde kullanıcı "bu sayı hangi FONKSİYONLA çıktı" diye sorduğunda
    # gösterilecek şey budur; kural ADI ne yaptığını söyler, `formula` NASIL
    # yaptığını gösterir.
    formula: str = ""

    def can_apply(self, known: dict[str, Value]) -> bool:
        if any(i not in known for i in self.inputs):
            return False
        args = [known[i].value for i in self.inputs]
        if not all(math.isfinite(a) for a in args):
            return False
        if self.guard is not None and not self.guard(*args):
            return False
        return True

    def apply(self, known: dict[str, Value]) -> float:
        return float(self.fn(*[known[i].value for i in self.inputs]))


# ---------------------------------------------------------------------------
# Kural kütüphanesi
# ---------------------------------------------------------------------------
#
# Düğüm adları (hepsi birim ekli):
#
#   GÖRÜNTÜLEME ZİNCİRİ (lens + dedektör)
#     lens_f_mm            lens odak uzaklığı
#     lens_fnum            diyafram sayısı
#     lens_pupil_mm        giriş pupili çapı
#     det_pitch_um         dedektör piksel pitch (x)
#     det_pitch_y_um       dedektör piksel pitch (y)
#     det_w_px, det_h_px   dedektör piksel sayısı
#     det_w_mm, det_h_mm   sensör fiziksel ölçüsü
#     det_diag_mm          sensör köşegeni
#     ifov_x_urad, ifov_y_urad, ifov_x_deg, ifov_x_arcsec
#     fov_x_deg, fov_y_deg, fov_diag_deg
#
#   REFERANS EKRAN (STOS gibi açısal kaynak ya da pasif panel)
#     scr_pitch_um         ekran piksel pitch
#     scr_w_px, scr_h_px   ekran çözünürlüğü
#     scr_aw_mm, scr_ah_mm ekranın aktif alanı
#     scr_ang_deg          açısal çözünürlük (derece/piksel)
#     scr_f_mm             ima edilen odak uzaklığı
#     scr_half_x_deg       ekranın yatay yarı-kapsaması
#     scr_half_y_deg       ekranın dikey yarı-kapsaması
#
#   ZİNCİRLER ARASI
#     scale_expected       ekran pikseli -> dedektör pikseli beklenen ölçek

def _mm(um: float) -> float:
    return um / 1000.0


def _pos(*a: float) -> bool:
    return all(x > 0 for x in a)


def _angle_ok(deg: float) -> bool:
    """Yarı-açı; 90°'de tan patlar, pinhole modeli zaten oraya gitmez."""
    return 0.0 < deg < 89.999


def _build_rules(model: str = "rectilinear") -> list[Rule]:
    """
    Kural listesini verilen PROJEKSİYON MODELİ için kurar.

    FOV/IFOV ile f, pitch ve sensör ölçüsü arasındaki bağıntılar lensin
    açı->yükseklik haritasına bağlıdır. Bu yüzden kurallar `projection`
    modülünden geçirilir; model değişince aynı düğümler arasındaki formüller
    otomatik değişir. Geometri kuralları (N × pitch = mm, köşegen, pupil)
    modelden BAĞIMSIZDIR ve aynen kalır.
    """
    from . import projection as proj

    # Kural adlarında görünen kısa model etiketi ("Rektilineer (pinhole)").
    # Türetim zincirinde hangi modelin kullanıldığı okunabilsin diye.
    etiket = proj.MODEL_LABELS.get(model, model).split(" —")[0]

    # Ters yön. `{r}` yer tutucusu çağıran tarafta gerçek yarıçapla
    # doldurulur (pitch/2, boyut/2 …), böylece ipucunda iç içe eşitlik
    # yerine TEK bir okunur bağıntı görünür:
    #     "IFOV = 2·atan( (pitch/2) / f )"   ← okunur
    #     "IFOV = 2 × [θ = atan(r/f)], r = pitch/2"   ← okunmaz
    # Kapalı formlar `projection.half_angle_deg` içindekilerle birebir aynı.
    # Kullanım hep "tam açı = 2 × yarı açı" biçiminde olduğu için buradaki
    # metinler DIŞ 2 ÇARPANI ZATEN İÇERİR. Böylece equisolid'de
    # "2 · 2·asin(...)" gibi sadeleşmemiş bir ifade değil, doğrudan
    # "4·asin(...)" görünür; equidistant'ta da çift parantez oluşmaz.
    _INV = {
        proj.RECTILINEAR: "2·atan( ({r}) / f )",
        # Equidistant'ta atan yok, bölme doğrudan; 2·(x/2)/f = x/f olduğu
        # için sadeleşmiş hâli yazılır — okuyanın kafasında sadeleştirmesi
        # beklenmemeli.
        proj.EQUIDISTANT: "{whole} / f",
        proj.EQUISOLID: "4·asin( ({r}) / 2f )",
        proj.STEREOGRAPHIC: "4·atan( ({r}) / 2f )",
        proj.ORTHOGRAPHIC: "2·asin( ({r}) / f )",
    }
    _inv_txt = _INV.get(model, "2·g⁻¹( ({r}) / f )")

    def inv_fmt(half: str, whole: str) -> str:
        """
        Ters bağıntıyı metin olarak üretir.

        `half` yarıçapın yarım hâli ("pitch/2"), `whole` tam hâli ("pitch").
        Equidistant dışındaki modeller yarım açıyla çalıştığı için `half`
        kullanılır; equidistant'ta 2 çarpanı bölmeyle sadeleştiği için
        doğrudan `whole` yazılır.
        """
        return _inv_txt.format(r=half, whole=whole)

    # İleri yön: açıdan görüntü yüksekliği. `{t}` yer tutucusu açıyla dolar.
    _FWD = {
        proj.RECTILINEAR: "f · tan({t})",
        proj.EQUIDISTANT: "f · {t}",
        proj.EQUISOLID: "2f · sin({t}/2)",
        proj.STEREOGRAPHIC: "2f · tan({t}/2)",
        proj.ORTHOGRAPHIC: "f · sin({t})",
    }
    fwd = _FWD.get(model, "f · g({t})")

    R: list[Rule] = []

    def add(name, inputs, output, fn, guard=None, formula=""):
        R.append(Rule(name, tuple(inputs), output, fn, guard, formula))

    # ---------------- Dedektör geometrisi ----------------
    # N * pitch = fiziksel ölçü — üç yönde de çözülebilir.
    add("sensör ölçüsü", ["det_w_px", "det_pitch_um"], "det_w_mm",
        lambda n, p: n * _mm(p), lambda n, p: _pos(n, p),
        formula="genişlik_mm = N × pitch")
    add("sensör ölçüsü", ["det_h_px", "det_pitch_y_um"], "det_h_mm",
        lambda n, p: n * _mm(p), lambda n, p: _pos(n, p),
        formula="yükseklik_mm = N × pitch")
    add("sensör ölçüsünden pitch", ["det_w_mm", "det_w_px"], "det_pitch_um",
        lambda mm, n: mm * 1000.0 / n, lambda mm, n: _pos(mm, n),
        formula="pitch = genişlik_mm / N")
    add("sensör ölçüsünden pitch", ["det_h_mm", "det_h_px"], "det_pitch_y_um",
        lambda mm, n: mm * 1000.0 / n, lambda mm, n: _pos(mm, n),
        formula="pitch_y = yükseklik_mm / N")
    add("sensör ölçüsünden piksel sayısı", ["det_w_mm", "det_pitch_um"], "det_w_px",
        lambda mm, p: mm * 1000.0 / p, lambda mm, p: _pos(mm, p),
        formula="N = genişlik_mm / pitch")
    add("sensör ölçüsünden piksel sayısı", ["det_h_mm", "det_pitch_y_um"], "det_h_px",
        lambda mm, p: mm * 1000.0 / p, lambda mm, p: _pos(mm, p),
        formula="N = yükseklik_mm / pitch_y")
    # Kare piksel varsayımı YOK; ama y pitch verilmemişse x ile aynı kabul
    # etmek yaygın ve zararsız — türetilmiş olarak işaretlenir, verilmişse
    # asla üzerine yazılmaz.
    add("kare piksel varsayımı", ["det_pitch_um"], "det_pitch_y_um",
        lambda p: p, _pos,
        formula="pitch_y = pitch_x")
    add("köşegen", ["det_w_mm", "det_h_mm"], "det_diag_mm",
        lambda w, h: math.hypot(w, h), lambda w, h: _pos(w, h),
        formula="köşegen = √(genişlik² + yükseklik²)")
    # Ters yön: datasheet çoğu zaman YALNIZCA köşegeni verir ("1/1.8\"").
    # Diğer kenar biliniyorsa Pisagor geri çözülür. Guard köşegenin
    # kenardan büyük olmasını şart koşar; değilse karekök negatife düşer.
    add("köşegenden genişlik", ["det_diag_mm", "det_h_mm"], "det_w_mm",
        lambda d, h: math.sqrt(d * d - h * h),
        lambda d, h: _pos(d, h) and d > h,
        formula="genişlik = √(köşegen² − yükseklik²)")
    add("köşegenden yükseklik", ["det_diag_mm", "det_w_mm"], "det_h_mm",
        lambda d, w: math.sqrt(d * d - w * w),
        lambda d, w: _pos(d, w) and d > w,
        formula="yükseklik = √(köşegen² − genişlik²)")
    # Kare sensörde köşegen tek başına yeter: G = Y = köşegen/√2. Piksel
    # sayıları eşitse sensör karedir (pitch de kare varsayılır).
    add("kare sensörde köşegenden kenar",
        ["det_diag_mm", "det_w_px", "det_h_px"], "det_w_mm",
        lambda d, nw, nh: d / math.sqrt(2.0),
        lambda d, nw, nh: _pos(d, nw, nh) and abs(nw - nh) < 0.5,
        formula="kare sensör: genişlik = köşegen / √2")
    add("kare sensörde köşegenden kenar",
        ["det_diag_mm", "det_w_px", "det_h_px"], "det_h_mm",
        lambda d, nw, nh: d / math.sqrt(2.0),
        lambda d, nw, nh: _pos(d, nw, nh) and abs(nw - nh) < 0.5,
        formula="kare sensör: yükseklik = köşegen / √2")

    # ---------------- Lens ----------------
    # D = f / N — üç yönde.
    add("pupil = f / f#", ["lens_f_mm", "lens_fnum"], "lens_pupil_mm",
        lambda f, n: f / n, lambda f, n: _pos(f, n),
        formula="D = f / N")
    add("f# = f / pupil", ["lens_f_mm", "lens_pupil_mm"], "lens_fnum",
        lambda f, d: f / d, lambda f, d: _pos(f, d),
        formula="N = f / D")
    add("f = pupil × f#", ["lens_pupil_mm", "lens_fnum"], "lens_f_mm",
        lambda d, n: d * n, lambda d, n: _pos(d, n),
        formula="f = D × N")

    # ---------------- IFOV <-> f, pitch ----------------
    # MERKEZ pikselin gördüğü açı. Rektilineerde 2·atan(pitch/2f), genel
    # halde `projection.ifov_rad`. Kenar pikselinin açısı FARKLIDIR
    # (rektilineerde daha küçük) — burada raporlanan merkez değeridir,
    # projenin doğrulanmış referansları (78.57 / 377.36 µrad/px) odur.
    add(f"IFOV ({etiket})", ["det_pitch_um", "lens_f_mm"], "ifov_x_urad",
        lambda p, f: proj.ifov_rad(model, f, _mm(p)) * 1e6,
        lambda p, f: _pos(p, f),
        formula="IFOV = " + inv_fmt("pitch/2", "pitch"))
    add(f"IFOV ({etiket})", ["det_pitch_y_um", "lens_f_mm"], "ifov_y_urad",
        lambda p, f: proj.ifov_rad(model, f, _mm(p)) * 1e6,
        lambda p, f: _pos(p, f),
        formula="IFOV = " + inv_fmt("pitch_y/2", "pitch_y"))
    # Ters: IFOV biliniyorsa f çıkar. Datasheet "açısal çözünürlük" veriyorsa
    # bu, elde f olmadan sistemin ölçeğini kurar.
    # Merkez pikseli eksene simetrik oturur, yani yarı-pitch <-> yarı-IFOV:
    #     f = (pitch/2) / g(IFOV/2)
    add(f"f = pitch/IFOV ({etiket})", ["det_pitch_um", "ifov_x_urad"], "lens_f_mm",
        lambda p, i: proj.focal_for_fov_mm(model, _mm(p), math.degrees(i * 1e-6)),
        lambda p, i: _pos(p, i) and i * 1e-6 < math.pi,
        formula="pitch/2 = " + fwd.format(t="IFOV/2") + "   →   f çözülür")
    # Ters: f ve IFOV biliniyorsa pitch çıkar.
    add(f"pitch = f·IFOV ({etiket})", ["lens_f_mm", "ifov_x_urad"], "det_pitch_um",
        lambda f, i: proj.sensor_mm_for_fov(model, f, math.degrees(i * 1e-6)) * 1000.0,
        lambda f, i: _pos(f, i) and i * 1e-6 < math.pi,
        formula="pitch = 2 · " + fwd.format(t="IFOV/2"))
    add(f"pitch = f·IFOV ({etiket})", ["lens_f_mm", "ifov_y_urad"], "det_pitch_y_um",
        lambda f, i: proj.sensor_mm_for_fov(model, f, math.degrees(i * 1e-6)) * 1000.0,
        lambda f, i: _pos(f, i) and i * 1e-6 < math.pi,
        formula="pitch_y = 2 · " + fwd.format(t="IFOV/2"))

    # IFOV birim dönüşümleri — hepsi çift yönlü.
    add("µrad → derece", ["ifov_x_urad"], "ifov_x_deg",
        lambda u: math.degrees(u * 1e-6), _pos,
        formula="°/px = µrad × 1e-6 × 180/π")
    add("derece → µrad", ["ifov_x_deg"], "ifov_x_urad",
        lambda d: math.radians(d) * 1e6, _pos,
        formula="µrad = °/px × π/180 × 1e6")
    add("µrad → arcsec", ["ifov_x_urad"], "ifov_x_arcsec",
        lambda u: math.degrees(u * 1e-6) * 3600.0, _pos,
        formula="″/px = µrad × 1e-6 × 180/π × 3600")
    add("arcsec → µrad", ["ifov_x_arcsec"], "ifov_x_urad",
        lambda s: math.radians(s / 3600.0) * 1e6, _pos,
        formula="µrad = ″/px / 3600 × π/180 × 1e6")
    # Y ekseni için de derece karşılığı — X'te var, Y'de yoktu. Asimetri
    # kullanıcı tarafında "neden X için °/px görüyorum da Y için görmüyorum"
    # sorusu doğuruyordu; fiziksel bir sebebi yok, eksiklikti.
    add("µrad → derece", ["ifov_y_urad"], "ifov_y_deg",
        lambda u: math.degrees(u * 1e-6), _pos,
        formula="°/px = µrad × 1e-6 × 180/π")
    add("derece → µrad", ["ifov_y_deg"], "ifov_y_urad",
        lambda d: math.radians(d) * 1e6, _pos,
        formula="µrad = °/px × π/180 × 1e6")
    add("µrad → arcsec", ["ifov_y_urad"], "ifov_y_arcsec",
        lambda u: math.degrees(u * 1e-6) * 3600.0, _pos,
        formula="″/px = µrad × 1e-6 × 180/π × 3600")
    add("arcsec → µrad", ["ifov_y_arcsec"], "ifov_y_urad",
        lambda s: math.radians(s / 3600.0) * 1e6, _pos,
        formula="µrad = ″/px / 3600 × π/180 × 1e6")

    # ---------------- FOV <-> sensör, f ----------------
    # Projeksiyon modeline göre: sensör yarı-boyutu y' = f·g(θ).
    # Üç yönde de çözülür (FOV, boyut, f) ve modeli `projection` belirler —
    # rektilineerde g = tan, equidistant'ta g = θ.
    for mm_node, fov_node in (("det_w_mm", "fov_x_deg"),
                              ("det_h_mm", "fov_y_deg"),
                              ("det_diag_mm", "fov_diag_deg")):
        add(f"FOV ({etiket})", [mm_node, "lens_f_mm"], fov_node,
            lambda s, f: proj.full_fov_deg(model, f, s),
            lambda s, f: _pos(s, f),
            formula="FOV = " + inv_fmt("boyut/2", "boyut"))
        add(f"f = boyut/FOV ({etiket})", [mm_node, fov_node], "lens_f_mm",
            lambda s, fov: proj.focal_for_fov_mm(model, s, fov),
            lambda s, fov: _pos(s) and _angle_ok(fov / 2.0),
            formula="boyut/2 = " + fwd.format(t="FOV/2") + "   →   f çözülür")
        add(f"boyut = f·FOV ({etiket})", ["lens_f_mm", fov_node], mm_node,
            lambda f, fov: proj.sensor_mm_for_fov(model, f, fov),
            lambda f, fov: _pos(f) and _angle_ok(fov / 2.0),
            formula="boyut = 2 · " + fwd.format(t="FOV/2"))

    # Köşegen FOV, x ve y FOV'undan da çıkar. Birleştirme SENSÖR DÜZLEMİNDE
    # yapılır (y' uzayında Pisagor), açı uzayında değil: açı doğrusal bir
    # büyüklük değildir, `hypot(fov_x, fov_y)` Hydra'da 0.365° fazla verir.
    add("köşegen FOV = x ve y'den", ["fov_x_deg", "fov_y_deg"], "fov_diag_deg",
        lambda fx, fy: 2.0 * proj.half_angle_deg(
            model, 1.0, math.hypot(proj.image_height_mm(model, 1.0, fx / 2.0),
                                   proj.image_height_mm(model, 1.0, fy / 2.0))),
        lambda fx, fy: _angle_ok(fx / 2.0) and _angle_ok(fy / 2.0),
        formula=("köşegen = " + inv_fmt("√(r_x² + r_y²)", "2·√(r_x² + r_y²)")
                 + "   — Pisagor SENSÖR düzleminde, açı uzayında DEĞİL"))

    # FOV <-> IFOV × piksel sayısı. DİKKAT: bu bağıntı TAN TABANLIDIR,
    # `fov = N × ifov` küçük açı yaklaşımı DEĞİL (bkz. §7C: kenarda %2+ sapar).
    #
    # Türetimi (yarım açılar üzerinden, katsayı hatası kolay yapıldığı için
    # açıkça yazılıyor):
    #     tan(IFOV/2) = pitch / (2f)          -> pitch/f = 2·tan(IFOV/2)
    #     tan(FOV/2)  = (N·pitch/2) / f = (N/2)·(pitch/f)
    #                 = N · tan(IFOV/2)
    # Yani doğru bağıntı `tan(FOV/2) = N·tan(IFOV/2)`; sağdaki N/2 ile
    # soldaki 1/2'nin sadeleştiğini kaçırmak FOV'u tam iki kat yanlış verir.
    # Genel halde bağıntı: g(FOV/2) = N · g(IFOV/2), yani sensörün yarı
    # yüksekliği, N tane pikselin yarı yüksekliğinin toplamı. Rektilineerde
    # bu `tan(FOV/2) = N·tan(IFOV/2)` olur; equidistant'ta `FOV = N·IFOV`
    # TAM olarak doğrudur (o modelde piksel ölçeği alan boyunca sabittir).
    def _g(a_deg):
        return proj.image_height_mm(model, 1.0, a_deg)

    def _g_inv(h):
        return proj.half_angle_deg(model, 1.0, h)

    # Ortak bağıntı: sensörün yarı yüksekliği = N tane pikselin yarı
    # yüksekliği. `g` modelin açı→yükseklik haritası; rektilineerde tan.
    _F_FWD = (fwd.format(t="FOV/2") + "  =  N · " + fwd.format(t="IFOV/2")
              + "   (f sadeleşir)")
    add(f"FOV = N × IFOV ({etiket})", ["det_w_px", "ifov_x_urad"], "fov_x_deg",
        lambda n, i: 2.0 * _g_inv(n * _g(math.degrees(i * 1e-6) / 2.0)),
        lambda n, i: _pos(n, i), formula=_F_FWD)
    add(f"FOV = N × IFOV ({etiket})", ["det_h_px", "ifov_y_urad"], "fov_y_deg",
        lambda n, i: 2.0 * _g_inv(n * _g(math.degrees(i * 1e-6) / 2.0)),
        lambda n, i: _pos(n, i), formula=_F_FWD)
    add(f"IFOV = FOV / N ({etiket})", ["fov_x_deg", "det_w_px"], "ifov_x_urad",
        lambda fov, n: math.radians(2.0 * _g_inv(_g(fov / 2.0) / n)) * 1e6,
        lambda fov, n: _angle_ok(fov / 2.0) and _pos(n), formula=_F_FWD)
    add(f"IFOV = FOV / N ({etiket})", ["fov_y_deg", "det_h_px"], "ifov_y_urad",
        lambda fov, n: math.radians(2.0 * _g_inv(_g(fov / 2.0) / n)) * 1e6,
        lambda fov, n: _angle_ok(fov / 2.0) and _pos(n), formula=_F_FWD)
    add(f"N = FOV / IFOV ({etiket})", ["fov_x_deg", "ifov_x_urad"], "det_w_px",
        lambda fov, i: _g(fov / 2.0) / _g(math.degrees(i * 1e-6) / 2.0),
        lambda fov, i: _angle_ok(fov / 2.0) and _pos(i),
        formula="N = " + fwd.format(t="FOV/2") + " / " + fwd.format(t="IFOV/2"))
    add(f"N = FOV / IFOV ({etiket})", ["fov_y_deg", "ifov_y_urad"], "det_h_px",
        lambda fov, i: _g(fov / 2.0) / _g(math.degrees(i * 1e-6) / 2.0),
        lambda fov, i: _angle_ok(fov / 2.0) and _pos(i),
        formula="N = " + fwd.format(t="FOV/2") + " / " + fwd.format(t="IFOV/2"))

    # ---------------- Referans ekran (açısal kaynak) ----------------
    # Ekranın açısal çözünürlüğü bir odak uzaklığı ima eder:
    #     f_scr = pitch / tan(ang)
    # Bu, RefScreen.implied_focal_mm ile AYNI bağıntı — burada üç yönde yazılı.
    add("ekran f = pitch / tan(°/px)", ["scr_pitch_um", "scr_ang_deg"], "scr_f_mm",
        lambda p, a: _mm(p) / math.tan(math.radians(a)),
        lambda p, a: _pos(p) and _angle_ok(a),
        formula="f_ekran = pitch / tan(°/px)")
    add("°/px = atan(pitch / f)", ["scr_pitch_um", "scr_f_mm"], "scr_ang_deg",
        lambda p, f: math.degrees(math.atan(_mm(p) / f)),
        lambda p, f: _pos(p, f),
        formula="°/px = atan(pitch / f_ekran)")
    add("ekran pitch = f · tan(°/px)", ["scr_f_mm", "scr_ang_deg"], "scr_pitch_um",
        lambda f, a: f * math.tan(math.radians(a)) * 1000.0,
        lambda f, a: _pos(f) and _angle_ok(a),
        formula="pitch = f_ekran × tan(°/px)")

    # Ekranın aktif alanı ile pitch/piksel sayısı (dedektördekinin aynısı).
    add("ekran aktif alan", ["scr_w_px", "scr_pitch_um"], "scr_aw_mm",
        lambda n, p: n * _mm(p), lambda n, p: _pos(n, p),
        formula="aktif_G = N × pitch")
    add("ekran aktif alan", ["scr_h_px", "scr_pitch_um"], "scr_ah_mm",
        lambda n, p: n * _mm(p), lambda n, p: _pos(n, p),
        formula="aktif_Y = N × pitch")
    add("aktif alandan ekran pitch", ["scr_aw_mm", "scr_w_px"], "scr_pitch_um",
        lambda mm, n: mm * 1000.0 / n, lambda mm, n: _pos(mm, n),
        formula="pitch = aktif_G / N")
    add("aktif alandan ekran piksel sayısı", ["scr_aw_mm", "scr_pitch_um"], "scr_w_px",
        lambda mm, p: mm * 1000.0 / p, lambda mm, p: _pos(mm, p),
        formula="N = aktif_G / pitch")
    # Dikey karşılıkları — yatayda üç yön de yazılıyken dikeyde yalnızca
    # "aktif alan" yönü vardı.
    add("aktif alandan ekran pitch", ["scr_ah_mm", "scr_h_px"], "scr_pitch_um",
        lambda mm, n: mm * 1000.0 / n, lambda mm, n: _pos(mm, n),
        formula="pitch = aktif_Y / N")
    add("aktif alandan ekran piksel sayısı", ["scr_ah_mm", "scr_pitch_um"], "scr_h_px",
        lambda mm, p: mm * 1000.0 / p, lambda mm, p: _pos(mm, p),
        formula="N = aktif_Y / pitch")

    # Ekranın açısal kapsaması: yarı genişliğin gördüğü açı (TAN tabanlı).
    add("ekran kapsaması", ["scr_w_px", "scr_ang_deg"], "scr_half_x_deg",
        lambda n, a: math.degrees(math.atan(
            (n / 2.0) * math.tan(math.radians(a)))),
        lambda n, a: _pos(n) and _angle_ok(a),
        formula="yarı_X = atan( (N/2) × tan(°/px) )")
    add("ekran kapsaması", ["scr_h_px", "scr_ang_deg"], "scr_half_y_deg",
        lambda n, a: math.degrees(math.atan(
            (n / 2.0) * math.tan(math.radians(a)))),
        lambda n, a: _pos(n) and _angle_ok(a),
        formula="yarı_Y = atan( (N/2) × tan(°/px) )")
    add("kapsamadan °/px", ["scr_half_y_deg", "scr_h_px"], "scr_ang_deg",
        lambda h, n: math.degrees(math.atan(
            math.tan(math.radians(h)) / (n / 2.0))),
        lambda h, n: _angle_ok(h) and _pos(n),
        formula="°/px = atan( tan(yarı_Y) / (N/2) )")
    add("kapsamadan °/px", ["scr_half_x_deg", "scr_w_px"], "scr_ang_deg",
        lambda h, n: math.degrees(math.atan(
            math.tan(math.radians(h)) / (n / 2.0))),
        lambda h, n: _angle_ok(h) and _pos(n),
        formula="°/px = atan( tan(yarı_X) / (N/2) )")

    # ---------------- Kenar pikseli ----------------
    # Piksel ölçeği alan boyunca SABİT DEĞİLDİR. Rektilineerde kenar
    # pikseli merkezden daha küçük bir açı görür (cos²θ ile daralır);
    # equidistant'ta tanım gereği sabittir. Panel bu değeri gösteriyordu
    # ama çözücü bilmiyordu — aynı büyüklüğün iki ayrı yerde hesaplanması
    # §5'teki panel↔tablo ayrışmasının aynısı olurdu.
    add(f"kenar pikseli ({etiket})",
        ["det_pitch_um", "lens_f_mm", "fov_x_deg"], "ifov_edge_urad",
        lambda p, f, fov: proj.ifov_rad(model, f, _mm(p), fov / 2.0) * 1e6,
        lambda p, f, fov: _pos(p, f) and _angle_ok(fov / 2.0),
        formula="IFOV_kenar = yarı-FOV açısındaki yerel piksel açısı")
    # Kenar/merkez oranı: tek bir IFOV sayısının alanı ne kadar temsil
    # ettiğini söyler. 1'e yakınsa tek sayı yeterli, değilse değil.
    add("kenar/merkez oranı", ["ifov_edge_urad", "ifov_x_urad"],
        "ifov_edge_ratio",
        lambda e, c: e / c, lambda e, c: _pos(e, c),
        formula="oran = IFOV_kenar / IFOV_merkez")

    # ---------------- Görüntü dairesiyle kırpılmış GERÇEK FOV ----------
    # Lensin dairesi sensörden küçükse köşeler karanlıktır ve sistemin
    # FOV'u geometrik değer DEĞİL, dairenin kırptığı değerdir. Hydra'da
    # geometrik köşegen 30.56°, gerçek FOV 21.50° — ikisi de doğru sayı
    # ama yalnızca biri "sistemin FOV'u" sorusunun cevabı.
    add(f"gerçek FOV = daire kırpması ({etiket})",
        ["lens_f_mm", "lens_image_circle_mm"], "fov_eff_diag_deg",
        lambda f, d: proj.full_fov_deg(model, f, d),
        lambda f, d: _pos(f, d),
        formula="gerçek FOV = " + inv_fmt("çap/2", "çap")
                + "   (daire sensörden küçükken)")

    # ---------------- ÖLÇÜMDEN gelen odak uzaklığı ----------------
    # Ölçek görüntüden ölçülür; ekranın açısal ölçeği biliniyorsa lensin
    # f'i ondan çıkar. `scale_expected` düğümü ölçülen ölçekle beslenir.
    # Bu, panelin "Ölçülen f" satırının çözücüdeki karşılığıdır — aynı
    # bağıntının iki yerde ayrı yazılması ayrışma riski doğururdu.
    # DİKKAT — girdi `scale_measured`, `scale_expected` DEĞİL.
    #
    # İkisi ayrı düğüm olmalı: `scale_expected` donanımdan TÜRETİLİR
    # (f_lens'ten), `scale_measured` GÖRÜNTÜDEN ölçülür. Aynı düğümü
    # paylaşsalardı zincir kendi kuyruğunu yerdi:
    #     f_lens → beklenen ölçek → "ölçülen" f_lens
    # ve panel, girilen f'i "ölçüldü" diye geri yazardı. Ölçüm hiçbir şey
    # ölçmemiş olurdu ama öyle görünürdü — sessiz ve tehlikeli bir hata.
    add("ölçülen f = ölçekten",
        ["scale_measured", "scr_f_mm", "scr_pitch_um", "det_pitch_um"],
        "lens_f_measured_mm",
        lambda sc, fs, ps, pd: sc * (fs / _mm(ps)) * _mm(pd),
        lambda sc, fs, ps, pd: _pos(sc, fs, ps, pd),
        formula="f_ölçülen = ölçek × (f_ekran / pitch_ekran) × pitch_det")
    add(f"ölçülen FOV ({etiket})",
        ["lens_f_measured_mm", "det_w_mm"], "fov_measured_x_deg",
        lambda f, s: proj.full_fov_deg(model, f, s),
        lambda f, s: _pos(f, s),
        formula="FOV_ölçülen = " + inv_fmt("boyut/2", "boyut"))
    # Datasheet ile ölçümün farkı — sistemin sağlık göstergesi.
    # Ölçülen ile beklenen ölçeğin farkı — donanım tanımının görüntüyle
    # tutarlılığı. `f sapması` ile aynı bilgiyi ölçek uzayında verir.
    add("ölçek sapması", ["scale_measured", "scale_expected"],
        "scale_error_pct",
        lambda m, e: 100.0 * (m - e) / e, lambda m, e: _pos(m, e),
        formula="sapma% = 100 × (ölçülen − beklenen) / beklenen")

    add("f sapması", ["lens_f_measured_mm", "lens_f_mm"], "focal_error_pct",
        lambda fm, fd: 100.0 * (fm - fd) / fd, lambda fm, fd: _pos(fm, fd),
        formula="sapma% = 100 × (f_ölçülen − f_datasheet) / f_datasheet")

    # ---------------- Görüntü dairesi (üreticinin kullanılabilir FOV'u) ----
    # `useful_fov_deg` ÜRETİCİNİN VERDİĞİ bir sayıdır — türetilmiş değil,
    # datasheet girdisidir. Çözücüye tanıtılmazsa arayüz o satırı başka bir
    # yoldan hesaplayıp "türetildi" diye etiketler; oysa kullanıcı onu kendi
    # girmiştir. Rozetin doğru olması için düğümün burada var olması şart.
    add("görüntü dairesi = 2f·tan(FOV_kull/2)",
        ["lens_f_mm", "lens_useful_fov_deg"], "lens_image_circle_mm",
        lambda f, fov: proj.sensor_mm_for_fov(model, f, fov),
        lambda f, fov: _pos(f) and _angle_ok(fov / 2.0),
        formula="çap = 2 · " + fwd.format(t="FOV_kullanılabilir/2"))
    add("kullanılabilir FOV = daireden",
        ["lens_f_mm", "lens_image_circle_mm"], "lens_useful_fov_deg",
        lambda f, d: proj.full_fov_deg(model, f, d),
        lambda f, d: _pos(f, d),
        formula="FOV_kullanılabilir = " + inv_fmt("çap/2", "çap"))
    add("f = daire/FOV_kull",
        ["lens_image_circle_mm", "lens_useful_fov_deg"], "lens_f_mm",
        lambda d, fov: proj.focal_for_fov_mm(model, d, fov),
        lambda d, fov: _pos(d) and _angle_ok(fov / 2.0),
        formula="çap/2 = " + fwd.format(t="FOV_kullanılabilir/2") + "   →   f çözülür")

    # ---------------- Zincirler arası: beklenen ölçek ----------------
    # §7E'deki çapraz doğrulama. Ekranın bir pikseli dedektörde kaç piksel:
    #     ölçek = (f_lens / pitch_det) / (f_scr / pitch_scr)
    add("beklenen ölçek (donanımdan)",
        ["lens_f_mm", "det_pitch_um", "scr_f_mm", "scr_pitch_um"],
        "scale_expected",
        lambda fl, pd, fs, ps: (fl / _mm(pd)) / (fs / _mm(ps)),
        lambda fl, pd, fs, ps: _pos(fl, pd, fs, ps),
        formula="ölçek = (f_lens / pitch_det) / (f_ekran / pitch_ekran)")
    # Tersi: ölçek ölçüldüyse (görüntüden) ve ekran biliniyorsa lens f'i çıkar.
    # Bu, lens odak uzaklığının GÖRÜNTÜDEN ölçülmesi demektir.
    add("ölçekten lens f",
        ["scale_expected", "det_pitch_um", "scr_f_mm", "scr_pitch_um"],
        "lens_f_mm",
        lambda s, pd, fs, ps: s * (fs / _mm(ps)) * _mm(pd),
        lambda s, pd, fs, ps: _pos(s, pd, fs, ps),
        formula="f_lens = ölçek × (f_ekran / pitch_ekran) × pitch_det")
    # Tersinin tersi: ölçek ölçüldüyse ve LENS tarafı biliniyorsa ekranın
    # ima ettiği f çıkar — oradan da °/px. Kullanıcının asıl sorduğu yön
    # budur: "0.027 °/px elimde yok, FOV ve donanımdan bul".
    add("ölçekten ekran f",
        ["scale_expected", "lens_f_mm", "det_pitch_um", "scr_pitch_um"],
        "scr_f_mm",
        lambda s, fl, pd, ps: (fl / _mm(pd)) / s * _mm(ps),
        lambda s, fl, pd, ps: _pos(s, fl, pd, ps),
        formula="f_ekran = (f_lens / pitch_det) / ölçek × pitch_ekran")

    return R


RULES: list[Rule] = _build_rules()

# Model başına kural listesi bir kez kurulup saklanır — `solve` her
# çağrıldığında yeniden inşa etmek gereksiz.
_RULES_BY_MODEL: dict[str, list[Rule]] = {"rectilinear": RULES}


def rules_for(model: str) -> list[Rule]:
    """Verilen projeksiyon modeli için kural listesi."""
    if model not in _RULES_BY_MODEL:
        _RULES_BY_MODEL[model] = _build_rules(model)
    return _RULES_BY_MODEL[model]


# Düğümlerin insan-okur adları (arayüz ve rapor için).
NODE_LABELS: dict[str, str] = {
    "lens_f_mm": "Lens odak uzaklığı f",
    "lens_fnum": "Diyafram f/#",
    "lens_pupil_mm": "Giriş pupili çapı",
    "lens_useful_fov_deg": "Kullanılabilir FOV (üretici)",
    "lens_image_circle_mm": "Görüntü dairesi çapı",
    "det_pitch_um": "Dedektör piksel pitch X",
    "det_pitch_y_um": "Dedektör piksel pitch Y",
    "det_w_px": "Dedektör genişlik",
    "det_h_px": "Dedektör yükseklik",
    "det_w_mm": "Sensör genişliği",
    "det_h_mm": "Sensör yüksekliği",
    "det_diag_mm": "Sensör köşegeni",
    "ifov_x_urad": "IFOV X",
    "ifov_y_urad": "IFOV Y",
    "ifov_x_deg": "IFOV X (derece)",
    "ifov_x_arcsec": "IFOV X (arcsec)",
    "ifov_y_deg": "IFOV Y (derece)",
    "ifov_y_arcsec": "IFOV Y (arcsec)",
    "fov_x_deg": "FOV X",
    "fov_y_deg": "FOV Y",
    "fov_diag_deg": "FOV köşegen",
    "scr_pitch_um": "Ekran piksel pitch",
    "scr_w_px": "Ekran genişlik",
    "scr_h_px": "Ekran yükseklik",
    "scr_aw_mm": "Ekran aktif alan G",
    "scr_ah_mm": "Ekran aktif alan Y",
    "scr_ang_deg": "Ekran açısal çözünürlük",
    "scr_f_mm": "Ekranın ima ettiği f",
    "scr_half_x_deg": "Ekran yarı-kapsama X",
    "scr_half_y_deg": "Ekran yarı-kapsama Y",
    "scale_expected": "Ekran→dedektör ölçek (beklenen)",
    "scale_measured": "Ekran→dedektör ölçek (ÖLÇÜLEN)",
    "scale_error_pct": "Ölçek sapması",
    "ifov_edge_urad": "IFOV kenar pikseli",
    "ifov_edge_ratio": "Kenar/merkez oranı",
    "fov_eff_diag_deg": "Gerçek FOV (daire kırpık)",
    "lens_f_measured_mm": "Ölçülen odak uzaklığı",
    "fov_measured_x_deg": "Ölçülen FOV X",
    "focal_error_pct": "f sapması",
}

NODE_UNITS: dict[str, str] = {
    "lens_f_mm": "mm", "lens_pupil_mm": "mm", "lens_fnum": "",
    "lens_useful_fov_deg": "°", "lens_image_circle_mm": "mm",
    "det_pitch_um": "µm", "det_pitch_y_um": "µm",
    "det_w_px": "px", "det_h_px": "px",
    "det_w_mm": "mm", "det_h_mm": "mm", "det_diag_mm": "mm",
    "ifov_x_urad": "µrad/px", "ifov_y_urad": "µrad/px",
    "ifov_x_deg": "°/px", "ifov_y_deg": "°/px",
    "ifov_x_arcsec": "″/px", "ifov_y_arcsec": "″/px",
    "fov_x_deg": "°", "fov_y_deg": "°", "fov_diag_deg": "°",
    "scr_pitch_um": "µm", "scr_w_px": "px", "scr_h_px": "px",
    "scr_aw_mm": "mm", "scr_ah_mm": "mm",
    "scr_ang_deg": "°/px", "scr_f_mm": "mm",
    "scr_half_x_deg": "°", "scr_half_y_deg": "°",
    "scale_expected": "×", "scale_measured": "×", "scale_error_pct": "%",
    "ifov_edge_urad": "µrad/px", "ifov_edge_ratio": "",
    "fov_eff_diag_deg": "°", "lens_f_measured_mm": "mm",
    "fov_measured_x_deg": "°", "focal_error_pct": "%",
}


# Yalnızca BİRİM DÖNÜŞÜMÜ yapan kurallar. Bunların çıktısı matematiksel
# olarak yeni bir bilgi değildir — aynı büyüklüğün başka birimde yazılışıdır.
# 78.57 µrad/px ile 16.207 ″/px aynı ölçümdür; ikincisine "türetildi"
# demek kullanıcıya hesap yapılmış izlenimi verir, oysa yapılan tek şey
# çarpandır. Rozet mantığı bu kümeye bakarak karar verir.
BIRIM_KURALLARI: frozenset[str] = frozenset({
    "µrad → derece", "derece → µrad",
    "µrad → arcsec", "arcsec → µrad",
})


def sadece_birim_mi(v: "Value") -> bool:
    """
    Bu değer yalnızca birim çevrilerek mi elde edildi?

    Zincirin TAMAMI birim dönüşümüyse True. Araya gerçek bir optik bağıntı
    girmişse (ör. IFOV önce f ve pitch'ten hesaplanıp sonra arcsec'e
    çevrildiyse) False — o zaman değer gerçekten türetilmiştir.
    """
    return (not v.is_given) and v.rule in BIRIM_KURALLARI


def label(node: str) -> str:
    return NODE_LABELS.get(node, node)


def unit(node: str) -> str:
    return NODE_UNITS.get(node, "")


# ---------------------------------------------------------------------------
# Çözücü
# ---------------------------------------------------------------------------

# Türetilenle verilenin ayrıştığını "çelişki" saymak için bağıl eşik.
# %1: yuvarlama (datasheet'te 0.027 °/px gibi 2 haneli değerler) buna sığar,
# gerçek parametre hatası sığmaz.
DEFAULT_TOLERANCE = 0.01


@dataclass
class SolveResult:
    values: dict[str, Value] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    # Uygulanabilir hiçbir kural bulunamayan, sorulmuş ama çözülememiş düğümler
    unresolved: list[str] = field(default_factory=list)

    def get(self, node: str) -> float:
        v = self.values.get(node)
        return v.value if v is not None else float("nan")

    def is_known(self, node: str) -> bool:
        return node in self.values

    def is_derived(self, node: str) -> bool:
        v = self.values.get(node)
        return v is not None and not v.is_given

    def kaynak_turu(self, node: str) -> str:
        """
        Rozet için kaynak sınıfı: "given" | "derived" | "unit".

        * given   — kullanıcı/datasheet girdisi
        * unit    — aynı değerin başka birimde yazılışı (yeni bilgi değil)
        * derived — gerçek bir optik bağıntıyla hesaplandı

        "unit" ayrımı olmadan arayüz her birim çevrimine "türetildi" basıyor
        ve rozet anlamını yitiriyor: ekranın yarısı türetildi olunca
        kullanıcı hangi sayının gerçekten hesaplandığını göremiyor.
        """
        v = self.values.get(node)
        if v is None:
            return "derived"
        if v.is_given:
            return "given"
        if sadece_birim_mi(v):
            # Kaynağı da birim dönüşümüyse zincir boyunca izle: asıl
            # değer verilmişse bu da "verilmiş" sayılır.
            gorulen = set()
            cur = v
            while cur is not None and sadece_birim_mi(cur):
                if cur.name in gorulen:
                    break
                gorulen.add(cur.name)
                if not cur.inputs:
                    break
                cur = self.values.get(cur.inputs[0])
            if cur is not None and cur.is_given:
                return "given"
            return "unit"
        return "derived"

    def given_nodes(self) -> list[str]:
        return sorted(n for n, v in self.values.items() if v.is_given)

    def derived_nodes(self) -> list[str]:
        return sorted(n for n, v in self.values.items() if not v.is_given)

    def explain(self, node: str) -> str:
        v = self.values.get(node)
        return v.explain() if v is not None else "bilinmiyor"

    def trace(self, node: str, _seen: set[str] | None = None) -> list[str]:
        """
        Bir düğümün türetim zincirini kökten (verilen değerler) itibaren
        satır satır verir. Kullanıcı "bu sayı nereden çıktı" diye sorduğunda
        gösterilecek şey budur.
        """
        _seen = _seen or set()
        if node in _seen:
            return []
        _seen.add(node)
        v = self.values.get(node)
        if v is None:
            return [f"{label(node)}: bilinmiyor"]
        if v.is_given:
            return [f"{label(node)} = {v.value:.6g} {unit(node)} (girdi)"]
        lines: list[str] = []
        for inp in v.inputs:
            lines += self.trace(inp, _seen)
        lines.append(f"{label(node)} = {v.value:.6g} {unit(node)}"
                     f"  ←  {v.rule} ({', '.join(label(i) for i in v.inputs)})")
        return lines

    def describe(self, node: str) -> str:
        """
        Bir değerin tam açıklaması — arayüzdeki rozet ipucunun içeriği.

        Kullanıcının sorduğu iki soruya sırayla cevap verir:
          1. **Hangi değerlerden?** — girdiler, değerleriyle ve birimleriyle
          2. **Hangi fonksiyonla?** — uygulanan bağıntının yazılışı

        Sonra tam türetim zinciri gelir; zincir birden fazla adımsa
        (ör. arcsec, önce µrad'dan geçer) ara adımlar da görünür.
        """
        v = self.values.get(node)
        if v is None:
            return f"{label(node)}: bilinmiyor"

        bas = f"{label(node)} = {v.value:.6g} {unit(node)}".strip()
        if v.is_given:
            return f"{bas}\n\nDatasheet / girdi olarak verildi."

        satirlar = [bas, ""]
        if v.inputs:
            satirlar.append("Şu değerlerden türetildi:")
            for i in v.inputs:
                iv = self.values.get(i)
                if iv is None:
                    continue
                kaynak = "girdi" if iv.is_given else "türetilmiş"
                satirlar.append(
                    f"   • {label(i)} = {iv.value:.6g} {unit(i)}".rstrip()
                    + f"   ({kaynak})")
            satirlar.append("")
        if v.formula:
            satirlar.append(f"Bağıntı:   {v.formula}")
            satirlar.append("")

        zincir = self.trace(node)
        if len(zincir) > len(v.inputs) + 1:
            satirlar.append("Tam zincir:")
            satirlar += [f"   {z}" for z in zincir]
        return "\n".join(satirlar).rstrip()


def solve(given: dict[str, float],
          rules: Iterable[Rule] | None = None,
          tolerance: float = DEFAULT_TOLERANCE,
          max_iter: int = 50,
          model: str = "rectilinear") -> SolveResult:
    """
    Verilen değerlerden türetilebilecek her şeyi türetir.

    `given` içindeki değerler DOKUNULMAZ: bir düğüm hem verilmiş hem
    türetilebiliyorsa verilen kalır, türetilen yalnızca TUTARLILIK
    DENETİMİNDE kullanılır (`conflicts`). Bu kural §5 ve §7B'deki dersin
    aynısı: ölçüm/girdi katmanının söylediğinin üzerine yedek sayı yazılmaz.

    NaN ve <=0 girdiler "verilmemiş" sayılır — arayüzdeki boş alanlar 0
    gelir ve 0 bir odak uzaklığı ya da pitch olarak anlamsızdır.
    """
    rules = list(rules_for(model) if rules is None else rules)

    known: dict[str, Value] = {}
    for name, raw in given.items():
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val) or val <= 0:
            continue
        known[name] = Value(name, val, GIVEN)

    conflicts: list[Conflict] = []
    seen_conflict: set[tuple[str, str]] = set()

    for _ in range(max_iter):
        changed = False
        for rule in rules:
            if not rule.can_apply(known):
                continue
            try:
                out = rule.apply(known)
            except (ValueError, ZeroDivisionError, OverflowError):
                continue
            if not math.isfinite(out):
                continue

            prev = known.get(rule.output)
            depth = max((known[i].depth for i in rule.inputs), default=0) + 1

            if prev is None:
                known[rule.output] = Value(rule.output, out, DERIVED,
                                           rule.name, rule.inputs, depth,
                                           rule.formula)
                changed = True
                continue

            # Zaten biliniyor: değeri değiştirme, yalnızca tutarlılığı denetle.
            ref = abs(prev.value)
            rel = abs(out - prev.value) / ref if ref > 0 else abs(out - prev.value)
            if rel > tolerance and prev.is_given:
                key = (rule.output, rule.name)
                if key not in seen_conflict:
                    seen_conflict.add(key)
                    conflicts.append(Conflict(rule.output, prev.value, out,
                                              rule.name, rel))
            # Türetilmiş bir değer daha KISA bir yoldan da elde edilebiliyorsa
            # kısa yolu tut — açıklama zinciri gereksiz uzamasın.
            elif not prev.is_given and depth < prev.depth:
                known[rule.output] = Value(rule.output, out, DERIVED,
                                           rule.name, rule.inputs, depth,
                                           rule.formula)
                changed = True
        if not changed:
            break

    return SolveResult(values=known, conflicts=conflicts)


def solve_for(given: dict[str, float], wanted: Iterable[str],
              **kw) -> SolveResult:
    """`solve` ile aynı; istenip de çözülemeyenleri `unresolved`'a yazar."""
    res = solve(given, **kw)
    res.unresolved = [n for n in wanted if n not in res.values]
    return res


# ---------------------------------------------------------------------------
# SystemConfig köprüsü
# ---------------------------------------------------------------------------

def from_config(cfg) -> dict[str, float]:
    """
    SystemConfig'i çözücünün düğüm sözlüğüne çevirir.

    Yalnızca GERÇEKTEN VERİLMİŞ alanlar aktarılır. `SystemConfig` bazı
    alanları kendisi türetiyor (`sensor_width_mm`, `effective_pupil_mm`) —
    bunlar buraya "verilmiş" olarak GİRMEZ, yoksa çözücü kendi türettiği
    şeyi datasheet sanır ve tutarlılık denetimi anlamsızlaşır.
    """
    g: dict[str, float] = {
        "lens_f_mm": cfg.lens.focal_length_mm,
        "lens_fnum": cfg.lens.f_number,
        "det_w_px": cfg.detector.width_px,
        "det_h_px": cfg.detector.height_px,
        "det_pitch_um": cfg.detector.pixel_pitch_um,
        "det_pitch_y_um": cfg.detector.pixel_pitch_y_um,
        "scr_w_px": cfg.oled.width_px,
        "scr_h_px": cfg.oled.height_px,
        "scr_pitch_um": cfg.oled.pixel_pitch_um,
    }
    # pupil_diameter_mm = 0 "verilmedi" demek (config.py'deki konvansiyon).
    if cfg.lens.pupil_diameter_mm > 0:
        g["lens_pupil_mm"] = cfg.lens.pupil_diameter_mm
    # Üreticinin kullanılabilir FOV'u ve görüntü dairesi — ikisi de
    # DATASHEET girdisi. Aktarılmazsa arayüz bunları başka yoldan
    # hesaplayıp "türetildi" rozeti basar; oysa kullanıcı bunları girmiştir.
    if getattr(cfg.lens, "useful_fov_deg", 0.0) > 0:
        g["lens_useful_fov_deg"] = cfg.lens.useful_fov_deg
    if getattr(cfg.lens, "image_circle_mm", 0.0) > 0:
        g["lens_image_circle_mm"] = cfg.lens.image_circle_mm
    # angular_res_deg = 0 "pasif panel" demek — açısal kaynak değilse
    # ekran zinciri hiç kurulmaz.
    if getattr(cfg.oled, "angular_res_deg", 0.0) > 0:
        g["scr_ang_deg"] = cfg.oled.angular_res_deg
    # Aktif alan yalnızca pitch × piksel ile tutarsızsa bilgi taşır; yine de
    # verilmiş alan olarak geçer ki çelişki denetimi onu da görsün.
    if cfg.oled.active_width_mm > 0:
        g["scr_aw_mm"] = cfg.oled.active_width_mm
    if cfg.oled.active_height_mm > 0:
        g["scr_ah_mm"] = cfg.oled.active_height_mm
    return g


def solve_config(cfg, **kw) -> SolveResult:
    """
    SystemConfig'ten doğrudan çöz — arayüzün ve testlerin giriş noktası.

    Projeksiyon modeli config'ten okunur; çağıran `model=` ile ezebilir
    ("aynı donanım f-theta olsaydı ne çıkardı" karşılaştırması için).
    """
    kw.setdefault("model", getattr(cfg.lens, "projection", "rectilinear"))
    return solve(from_config(cfg), **kw)


def eksikler_icin(hedef: str, given: Iterable[str],
                  model: str = "rectilinear") -> list[tuple[str, ...]]:
    """
    `hedef`i çözebilmek için HANGİ girdilerin eksik olduğunu söyler.

    Dönen: her biri "şunları da girersen çözülür" anlamına gelen düğüm
    demetleri, EN AZ eksik olan önce. Boş liste = hedefi üreten hiçbir
    kural yok (o büyüklük bu modelde türetilemez).

    Neden gerekli: kullanıcı bir alanı boş bırakıp "hesapla" dediğinde
    "yeterli bilgi yok" demek yetmiyor — NE girmesi gerektiğini de
    söylemek gerekiyor. Aksi hâlde kullanıcı hangi alanı dolduracağını
    tahmin etmek zorunda kalır.

    Yalnızca TEK ADIM bakar: hedefi doğrudan üreten kuralların girdileri.
    O girdilerin kendileri de türetilebilir olabilir; bu yüzden önce
    mevcut bilinenlerle bir çözüm koşulur ve türetilmiş olanlar da
    "elde var" sayılır.
    """
    rules = rules_for(model)
    # Eldekilerden türetilebilen her şey zaten "var" sayılmalı.
    elde = set(solve({g: 1.0 for g in given}, model=model).values)
    elde |= set(given)

    oneriler: list[tuple[str, ...]] = []
    for r in rules:
        if r.output != hedef:
            continue
        eksik = tuple(i for i in r.inputs if i not in elde)
        if eksik and eksik not in oneriler:
            oneriler.append(eksik)
    oneriler.sort(key=len)
    return oneriler


def report(res: SolveResult, nodes: Iterable[str] | None = None) -> str:
    """Çözüm tablosunu metin olarak biçimlendirir (konsol / rapor için)."""
    keys = list(nodes) if nodes is not None else sorted(res.values)
    lines = []
    for n in keys:
        v = res.values.get(n)
        if v is None:
            lines.append(f"{label(n):<28} —          (bilinmiyor)")
            continue
        tag = "datasheet" if v.is_given else v.rule
        lines.append(f"{label(n):<28} {v.value:>12.5f} {unit(n):<8} {tag}")
    if res.conflicts:
        lines.append("")
        lines.append("ÇELİŞKİLER:")
        for c in res.conflicts:
            lines.append("  ! " + c.describe())
    return "\n".join(lines)


if __name__ == "__main__":
    from .config import system_from_preset

    for name in ("CMV4000 + Rodenstock 70mm", "Hydra yıldız izleyici"):
        cfg = system_from_preset(name)
        r = solve_config(cfg)
        print("=" * 70)
        print(name)
        print("=" * 70)
        print(report(r))
        print()
