# OPTİK ANALİZ PROJESİ — DEVAM YÖNERGESİ

> **Devam etmek için:** Claude Code'u `/home/test123/Desktop/optik_analiz` dizininde açıp
> şunu yaz: **"Optik analiz projesine devam edelim, DEVAM_YONERGESI.md'yi oku"**

Son güncelleme: 2026-08-12

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
└── test_tilt_synth.py     ✅ Sentetik bilinen-tilt doğrulaması
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

## 6. ÇALIŞTIRMA

```bash
cd /home/test123/Desktop/optik_analiz
python3 run_gui.py            # arayüz
python3 test_pipeline.py      # uçtan uca akış (görüntülerle)
python3 test_tilt_synth.py    # sentetik doğrulama (görüntü gerekmez)
python3 core/siemens_star.py  # sadece elips tespiti + debug PNG
```

Ortam: Python 3.12 — OpenCV 4.13, NumPy 1.26, SciPy 1.11, PyQt5 5.15, matplotlib 3.10

---

## 7. ARAYÜZ KULLANIMI

- **Sol panel:** Görüntü seçimi + tüm sistem parametreleri (lens/dedektör/OLED).
  Her alan düzenlenebilir; sensör boyutu canlı güncellenir.
  Preset **Kaydet/Yükle/Varsayılan** butonları `presets/` altına JSON yazar.
- **Orta panel:** Üç sekme — Ground truth, Dedektör, Hizalama (overlay).
  İlk ikisinde yeşil elips = tespit edilen yıldız sınırı.
  Overlay'de kırmızı = dedektör, yeşil = hizalanmış GT; **sarı = iyi örtüşme**.
- **Sağ panel:** FOV, IFOV, tilt, yıldız elipsi ve eşleme kalitesi.
  Değerler renk kodlu (yeşil iyi / sarı dikkat / kırmızı sorunlu).
- Analiz arka plan thread'inde koşar; arayüz donmaz.

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
