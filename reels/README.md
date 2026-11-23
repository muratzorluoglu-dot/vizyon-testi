# reels — Türkçe dikey kısa video üretim akışı

Tek kaynak dosya (`episode.json`) → seslendirme → zamanlama → altyazı → ses miksi → 1080×1920 / 30 fps MP4 → dosya kontrolü → `script.md` (video kartı, metin, sahne planı, SRT, yayın paketi, üretim durumu).

## Kurulum

```bash
pip install -r reels/requirements.txt     # numpy, scipy, skia-python
# Linux'ta skia için: apt-get install -y libegl1 libgl1
# ffmpeg / ffprobe gerekir
```

## Kullanım

```bash
python3 reels/make.py new "Türkiye'nin En Yüksek 10 Dağı"          # bölüm iskeleti
python3 reels/make.py build reels/episodes/<slug> --preview         # 540×960 hızlı deneme
python3 reels/make.py build reels/episodes/<slug>                   # tam render
python3 reels/make.py still reels/episodes/<slug> 12.5              # tek kare PNG
```

## Gerçek görüntüler (Wikimedia Commons)

Segmentte `media` alanı varsa sahne tam ekran gerçek fotoğrafla (yavaş zoom/kaydırma) çizilir:

```json
"media": {"commons": "File:SCUBA Silfra.jpg", "focus": [0.5, 0.5], "zoom": [1.0, 1.12],
          "fit": "contain", "representative": "Moalboal, Filipinler"}
```

- `python3 reels/make.py media reels/episodes/<slug>` görselleri `media/<id>.jpg` olarak indirir, yazar/lisansı `media/credits.json`'a yazar (`build` bunu otomatik de yapar).
- Kabul edilen lisanslar: CC0, kamu malı, CC BY, CC BY-SA (BY-SA kullanılırsa video da CC BY-SA ile yayınlanmalıdır).
- `fit: "contain"` düşük çözünürlüklü görselleri bulanık arka planla çerçeveler.
- `representative` dolu ise ekranda "TEMSİLÎ GÖRÜNTÜ" etiketi çıkar (tür doğru, konum birebir o nokta değil).
- Her fotoğrafın atfı ekranda ve `script.md` içinde yer alır.
- Wikimedia bulut IP'lerini sınırlayabilir (HTTP 429); bu durumda `media` komutunu kendi bilgisayarında çalıştırıp `media/` klasörünü commit et.

## Seslendirme (Google Cloud Text-to-Speech)

Anahtar yalnızca ortam değişkeninden okunur, hiçbir dosyaya yazılmaz:

- `GOOGLE_TTS_API_KEY`: REST çağrısı (`X-Goog-Api-Key` başlığı)
- veya `GOOGLE_APPLICATION_CREDENTIALS`: servis hesabı (`pip install google-auth`)

Ses varsa her segmentin gerçek süresi ölçülür; sahneler ve altyazı (`altyazi.srt`) buna göre yeniden zamanlanır, müzik konuşmanın altında kısılır. Ses yoksa zamanlama hece sayısından tahmin edilir ve altyazı `altyazi.taslak.srt` adıyla yazılır.
Okunuşu zor adlar için segmentte `tts` alanı kullanılır (ekranda `text`, seslendirmede `tts` gider).

## episode.json

- `brief`: kapsam, öğe türü, adet, ölçüt ve ölçütün dayanağı (video kartında görünür)
- `segments`: konuşma cümleleri; her biri bir sahnedir. `scene` alanı çizeri seçer:
  `silfra, angelita, reef, octopus, wreck, sharks, sardines, tornado, manta, steps, title, grid, end`;
  bilinmeyen değerler tipografik `typo` sahnesine düşer.
- Liste öğelerinde `place`, `country`, `lonlat` alanları numara/başlık/mini harita katmanını açar.
- `publish`, `sources`: yayın paketi ve editör kaynakları.

Görseller kodla çizilmiş temsili motion graphics'tir. Harita verisi: Natural Earth 1:110m (kamu malı) / world-atlas (ISC). Müzik ve efektler kodla sentezlenir.
