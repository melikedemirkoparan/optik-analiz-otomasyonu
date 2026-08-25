# FOV VE TILT ÖLÇÜM ALGORİTMASI

**Piksel piksel (desen-agnostik) yaklaşım**

Sürüm: 2026-08-17 · Modül: `core/dense_align.py` · Doğrulama: `test_dense_align.py`

---

## ÖNCE BİR DÜZELTME: FOV PİKSEL PİKSEL ÖLÇÜLMÜYOR

Bu dokümanın en başında netleştirilmesi gereken bir nokta var, çünkü
yanlış anlaşılması ölçüm sonuçlarının yorumunu bozar:

> **FOV ve IFOV görüntüden ölçülmüyor.** Yalnızca lens ve dedektör
> parametrelerinden hesaplanıyor. Piksel piksel çalışan algoritma
> **dönme, tilt, ölçek ve distorsiyon** üretiyor — FOV'a katkısı yok.

Sebebi fiziksel: FOV, sensörün fiziksel boyutu ile odak uzaklığının
oranıdır. Bu iki değer donanımdan bilinir; görüntüye bakmak gerekmez.

```
FOV  = 2 · atan( N · pitch / (2f) )
IFOV = 2 · atan( pitch / (2f) )
```

Mevcut donanımda (f = 70 mm, CMV4000 2048² @ 5.5 µm):

| Büyüklük | Değer |
|---|---|
| FOV yatay / dikey | 9.200° × 9.200° |
| FOV köşegen | 12.983° |
| IFOV | 78.57 µrad/px (16.207 arcsec/px) |
| Sensör | 11.26 × 11.26 mm |

Kod: `optics.compute_fov(cfg)` — girdisi yalnızca `SystemConfig`, görüntü
parametresi **yok**. `pipeline.py` de bunu açıkça belirtir: karşılaştırma
tablosunda FOV/IFOV bulunmaz, çünkü kırpmayla değişmezler.

**Görüntüden ölçülebilen şey ise ÖLÇEK'tir** — ve ondan efektif IFOV
türetilebilir. Bunun nasıl yapıldığı ve neden şu an raporlanmadığı
Bölüm 6'da.

---

## 1. PROBLEM

İki görüntü var:

- **Ground truth (GT):** OLED'e basılan bilinen test deseni
- **Dedektör görüntüsü:** lens + sensör üzerinden çekilen gerçek görüntü

Aralarındaki geometrik farkı çözüp dönme, tilt ve distorsiyonu ölçmek
gerekiyor. Zorluklar:

1. **Farklı çözünürlük ve kadraj.** GT 894×730, dedektör 1600×1600 olabilir.
   GT ayrıca OLED'e kırpılarak basılmış olabilir.
2. **Ölçek bilinmiyor.** "Ground truth her zaman benzer ölçeklerde
   verilmeyecek" — bu, mutlak referansın kullanılamayacağı anlamına gelir
   (bkz. Bölüm 5).
3. **Desen serbest.** Sınırsız çeşitlilikte patern gelecek: eş merkezli
   çember, Siemens star, ızgara, harf, bilinmeyen başka şeyler.
4. **Ayna belirsizliği.** Dedektör görüntüsü tipik olarak aynalanmıştır.

Üçüncü madde algoritmanın tüm tasarımını belirler.

---

## 2. NEDEN ÖZELLİK TABANLI YÖNTEM (SIFT) YETMEDİ

Mevcut `image_analysis.py` SIFT kullanır: köşe/blob bulur, tanımlayıcıyla
eşler, RANSAC ile homografi çıkarır. Bu yaklaşım **desene bağımlıdır** ve
kendine-benzer desenlerde çöker.

Siemens star'da bu daha önce yaşandı (bkz. `DEVAM_YONERGESI.md` §5.2):
SIFT merkez çevresinde yüzlerce sahte eşleşme üretti, RANSAC bunları
görüntüyü tek noktaya çökerten bir homografiyle "açıkladı", sonuç
**+39.7° gibi tamamen uydurma bir dönme** oldu. Çözüm olarak
`_homography_is_sane()` eklendi.

**Ama yeni çember paterni bu savunmayı aştı.** Ölçüm:

| desen | gerçek dönme | SIFT sonucu | dejenere bayrağı | yoğun yöntem |
|---|---|---|---|---|
| `v1_1deg_fov` | +3.00° | +3.87° | False | +3.19° |
| `v3_0.5deg_dense` | +3.00° | **−80.53°** | **False** | **+3.22°** |

İkinci satır kritik: SIFT 83° hatalı bir sayı üretti ve dejenerelik
kontrolleri bunu **yakalayamadı** — sonuç sessizce yanlış raporlanacaktı.
Sebep, 0.5° adımlı yoğun çemberlerde her halkanın diğerine benzemesi;
13 inlier'ın hepsi tutarlı ama yanlış bir eşleşmede birleşebiliyor.

**Çıkarım:** güvenlik kontrolü eklemek yetmiyor. Az sayıda eşleşmeye
dayanan her yöntem, kendine-benzer desende kandırılabilir. Ölçümün
**tüm piksellere** bakması gerekiyor.

---

## 3. YOĞUN (DESEN-AGNOSTİK) YAKLAŞIM

Algoritma hiçbir özellik **aramaz**. Tek sorduğu soru:

> *"Ground truth'u şu dönüşümle warp edersem dedektör görüntüsüne
> ne kadar benzer?"*

ve benzerliği artıran yöne gider. Desenin ne olduğu umurunda değildir.
Tek gereksinim desenin **dokusu** olmasıdır — düz gri bir alan hizalanamaz,
ama bu teorik bir sınırdır, algoritmanın eksiği değil.

### Üç kademe

```
GT + dedektör görüntüsü
        │
        ├─ 1. coarse_align    log-polar faz korelasyonu
        │                     → çeviri, ölçek, dönme (kaba)
        │
        ├─ 2. refine_ecc      ECC, piramitli
        │                     → alt-piksel 3×3 homografi
        │
        └─ 3. residual_flow   yoğun optik akış
                              → HER PİKSEL için sapma vektörü
```

---

### Kademe 1 — Kaba hizalama (`coarse_align`)

**Amaç:** başlangıç tahmini olmadan, global olarak çeviri + ölçek + dönme.

**Temel numara:** Fourier genliği **çeviriden bağımsızdır** — bir görüntüyü
kaydırmak yalnızca fazı değiştirir, genliği değiştirmez. Bu sayede üç
bilinmeyeni aynı anda aramak yerine ikiye bölebiliriz:

1. Genlik spektrumundan → **ölçek ve dönme**
2. Fazdan → **çeviri**

Kartezyen düzlemde ölçek ve dönme olan fark, **log-polar** düzlemde iki
eksende çeviriye dönüşür — ve çeviriyi faz korelasyonu doğrudan ölçer.

```
görüntü → FFT genlik → yüksek-geçiren filtre → log-polar → faz korelasyonu
                                                              ↓
                                              x kayması → ölçek
                                              y kayması → dönme açısı
```

**Neden yüksek-geçiren filtre şart:** Genlik spektrumunun enerjisi ezici
biçimde merkezde (düşük frekanslarda) toplanır. Filtresiz bırakıldığında
bu dev tepe dönme/ölçek bilgisini taşıyan orta frekansları boğar ve faz
korelasyonu gerçek tepe yerine **ızgaranın kendi simetrisine (45°/90°)
kilitlenir**. Geliştirme sırasında tam olarak bu görüldü: ölçüm her
girdide 45 veya 90 döndürüyordu. Filtre `H = (1−X)(2−X)`, `X = cos(πf)`
(Reddy–Chatterji yaklaşımı).

**Neden bu kademe dejenere olamaz:** çözüm uzayı yalnızca 4 parametredir
(çeviri×2, ölçek, dönme). Görüntüyü tek noktaya çökerten bir dönüşüm bu
uzayda **yoktur**. SIFT'in başına gelen türden bir çöküş yapısal olarak
imkânsızdır.

---

### Kademe 2 — İnce hizalama (`refine_ecc`)

**Amaç:** alt-piksel doğrulukta tam homografi (8 serbestlik — tilt dahil).

ECC (Enhanced Correlation Coefficient) yoğunlukları doğrudan hizalar ve
**aydınlatma farkına karşı bağışıktır**: parlaklık/kontrast farkını modelin
içinde soğurur. Bu projede kritik, çünkü GT ideal bir desen, dedektör
görüntüsü ise gerçek pozlamalı bir çekim.

Piramitli koşar (kabadan inceye): önce 1/4 çözünürlükte çözer, çözümü bir
üst seviyeye ölçekler. Hem hızlanır hem yerel minimuma takılma riski azalır.

**Çıktısı 3×3 homografidir** ve mevcut `optics.decompose_homography()`
tarafından **değiştirilmeden** ayrıştırılır. Bunun pratik sonucu: yoğun
yolun ürettiği dönme/tilt değerleri SIFT yolununkilerle **birebir aynı
konvansiyonda** ve doğrudan karşılaştırılabilir.

Ayrıştırma QR tabanlıdır: `A = R · K` (rotasyon × üst-üçgen). Anizotropik
ölçek ve kırpma böylece dönme ölçümüne karışmaz. Düzlem-dışı tilt ise
perspektif terimlerinden (`h₂₀`, `h₂₁`) görüntü boyutuyla normalize edilerek
çıkarılır.

---

### Kademe 3 — Piksel piksel kalıntı (`residual_flow`)

**Bu kademe, tek sayı yerine alan üreten kısımdır.**

Homografi **ideal** bir projektif dönüşümdür: düz çizgiyi düz çizgiye
götürür. Gerçek mercek götürmez. Dolayısıyla homografi uygulandıktan
sonra kalan sapma **distorsiyondur**.

```
warp(GT, H)  vs  dedektör
        ↓
  yoğun optik akış (Farnebäck)
        ↓
  her piksel için (dx, dy) vektörü
        ↓
  ┌─ büyüklük haritası (ısı haritası)
  ├─ radyal profil (merkezden uzaklığa göre)
  └─ radyal model: dr(r) = a₁r + a₃r³ + a₅r⁵
```

Farnebäck seçildi çünkü **desen bilmez** — her piksel çevresindeki yoğunluk
yüzeyini polinomla modeller, köşe/kenar aramaz.

**Model neden a₁ (ölçek) terimi içeriyor:** Homografi "en iyi uyum" ararken
distorsiyonlu bir görüntüyü hafifçe büyütüp/küçültüp toplam hatayı azaltır.
Bu, kalıntı profilini merkezde yukarı kaydırır — ölçülen eğri artık saf
distorsiyon değil, *distorsiyon + artık ölçek* toplamıdır. GT'nin ölçeği
bilinmediği için bu serbestlik **kaçınılmazdır ve fiziksel olarak doğrudur**.
Ama ikisi ayrılmazsa distorsiyon olduğundan **küçük** görünür. Bu yüzden a₁
modele dahil edilir ve distorsiyon yalnızca a₃/a₅'ten okunur.

---

## 4. DÜRÜSTLÜK MEKANİZMALARI

Projenin kalıcı kuralı (bkz. `DEVAM_YONERGESI.md` §5, "panel ↔ tablo
ayrışması"): **ölçüm katmanının ölçemediği yerde sayı uydurmaması.**
Bu modülde üç yerde uygulanır.

### 4.1 Radyallik denetimi — büyük kalıntı ≠ distorsiyon

Gerçek görüntü çiftinde kalıntı RMS **9.66 px** çıktı ve radyal modele
uydurulunca `−0.84%` "fıçı distorsiyonu" gibi okundu. **Ama ısı haritası
başka bir şey gösterdi:** sapma harflerin, rakamların ve ince kamaların
üstünde yoğunlaşmış, düz alanlarda sıfırdı. Gerçek distorsiyon olsaydı
merkezden dışa doğru düzgün artan **halkalar** görünmeliydi.

Bu distorsiyon değil, GT (894×730) ile dedektörün (1600×1600)
**keskinlik/örnekleme farkı**: ince detaylar birebir örtüşmüyor, optik akış
bunu kayma sanıyor.

**Ayırt edici ölçüt — radyallik** (`radial_fraction`): distorsiyon merkezden
uzaklığa bağlıdır, hangi yönde bakıldığına değil.

| durum | radyallik | karar |
|---|---|---|
| sentetik gerçek distorsiyon (k₁ = ±0.02…0.05) | **1.00** | kabul |
| gerçek görüntü çifti | **0.63** | **reddedildi** |
| saf keskinlik farkı (2× büyütme + bulanıklık) | **0.06** | **reddedildi** |

Eşik 0.90. Geçmezse sayı **yazılmaz**, yerine şu yazılır:
*"ölçülemedi — kalıntı radyal değil; büyük olasılıkla keskinlik/örnekleme
farkı, distorsiyon değil"*.

### 4.2 Sıra: önce büyüklük, sonra şekil

Kalıntı zaten gürültü seviyesindeyse (< 0.5 px) şeklini sormak anlamsızdır —
**sıfırın şekli olmaz**. O durumda "radyal değil" demek yanlış teşhis olur;
doğru cevap "distorsiyon yok". Kod bu sırayı uygular (`negligible` önce).

### 4.3 Bilgisiz girdide reddetme

Düz gri alanda hizalama teorik olarak imkânsızdır. Doğru davranış bir sayı
uydurmak değil, güveni düşük raporlamaktır. Test `[6]` bunu doğrular:
düz gri alan `ok=False`, skor −1.000 ile reddedilir.

---

## 5. ÖLÇEK NEDEN VERİDEN ÇÖZÜLÜYOR

Distorsiyonu ideal `f·tan(θ)` modeline göre **mutlak** ölçmek isterdik.
Yapılamıyor, sebebi şu:

> Ground truth'un ölçeği bilinmiyorsa, ölçülen sapmanın ne kadarının
> distorsiyon ne kadarının ölçek farkı olduğu **ayırt edilemez**.

İki bilinmeyen, tek denklem. Homografi bu bilinmeyen ölçeği/kadrajı
**verinin kendisinden** çözüp soğurur; geriye kalan kalıntı saf
distorsiyon olur. Referans budur.

**Pratik sonucu:** raporlanan distorsiyon "ideal mercek modeline göre"
değil, "veriden çözülen en iyi projektif uyuma göre" tanımlıdır. Bu bir
eksiklik değil, elde bilgi olmadığında **tek doğru** tanımdır.

---

## 6. FOV/IFOV İLE İLİŞKİ — ŞU AN NE VAR, NE YOK

**Şu an var:** nominal FOV/IFOV, `optics.compute_fov(cfg)` ile
parametrelerden. Görüntüden bağımsız.

**Şu an yok:** görüntüden ölçülen efektif IFOV.

Yoğun hizalama ölçeği (`scale_x`, `scale_y`) ölçüyor — gerçek çiftte
2.1287 / 2.0929 çıktı (SIFT: 2.1367 / 2.1385). Bu, GT ile dedektör
arasındaki büyütme oranıdır. **Ondan efektif IFOV türetmek matematiksel
olarak mümkündür**, ancak GT'nin açısal ölçeğinin bilinmesi gerekir:
OLED'e basılan desenin bir özelliğinin kaç dereceye karşılık geldiği.

`optics.measured_ifov_from_scale()` bu amaçla mevcut ama şu an nominal
değeri döndürüyor — yani **gerçek bir ölçüm yapmıyor**. Bunu işler hale
getirmek ayrı bir iştir ve GT'nin açısal kalibrasyonunu gerektirir.

Dokümanın başındaki uyarı bu yüzden: FOV bugün ölçülmüyor, hesaplanıyor.

---

## 7. TILT ÖLÇÜMÜ — ÜÇ BAĞIMSIZ KAYNAK

Tilt, tek bir yönteme bırakılmamıştır. `tilt_estimators.measure_tilt()`
birden çok tahminciyi koşturur, her birinin **belirsizliğini** hesaplar ve
en düşük sigmalıyı seçer.

| yöntem | kimlik | dayanak |
|---|---|---|
| Elips fit | `circle_ellipse` | bilinen dairesel desen → `acos(b/a)` |
| Kaçış noktası | `grid_vanishing` | ızgara/çizgi deseni → perspektif geometrisi |
| Homografi | (yoğun / SIFT) | perspektif terimleri `h₂₀`, `h₂₁` |

**Kritik kural:** rapor `ok=False` derse, çağıran taraf **yedek yola
düşmez**. Bu kural bir hata sonucu konuldu — panel, ölçüm katmanının
bilinçli reddini yok sayıp eski `res.tilt_deg` değerini basıyordu
(§5, 3. ayrışma). `tilt_estimators` tam olarak "gürültüden tilt uydurma"yı
engellemek için var.

Belirsizlik açıkça raporlanır:
- Ayırt edilebiliyorsa: `1.83° ± 0.20°`
- Ayırt edilemiyorsa: `< 3.62° (gürültü sınırı altında)`

İkincisi **"tilt yok" demek değildir** — "bu yöntemle ayırt edilemiyor"
demektir.

Yoğun yolun tilt katkısı homografinin perspektif terimlerindendir.
Sentetik doğrulamada saf benzerlik dönüşümlerinde tilt ≈ 0.01° çıkar
(olması gerektiği gibi), yani yöntem **sahte tilt üretmiyor**.

---

## 8. DOĞRULAMA

`test_dense_align.py` — 7 test grubu, tümü geçiyor.

Testler bilerek **birbirine hiç benzemeyen** desenlerle koşar: rastgele
doku, eş merkezli çember, Siemens star, satranç tahtası, nokta ızgarası,
tek yönlü çizgiler. Hepsinde aynı kod, aynı parametreler.

| # | test | sonuç |
|---|---|---|
| 1 | Desen-agnostiklik (5 desen, aynı dönüşüm) | dönme hatası ≤ 0.01°, ölçek 4 hane doğru |
| 2 | Dönüşüm taraması (±30°, ölçek 0.8–1.25) | hata < 0.35° |
| 3 | Bilinen distorsiyonu geri okuma | %0.3–13 hata |
| 4 | Temiz çiftte sahte distorsiyon üretmeme | RMS 0.009–0.06 px |
| 5 | SIFT karşılaştırması | Siemens star'da SIFT reddediyor, yoğun +3.01° |
| 6 | Bilgisiz girdide dürüstlük | düz gri reddediliyor |
| 7 | Artefaktı distorsiyon sanmama | keskinlik farkı radyallik 0.06 ile reddediliyor |

**Mevcut referans değerler korunmuştur** (yoğun yol eklenmesine rağmen):
FOV 9.200°, IFOV 78.57 µrad/px, dönme +1.583°, inlier 86, reproj 1.40 px.
`test_core`, `test_pipeline`, `test_tilt_synth`, `test_roi_analiz` — hepsi geçiyor.

---

## 9. BİLİNEN SINIRLAR

Bunlar kod hatası değil, **matematiksel sınırlardır**:

- **Periyodik desen:** 24 px aralıklı çizgilerde 17 px kayma ile 17+24k px
  kayma **ayırt edilemez**. Ölçüm periyot-modülo doğrudur.
- **Dairesel simetrik desen:** saf eş merkezli çemberde dönme ölçülemez —
  hiçbir algoritma ölçemez. `generate_circle_pattern.py`'ın dört köşeye
  farklı açılarda F koymasının sebebi budur.
- **Dokusuz alan:** düz gri hizalanamaz; doğru davranış reddetmektir.
- **Büyük distorsiyon:** kenarda ~30 px sapmada optik akış zayıflar,
  hata %10–13'e çıkar. Gerçek merceklerde bu uç durumdur.
- **Keskinlik farkı:** GT ile dedektör çok farklı çözünürlükteyse kalıntı
  haritası distorsiyon ölçemez (radyallik denetimi bunu yakalar). Anlamlı
  distorsiyon haritası için iki görüntü benzer çözünürlükte olmalıdır.

---

# EK A — NEDEN `cv2.flip`, NEDEN ArUco DEĞİL

## A.1 Soru

Ayna (mirror) tespiti için `cv2.flip` yerine `cv2.aruco`
(`opencv-contrib-python`) kullanmak daha iyi olur mu?

## A.2 Önce bir kavram düzeltmesi

`cv2.flip` ve ArUco **aynı işi yapmıyor**:

- `cv2.flip` bir **görüntü işlemidir** — ayna *tespit etmez*, ayna *uygular*.
- Tespiti yapan şey, dört varyantı (`raw`, `flip_h`, `flip_v`, `flip_both`)
  deneyip **hangisinin en iyi hizalandığına** bakan karar mekanizmasıdır.

Yani karşılaştırma "flip vs ArUco" değil, "hizalama skoruna dayalı arama
vs marker tabanlı tespit".

## A.3 ArUco ne yapardı

ArUco bir **marker tespit** kütüphanesidir. Belirli bir sözlükten
(`DICT_4X4_50` vb.) gelen, siyah çerçeveli kare fiducial'ları arar ve
ID'lerini çözer. Ayna tespitini yan ürün olarak halleder: aynalanmış bir
marker geçerli bir kod üretmez, dolayısıyla `detectMarkers` onu bulamaz.

## A.4 Neden bu projeye uymuyor

**1. Desen bağımlılığı geri gelir — modülün varlık sebebine aykırı.**

Bu modülün tüm amacı desen-agnostik olmak. ArUco yalnızca **içinde ArUco
markerı olan** paternlerde çalışır. Kullanıcı gereksinimi ise açık:
*"sınırsız garip pattern olacak"*. Dışarıdan gelen, bizim üretmediğimiz
bir paternde ArUco markerı bulunmayacaktır. Yöntem, tam da genel olması
gereken yerde özelleşir.

**2. Markerın çözünür olması gerekir.**

FOV kenarına konan küçük bir marker; bulanıklık, düşük kontrast veya
düşük çözünürlükte okunamaz. O durumda ayna tespiti **tamamen çöker**.
Mevcut yöntem ise bozulmaz — yalnızca güveni düşer ve bunu raporlar.

**3. Ek bağımlılık ve çakışma.**

`opencv-contrib-python`, mevcut `opencv-python` 4.13 ile çakışır; birini
kaldırıp diğerini kurmak gerekir. Bu, doğrulanmış tüm referans değerlerin
(FOV 9.200°, dönme +1.583°, sentetik tilt hatası 0.29°) yeniden
sınanmasını zorunlu kılar.

**4. Çözdüğü belirsizlik ölçüme yansımıyor.** ← en önemli gerekçe

Aşağıda ölçüldü.

## A.5 Ölçüm: gerçek bir dejenerelik var, ama zararsız

Soru haklı bir sezgiye dayanıyordu — ayna tespitinde **gerçekten** bir
dejenerelik var. `patterns/v1_1deg_fov.png` üzerinde, dört varyantın ECC
korelasyon skorları (tam çözünürlük, 1280×1024):

| uygulanan | raw | flip_h | flip_v | flip_both | 1. ile 2. farkı |
|---|---|---|---|---|---|
| `raw` | **1.0000** | 0.9198 | 0.9198 | **1.0000** | **0.0000** |
| `flip_h` | 0.9198 | **1.0000** | **1.0000** | 0.9198 | **0.0000** |

Skorlar **ikili gruplar halinde birebir eşit**. Sebep:

```
flip_v  =  flip_h  +  180° dönme
```

ECC dönmeyi serbest bıraktığı için ikisini özdeş görür. `generate_circle_pattern.py`
F'lere 0/90/**45**/270 dönme vererek bu simetriyi kırmayı hedeflemişti,
ancak ayrım ECC'nin göreceği büyüklüğe ulaşmıyor.

**Peki bu ölçümü bozuyor mu?** Ölçüldü — `flip_h` + 3.00° dönme uygulanmış
görüntüde, iki aday varyantın verdiği sonuçlar:

| varyant | ECC | dönme | tilt | ölçek x | ölçek y |
|---|---|---|---|---|---|
| `flip_h` | 0.9924 | **+3.083°** | 0.001° | 0.9998 | 0.9998 |
| `flip_v` | 0.9924 | **+3.084°** | 0.001° | 0.9998 | 0.9998 |

**Ölçülen tüm büyüklükler aynı.** Fark yalnızca varyantın *adında*.

Sebep basit: iki yol fiziksel olarak **aynı dönüşümü** tarif ediyor
(`flip_v` = `flip_h` + 180°), ve 180°'lik fark projenin dönmeyi ±90°'ye
indirgemesinde yok oluyor. "Ayna var mı?" sorusunun cevabı da her iki
adayda aynı: **evet**.

## A.6 Sonuç

ArUco'ya geçmek **önerilmez**:

| ölçüt | `cv2.flip` + hizalama skoru | ArUco |
|---|---|---|
| Rastgele/bilinmeyen desende çalışır | **evet** | hayır |
| Ek bağımlılık | yok | `opencv-contrib-python` |
| Düşük kontrast/bulanıklıkta | güven düşer, çalışır | tamamen çöker |
| Ayna eksenini kesin belirler | hayır (dejenere olabilir) | **evet** |
| Belirsizliğin ölçüme etkisi | **yok** (A.5'te ölçüldü) | — |

Son iki satır birlikte okunmalı: ArUco'nun tek üstünlüğü, **ölçülen hiçbir
değeri değiştirmeyen** bir etiketi kesinleştirmek. Bunun için desen
bağımlılığı ve bağımlılık çakışması kabul etmek orantısız.

## A.7 Yine de yapılabilecek iki iyileştirme

Sorunun işaret ettiği zayıflık gerçek olduğu için, ArUco'suz iki seçenek
kayda geçiriliyor (henüz **uygulanmadı**):

**1. Belirsizliği raporlamak (ucuz, her patern için geçerli).**
En iyi iki varyant skorunun farkı ihmal edilebilirse
*"ayna ekseni belirsiz (ölçüme etkisi yok)"* yazmak. Şu an ilk gelen
varyant sessizce seçiliyor. Bu, §4'teki dürüstlük kuralıyla tutarlı olurdu.

**2. Deseni güçlendirmek (ArUco'ya en yakın *doğru* çözüm).**
`generate_circle_pattern.py`'da F dizilimini 180° dönme altında gerçekten
asimetrik yapmak — örneğin bir F'yi diğerlerinden farklı boyda yapmak ya da
tek bir azimuta ikinci bir işaret koymak. Bu, ArUco'nun sağladığı ayrımı
**ek bağımlılık ve desen kısıtı olmadan** verir. Yalnızca kendi
ürettiğimiz paternleri iyileştirir.

---

# EK B — GELİŞTİRME SIRASINDA ÇÖZÜLEN HATALAR

Bu hatalar kaydediliyor çünkü üçü sentetik testte **görünmüyordu** ve
benzer bir modül yazılırken tekrar edilmesi muhtemel.

### B.1 Kaba kademe hiçbir şey bulamıyordu

**Belirti:** ölçüm her girdide 45° veya 90° veriyordu; `sy = −99.9`
(görüntü yüksekliğinin tam çeyreği).
**Sebep:** FFT genlik spektrumunun enerjisi merkezde toplanıyor, faz
korelasyonu gerçek tepe yerine ızgara simetrisine kilitleniyordu.
**Çözüm:** yüksek-geçiren spektrum filtresi + log-polar örneklemeyi
görüntü boyutundan ayırmak (720 açısal × 512 radyal).

### B.2 Ölçek ters çıkıyordu

**Belirti:** 1.25 yerine 0.804 (= 1/1.244).
**Sebep:** Fourier ölçek karşıtlığı — görüntü büyürse spektrumu küçülür.
**Çözüm:** `scale = exp(−sx/M)` (işaret düzeltildi).

### B.3 Dönme yönü tersti

**Belirti:** doğru parametrelerle bile korelasyon 0.009; `−5°` denenince
0.995.
**Sebep:** görüntü koordinatlarında y **aşağı** bakar, standart matematiksel
dönme matrisi ekranda ters döner.
**Çözüm:** OpenCV `getRotationMatrix2D` konvansiyonuna uyum (`th = −rot`).

### B.4 Gerçek görüntü çiftinde ölçek hiç görülmüyordu

**En önemlisi — yalnızca gerçek veriyle yakalandı.**

**Belirti:** gerçek ölçek 2.192 iken ölçüm 0.994 veriyordu; ECC korelasyonu
0.076'da kalıyordu.
**Sebep:** `_coarse_one` iki görüntüyü ortak tuvale **sıfırla doldurarak**
oturtuyordu. GT 894×730, dedektör 1600×1600 olduğundan GT'nin etrafında dev
bir sıfır çerçevesi kalıyor, bu yapay kenar spektrumu domine ediyordu.
**Çözüm:** doldurma yerine **ölçekleme**; uygulanan çarpan (`pre_scale`)
sonuçtan geri çıkarılır.

> **Ders:** sentetik testlerde iki görüntü hep aynı boyuttaydı, bu yüzden
> hata görünmüyordu. **Gerçek görüntü çiftiyle test şart.**

### B.5 Ayna varyantı yanlış seçiliyordu

**Belirti:** gerçek çiftte kaba korelasyon `raw` 0.386 / `flip_h` 0.338
verip **yanlış** olanı seçiyordu; SIFT ise `flip_h` buluyordu.
**Sebep:** kaba korelasyon varyantlar arasında zayıf bir ayırt edici.
**Çözüm:** seçim ECC'ye taşındı — aynı çiftte 0.759 / **0.868** ile
doğrusunu net ayırıyor. Bedeli varyant başına bir ECC koşusu.

### B.6 Kenar artefaktı sahte distorsiyon üretiyordu

**Belirti:** satranç tahtasında kalıntı RMS 1.95 px (olması gereken ~0).
**Sebep:** geçerlilik maskesi 9×9 ile aşındırılıyordu, ama Farnebäck her
pikselde `winsize`(=25) genişliğinde komşuluğa bakar; sınıra 12 px'den
yakın pikseller warp dışındaki boşluğu "görüyordu". Bozuk piksellerin
%100'ü kenardan ≤10 px içerideydi.
**Çözüm:** aşındırma yarıçapı `winsize/2`'ye bağlandı; ayrıca özet
istatistikler aykırı değere dayanıklı hale getirildi (%99 kırpma).
Sonuç: 1.95 px → **0.040 px**.

---

## KAYNAK DOSYALAR

| dosya | içerik |
|---|---|
| `core/dense_align.py` | üç kademe + radyal model + dürüstlük denetimleri |
| `core/optics.py` | FOV/IFOV, homografi ayrıştırma (değişmedi) |
| `core/tilt_estimators.py` | çoklu yöntem tilt + belirsizlik (değişmedi) |
| `core/pipeline.py` | `dense=True` ile paralel koşum |
| `test_dense_align.py` | 7 test grubu |
| `DEVAM_YONERGESI.md` §7B | proje geneli devam notları |
