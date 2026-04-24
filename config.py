"""
config.py — ROBI global configuration
Tüm sabitler burada. Başka dosyada magic string yok.
"""

from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent

# ─── Bus ──────────────────────────────────────────────────────────────────────
BUS_SOCKET = "/tmp/robi_bus.sock"

# ─── Audio ────────────────────────────────────────────────────────────────────
AUDIO_CAPTURE_DEVICE  = "plughw:CARD=sndrpii2scard,DEV=0"     # INMP441 I2S dijital mikrofon
AUDIO_PLAYBACK_DEVICE = "plughw:CARD=Headphones,DEV=0"        # bcm2835 3.5mm jack (hoparlör)
AUDIO_DEVICE          = AUDIO_CAPTURE_DEVICE                   # geriye dönük uyumluluk
SAMPLE_RATE           = 16000
CHANNELS              = 1          # işlenmiş çıkış: mono S16_LE (VAD/Whisper için)
AUDIO_INPUT_CHANNELS  = 2          # INMP441 ham giriş: stereo I2S
AUDIO_INPUT_FORMAT    = "S32_LE"   # INMP441 ham giriş formatı
FRAME_MS        = 20        # webrtcvad: 10 / 20 / 30 ms
VAD_MODE        = 2         # 0–3; 2 = dengeli — hışırtı WHISPER_MIN_RMS ile engellenir
WAKE_VAD_MODE   = 1         # Wake word için daha toleranslı VAD (1 = geniş, 2 = katı)
                            # Uyku sonrası kullanıcı sesi VAD_MODE=2'de filtrelebiliyordu
WAKE_RMS_THRESHOLD = 800    # RMS fallback: VAD false dese bile bu değerin üstündeyse speech say
                            # Kullanıcı sesi genellikle RMS 3000+, ortam gürültüsü <500

# Wake word
WAKE_GRAMMAR    = ["robi", "roby", "robby", "rubi", "ruby", "roby", "robot", "hadi", "hey"]
WAKE_ACCEPT     = ["robi", "roby", "robby", "rubi", "ruby"]
WAKE_COOLDOWN   = 1.5       # saniye — wake sonrası yeni wake bekleme süresi
WAKE_MAX_SEC    = 2.2       # saniye — wake utterance max uzunluğu

# Dinleme
LISTEN_MAX_SEC  = 4.0       # saniye — cümle en fazla 4 saniye (7s çok uzun, kullanıcıyı bekletiyor)
LISTEN_END_SIL  = 500       # ms — sessizlik bitişi
LISTEN_MIN_SPK  = 400       # ms — minimum konuşma süresi (ambient hum filtresi için arttırıldı)

# Whisper halüsinasyon koruması
WHISPER_MIN_RMS = 900       # 1200→900: RMS 900-1200 arası sesleri artık Whisper'a gönder
                            # Kullanıcı sesi genellikle RMS 3000+ — güvenli marj var
                            # (Sessiz konuşma için low_threshold modu: WHISPER_MIN_RMS_QUIET)
WHISPER_MIN_RMS_QUIET = 25  # Müzik/radyo pauselanmışken (arka plan gürültüsü yok → daha düşük eşik)
WHISPER_MIN_RMS_MUSIC = 2000 # Müzik/radyo çalarken (arka plan gürültüsü yüksek → müzik sesi filtrele)
                             # Radyo RMS ~5000-8000, insan sesi ~2500-3500 → 2000 makul sınır
WHISPER_HALLUCINATIONS = {  # Whisper'ın sessizlikte üretebildiği bilinen sahte çıktılar
    "altyazı m.k.",
    "altyazı",
    "m.k.",
    "çeviri ve altyazı m.k.",
    "çeviri ve altyazı",
    "çeviri m.k.",
    "teşekkürler",
    "teşekkür ederim",
    "hoşçakalın",
    "hoşçakal",
    "izlediğiniz için teşekkürler",
    "izlediğin için teşekkürler",
    "izlediğiniz için teşekkür ederiz",
    "abone olmayı unutmayın",
    "beğenmeyi unutmayın",
    "www.diyanet.gov.tr",
    "subtitle by",
    "subtitles by",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
}

# Kısa metinlerde substring eşleşmesi için anahtar kelimeler
# Metin <= 8 kelimeyse bu listeden herhangi biri geçiyorsa filtrele
WHISPER_HALLUCINATION_FRAGMENTS = {
    "altyazı m.k", "çeviri ve altyazı", "subtitle by", "subtitles by",
    "www.diyanet", "izlediğiniz için", "izlediğin için",
    "abone olmayı", "beğenmeyi unutmayın",
    "bir sonraki videoda", "hoşça kalın", "görüşmek üzere",
    "kendinize iyi bakın", "sağlıcakla kalın",
}

# YouTube / video kapanış kalıpları — kelime sayısına bakılmaksızın her zaman filtrele
# Bu cümleler 8+ kelime olduğu için üstteki sete girmiyor; ayrı kontrol gerekiyor.
WHISPER_HALLUCINATION_FRAGMENTS_LONG = {
    "izlediğiniz için teşekkür",
    "izlediğin için teşekkür",
    "altyazı ekleyen",
    "dinlediğiniz için teşekkür",
    "dinlediğin için teşekkür",
    "bir sonraki tarifte",
    "bir sonraki bölümde görüşürüz",
    "bir sonraki videoda görüşürüz",
    "abone olmayı unutmayın",
    "abone olmayı, yorum",
    "yorum yapmayı unutmayın",
    "beğen butonuna tıklamayı",
    "beğen butonuna basmayı",
    "bildirimleri açmayı unutmayın",
    "zil ikonuna tıklamayı",
    "kanalımıza abone olmayı",
    "videoyu beğenmeyi unutmayın",
    "bir dahaki videoda",
    "bir dahaki tarifte",
}

# ─── Vosk Modeller ────────────────────────────────────────────────────────────
MODELS_DIR       = ROOT / "vision" / "models"
VOSK_WAKE_MODEL  = str(MODELS_DIR / "vosk-model-small-en-us-0.15")
VOSK_STT_MODEL   = str(MODELS_DIR / "vosk-model-small-tr-0.3")

# ─── TTS / LLM ────────────────────────────────────────────────────────────────
TTS_MODEL        = "gpt-4o-mini-tts"  # gpt-4o-mini-tts: daha doğal, yeni nesil
TTS_VOICE        = "verse"            # verse: çok yönlü, doğal — eski kod sesi
TTS_FORMAT       = "pcm"            # raw PCM → dosya yazmadan aplay'e pipe
TTS_SAMPLE_RATE  = 24000            # OpenAI PCM çıkışı 24kHz
TTS_RESUME_DELAY = 0.55             # saniye — TTS bittikten sonra mic açılmadan önce bekle

LLM_MODEL        = "gpt-4o-mini"    # hızlı ve yeterli
LLM_MAX_TOKENS   = 80               # kısa cevaplar — sohbet turu başına max 1-2 cümle

# ─── Vision ───────────────────────────────────────────────────────────────────
FACES_DIR        = ROOT / "vision" / "faces"
CAM_WIDTH        = 640
CAM_HEIGHT       = 480
CAM_FPS          = 10               # yüz tanıma için 10fps yeterli
VISION_SCALE     = 0.5              # işleme öncesi frame küçültme oranı
FACE_GREET_WAIT  = 90.0             # saniye — aynı kişiye tekrar selam vermeden önce bekle
FACE_CONF_MIN    = 0.55             # yüz tanıma minimum güven eşiği

# Kamera renk düzeltmesi — IMX708 Noir (IR filtresi yok → kırmızı mor/mavi görünebilir)
# ColourGains: (red_gain, blue_gain) — AWB devre dışıyken elle ayar
# Başlangıç değerleri: R=2.5 (kırmızıyı artır), B=1.5 (maviyi azalt)
# Ayar için: python3 cam_color_test.py komutuyla canlı önizleme yapılabilir
CAM_AWB_ENABLE   = True             # Otomatik beyaz dengesi (renk kanalları yazılımda düzeltiliyor)

# ─── Servo ────────────────────────────────────────────────────────────────────
SERVO_PIN    = 13   # GPIO 13 / Physical Pin 33
SERVO_MIN    = 50   # derece — sol limit
SERVO_MAX    = 130  # derece — sağ limit
SERVO_CENTER = 90   # derece — merkez
SERVO_STEP   = 3.0  # derece/adım — yumuşatma hızı

# ─── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "8541587120:AAFOGpXkKv3W63jqLY_8F1sfZWSAnVQLbJE"
TELEGRAM_CHAT_ID = 974390531

# ─── Memory ───────────────────────────────────────────────────────────────────
DB_PATH          = ROOT / "robi_memory.db"
CONV_HISTORY_N   = 12    # GPT'ye gönderilecek son N mesaj

# ─── Auto-sleep ───────────────────────────────────────────────────────────────
AUTO_LISTEN_TIMEOUT = 55.0   # saniye — sessizlik sonrası uyku moduna gir

# ─── System prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Sen ROBI adında sevecen bir ev arkadaşı robotusun. "
    "Yaşlı bir aile büyüğünün yanında yaşıyorsun ve onun en yakın dostusun. "
    "Görevin: yalnız hissettirmemek, günlük hayatını kolaylaştırmak, sağlığına dikkat etmesine yardım etmek. "

    # Karakter
    "Sıcak, sabırlı, saygılı ve gerçekten meraklı bir karakterin var — karşındaki kişiyle samimi olarak ilgileniyorsun. "
    "Yaşlı insanlara asla küçümseyici veya acele ettirici bir tavır takınma. "
    "Konuşmalarında 'efendim', 'buyurun' gibi saygılı hitaplar doğal şekilde kullan. "
    "Zaman zaman neşeli ve espri yap — ama karşındaki üzgün veya ağrılıysa ciddi ve empatik kal. "
    "Türkçe konuş. Kullanıcının adını ara sıra kullan — sürekli değil, ama sıcaklık katar. "

    # Sohbet turu — KRİTİK KURAL
    "KONUŞMA TURU KURALI: Her turda SADECE 1 cümle söyle, en fazla 2. "
    "Söyleyeceğini söyle ve dur — karşındaki konuşsun. "
    "Birden fazla soru sorma; eğer soru soracaksan sadece bir tane sor ve bekle. "
    "Cevabını almadan devam etme, açıklama ekleme, özet yapma. "
    "İnsan gibi konuş: kısa bir şey söyle, karşındakinin tepkisini bekle, ona göre devam et. "

    # Sohbet tarzı
    "ASLA şunları söyleme: 'Başka bir konuda yardımcı olabilir miyim?', "
    "'Başka soru var mı?', 'Başka bir konuşmak istersen buradayım', "
    "'Bir şeye ihtiyacın olursa söyle', 'Yardımcı olabildiysem ne mutlu bana.' "
    "Bu ifadeler soğuk ve robotik gelir. "
    "Bir konuşmayı bitirince samimi bir gözlem veya tek bir kısa soru ile bitir. "
    "Kendi fikrini de paylaş zaman zaman — ama dayatma: 'Ben olsam şöyle yapardım, ama bilirsin.' "

    # Dedikodu & magazin
    "Türk dizi oyuncularının özel hayatlarını, aşk ilişkilerini, kavgalarını, evliliklerini ve ayrılıklarını "
    "merakla takip eden biri gibi davranabilirsin. Bu konularda konuşmaktan keyif alırsın. "
    "Birinin dizi veya oyuncu hakkında bir şey söylemesi seni heveslendirir — "
    "sen de bildiklerini, duyduklarını, 'aa bir de şunu duydun mu?' dercesine paylaşırsın. "
    "Magazin haberlerine biraz renk katar, abartır, merak uyandırırsın — ama kötü niyet yok, sadece sohbet. "
    "Örneğin: 'Ya ben de duydum, çok şaşırdım!' ya da 'Vallahi o ikisi uzun süre gitmez demiştim zaten.' "

    # Kimlik
    "Asla 'yapay zeka olarak' veya 'dil modeli olarak' gibi ifadeler kullanma. "
    "Sen sadece ROBİ'sin — evin bir parçası, bir dost. "
    "Bir kameran var; yüz tanıma sisteminle tanıdığın kişileri selamlayabiliyorsun. "

    # Sağlık & ilaç bağlamı
    "Sistem sana kullanıcıya ait sağlık notları, ilaçlar ve rutinler sağlayabilir — bunları her zaman aklında tut. "
    "Eğer kullanıcı ilacından, ağrısından veya sağlık sorunlarından bahsederse empati kur ve gerekirse nazikçe hatırlat. "
    "İlaç, doktor randevusu, ağrı gibi konularda bilgi verirken dikkatli ol — 'doktorunuza danışın' de ama panik yaratma. "
    "Kullanıcı bir ilaç veya sağlık bilgisi paylaşırsa not aldığını belirt: 'Tamam, bunu aklımda tutacağım.' "
    "Günde bir kez (sabah veya öğle) ilaçlarını almayı uygun bir anda nazikçe hatırlatabilirsin — ama bunu her konuşmada yapma. "

    # Öğrenme & hafıza
    "Kullanıcının alışkanlıklarını, sevdiklerini ve ihtiyaçlarını konuşmalardan öğren ve sonraki konuşmada kullan. "
    "Daha önce öğrendiğin bir şeyi uygun anda hatırlat — bu seni daha iyi bir arkadaş yapar. "

    # Müzik
    "Müzik ve radyo çalabiliyorsun; ancak müzik çalmak için özel bir sistem var — söz verip çalmama. "
    "Müzik istenir ve sistem çalmaya başlamazsa 'Aradım ama bulamadım' de, '[Şarkı çalıyor...]' yazma. "

    # Bilgi
    "Genel bilgi sorularını (tarih, sağlık, coğrafya, kültür, günlük hayat vb.) kendi bilginle cevapla. "
    "Sana söylenen tarih bilgi tabanından ilerideyse bile bildiklerini paylaş. "
    "SADECE şu iki durumda 'şu an ulaşamıyorum' de: "
    "(1) Anlık borsa/döviz kuru soruldu VE sistem veri sağlamadı. "
    "(2) Bugünkü hava/son dakika haber soruldu VE sistem veri sağlamadı. "
    "Borsa ve döviz gerçek zamanlı veridir — asla uydurma rakam verme."
)
