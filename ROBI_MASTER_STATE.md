# ROBI_MASTER_STATE.md

## 🎯 ROBİ – Amaç
ROBI, yaşlı bir kadına akıllı, sempatik, esprili bir arkadaş olacak.
Bir cihaz gibi değil, evin içinden biri gibi davranır.

---

## 🧠 Temel Davranışlar

### 🔊 Uyanma & Sohbet
- “ROBİ” dendiğinde uyanır
- Uyanınca uzun süre awake kalır (hemen IDLE’a düşmez)
- Wake sonrası:
  - sempatik
  - esprili
  - doğal sohbet kurar
- ChatGPT değil ama ChatGPT hissi verir

---

### 📰 Bilgi & Medya
- Günün önemli haberlerini özetler
- Hava durumu ve borsa bilgisi verir
- Müzik çalar (türe göre)
- Radyo / TV kanallarına erişebilir
- Fıkra anlatabilir

---

### 👀 Görme & Tepki
- IDLE modda:
  - ses duyarsa etrafı kontrol eder
  - yüz yakalarsa konuşmaya başlar
  - yüz yoksa: “Biri var sandım…”
- Sabah:
  - birini görürse wake beklemeden “Günaydın” der

---

### 🧍‍♀️ İnsan Tanıma
- Tanımlı insanlara adıyla hitap eder
- Tanımadığı biriyle tanışır
- 2. fazda yeni tanıştıklarını da hatırlar

---

### 🧠 Hafıza
- Kişiye özel bilgiler tutar
- Sevdiği / sevmediği şeyleri bilir
- Zamanla daha kişisel bir arkadaş olur

---

## 🧩 Mimari Gerçekler

### Python Ortamları
- **TEK VENV**: `venv/` (Python 3.13) — tüm servisler bu venv ile çalışır
- `venv311/` ESKİ, kullanılmıyor, silinebilir
- Başlatma: `venv/bin/python3 main.py` (start_robi.sh ESKİ)

### Servis Başlatma
```bash
cd ~/ai-robot && venv/bin/python3 main.py
# Vision olmadan test için:
venv/bin/python3 main.py --no-vision
```

---

## 🎙️ Mikrofon — INMP441 I2S Dijital Mikrofon

### Donanım
- **Eski:** Google Voice HAT (snd_rpi_googlevoicehat_soundcard) — KALDIRILDI
- **Yeni:** INMP441 I2S Dijital Mikrofon
- **GPIO:** DATA=GPIO20, WS=GPIO19, SCK=GPIO18
- **ALSA Kart Adı:** `sndrpii2scard`

### /boot/firmware/config.txt [all] bloğu
```
[all]
dtoverlay=disable-bt
dtoverlay=i2s-mmap
dtparam=i2s=on
dtoverlay=inmp441
```
- `disable-bt`: Bluetooth kapalı → I2S/WiFi paraziti önlenir (Pi 4 bilinen sorun)
- `/boot/firmware/overlays/inmp441.dtbo` manuel derlendi (kernel 6.12 için)

### config.py — Ses Ayarları
```python
AUDIO_CAPTURE_DEVICE = "plughw:CARD=sndrpii2scard,DEV=0"
CHANNELS             = 1          # işlenmiş çıkış (mono S16_LE)
AUDIO_INPUT_CHANNELS = 2          # ham giriş (stereo I2S)
AUDIO_INPUT_FORMAT   = "S32_LE"   # ham giriş formatı
SAMPLE_RATE          = 16000
VAD_MODE             = 2          # dengeli — hışırtı WHISPER_MIN_RMS ile engellenir
WHISPER_MIN_RMS      = 500        # hışırtı ~RMS 421 → 500 eşiği geçemez
```

### robi_audio.py — Ses İşleme Pipeline
- `arecord` → S32_LE stereo (2 kanal)
- `_convert_raw()` → sol kanal alınır, int32→int16 ölçeklenir, **25dB yazılım kazancı** uygulanır
- Çıkış: S16_LE mono → VAD + Whisper’a gider
- **x4 gain ve pre-emphasis kaldırıldı** (Google Voice HAT’a özgüydü)

---

## 🐛 Düzeltilen Hatalar (Son Oturum)

### 1. Whisper Halüsinasyon — Kök Neden
- INMP441 + 25dB gain → sessiz ortamda hışırtı RMS≈421
- Whisper belirsiz sesden deterministik YouTube kalıpları üretiyor
- **Fix:** `VAD_MODE=2` + `WHISPER_MIN_RMS=500` kombinasyonu
- `WHISPER_HALLUCINATION_FRAGMENTS_LONG` seti eklendi (kelime sayısından bağımsız filtre)

### 2. is_music_playing() — 35s Zaman Penceresi
- `robi_skills.py`: 35s zaman penceresi yavaş ağda yetersizdi
- **Fix:** `_music_starting` flag ile değiştirildi
  - `start_radio()` / `start_youtube_music()` → `_music_starting = True`
  - `_mpv_play()` finally bloğu → `_music_starting = False`

### 3. Duplike Timeout — Çift "İhtiyacın olursa"
- `_on_timeout()` ve `_sleep_watchdog()` aynı anda AUTO_LISTEN sleep tetikleyebiliyordu
- **Fix:** Sleep geçişi sadece `_sleep_watchdog`’da bırakıldı

### 4. pause/resume_music() Lock Eksikliği
- `_music_proc`’a lock olmadan erişiliyordu
- **Fix:** `_music_lock` altında `proc` referansı alınıp kullanılıyor

### 5. WiFi / I2S Paraziti
- `dtparam=i2s=on` aktif edince Pi 4’te WiFi paket kaybı başladı
- **Fix:** `dtoverlay=disable-bt` eklendi (BCM43455 BT+WiFi çakışması)
- DNS `/etc/resolv.conf`’a `options timeout:1 attempts:5` eklendi (immutable flag kaldırılıp eklendi)

---

## 📦 Kurulu Kütüphaneler (Son Eklenenler)
- `luma.led_matrix` — MAX7219 8x8 LED matrix kontrolü
- `luma.core`, `cbor2` — luma bağımlılıkları

---

## 🖥️ LED Matrix — MAX7219 8x8
- SPI bağlantısı (luma.led_matrix kütüphanesi)
- `robi_display.py` — idle/listening/thinking/speaking/clear animasyonları
- `robi_brain.py` — `_set_state()` içinde display çağrıları
- Import: try/except ile — donanım yoksa sessizce devre dışı

---

## 🌐 Ağ Notları
- Pi 4 bilinen sorun: I2S aktifken WiFi paraziti
- `disable-bt` overlay bunu büyük ölçüde giderir
- Ping (ICMP) router tarafından kısıtlanıyor olabilir — asıl test `curl https://api.openai.com`
- DNS retry: `/etc/resolv.conf` → `options timeout:1 attempts:5` (immutable dosya: `sudo chattr -i` gerekir)

---

## ⚠️ Temizlenecek Eski Dosyalar
Kullanılmıyor, silinebilir:
- `start_robi.sh` (main.py ile değiştirildi)
- `venv311/` (tek venv’e geçildi)
- `radio.py` (robi_skills.py’a entegre edildi)
- `memory.py` (robi_memory.py kullanılıyor)
- `robi_perception.py` (robi_vision.py kullanılıyor)
- `robi_core.py` (referans kod, kullanılmıyor)
- `robi_constants.py` (config.py kullanılıyor)
- `robi_brain_full.py` (deneme kodu, kullanılmıyor)
- `robi_speech copy.py` (yedek kopya)
- `robi_hw.py` (robi_display.py kullanılıyor)

---

## 🛣️ Yol Haritası

### FAZ 1 – DAVRANIŞ
- AWAKE state
- Wake sonrası uzun dinleme
- Hızlı IDLE yok

### FAZ 2 – BEDEN
- LED
- Servo
- Kamera
- Yüz tanıma

### FAZ 3 – ZEKA
- GPT entegrasyonu
- Kişisel hafıza
- Karakter derinliği

---

## 📜 Altın Kurallar
- Çalışanı bozma
- Tek seferde tek sistem
- Hardware olmadan ruh olmaz
- ROBİ oyuncak değil, ev arkadaşıdır

---

## 🔑 Yeni Sohbet Kuralı
Yeni sohbet şu cümleyle başlar:

“ROBI_MASTER_STATE.md’e göre devam ediyoruz.
Şu an FAZ 1 – AWAKE state’teyiz.”

---

## 🧠 Core Architecture Principles (Non-Negotiable)

- GPT / LLM logic ONLY lives in `RobiBrain`
- `RobiCore` never generates text or calls LLM
- `RobiAudio` never thinks, decides, or responds
- Audio → text
- Core → decision
- Brain → meaning + language + personality
- User input is NEVER echoed back
- All conversational memory belongs to Brain

---

## 🧩 Reasoning

This project evolved from v1 → v11 as a monolithic loop.
When split into Brain / Core / Audio, the most critical rule is:

"Conversation intelligence must remain centralized."

If intelligence leaks into Core or Audio, ROBI becomes unstable,
repeats the user, or enters dead states.

---

## 🧠 ROBI Consciousness Rules (Hard Rules)

- ROBI has exactly ONE brain: RobiBrain
- GPT / LLM calls live ONLY in RobiBrain
- RobiCore NEVER generates text
- RobiCore ONLY manages state transitions
- RobiAudio NEVER decides or responds
- User input is NEVER echoed back
- Conversation memory lives ONLY in RobiBrain (messages[])
- Wake → Listen → Think → Speak → Idle is the only valid loop

Violating these rules causes:
- echoing the user
- dead states
- wake-only behavior
- half-alive ROBI
