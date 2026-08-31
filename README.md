# Optik Analiz Otomasyonu

Ground truth ile dedektör görüntüsünü karşılaştırarak **FOV**, **IFOV**,
**tilt** ve **yönelim hatalarını** otomatik hesaplayan PyQt5 masaüstü
uygulaması.

| Parametre | Anlamı |
|---|---|
| **FOV** (Field of View) | Sensörün gördüğü toplam açı |
| **IFOV** (Instantaneous FOV) | Tek bir pikselin gördüğü açı |
| **Tilt** | Düzlem-dışı eğiklik (perspektif) |
| **Roll** | Düzlem-içi dönme — F işaretlerinden 0..360° tek değer |
| **Decenter** | Bore-sight kaçıklığı — desen merkezi ile sensör merkezi arasındaki açı |
| **Kapsama** | Desenin sensöre düşen, sensörün desenle dolan oranı |

Tasarım **parametriktir**: hiçbir donanım değeri koda gömülü değildir. Lens,
dedektör veya referans ekran değişirse arayüzdeki alanları güncellemek
yeterlidir.

![Arayüz — analiz sonrası](docs/gorseller/arayuz_sonuc.png)

## Kurulum

Python 3.12 ve şu paketler:

```
OpenCV 4.13 · NumPy 1.26 · SciPy 1.11 · PyQt5 5.15 · matplotlib 3.10
```

```bash
pip install opencv-python numpy scipy PyQt5 matplotlib
```

## Çalıştırma

```bash
python3 run_gui.py
```

VS Code'da **F5** → "Arayüzü başlat (GUI)" de aynı işi yapar.

Arayüzde: **Ground truth seç…** → **Dedektör görüntüsü seç…** → **ANALİZ ET**.
Sonuçlar sağ panelde görünür.

Sol panelde hazır sistemler (`CMV4000 + Rodenstock 70mm`, `Hydra yıldız
izleyici`), lens/dedektör/referans ekran katalogları ve **projeksiyon modeli**
seçicisi bulunur. Katalog yalnızca kolaylıktır — bir kalem seçmek alanları
doldurur, her alan sonra elle düzenlenebilir ve düzenlenen sistem "Özel"
olarak işaretlenir. Preset'ler JSON olarak kaydedilip yüklenebilir.

## Ölçüm yöntemi

### FOV / IFOV — projeksiyon modeli seçilebilir

Kollimatör olmadığı için varsayılan model rektilineer (pinhole) projeksiyondur:

```
IFOV = 2 · arctan( pitch / (2f) )
FOV  = 2 · arctan( (N · pitch) / (2f) )
```

Ama bu **tek varsayım değildir**. `core/projection.py` literatürden beş
standart modeli (Optics for Hire Tablo 1.1; Kannala–Brandt 2006; OpenCV
`cv::fisheye`) sunar:

| model | r(θ) | tipik kullanım |
|---|---|---|
| **rectilinear** | f·tan θ | 40–60° tasarımlar; **projenin varsayılanı** |
| equidistant | f·θ | f-theta; ölçüm ve balıkgözü objektifleri |
| equisolid | 2f·sin(θ/2) | eşit alan; ışık ölçümü |
| stereographic | 2f·tan(θ/2) | açıları yerel korur |
| orthographic | f·sin θ | θ < 90° ile sınırlı |

Model seçimi FOV'u gerçekten değiştirir — aynı Hydra donanımında modeller
arası yayılım **0.41°**'dir (21.87° ile 22.28° arası). Model **tahmin
edilmez, ölçülebilir**: `fit_projection_model()` bilinen açı–yarıçap
çiftlerinden gerçek modeli seçer ve her model için en iyi f'i ayrıca uydurur.
Dar açı aralığında bütün modeller birbirine yakınsadığı için sonuç ancak hem
bağıl oran hem **mutlak ayrım tabanı (0.2 px)** sağlanırsa "kesin" işaretlenir.

> **`FOV = N × IFOV` yaklaşımı kullanılmaz.** Hata FOV ile patlar: 10°'de
> %0.25, 30°'de %2.35, 90°'de **%27.3**. Doğru bağıntı `g(FOV/2) = N·g(IFOV/2)`.
> Bu eşitlik yalnızca equidistant modelde tam doğrudur.
> Köşegen FOV da `hypot(fov_x, fov_y)` ile hesaplanmaz — açı doğrusal bir
> büyüklük değildir; sensörün köşegen **ölçüsünden** hesaplanır.

### İlişki çözücü — bilinenlerden bilinmeyeni türetme

`core/solver.py` hesabı tek yönlü olmaktan çıkarır. Her büyüklük bir **düğüm**,
her fiziksel bağıntı bir **kuraldır**; çözüm bilinenlerin doyuma kadar
tekrarlı yayılımıdır:

| elde olan | istenen |
|---|---|
| FOV + dedektör | lens f |
| IFOV (arcsec) + N | FOV |
| IFOV + pitch | f |
| ekran f + pitch | °/px |
| ölçülen ölçek + ekran | lens f |

Her değer **nereden geldiğini taşır**: `given` (datasheet/girdi) ya da
`derived` (hangi kuraldan, hangi girdilerden). `res.trace(düğüm)` zinciri
kökten yazar, arayüz de her sonuç satırına kaynak rozeti koyar.

**Verilen değerin üzerine yazılmaz.** Bir büyüklük hem verilmiş hem
türetilebiliyorsa verilen kalır; türetilen yalnızca tutarlılık denetiminde
kullanılır ve ayrışma `Conflict` olarak raporlanır. Tolerans %1'dir çünkü
datasheet değerleri yuvarlıdır (Hydra'da 34.0 × 1.4 = 47.6 ≠ 47.7); bu
üretici yuvarlamasını yutar, gerçek parametre hatasını yutmaz.

### Tilt, roll ve decenter

Düzlem-dışı tilt için birden çok yöntem çalışır (`core/tilt_estimators.py`).
Merkezde Siemens star varsa daire→elips ilişkisi kullanılır:

```
eksen oranı (b/a) = cos(tilt) → tilt = arccos(b/a)
```

Eş merkezli çember deseninde yıldız yoktur; tilt `concentric_rings`
yöntemiyle ölçülür. Düzlem-içi dönme homografinin QR ayrıştırmasından gelir.

**Decenter** desenin merkezindeki cross'tan ölçülür. Hizalama tümüyle
çökse bile bu yol çalışır: eş merkezli desen dairesel simetrik olduğu için
faz korelasyonu kırılgandır (gerçek bir ölçümde NCC 0.09), ama aynı görüntüde
cross şablonla NCC 0.96 bulunur. Bu yol yalnızca decenter'ı doldurur —
roll/tilt cross'tan çıkmaz, o satırlar dürüstçe eksik kalır.

### Kapsamanın paydası: ekranın tamamı değil, görüntü dairesi

Ground truth referans ekranın TÜM karesidir, ama cihaz o karenin yalnızca
**ortasındaki daireyi** görebilir. Payda ekranın tamamı alınırsa kusursuz
hizalı bir sistem bile "%40" gibi bir sayı verir ve "desen kırpılıyor" diye
okunur. `measure_pointing` bu yüzden iki bölgeyi kesiştirir:

| taraf | bölge |
|---|---|
| ground truth | **karşılaştırma dairesi** (yarı-FOV'un ekrandaki yarıçapı), GT çerçevesine kırpılmış |
| dedektör | **aydınlık alan** = sensör dikdörtgeni ∩ lensin görüntü dairesi |

Lensin görüntü dairesi sensörden küçükse köşeler karanlıktır (Hydra'da daire
503 px, sensörün yarı-kenarı 512 px). Deseni tam görmek için kalan pay bu
yüzden iki sınırın küçüğüdür:

```
pay = min( sensör kenarına mesafe − r_desen ,
           görüntü dairesi yarıçapı − decenter − r_desen )
```

Hangisinin bağladığı `margin_limit` ile raporlanır — pratikte bu, **"daha
büyük dedektör al"** ile **"lensi değiştir"** arasındaki farktır.

Desen yarıçapı bilinmiyorsa bölge zorunlu olarak tüm ekrana düşer; o zaman
`ref_region` bunu **söyler** ("tüm ekran — desen yarıçapı bilinmiyor") ve
arayüz uyarı ekler. Sayı sessizce eski anlamına kaymaz.

### Kendine benzeyen desenlerde hizalama

İnce halkalı desenler SIFT'i aç bırakır (1280×1024 GT'de yalnızca 122
keypoint) ve kendine-benzer halkalarda tanımlayıcılar birbirinin aynı olduğu
için **çoktan-teke** eşleşme homografiyi çökertir. Dört düzeltme var:

1. **Karşılıklı (mutual) en yakın komşu** — bir hedef yalnızca kendi en iyi
   kaynağıyla eşleşir; çöküşü doğuran tekrarlar baştan silinir.
2. **Çift taraflı yayılım denetimi** — dejenerelik kontrolü artık yalnızca
   kaynak tarafına bakmıyor, iki taraftaki yayılımın küçüğünü alıyor.
3. **Polarite uyumu** — SIFT kontrast terslemesine bağışık değildir; beyaz
   zeminli GT ile koyu zeminli çekim arasında tek eşleşme bile bulunamaz.
   `analyze()` ayna × polarite (8 kombinasyon) dener.
4. **Güdümlü eşleme** — kör SIFT sonuç veremezse GT, yoğun hizalamanın
   homografisiyle ön-warp edilir ve SIFT yalnızca **artık** dönüşümü çözer
   (keypoint 122 → 458). Bu, yoğun yolun sayısını kopyalamak değildir: SIFT
   ölçmeye devam eder, ön-bilgi yalnızca arama uzayını daraltır. Ön-bilgi
   yanlışsa warp tutmaz, 20 px yarıçap kapısından yeterli eşleşme geçmez ve
   yol `None` döner. Artık dönüşüm **afin** aranır — aynı 8 noktaya projektif
   model uydurmak 69° tilt uyduruyordu.

### Ölçüm sınırları — dürüstlük

Kosinüs sıfır civarında yassı olduğu için elips yöntemi küçük açılara
duyarsızdır: 1° eğiklik eksen oranını yalnızca 0.00015 değiştirir. Tipik
oran gürültüsü (σ ≈ 0.002) bunun karşılığı olan **~3.6°**'ye kadar olan
eğiklikleri gizler. Bu yüzden yazılım küçük açılarda kesin bir sayı yerine
üst sınır raporlar:

```
Eğiklik    < 3.6°
           ölçüm sınırının altında — ayırt edilemiyor
```

> `< 3.6°`, "tilt sıfır" demek **değildir**; "tilt bu değerden küçük, tam
> sayısı bu yöntemle çıkarılamıyor" demektir.

**Roll'ün eski `mod 90°` belirsizliği çözüldü** — bkz. "Roll: F
işaretlerinden tam yönelim". Homografi tek başına 90°'nin katlarını ayırt
edemez, çünkü halka deseni 90° dönmede kendini tekrar eder (ölçülen
öz-benzerlik: 90° ve 180° dönmede 0.9648). Ayrım desendeki **45° eğik F
işaretinden** gelir.

Ölçüm katmanı her yöntemin belirsizliğini hesaplar, desen tespit güveni
0.7'nin altındaysa ölçüm üretmez ve doğrulanmamış yöntemleri birincil olarak
seçmez — olmayan desenden sayı uydurulmaz.

## Sonuca güvenilir mi?

Sağ paneldeki **Durum** satırı bu soruyu tek satırda cevaplar:

| Gösterge | Anlamı |
|---|---|
| 🟢 Ölçüm güvenilir | Desen net bulundu, hizalama sağlam |
| 🟡 Dikkat | Ölçüm yapıldı ama bir zayıflık var |
| 🔴 Sonuca güvenmeyin | Desen seçilemedi |

Uyarı alanı yalnızca **gerçek uyarıları** gösterir ve uyarı yoksa boş kalır.
Yöntem notları (`Bilgi:` ile başlayanlar — "dönme faz korelasyonuyla
okunamadı, tam açı taraması + ECC koşuldu" gibi) `▸ Ayrıntılar` kutusuna
taşınır; bunlar arıza değil yol seçimidir.

Sarı/kırmızı durumda üç kontrol:

1. **Overlay sekmesi** — geniş sarı alanlar iyi hizalama demektir. Kırmızı/yeşil
   ayrışmışsa tilt değerine güvenmeyin.
2. **Elipsin yeri** — yeşil elips merkezi yıldızın dış sınırına oturmalıdır.
3. **Tespit güveni** — `▸ Ayrıntılar` altında; 0.7 altındaysa yıldız net
   seçilememiştir.

## Testler

```bash
python3 test_core.py             # Çekirdek FOV/IFOV matematiği
python3 test_projection.py       # Projeksiyon modelleri (86 kontrol)
python3 test_solver.py           # İlişki çözücü (123 kontrol)
python3 test_pointing.py         # Decenter/roll/tilt + kapsama
python3 test_goruntu_dairesi.py  # Görüntü dairesi kısıtı (75 kontrol)
python3 test_ui_kaynak.py        # Panel kaynak rozetleri (52 kontrol)
python3 test_tilt_synth.py       # Sentetik doğrulama (görüntü gerekmez)
python3 test_tilt_multi.py       # Ölçüm katmanı: doğruluk + dürüstlük
python3 test_dense_align.py      # Yoğun hizalama
python3 test_hydra.py            # Hydra donanımı uçtan uca
python3 test_pipeline.py         # Uçtan uca test (örnek görüntülerle)
python3 test_roi.py              # ROI seçimi
python3 test_roi_analiz.py       # Tam kare ↔ kırpma karşılaştırması
python3 test_f_markers.py        # F işaretlerinden tam roll + ayna (24 kontrol)
```

Ayrıca modüller doğrudan çalıştırılabilir:

```bash
python3 -m core.solver       # katalog sistemlerinin tam çözüm tablosu
python3 -m core.projection   # aynı donanımda modellerin FOV karşılaştırması
```

**On dört test dosyasının hepsi geçiyor.** Doğrulama noktaları: sentetik tilt doğrulamasında 0°–40° arasında en büyük hata 0.29°;
`test_projection` [2] equidistant çıktısını `cv2.fisheye` ile **1e-9
hassasiyetle** karşılaştırır; `test_solver` [2] 27 düğümün her birini tek tek
silip kalanlardan geri türeterek ileri/ters formül ayrışmasını yakalar;
`test_pointing` beklenen kapsamayı, aynı kırpma rutinini çağırmak yerine
daire∩kare kesişiminin **analitik** formülünden üretir.

`test_tilt_multi.py` ayrıca **dürüstlüğü** sınar: desensiz bir görüntü
verildiğinde sistem "ölçülemedi" demeli, sıfır üretmemelidir.

## Roll: F işaretlerinden tam yönelim

Homografi roll'ü yalnızca `mod 90°` verir — halka deseni 90° dönmede kendini
tekrar eder. Ayrımı yapan şey desenin kendisidir: dört köşe **F harfinden
üçüncüsü bilerek 45° eğik** basılır
(`generate_circle_pattern_passive.corner_positions()` → `rots = [0, 90, 45, 270]`),
böylece hiçbir dönme/aynalama kombinasyonu paterni kendine götürmez. Roll'ü
tekleştiren bilginin tamamı o eğik F'dedir.

`core/f_markers.py` her F için iki açı ölçer — merkeze göre **azimut** ve
şablonu oturtmak için gereken **şekil açısı**. Görüntü döndürülmüşse ikisi
aynı miktarda kayar, dolayısıyla farkları bir **imzadır**. Dört F'nin her biri
ayrı şablon olarak kesilir ve 4! = 24 permütasyon taranarak dedektör F'leri
GT F'lerine **birebir** atanır: doğru eşlemede dört okuma da aynı roll'ü verir,
yanlış eşlemede eğik F ayrışır.

Aday eleme **dönme değişmezi** olmak zorundadır. Şekle dayanan ölçütler
(doluluk, alan benzerliği) dönmeyle değişir ve tam da simetriyi kıran eğik F'yi
eler — 45° eğik çizim piksel ızgarasında farklı bir kutuya oturduğu için
doluluğu 0.186, diğer üçününki 0.239'dur. Bu yüzden eleme yarıçapa dayanır:
F'ler merkezden eşit uzaklıktadır ve yarıçap dönmeyle değişmez.

**Ayna kararı görüntü üzerinde denenir**, nokta uzayında işaret çevirerek
değil — ayna F'nin şeklini de çevirir, şablon aynalanmadıkça aynalanmış F'ye
hiçbir dönmede oturmaz. Gerçek Hydra + OLED çiftinde ayrım tartışmasızdır:

| hipotez | tutarsızlık | NCC | sonuç |
|---|---|---|---|
| düz | 0.74° | 0.977 | **KABUL** |
| ayna | 45.36° | 0.659 | RED |

Bağımsız bir hakem de aynı yönü gösterir: F merkezlerinin **işaretli alanı
(chirality)** GT'de +430775, dedektörde +681105 — ikisi de pozitif, dizilim
yönü aynı, ayna yok. Bu ölçüt şablon eşleşmesinden tamamen bağımsızdır.

Sentetik doğrulama (`test_f_markers.py`, **24/24**): roll 0 → 0.0°, 134 →
133.9°, 225 → 224.6°, 310 → 310.0°; **44° ile 134° ayırt ediliyor** (44.1 vs
133.9); 1.4×/1.8× ölçek ve kaçık merkezde hata 0.0–1.0°. Gerçek çiftte roll
**223.30°**, homografinin mod-90 değeriyle tutarlı (223.30 mod 90 = 43.30).

> **Not:** yöntem doğrulandı ve testleri geçiyor, ancak `core/pipeline.py`
> içinde henüz **bağlanmamış** durumdadır — arayüzde gösterilen roll hâlâ
> homografiden gelir. Bağlanması bekleyen tek adım budur.

## Test deseni üreteçleri

```bash
python3 generate_circle_pattern.py          # 1280×1024 açısal kaynak (STOS)
python3 generate_circle_pattern_hd.py       # 1920×1080, aynı açısal kalibrasyon
python3 generate_circle_pattern_passive.py  # pasif panel — mm adımlı
python3 make_pattern_set.py                 # varyant seti → patterns/
```

Açısal üreteçlerde piksel pitch'i (13.62 µm) ve ima edilen odak uzaklığı
(28.90 mm) değişmez, dolayısıyla açı → piksel dönüşümü referans panelle
birebir aynıdır: `r(θ) = f · tan(θ) / pitch` (0.027 °/px). Tuval büyür,
kalibrasyon kaymaz.

Pasif panel sürümü **farklıdır**: GL049 panelinin kendi açısal ölçeği yoktur,
paterni açılara bağlayan bir projeksiyon optiği yoktur. Bu yüzden çemberler
açı değil sabit **milimetre** adımlarıyla konumlanır.

> **F'ler panel köşesine konmaz.** Projektör paneli cihazın FOV'undan geniştir
> (panel köşesi 820 px'te, cihaz yalnızca merkezî r = 403 px dairesini görür);
> köşeye konan F'ler görüntüye hiç girmez.

## Dokümantasyon

Ayrıntılı kullanım kılavuzu `docs/` altında üç biçimde:

- [`docs/KULLANIM_KILAVUZU.md`](docs/KULLANIM_KILAVUZU.md) — GitHub'da doğrudan okunur
- `docs/KULLANIM_KILAVUZU.pdf` — yazdırmak/paylaşmak için (14 sayfa)
- `docs/KULLANIM_KILAVUZU.html` — tarayıcıda okumak için
- [`docs/OLCUM_ALGORITMASI.md`](docs/OLCUM_ALGORITMASI.md) — ölçüm algoritmasının ayrıntısı

Geliştirme durumu ve teknik notlar: [`DEVAM_YONERGESI.md`](DEVAM_YONERGESI.md)

## Proje yapısı

```
optik_analiz/
├── core/
│   ├── config.py          Parametrik config + lens/dedektör/ekran katalogları
│   ├── optics.py          FOV/IFOV hesabı + homografi ayrıştırma
│   ├── projection.py      Beş lens projeksiyon modeli + model uydurma
│   ├── solver.py          İlişki çözücü: bilinenlerden bilinmeyeni türetir
│   ├── image_analysis.py  SIFT eşleme, polarite/ayna, güdümlü eşleme, overlay
│   ├── dense_align.py     Yoğun (desenden bağımsız) hizalama
│   ├── siemens_star.py    Elips-fit tilt ölçümü
│   ├── tilt_estimators.py Çoklu tilt yöntemi + belirsizlik raporlama
│   ├── pointing.py        Decenter/roll/tilt + görüntü dairesiyle kapsama
│   ├── cross_locate.py    Merkez cross'u bulma (hizalama çökse de decenter)
│   ├── f_markers.py       F işaretlerinden tam roll + ayna kararı
│   └── pipeline.py        Tüm akışı birleştiren giriş noktası
├── gui/
│   ├── main_window.py     Ana pencere (3 panel, sekmeler, ROI, arka plan thread)
│   └── widgets.py         Tema, görüntü göstericisi, sonuç satırları
├── docs/                  Kullanım kılavuzu (md/html/pdf) + görseller
├── patterns/              Üretilmiş test deseni varyantları
├── presets/               Preset JSON'ları
├── data/                  Debug/önizleme çıktıları
├── run_gui.py             ← Arayüzü başlatır
├── generate_circle_pattern*.py, make_pattern_set.py   Desen üreteçleri
└── test_*.py              Testler
```

## Donanım

Katalogda iki hazır sistem var:

| Bileşen | Model | Kritik değerler |
|---|---|---|
| Lens | Rodenstock HR Digaron-W | f = 70 mm, f/5.6 |
| Dedektör | CMV4000 (ams/OSRAM) | 2048×2048 px, pitch 5.5 µm |
| Referans ekran | GL049AMN10A (Guangli 0.49") | 1920×1080, pitch 5.616 µm — **pasif** |

| Bileşen | Model | Kritik değerler |
|---|---|---|
| Lens | Hydra yıldız izleyici objektifi | f = 47.7 mm, f/1.4, pupil 34 mm, kullanılabilir FOV 21.5° |
| Dedektör | Hydra dedektörü | 1024×1024 px, pitch 18 µm |
| Referans ekran | STOS görüntüleme ekranı | 1280×1024, pitch 13.62 µm, **0.027 °/px** (ima edilen f ≈ 28.90 mm) |

Düzenek kollimatörsüzdür — varsayılan olarak pinhole (rektilineer) kamera
modeli kullanılır. Hydra sisteminde lensin görüntü dairesi (18.11 mm) sensör
köşegeninden (26.07 mm) küçüktür; köşeler karanlıktır ve gerçek FOV her yönde
21.5°'dir.

**Pasif ekran ile açısal kaynak farkı önemlidir:** GL049 pasif bir yüzeydir,
kendi açısal ölçeği yoktur. STOS ise açısal kaynaktır — üreticinin verdiği
°/px bir odak uzaklığı ima eder ve çözücü bunu kullanır.
