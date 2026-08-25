# OPTİK ANALİZ PROJESİ — DEVAM YÖNERGESİ

> **Devam etmek için:** Claude Code'u `/home/test123/Desktop/optik_analiz` dizininde açıp
> şunu yaz: **"Optik analiz projesine devam edelim, DEVAM_YONERGESI.md'yi oku"**

Son güncelleme: 2026-08-14

---

## 1. PROJE NEDİR?

Bir **optik test/kalibrasyon yazılımı**. Yazılım:
- **Ground truth görüntüsü** (OLED'e yansıtılan bilinen test deseni — WTW Camera Test Chart) ile
- **Dedektör görüntüsünü** (lens + dedektör üzerinden çekilen gerçek görüntü)

karşılaştırıp şu optik parametreleri **otomatik** hesaplar:
- **FOV** (Field of View — görüş açısı)
- **Açısal FOV / IFOV** (bir pikselin gördüğü açı)
- **Tilt** (hem düzlem-içi dönme hem düzlem-dışı perspektif)

**En kritik gereksinim:** Yazılım **PARAMETRİK**. Lens, dedektör veya OLED
değişirse matematik otomatik yeni değerlere göre kurulur — hiçbir değer koda gömülü değil.
Arayüz PyQt5 ile yapıldı.

---

## 2. DONANIM PARAMETRELERİ

| Bileşen | Model | Kritik değerler |
|---|---|---|
| **Lens** | Rodenstock HR Digaron-W | **f = 70 mm**, açıklık f/5.6 |
| **Dedektör** | CMV4000 (ams/OSRAM) | 2048×2048 px, pitch **5.5 µm**, kare sensör ~11.26×11.26 mm |
| **OLED** | GL049AMN10A (Guangli 0.49") | 1920×1080, pitch 5.616 µm, aktif 10.783×6.065 mm |

**Datasheet dosyaları:**
- `/home/test123/Downloads/osramDedektör_CMV4000_DS000728_8-01.pdf`
- `/home/test123/Downloads/SPEC-GL049AMN10A-V0.pdf`

---

## 3. DÜZENEK VE KARARLAR

1. **Arayüz:** PyQt5 masaüstü uygulaması.
2. **Düzenek:** Kollimatör YOK → **pinhole kamera modeli**.
3. **Tilt:** Hem düzlem-içi dönme hem düzlem-dışı tilt ölçülüyor.
4. **Görüntüler klasörden yükleniyor** (dosya diyaloğu).
5. **Ground truth OLED'e KIRPILARAK basılmış** → GT ve dedektör görüntüleri
   farklı çözünürlük + farklı kadraj. Bu yüzden tilt ölçümü ölçekten
   bağımsız olan **elips yöntemine** dayanıyor.

**Test görüntüleri (örnek çift):**
- Ground truth: `/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53 (1).jpeg` (894×730)
- Dedektör: `/home/test123/Downloads/WhatsApp Image 2026-08-11 at 15.54.53.jpeg` (1600×1600, **yatay aynalı** + hafif dönme)

---

## 4. MEVCUT DURUM — TAMAMLANDI ✅

```
optik_analiz/
├── core/
│   ├── config.py          ✅ Parametrik config (Lens/Detector/OLED/SystemConfig) — JSON kaydet/yükle
│   ├── optics.py          ✅ FOV/IFOV hesabı + homografi tilt ayrıştırma
│   ├── image_analysis.py  ✅ SIFT eşleme, ayna tespiti, DEJENERE HOMOGRAFİ REDDİ, overlay
│   ├── siemens_star.py    ✅ Elips-fit tilt — DOĞRULANDI (hata < 0.3°)
│   ├── pipeline.py        ✅ Tüm akışı birleştiren tek giriş noktası (GUI bunu çağırır)
│   └── __init__.py
├── gui/
│   ├── main_window.py     ✅ Ana pencere (3 panel + arka plan thread)
│   ├── widgets.py         ✅ Tema, ImageView, ResultRow
│   └── __init__.py
├── presets/               (preset JSON'ları buraya kaydedilir)
├── data/                  (debug çıktıları)
├── run_gui.py             ✅ Arayüzü başlatır
├── test_core.py           ✅ Çekirdek test
├── test_pipeline.py       ✅ Uçtan uca akış testi
├── test_tilt_synth.py     ✅ Sentetik bilinen-tilt doğrulaması
├── test_tilt_multi.py     ✅ Çoklu tilt yöntemi raporu
├── test_roi.py            ✅ Kırpma (ROI) arayüz testi
└── test_roi_analiz.py     ✅ Çift analiz + panel↔tablo tutarlılığı
```

### Doğrulanmış sonuçlar (örnek görüntü çifti)

| Ölçüm | Değer |
|---|---|
| FOV | 9.200° × 9.200° (köşegen 12.983°) |
| IFOV | 78.57 µrad/px (16.207 arcsec/px) |
| Düzlem-içi dönme | +1.583° |
| Düzlem-dışı tilt | 0.000° |
| Ayna | EVET (yatay flip) |
| Eşleme | 86 inlier, 1.40 px reproj |
| Elips güveni | 0.95 |

### Sentetik doğrulama (test_tilt_synth.py)
Bilinen tiltle eğilmiş yapay Siemens star üretilip ölçüm geri okunuyor:
- 0°–40° arası **en büyük hata 0.29°**
- Mükemmel dairede oran = 1.0000 (sıfır sistematik sapma)
- Düzlem-içi dönme birebir izleniyor (15° döndür → açı 15° kayıyor)

---

## 5. BUGÜN ÇÖZÜLEN ÜÇ ÖNEMLİ HATA

### 1. Elips tüm chart'ı yakalıyordu (İŞ 1)
`_radial_boundary_ellipse` en dıştaki kenarı (`idx.max()`) alıyordu → köşe
yıldızları ve çerçeve dahil oluyordu. **Çözüm:** merkezi yıldızı ayıran özellik
**teğetsel geçiş yoğunluğu**. Yıldız içinde her halkada ~110 siyah-beyaz geçiş
var, dışında düşük. Profildeki ilk *kalıcı* düşüş = yıldızın sınırı.

### 2. Dejenere homografi (sessiz ve tehlikeliydi)
SIFT, Siemens star'ın kendine-benzer radyal deseninde merkez çevresinde
yüzlerce sahte eşleşme üretiyordu. RANSAC bunları **görüntüyü tek noktaya
çökerten** bir homografiyle "açıklıyordu" — reproj hatası 0.41 px olduğu için
sağlam görünüyordu, ama GT'nin dört köşesi de aynı noktaya düşüyordu.
Sonuç: **+39.7° gibi tamamen uydurma bir dönme değeri.**
**Çözüm:** `_homography_is_sane()` — izdüşen dörtgenin alanı, kenar oranları,
dışbükeyliği ve ölçeği denetleniyor; dejenere adaylar reddediliyor.
Doğru varyant (`flip_h`) seçilince dönme **+1.583°** oldu.

### 3. Elips ölçümünde %3 sistematik daralma
Mükemmel dairede bile oran 0.968 çıkıyordu (sahte 14.5° tilt). İki kaynak:
- `_refine_center` geçiş sayısını maksimize ediyordu — bu merkez etrafında
  düz bir tepe, merkezi 13 px kaydırıyordu. **Çözüm:** nokta-simetri skoru
  (halka profilini 180° kaydırıp kendisiyle korele et) — çok keskin tepe.
- `_boundary_points` global bir kontrast eşiği kullanıyordu; kamalar merkeze
  doğru sıklaştığı için bazı ışınlar erken kesiliyordu. **Çözüm:** her ışın
  kendi 80. yüzdeliğine göre normalize ediliyor + alt-piksel ara-değerleme.

---

### Kırpma analizinde dikkat: dar ROI'de eşleme dejenere oluyor

Çift analiz eklenirken ölçülen bir gerçek: **yıldız merkezine yakın küçük
bir ROI seçilirse eşleme DEJENERE oluyor** — bölüm 5.2'deki hatanın aynısı,
çünkü Siemens star'ın radyal deseni kendine benzer ve kesit daraldıkça
başka ayırt edici desen kalmıyor.

Ölçülen örnekler (GT 894×730, ROI kaynağı = ground truth):

| ROI | inlier | durum | dönme |
|---|---|---|---|
| 300×300 @ yıldız merkezi (297,215) | 114 | **dejenere** | ölçülemedi |
| 150×150 @ (297,215) | 0 | eşleşmedi | ölçülemedi |
| 500×400 @ (100,100) | 22 | sağlam | +1.856° |

`_homography_is_sane()` bunları doğru reddediyor, **ama** reddedilince
`rotation_deg` sessizce yıldız elipsine düşüp `0.000°` veriyordu — tabloda
"tam kare 1.583 / kırpma 0.000" diye **gerçek bölgesel farkmış gibi**
okunuyordu. Bu yüzden karşılaştırma tablosunda:

- `_match_ok()` — homografi yoksa **ya da dejenereyse** dönme gizlenir.
- `_match_state()` — "sağlam / dejenere / eşleşmedi". **Dejenere kontrolü
  önce yapılmalı**: dejenere durumda `homography` zaten `None`'a çekiliyor,
  None kontrolü öne alınırsa gerçek neden gizlenir ve yanlışlıkla
  "eşleşmedi" görünür.
- `_fmt_tilt()` — sigma sonsuzken "< inf" yerine "ölçülemedi" yazar.

**Pratik sonuç:** anlamlı kırpma karşılaştırması için ROI geniş olmalı ve
yıldız dışında desen (harf, kama, gri kademe) içermeli.

---

### Panel ↔ tablo ayrışması (çözüldü)

Aynı koşu **iki yerde** gösteriliyor: sağ paneldeki sonuç satırları ve
karşılaştırma tablosunun "tam kare" sütunu. İkisi ayrı kodla yazıldığı için
üç kez ayrıştı — hepsi de aynı hatanın türevi: **panel, ölçüm katmanının
"ölçemedim" dediği yerde bir yedek sayı yazıyordu.**

| # | Belirti | Kök neden |
|---|---|---|
| 1 | Panel `< 3.6`, tablo `< 7.25` | Tabloda sigma × 2 kullanılmıştı; proje standardı `sigma_deg` (1-sigma) |
| 2 | Panel `+0.000`, tablo `—` | Homografi dejenereyken panel yıldız elipsi yedeğini yazıyordu |
| 3 | Panel `5.859`, tablo `ölçülemedi` | `measure_tilt` yalnızca *doğrulanmamış* yöntem sonuç verdiği için `ok=False` demişti; panel bu **bilinçli reddi** yok sayıp eski `res.tilt_deg` yoluna düşüyordu |

3'ü en tehlikelisiydi: `tilt_estimators` katmanı tam olarak "gürültüden tilt
uydurma"yı engellemek için var, panel ise onun reddettiği sayıyı ekrana
basıyordu.

**Kural — tek doğruluk kaynağı.** Panel ve tablo aynı yardımcıları çağırır;
biçimlendirme kararı tek yerde verilir, çağırana yalnızca eksik değerin
*yazılışı* bırakılır (tabloda `—`, panelde `ölçülemedi`):

- `_fmt_rotation(res, missing=...)` — eşleme güvenilir değilse sayı yazmaz.
- `_fmt_tilt(res)` / `_show_tilt(res)` — ikisi de `res.tilt` raporunu
  dinler. **Rapor `ok=False` ise yedek yola DÜŞÜLMEZ**; rapor `None` ise
  (eski sonuç nesnesi) eski davranış geçerli.
- `_show_verdict()` — dejenereyi "eşleştirilemedi" ile birleştirmez; ikisi
  farklı teşhis, kullanıcıyı farklı yere bakmaya yollar.

**Bu ayrışmayı kalıcı olarak `test_roi_analiz.py` [8] yakalar:** aynı sonucu
hem panele hem tabloya yazdırıp karşılaştırır — biri sayı diğeri değilse,
sayılar farklıysa ya da `<` üst-sınır gösterimi ayrışıyorsa test patlar.
Sonuç satırı okunabilsin diye `ResultRow.value()` eklendi.

> Yeni bir ölçüm satırı eklenirken: değeri biçimlendiren fonksiyonu
> **paylaş**, tabloya ayrı bir lambda yazma — üç ayrışma da böyle doğdu.

---

## 6. ÇALIŞTIRMA

```bash
cd /home/test123/Desktop/optik_analiz
python3 run_gui.py            # arayüz
python3 test_pipeline.py      # uçtan uca akış (görüntülerle)
python3 test_tilt_synth.py    # sentetik doğrulama (görüntü gerekmez)
python3 test_tilt_multi.py    # çoklu tilt yöntemi raporu
python3 test_roi.py           # kırpma (ROI) arayüz testi (offscreen)
python3 test_roi_analiz.py    # çift analiz + panel↔tablo tutarlılığı
python3 test_dense_align.py   # yoğun (desen-agnostik) hizalama — bkz. 7B
python3 test_hydra.py         # Hydra donanımı + kırpılmış görüntü — bkz. 7C
python3 test_pointing.py      # decenter/roll/tilt + kapsama — bkz. 7C
python3 core/siemens_star.py  # sadece elips tespiti + debug PNG
```

Son koşu (2026-08-14): **hepsi geçiyor.** Referans değerler değişmedi —
FOV 9.200°, IFOV 78.57 µrad/px, dönme +1.583°, sentetik tiltte en büyük
hata 0.29°.

Ortam: Python 3.12 — OpenCV 4.13, NumPy 1.26, SciPy 1.11, PyQt5 5.15, matplotlib 3.10

---

## 7. ARAYÜZ KULLANIMI

- **Sol panel:** Görüntü seçimi + tüm sistem parametreleri (lens/dedektör/OLED).
  Her alan düzenlenebilir; sensör boyutu canlı güncellenir.
  Preset **Kaydet/Yükle/Varsayılan** butonları `presets/` altına JSON yazar.
- **Orta panel:** Üç sekme — Ground truth, Dedektör, Hizalama (overlay).
  İlk ikisinde yeşil elips = tespit edilen yıldız sınırı.
  Overlay'de kırmızı = dedektör, yeşil = hizalanmış GT; **sarı = iyi örtüşme**.
- **Kırpma (ROI):** Sol panelde ölçü ve konum girilebilen inceleme aracı.
  Alanlar: kaynak (GT/dedektör), genişlik, yükseklik, merkez X, merkez Y —
  hepsi piksel cinsinden. Konum **elle girilir** (hassasiyet için asıl yöntem);
  görüntüye tıklamak da merkezi oraya taşıyıp alanları doldurur. "Ortala"
  butonu görüntünün tam ortasına alır.
  Varsayılan **boş** (ölçü "—", kırpma kapalı); ölçü ve konum girilene kadar
  hiçbir alan seçilmez. Seçili alan kaynak sekmesinde mavi dikdörtgenle
  gösterilir, **Kırpma** sekmesinde büyütülür.
  Ölçü ve konum alanlarının üst sınırı yüklenen görüntünün boyutuna göre
  otomatik kısılır; kaynak ya da görüntü değişince konum sıfırlanır.
  Kırpma ham dosyadan alınır (önizleme üzerindeki elips çiziminden değil),
  böylece gerçek piksel verisi incelenir.
- **Çift analiz (tam kare ↔ kırpma):** ROI seçiliyken "ANALİZ ET" **iki**
  ölçüm koşar: (1) tam kare — asıl/referans sonuç, (2) kırpılan bölge.
  İkinci koşu için ROI'nin diğer görüntüdeki karşılığı tam kare koşusunun
  homografisiyle bulunur (homografi yoksa oransal eşleme yedeği); iki kesit
  geçici PNG'ye yazılıp **aynı** `pipeline.run_analysis` çağrılır — böylece
  iki sonuç birebir aynı kodu kullanır. Sonuçlar sağ paneldeki
  **"Tam kare ↔ Kırpma"** tablosunda yan yana; farklı değerler sarı vurgulanır.
  **Tam kare sonucu ROI'den etkilenmez** (test_roi_analiz.py bunu doğruluyor).
  **FOV/IFOV tabloda yok** — onlar görüntüden değil lens/dedektör
  parametrelerinden hesaplanır, kırpmayla değişmez. Karşılaştırılan şeyler:
  dönme, eğiklik, eşleşen nokta, hizalama hatası, desen güveni, eşleme durumu.
- **Sağ panel:** FOV, IFOV, tilt, yıldız elipsi ve eşleme kalitesi.
  Değerler renk kodlu (yeşil iyi / sarı dikkat / kırmızı sorunlu).
- Analiz arka plan thread'inde koşar; arayüz donmaz.

---

## 7B. YOĞUN (DESEN-AGNOSTİK) HİZALAMA — `core/dense_align.py`

Son güncelleme: 2026-08-17

### Neden eklendi

SIFT **desene bağımlıdır**: köşe/blob bulup tanımlayıcıyla eşler. Kendine-benzer
desenlerde çöker. Bölüm 5.2'deki Siemens star hatası bunun bir örneğiydi; eş
merkezli çember paterninde de aynı sorun çıktı ve **mevcut dejenerelik
kontrolleri onu yakalayamadı**:

| desen | gerçek dönme | SIFT | yoğun |
|---|---|---|---|
| `v3_0.5deg_dense` çember | +3.00° | **-80.53°** (dejenere=False!) | +3.22° |

Kullanıcı "sınırsız garip pattern olacak" dediği için ölçüm katmanı desenden
bağımsız olmak zorunda.

### Nasıl çalışıyor

Hiçbir özellik ARAMAZ. Tek sorduğu: *"GT'yi şu dönüşümle warp edersem dedektör
görüntüsüne ne kadar benzer?"* Üç kademe:

1. **`coarse_align`** — log-polar faz korelasyonu → çeviri + ölçek + dönme.
   Fourier genliği çeviriden bağımsız olduğu için ölçek/dönme çeviriden ayrı
   çözülür. Global; yerel minimuma takılmaz.
2. **`refine_ecc`** — ECC ile alt-piksel homografi (piramitli). Çıktı 3x3;
   `optics.decompose_homography` **değişmeden** kullanılır → dönme/tilt
   SIFT yoluyla birebir karşılaştırılabilir.
3. **`residual_flow`** — homografi sonrası kalan kaymayı **her piksel için**
   optik akışla ölçer. Homografi ideal projektif dönüşümdür; kalan artık
   distorsiyondur. Çıktı tek sayı değil, **alan**.

`analyze_dense()` üçünü tek çağrıda koşar. `pipeline.run_analysis(..., dense=True)`
ile SIFT'in **yanında** koşar — yerine geçmez.

### Ölçek neden veriden çözülüyor

Ground truth her zaman aynı ölçekte gelmez. Ölçek bilinmeden sapmanın ne kadarı
distorsiyon, ne kadarı ölçek farkı ayırt edilemez — bu yüzden ideal `f·tan(θ)`
modeline göre **mutlak** ölçüm yapılamaz. Homografi bu bilinmeyeni verinin
kendisinden çözüp soğurur; kalıntı saf distorsiyon olur. Referans budur.

### Doğrulama — `test_dense_align.py` (hepsi geçiyor)

Bilerek birbirine benzemeyen desenlerle: rastgele doku, eş merkezli çember,
Siemens star, satranç tahtası, nokta ızgarası, tek yönlü çizgiler.

- Desen-agnostiklik: 5 desende dönme ≤0.01°, ölçek 4 hane doğru
- Dönüşüm taraması: ±30° dönme, 0.8–1.25 ölçek — hata < 0.35°
- Distorsiyon geri okuma: bilinen `k1` %0.3–13 hatayla geri okunuyor
- Bilgisiz girdide dürüstlük: düz gri alan **reddediliyor**, sayı uydurulmuyor

### Çözülen dört hata (hepsi kalıcı testle korunuyor)

1. **Kaba kademe hiçbir şey bulamıyordu** — FFT genlik spektrumunun enerjisi
   merkezde toplandığı için faz korelasyonu ızgara simetrisine (45/90°)
   kilitleniyordu. **Çözüm:** yüksek-geçiren spektrum filtresi + log-polar
   örneklemeyi görüntü boyutundan ayırmak.
2. **Ölçek ters çıkıyordu** (1.25 yerine 0.804) — Fourier ölçek karşıtlığı:
   görüntü büyürse spektrumu küçülür. İşaret düzeltildi.
3. **Dönme yönü tersti** — görüntü koordinatlarında y aşağı bakar; standart
   matematiksel dönme matrisi ekranda ters döner. Artık OpenCV konvansiyonu.
4. **Gerçek görüntü çiftinde ölçek hiç görülmüyordu** (2.19 yerine 0.99) —
   `_coarse_one` iki görüntüyü ortak tuvale **sıfırla doldurarak** oturtuyordu;
   GT 894×730, dedektör 1600×1600 olduğu için etraftaki dev sıfır çerçevesi
   spektrumu domine ediyordu. **Çözüm:** doldurma değil **ölçekleme** (`pre_scale`
   sonuçtan geri çıkarılır). Sentetikte iki görüntü hep aynı boyutta olduğu için
   bu hata sentetik testlerde görünmüyordu — **gerçek çiftle test şart.**

### İki tuzak — mutlaka okuyun

**a) Ayna varyantı seçimi ECC'ye ait, kaba kademeye değil.** Gerçek çiftte kaba
korelasyon `raw` 0.386 / `flip_h` 0.338 verip **yanlış** olanı seçiyordu; ECC
aynı çiftte 0.759 / **0.868** ile doğrusunu net ayırıyor. Yanlış varyant tüm
zinciri saptırır. Bedeli varyant başına bir ECC koşusu — ödemeye değer.

**b) Büyük kalıntı ≠ distorsiyon.** Gerçek çiftte kalıntı RMS 9.66 px çıktı ve
radyal modele uydurulunca `-0.84%` "fıçı distorsiyonu" gibi okundu. **Ama ısı
haritası sapmanın harflerin ve ince kamaların üstünde yoğunlaştığını, düz
alanlarda sıfır olduğunu gösterdi** — bu distorsiyon değil, GT (894×730) ile
dedektörün (1600×1600) **keskinlik/örnekleme farkı**.

Ayırt edici ölçüt **radyallik** (`radial_fraction`): distorsiyon merkezden
uzaklığa bağlıdır (gerçek distorsiyonda 1.00), artefakt desenin ayrıntılı
bölgelerine yığılır (gerçek çiftte 0.63, keskinlik testinde 0.06).
`distortion_trustworthy` bu eşiği (0.90) uygular; geçmezse sayı **yazılmaz**.

> Bu, bölüm 5'teki "panel ↔ tablo ayrışması" ile **aynı sınıftan** bir hatadır:
> ölçüm katmanının ölçemediği yerde sayı uydurması. Kural aynı — ölçülemiyorsa
> yedek sayıya düşme, "ölçülemedi" yaz.
>
> Sıra da önemli: **önce büyüklük, sonra şekil.** Kalıntı zaten gürültü
> seviyesindeyse (< 0.5 px) "radyal değil" demek yanlış teşhistir; sıfırın
> şekli olmaz. Doğru cevap "distorsiyon yok".

### Bilinen sınırlar (kod hatası değil, matematiksel)

- **Periyodik desen:** 24 px aralıklı çizgilerde 17 px kayma ile 17+24k px
  kayma ayırt **edilemez**. Test bunu periyot-modülo doğrular.
- **Dairesel simetrik desen:** saf eş merkezli çemberde dönme ölçülemez —
  hiçbir algoritma ölçemez. `generate_circle_pattern.py`'ın dört köşeye farklı
  açılarda F koymasının sebebi tam olarak budur.
- **Dokusuz alan:** düz gri hizalanamaz; doğru davranış reddetmektir.

---

## 7C. DONANIM KATALOĞU + YÖNELİM HATALARI

Son güncelleme: 2026-08-17

### Donanım kataloğu (`core/config.py`)

Lens ve dedektör artık açılır listeden seçilebiliyor. Katalog yalnızca
**kolaylık** — parametrik olma gereksinimini değiştirmez: bir kalem seçmek
alanları doldurur, sonra her alan elle düzenlenebilir.

| sistem | lens | dedektör | FOV | IFOV |
|---|---|---|---|---|
| CMV4000 + Rodenstock | f=70mm, f/5.6 | 2048², 5.5µm | 9.200° | 78.57 µrad/px |
| **Hydra yıldız izleyici** | f=47.7mm, f/1.4, pupil 34mm | 1024², 18µm | **21.870°** | **377.36 µrad/px** |

Hydra'nın hesaplanan FOV'u (21.870°) üreticinin "useful FOV" değerinden
(21.5°) **%1.72 büyük** — beklenen yön, çünkü useful FOV köşe kalitesi
düştüğü için tam sensörden dar tanımlanır. Tutarlılık doğrulandı.

`Lens`'e iki alan eklendi: `pupil_diameter_mm` (0 = f/#'ten türet) ve
`useful_fov_deg` (üretici değeri, karşılaştırma için).

**Kural — seçici ile alanlar ayrışmaz.** `_sync_catalog_selectors()` her
alan değişiminde çalışır; elle düzenlenen sistem otomatik "Özel"e döner.
Bu, §5'teki panel↔tablo ayrışmasının aynı sınıftan olan hâlini önler.

### KRİTİK HATA — en-boy oranı uyuşmazlığı (çözüldü)

Kırpılmış dedektör görüntüsü (**256×1022**, oran 0.25) ile tam ground truth
(**1280×1024**, oran 1.25) hizalanamıyordu: skor **0.016**.

Sebep `_coarse_one`: iki görüntüyü ortak kareye **ayrı ayrı gerdiriyordu**
(anizotropik resize). Dedektör şeridi yatayda 5 kat gerilince çemberler
elipse dönüyor, hiçbir şey eşleşmiyordu.

**Çözüm:** en-boy oranını koruyarak ölçekle + ortak tuvale ortala.
Skor 0.016 → **0.69**. `test_hydra.py [3]` bunu sentetik olarak da korur
(256/384/512 px şeritlerde dönme hatası < 0.01°).

> Bu hata B.4 ile aynı aileden: **ortak boyuta getirme adımı** iki kez
> hata kaynağı oldu. Doldurma da gerdirme de sinsi; oranı koru, ortala.

### Yönelim hataları (`core/pointing.py`)

Decenter / roll / tilt + FOV kapsaması. Hepsi `dense_align` homografisinden
türetilir — **ek ölçüm yapılmaz**.

| büyüklük | tanım |
|---|---|
| **decenter** | Desen merkezinin sensör merkezinden kaçıklığı (bore-sight hatası), px ve derece |
| **roll** | Düzlem-içi dönme |
| **tilt** | Düzlem-dışı yatış (x/y keystone ayrı) |
| **kapsama** | Desenin kaç %'i sensörde, sensörün kaç %'i dolu, kenar açıları, pay |

**Açıya çevirme TAN TABANLIDIR:** `theta = atan(r_px · pitch / f)`.
Küçük açı yaklaşımı (`r_px · IFOV`) kenarda %2'den fazla sapar — 21.5° FOV'lu
Hydra'da bu önemli, o yüzden kullanılmıyor.

**Decenter neden dedektör pikselinde ölçülür:** GT'nin ölçeği bilinmiyor
(bkz. §7B). Homografi bunu soğurur; kaçıklık dedektör düzleminde ölçülüp
dedektörün kendi pitch'iyle açıya çevrilir. Böylece GT'nin çözünürlüğü
sonucu etkilemez.

### Gerçek Hydra ölçümü (kırpılmış dikey şerit)

```
decenter : 0.3035°  (14.04 px, azimut +137.4°)
roll     : +1.6799°
tilt     : 0.2623°
sensörde en büyük açı : 11.231°
kenar açıları : sol/sağ 2.75°   üst/alt 10.90°
desenin görünen kısmı : %12.9   (desen taşıyor, -378.5 px)
```

Kenar açılarının asimetrisi (2.75° yatay, 10.90° dikey) doğrudan kırpmanın
sonucu: dedektör 256 px geniş ama 1022 px yüksek.

### Doğrulama

- `test_pointing.py` — 6 grup, hepsi geçiyor. Decenter piksel hatası
  < 0.006, roll < 0.01°, kapsama oranı birebir (%23.8 → %23.8).
  **[6]** aynı piksel kaçıklığının farklı donanımda farklı açı vermesini
  sınar — dönüşümün gerçekten parametrik olduğunun kanıtı.
- `test_hydra.py` — 4 grup, hepsi geçiyor. Katalog değerleri, seçici
  eşleştirmesi, en-boy oranı regresyonu, gerçek çift.

### Bilinen sınır — dar şeritte ayna belirsizliği

Hydra dedektör görüntüsü çok dar olduğu için dört ayna varyantının ECC
skorları **birebir eşit** (fark 0.0000) ve dönmeler −0.375° ile −2.650°
arasında değişiyor. Ölçüm hâlâ çalışıyor ama ayna ekseni belirsiz.
Daha geniş bir dedektör kadrajı bunu çözer (bkz. `docs/OLCUM_ALGORITMASI.md`
Ek A).

---

## 7D. İKİ CİDDİ DÖNME HATASI (2026-08-18 — çözüldü)

Kullanıcı Hydra çifti için "roll 135-140 arası çıkmalı" dedi; yazılım
**-1.68°** raporluyordu. Tahmin doğruydu, yazılım yanlıştı. İki bağımsız
kök neden vardı ve **ikisi de sessizce yanlış sayı üretiyordu**.

### Hata 1 — dairesel simetrik desende dönme hiç çözülemiyor

Log-polar faz korelasyonu ölçek ve dönmeyi genlik spektrumundan okur.
**Eş merkezli çember deseni dairesel simetriktir**, dolayısıyla genlik
spektrumu da simetriktir: log-polarda dönme ekseninde TEPE OLUŞMAZ.
Faz korelasyonu güven 0.126 ile `0.09°` okuyordu ve bu kabul ediliyordu.

Kaba taramayla ölçülen gerçek durum (GT elle döndürülüp ECC bakıldı):

| GT ön-dönme | ECC |
|---|---|
| 0° | 0.7529 |
| 45° | 0.8187 |
| **135°** | **0.8381** |
| 315° | 0.8381 |

**Çözüm:** faz korelasyonu güveni `ROT_CONF_MIN` (0.25) altındaysa tüm açı
aralığı `ROT_SCAN_STEP` (10°) adımlarla taranır; en iyi `ROT_TOPK` (6) aday
ECC'ye taşınır ve **nihai kararı ECC verir**. ECC 0.753 → **0.914**.

> **Kaba skor ile ECC farklı tepeleri işaret edebilir.** Ölçüldü: kaba skor
> 135°'yi (0.625) ve 315°'yi (0.640) yakın gösterirken ECC 315°'de 0.914,
> 135°'de 0.802 veriyor. Bu, ayna varyantı seçiminde öğrenilen dersin
> (§7B "İki tuzak", a maddesi) aynısı — **karar en ayırt edici ölçüte ait.**

### Hata 2 — ±90'a katlama gerçek yönelimi yok ediyor

`optics.decompose_homography` açıyı ±90'a katlıyordu:

    136° → 44°        224° → -44°

Bu, elips ana ekseni gibi "eksen yönü" büyüklüklerinde doğrudur ama bir
GÖRÜNTÜNÜN dönmesi 0..360 arasında anlamlıdır; 136 ile 44 **farklı
yönelimlerdir** ve katlama bilgiyi geri getirilemez biçimde siler.

**Çözüm:** `TiltResult.in_plane_rotation_full_deg` eklendi (0..360,
katlanmamış). Eski `in_plane_rotation_deg` **aynen bırakıldı** — doğrulanmış
referans (+1.583°) o konvansiyonda üretildi ve `test_pipeline` onu koruyor.
Yönelim raporları ve GUI artık `full` alanını gösterir.

### Yan düzeltme — ayna varyantının kattığı 180°

Homografi, varyantı UYGULANMIŞ dedektöre göre çözülür. Ama:

    flip_v = flip_h + 180°        flip_both = raw + 180°

Bu 180° ölçülen açıya karışır. `analyze_dense` artık `flip_v`/`flip_both`
seçildiğinde rapor açısını 180° geri alır. **Homografinin kendisine
dokunulmaz** — o, varyantlı görüntü için doğrudur ve warp/kalıntı hesapları
ona dayanır.

Ayrıca işaret: `optics` matematiksel yönde (saat tersi +) üretir, panel ve
kullanıcı saat yönünü görür; `pointing` çevirisi bu yüzden `360 - full`.

### Doğrulama kanıtı (görsel)

Merkez işareti GT'de **+**, dedektörde **×** — tam 45° fark. Artının 4 kollu
simetrisi yüzünden gerçek dönme 45+90k ailesindendir; ayrımı **F harfleri**
yapar (paternin dört köşesindeki 0/90/45/270° döndürülmüş F'ler tam bunun
için var).

### Düzeltilmiş Hydra sonucu

```
decenter : 0.3028°  (14.01 px, azimut +136.5°)
roll     : 136.031°      (±90 katlı gösterim: +43.97°)
tilt     : 0.617°  (x +0.610°, y -0.094°)
ECC      : 0.9138   (önceki hatalı hizalamada 0.7530)
```

### Regresyon koruması

`test_pointing.py [7]` — büyük açı dönmeler, **iki desen ailesinde**:

| desen | test edilen açılar | sonuç |
|---|---|---|
| rastgele doku | 30, 136, 200, 315 | hata < 0.01° |
| dairesel simetrik çember | 136, 250 | hata < 0.01° |

İkinci satır kritik: simetri kırıcı işaret olmadan bu ölçüm **imkânsızdır**,
onunla birlikte tarama gerçek tepeyi buluyor.

---

## 7E. REFERANS EKRAN: STOS (açısal kaynak) — 2026-08-19

Hydra düzeneğinde referans görüntü **OLED'e değil, STOS denen bir
görüntüleme ekranına** basılıyor. Fark yalnızca isim değil — STOS bir
**açısal kaynak**tır ve bu ölçüme yeni bilgi katar.

| | OLED (GL049) | **STOS** |
|---|---|---|
| çözünürlük | 1920×1080 | **1280×1024** |
| piksel pitch | 5.616 µm | **13.62 µm** |
| açısal çözünürlük | — (pasif panel) | **0.027 °/px** |
| ima edilen f | — | **28.90 mm** |
| panel kapsaması | — | **±16.78° × ±13.56°** |

### Neden önemli: açısal kaynak vs pasif panel

Pasif bir panel (OLED) kendi başına açısal ölçek TANIMLAMAZ; desen fiziksel
bir yüzeye basılır. STOS ise üreticiden **derece/piksel** verisiyle gelir, bu
da önünde bir optik olduğu ve bir odak uzaklığı ima ettiği anlamına gelir:

    f_implied = pitch / tan(angular_res) = 13.62µm / tan(0.027°) = 28.90 mm

`generate_circle_pattern.py` çemberleri zaten **tam bu ölçeğe göre**
yerleştiriyordu (`r = f·tan(θ)/pitch`); yazılımın konfigürasyonu bunu
bilmiyordu, artık biliyor.

### Kodda karşılığı

`OLED` sınıfı `RefScreen` olarak genelleştirildi (`OLED = RefScreen` takma
adı geriye dönük uyum için duruyor, eski preset JSON'ları çalışır).
Ayrımı `angular_res_deg` yapar: `0` ise pasif panel, `>0` ise açısal kaynak.
Yeni yardımcılar: `is_angular_source`, `implied_focal_mm`,
`half_angle_deg(r)`, `radius_px_for_angle(θ)`.

Sol panelde "Referans ekran" grubu: hazır ekran seçici (OLED / STOS / Özel),
açısal çözünürlük alanı ve canlı bilgi satırı (ima edilen f, panel kapsaması,
cihaz FOV'unun ekrandaki yarıçapı). `SYSTEM_PRESETS` artık **üçlü**:
(lens, dedektör, ekran).

### Kazanç 1 — desen yarıçapı elle girilmiyor

Cihaz FOV'unun ekrandaki yarıçapı hesaplanabildiği için "desen payı"
satırı artık kullanıcı girdisi gerektirmiyor: Hydra için **410 px**.

### Kazanç 2 — BAĞIMSIZ ÖLÇEK DOĞRULAMASI

İki tamamen ayrı yoldan aynı sayı üretilebiliyor:

```
beklenen = (f_lens/pitch_det) / (f_stos/pitch_stos)    ← sadece DONANIM
ölçülen  = hizalama homografisinden                     ← sadece GÖRÜNTÜ
```

Hydra çiftinde ölçülen sonuç:

| | değer |
|---|---|
| beklenen (donanımdan) | **1.2488** |
| ölçülen (görüntüden) | **1.2458** |
| fark | **%0.24** |

Bu, projedeki **ilk uçtan uca çapraz doğrulama**: donanım parametreleri ile
görüntü ölçümü birbirinden bağımsız olarak %0.24 uyuşuyor. Hem STOS
parametrelerinin hem hizalamanın doğru olduğunu aynı anda kanıtlıyor.

> Ölçek beklenenden belirgin saparsa (>%3) ya donanım parametresi yanlıştır
> ya da hizalama kaymıştır — bu satır artık o ayrımı yapan bir sağlık
> göstergesi.

### Doğrulama

`test_hydra.py` [1B] ve [1C] — STOS parametreleri, ima edilen f, panel
kapsamasının cihaz FOV'unu taşıması, ölçek öngörüsü ve gerçek çiftle uyumu.

---

## 7F. EŞ MERKEZLİ ÇEMBERDE TİLT (2026-08-19)

Hydra çiftinde "Eğiklik" satırı **"ölçülemedi"** çıkıyordu, sonra düzeltilince
**"< 18°"** gibi çok gevşek bir sınır veriyordu. İki ayrı sebep vardı.

### Sebep 1 — tilt yöntemi bu deseni tanımıyordu

`estimate_from_circle` → `siemens_star.detect_center_ellipse` çağırır. O
yöntem yıldızın sınırını **teğetsel geçiş yoğunluğundan** bulur (her halkada
~110 siyah-beyaz geçiş sayar). Eş merkezli çemberde böyle bir yoğunluk yoktur.

Ölçülen: WTW yıldızında güven **0.95**, çember paterninde **0.00** (bulamıyor).

**Çözüm:** `estimate_from_concentric_rings` — halkaları tek tek kontur olarak
çıkarıp elips fit eder. Yalnızca dedektör görüntüsü gerekir (GT tanım gereği
mükemmel dairelerden oluşur).

### Sebep 2 — F harfleri sahte halka sayılıyordu

İlk sürümde eleme ölçütü "kontur görüntü kenarına değiyor mu" idi. F harfleri
kenara değmiyor ama halka da değil; `fitEllipse` onlara da elips uyduruyordu:

| kontur | oran | merkez y | fit artığı |
|---|---|---|---|
| gerçek halka | 0.999 | 501 | **0.003–0.006** |
| F harfi | 0.52 | 930 / 91 | **0.55** |

Sahte halkalar merkez saçılmasını **242 px**'e, oran saçılmasını da %5'e
çıkarıyordu → sınır "< 18°".

**Çözüm:** fit artığı denetimi. Noktalar fit edilen elipse gerçekten oturuyor
mu (`u²+v²=1` sapmasının RMS'i). Ayrım iki büyüklük mertebesi.

> **Eşik neden 0.25 (dar değil):** kontur çizginin İKİ kenarını birden
> içerir, ideal elipsten ±kalınlık/2 sapar ve bu bağıl sapma BASIK
> elipslerde büyür — 35° tiltte 0.15'e çıkıyor. Eşik 0.06 iken gerçek
> tiltli halkalar da eleniyordu (35° ölçümü `nan` veriyordu).

**Sonuç:** 4 gerçek halka, merkez saçılması **0.01 px**, sınır **< 2.9°**.

### GEOMETRİ — decenter ile tilt karıştırılmamalı

Kullanıcının işaret ettiği nokta: *"çemberler eş merkezli olmuyor, görüntü
kaymış"*. Doğru gözlem ama kayma tilt DEĞİLDİR:

| etki | görsel imza |
|---|---|
| **decenter** | halkalar BİRLİKTE kayar; birbirlerine göre hâlâ eş merkezli |
| **tilt** | halkalar ELİPSE döner VE merkezleri birbirinden AYRIŞIR |

Hydra çiftinde ölçülen: merkez saçılması 0.01 px, medyan oran 0.9992 →
**kayma var (decenter 14 px), eğilme yok.**

### Doğrulama — `test_hydra.py` [1D]

| durum | sonuç |
|---|---|
| mükemmel daire | tilt 0.58° (sahte tilt üretmiyor) |
| saf decenter (45,−30) | tilt 0.59° (**kaymayı tilt sanmıyor**) |
| gerçek tilt 20° | 19.94° — hata 0.06° |
| gerçek tilt 35° | 35.05° — hata 0.05° |
| gerçek çift | 4 halka, merkez saçılması 0.01 px |

### İKİNCİ TUR DÜZELTME — "< 18°" hâlâ çıkıyordu (fullFrame çekimi)

İlk düzeltmeden sonra **dar şeritte** sınır < 2.9°'ye indi ama kullanıcı
**fullFrame** çekimini (1022×1022) kullanıyordu ve orada hâlâ **"< 18.0"**
görünüyordu. İki ek sorun vardı.

**a) Fit artığı denetimi yetmiyor.** F harfleri ve kesikli FOV halkasının
parçaları da düzgün elipse oturabiliyor:

| kontur | oran | merkez | durum |
|---|---|---|---|
| gerçek halka | 0.9998 | (520.6, 501.0) | ✓ |
| sahte | 0.87–0.95 | (461,563) (573,444)… | 60–80 px uzakta |

Sahte kayıtlar merkez saçılmasını **45 px**, oran saçılmasını **0.049**'a
çıkarıyordu → sınır 18°.

**Çözüm — ortak merkez ayıklaması.** Gerçek halkalar ORTAK bir merkezi
paylaşır. Medyan merkezden `CENTER_TOL_PX` (25 px) uzaktakiler atılır.
Sonuç: 12 sahte halka elendi, merkez saçılması **45 px → 0.08 px**.

> Bu, "halkalar eş merkezli" varsayımı DEĞİL. Gerçek tilt merkezleri
> birkaç piksel kaydırır; F harfleri onlarca piksel uzaktadır. Eşik
> ikisini ayıracak kadar geniş.

**b) Sigma yanlış ölçekteydi.** `RATIO_SIGMA = 0.002` Siemens star elips
yönteminin gürültüsüdür; halka-fit için fazla karamsar — 14 temiz halkada
ölçülen saçılma 0.0005 iken taban sınırı 3.6°'de tutuyordu. Ayrıca
`_ratio_sigma_to_deg` oran≈1 civarında TAVAN uyguladığı için
"tilt 2.30° ± 0.71°" gibi **tutarsız** (sigma < tilt) raporlar doğuyordu.

**Çözüm:** halka-fit için ayrı taban (`RING_RATIO_SIGMA = 0.0005`) ve
sigma'yı tilt ile AYNI dönüşümden geçirmek:

    sigma = |acos(oran − sigma_oran) − acos(oran)| / 2

`acos` oran≈1'de çok diktir (0.9990 → 2.56°, 0.9980 → 3.62°); belirsizliği
oran uzayında bırakıp dereceye ayrı formülle çevirmek tutarsızlık üretiyordu.

### Sonuç — her iki çekimde de gerçek ölçüm

| çekim | önce | sonra |
|---|---|---|
| vertical (256×1022) | ölçülemedi → < 2.9° | **2.30° ± 0.91°** |
| fullFrame (1022×1022) | **< 18.0°** | **1.37° ± 1.14°** |

İki bağımsız çekim tutarlı (1.37° ve 2.30°) ve ikisi de artık *çözülebilir*
— yani sınır değil, sayı.

### "< X" gösterimi nasıl okunur

Sınır `sigma ≈ acos(1 − oran_saçılması)`:

| saçılma | sınır |
|---|---|
| 0.002 | < 2.6° |
| 0.01 | < 8.1° |
| 0.05 | < 18.2° |

**"< 18" tilt 18° demek DEĞİLDİR** — ölçüm gürültüsü 18°'ye kadar bir tilti
gizleyebilir demektir. Sınır büyükse önce halka tespitine bakın: sahte halka
geçiyorsa saçılma şişer.

### Denenip GERİ ALINAN: "Dönme" satırını yoğun yoldan besleme

SIFT bu desende çöktüğü için "Dönme" satırı "ölçülemedi" kalıyor; yoğun yolun
136.03° ölçümünü oraya yazmak denendi. **`test_roi_analiz` bunu reddetti** ve
haklıydı: dejenere bir ROI'de yoğun yol da 181.5°/358.3° gibi anlamsız
değerler üretiyor.

Ayrımı yoğun yolun kendi metrikleriyle yapmak **mümkün değil** — ölçüldü:

| durum | ECC | kalıntı RMS |
|---|---|---|
| dejenere ROI (yanlış ölçüm) | 0.9312 | 3.77 px |
| Hydra (doğru ölçüm) | 0.9138 | 4.50 px |

Dejenere olan daha "iyi" görünüyor. Hizalama gerçekten oturuyor; sorun 180°
yön belirsizliği ve bunun sinyali yok.

**Karar:** "Dönme" satırı SIFT'e ait kalır ve SIFT ölçemediğinde
"ölçülemedi" yazar. Yoğun yolun ölçümü zaten **Roll** satırında görünür.
İki satırın farklı yöntemlere ait olması özelliktir, hata değil.

---

## 8. OLASI SONRAKİ ADIMLAR (fikir — kullanıcı istemedi)

- Sonuçları PDF/CSV rapor olarak dışa aktarma.
- Birden çok görüntü çiftini toplu işleme (batch).
- Distorsiyon (barrel/pincushion) ölçümü — chart'ın düz çizgilerinden.
- MTF / keskinlik ölçümü — Siemens star zaten bunun için ideal desen.

---

## 9. KULLANICI TERCİHLERİ (çalışma şekli)

- Her tool çağrısı manuel onaylanıyor; kullanıcı çoğu adımı görmek istiyor.
- Alt-agent (paralel işçi) çağrılarını reddetme eğiliminde — işi ana akışta
  doğrudan yapmak tercih ediliyor.
- Türkçe iletişim.
