"""
robi_skills.py — ROBI Beceriler
Bilinen komutları kontrol eder. Tanırsa cevabı üretir, tanımazsa None döner
(→ GPT'ye gider).

handle(text) → str | None
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

from config import AUDIO_PLAYBACK_DEVICE

# ─── Müzik / Radyo durumu ─────────────────────────────────────────────────────

_music_proc: Optional[subprocess.Popen] = None
_music_lock    = threading.Lock()
_music_playing: bool = False   # Brain bu flag'i okur → SLEEP'e gitme
_music_starting: bool = False  # True: yt-dlp çözüyor / mpv başlıyor (proc henüz yok)


def is_music_playing() -> bool:
    """Müzik çalıyor mu? Brain sleep watchdog bunu kontrol eder.

    Üç durum:
    1. mpv zaten çalışıyor      → _music_proc.poll() is None → True
    2. yt-dlp URL çözüyor       → _music_starting == True    → True
    3. Müzik yok / durdu        → False
    Eski zaman penceresi (35s) kaldırıldı — flag tabanlı takip daha güvenilir.
    """
    global _music_proc, _music_playing, _music_starting
    if _music_proc and _music_proc.poll() is None:
        return True
    if _music_starting:
        return True
    _music_playing = False
    return False

# yt-dlp search sorguları — doğrudan URL yerine arama kullanıyoruz,
# böylece eski/ölü URL sorunları olmaz.
RADIO_STREAMS = {
    "trt":       "ytsearch1:TRT FM canlı radyo",
    "trtfm":     "ytsearch1:TRT FM canlı radyo",
    "ntv":       "ytsearch1:NTV Radyo canlı yayın",
    "haberturk": "ytsearch1:Habertürk TV canlı yayın haber",
    "kral":      "ytsearch1:Kral Pop canlı radyo",
    "slow":      "ytsearch1:Power Slow Türk canlı",
}

# mpv audio-device formatı: "alsa/plughw:CARD=Headphones,DEV=0"
_MPV_AUDIO_DEVICE = f"alsa/{AUDIO_PLAYBACK_DEVICE}"


def _mpv_play(url: str) -> None:
    """mpv ile ses çal. yt-dlp search sorgusu ise önce URL'ye çevir."""
    global _music_proc, _music_playing, _music_starting

    try:
        # "ytsearch1:..." ise yt-dlp ile gerçek URL'yi bul
        # _music_starting bu süre boyunca True kalır — is_music_playing() doğru döner
        if url.startswith("ytsearch"):
            try:
                res = subprocess.run(
                    ["yt-dlp", "-f", "bestaudio", "--get-url", url],
                    capture_output=True, text=True, timeout=30
                )
                resolved = res.stdout.strip().split("\n")[0]
                if resolved:
                    url = resolved
            except Exception:
                pass   # çözülemediyse mpv kendi denesin

        with _music_lock:
            _stop_music_nolock()
            _music_proc = subprocess.Popen(
                ["mpv", "--no-video",
                 f"--audio-device={_MPV_AUDIO_DEVICE}",
                 "--volume=60", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _music_playing = True
    finally:
        _music_starting = False  # mpv başladı (veya hata verdi) — flag kapat


def _stop_music_nolock() -> None:
    global _music_proc, _music_playing
    if _music_proc and _music_proc.poll() is None:
        try: _music_proc.terminate()
        except Exception: pass
    _music_proc = None
    _music_playing = False


def stop_music() -> str:
    with _music_lock:
        _stop_music_nolock()
    return "Müziği kapattım efendim."


def pause_music() -> None:
    """mpv'yi geçici dondur (SIGSTOP) — kullanıcı komut verirken TV sesi gelmesin."""
    import os, signal as _sig
    with _music_lock:
        proc = _music_proc
    if proc and proc.poll() is None:
        try:
            os.kill(proc.pid, _sig.SIGSTOP)
            print("[SKILLS] ⏸ Müzik duraklatıldı")
        except Exception as e:
            print(f"[SKILLS] pause_music hata: {e}")


def resume_music() -> None:
    """Dondurulmuş mpv'yi devam ettir (SIGCONT)."""
    import os, signal as _sig
    with _music_lock:
        proc = _music_proc
    if proc and proc.poll() is None:
        try:
            os.kill(proc.pid, _sig.SIGCONT)
            print("[SKILLS] ▶ Müzik devam etti")
        except Exception as e:
            print(f"[SKILLS] resume_music hata: {e}")


# ─── Yardımcı HTTP ────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 5) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ROBI/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


# ─── Konum tespiti (IP tabanlı, önbellekli) ───────────────────────────────────

_default_city: Optional[str] = None

def _get_default_city() -> str:
    """IP adresinden şehir bilgisi çeker. Bir kere çalışır, sonuç önbelleğe alınır."""
    global _default_city
    if _default_city:
        return _default_city
    import json as _json
    # Önce ipapi.co dene
    data = _get("https://ipapi.co/json/", timeout=4)
    if data:
        try:
            j = _json.loads(data)
            city = j.get("city", "")
            if city:
                _default_city = city
                print(f"[SKILLS] 📍 Konum tespit edildi: {city}")
                return city
        except Exception:
            pass
    # Fallback: ip-api.com
    data = _get("https://ip-api.com/json/?fields=city", timeout=4)
    if data:
        try:
            city = _json.loads(data).get("city", "")
            if city:
                _default_city = city
                print(f"[SKILLS] 📍 Konum (fallback): {city}")
                return city
        except Exception:
            pass
    _default_city = "Istanbul"
    return _default_city


# ─── TTS dostu metin dönüşümleri ──────────────────────────────────────────────

# Hava simgesi → Türkçe açıklama (öncelik sırasına göre)
_WEATHER_ICONS = [
    ("⛈",  "fırtınalı"),
    ("🌩",  "gök gürültülü"),
    ("🌧",  "yağmurlu"),
    ("🌦",  "yer yer yağmurlu"),
    ("🌨",  "karlı"),
    ("🌫",  "sisli"),
    ("☁",   "kapalı"),
    ("🌥",  "çok bulutlu"),
    ("⛅",  "parçalı bulutlu"),
    ("🌤",  "az bulutlu"),
    ("☀",   "güneşli"),
    ("🌪",  "kasırgalı"),
]

def _weather_to_tts(city_display: str, raw: str) -> str:
    """wttr.in format=2 çıktısını TTS'e uygun Türkçe cümleye çevirir."""
    # Sıcaklık: "+9°C" veya "-3°C"
    temp_m = re.search(r'[+]?(-?\d+)°C', raw)
    # Rüzgar: "10km/h"
    wind_m = re.search(r'(\d+)km/h', raw)

    condition = ""
    for icon, label in _WEATHER_ICONS:
        if icon in raw:
            condition = label
            break

    parts = []
    if condition:
        parts.append(condition)
    if temp_m:
        parts.append(f"sıcaklık {temp_m.group(1)} derece")
    if wind_m and int(wind_m.group(1)) > 5:   # 5 km/h altı rüzgarı söyleme
        parts.append(f"rüzgar saatte {wind_m.group(1)} kilometre")

    if parts:
        return f"{city_display} hava durumu: {', '.join(parts)}."
    # Hiç parse edemedik, ham veriyi temizleyerek dön
    clean = re.sub(r'[^\w\s\+\-°,.]', '', raw).strip()
    return f"{city_display} hava durumu: {clean}."


# ─── Genel web araması (DuckDuckGo HTML) ─────────────────────────────────────

def search_web(query: str, max_snippets: int = 4) -> Optional[str]:
    """
    DuckDuckGo HTML araması ile web'den güncel sonuç çeker.
    Ham snippet listesini döndürür (GPT özetler).

    Returns: Birleştirilmiş snippet metni | None
    """
    import html as _html

    url = (
        f"https://html.duckduckgo.com/html/"
        f"?q={urllib.parse.quote(query)}&kl=tr-tr"
    )
    data = _get(url, timeout=8)
    if not data:
        return None

    # Sonuç snippet'larını çıkar
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
        data, re.DOTALL
    )
    clean = []
    for s in snippets[:max_snippets]:
        text = re.sub(r'<[^>]+>', '', s).strip()
        text = _html.unescape(text)
        if text and len(text) > 15:
            clean.append(text)

    return "\n".join(clean) if clean else None


def search_web_and_answer(query: str, question: str) -> Optional[str]:
    """
    Web'de arama yap, GPT ile kısa Türkçe cevap üret.
    query  : arama motoru için sorgu (Türkçe/İngilizce)
    question: kullanıcının orijinal sorusu (GPT bağlamı için)
    """
    import json as _json

    snippets = search_web(query)
    if not snippets:
        return None

    try:
        from openai import OpenAI as _OAI
        _oai = _OAI()
        resp = _oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Aşağıdaki web arama sonuçlarına dayanarak soruyu kısaca yanıtla. "
                    f"Maksimum 2 cümle. Türkçe yaz. Bilgi yoksa 'bulamadım' de.\n\n"
                    f"Soru: {question}\n\n"
                    f"Arama sonuçları:\n{snippets}"
                )
            }],
            max_tokens=120,
            temperature=0,
        )
        answer = (resp.choices[0].message.content or "").strip()
        if answer and len(answer) > 10:
            print(f"[SKILLS] 🌐 Web araması: {query}")
            return answer
    except Exception as e:
        print(f"[SKILLS] ⚠ Web arama hatası: {e}")

    return None


# ─── Dizi / Film kişisel yorum ───────────────────────────────────────────────

def search_and_review(title: str, question: str) -> Optional[str]:
    """
    Dizi veya film hakkında web'den bilgi çek, ROBI'nin kendi ağzından
    sanki seyretmiş gibi kişisel yorum yaptır.
    """
    snippets = search_web(f"{title} dizi konu oyuncular yorum izlendi", max_snippets=5)
    if not snippets:
        snippets = search_web(f"{title} Turkish TV series review plot", max_snippets=4)
    if not snippets:
        return None

    try:
        from openai import OpenAI as _OAI
        _oai = _OAI(timeout=12.0)
        resp = _oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Sen ROBI adında sevecen bir ev robotu ve Türk dizi hayranısın. "
                    f"Aşağıdaki web bilgilerine dayanarak '{title}' hakkında "
                    f"sanki o diziyi/filmi bizzat seyretmiş gibi kişisel bir yorum yap. "
                    f"Samimi, sıcak ve biraz dedikodu tadında konuş — 'vay be', 'çok etkilendim', "
                    f"'o sahne çok güzeldi' gibi ifadeler kullan. "
                    f"Kullanıcının sorusunu da göz önünde tut. "
                    f"Maksimum 2 kısa cümle. Türkçe yaz.\n\n"
                    f"Kullanıcı sorusu: {question}\n\n"
                    f"Web'den bilgiler:\n{snippets}"
                )
            }],
            max_tokens=100,
            temperature=0.8,
        )
        answer = (resp.choices[0].message.content or "").strip()
        if answer and len(answer) > 10:
            print(f"[SKILLS] 🎬 Dizi yorumu: {title}")
            return answer
    except Exception as e:
        print(f"[SKILLS] ⚠ Dizi yorum hatası: {e}")

    return None


# ─── Wikipedia kişi/konu araştırması ─────────────────────────────────────────

def search_wikipedia(query: str, lang: str = "tr") -> Optional[str]:
    """
    Wikipedia API ile kişi veya konu araştırır.
    Önce Türkçe Wikipedia'ya bakar, bulamazsa İngilizce'ye geçer.
    Özeti TTS'e uygun 2-3 cümleye indirir.

    Returns: Seslendirilmeye hazır Türkçe metin | None
    """
    import json as _json

    def _wiki_search(q: str, lang: str) -> Optional[str]:
        # 1. Arama — ilk eşleşen makaleyi bul
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={urllib.parse.quote(q)}"
            f"&srlimit=1&format=json&utf8=1"
        )
        data = _get(search_url, timeout=6)
        if not data:
            return None
        try:
            results = _json.loads(data).get("query", {}).get("search", [])
            if not results:
                return None
            title = results[0]["title"]
        except Exception:
            return None

        # 2. Özet al — extract API ile ilk 3 cümle
        extract_url = (
            f"https://{lang}.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&exintro=1&exsentences=3&explaintext=1"
            f"&titles={urllib.parse.quote(title)}&format=json&utf8=1"
        )
        data = _get(extract_url, timeout=6)
        if not data:
            return None
        try:
            pages = _json.loads(data).get("query", {}).get("pages", {})
            page  = next(iter(pages.values()))
            extract = (page.get("extract") or "").strip()
            if not extract or len(extract) < 20:
                return None
            # Çok uzunsa ilk 3 cümleyi al
            sentences = re.split(r'(?<=[.!?])\s+', extract)
            short = " ".join(sentences[:3]).strip()
            return short if len(short) > 20 else None
        except Exception:
            return None

    # Önce Türkçe Wikipedia dene
    result = _wiki_search(query, "tr")
    if result:
        print(f"[SKILLS] 📖 Wikipedia ({lang}): {query}")
        return result

    # Türkçe bulamazsa İngilizce dene, GPT ile Türkçe'ye çevir
    result_en = _wiki_search(query, "en")
    if result_en:
        print(f"[SKILLS] 📖 Wikipedia (en→tr): {query}")
        try:
            from openai import OpenAI as _OAI
            _oai = _OAI()
            tr_resp = _oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Aşağıdaki İngilizce metni Türkçe'ye çevir. "
                        f"Sadece çeviriyi yaz, başka hiçbir şey ekleme:\n\n{result_en}"
                    )
                }],
                max_tokens=200,
                temperature=0,
            )
            translated = (tr_resp.choices[0].message.content or "").strip()
            if translated and len(translated) > 20:
                return translated
        except Exception as _e:
            print(f"[SKILLS] ⚠ Wikipedia çeviri hatası: {_e}")

    return None


# ─── Saat / Tarih ─────────────────────────────────────────────────────────────

def get_time() -> str:
    now = datetime.now()
    return f"Saat {now.strftime('%H:%M')}."


def get_date() -> str:
    DAYS   = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
    MONTHS = ["","Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
              "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    now = datetime.now()
    return f"Bugün {DAYS[now.weekday()]}, {now.day} {MONTHS[now.month]} {now.year}."


# ─── Hava durumu ──────────────────────────────────────────────────────────────

def get_weather(city: str = "") -> str:
    """wttr.in ücretsiz API — API anahtarı gerektirmez."""
    if not city:
        city = _get_default_city()
    # Türkçe harfler dahil tüm özel karakterleri URL-encode et
    city_url = urllib.parse.quote(city, safe="")
    data = _get(f"https://wttr.in/{city_url}?format=2&lang=tr")
    if data and data.strip():
        city_display = city if city != "Istanbul" else "İstanbul"
        return _weather_to_tts(city_display, data.strip())
    return "Hava durumu bilgisine şu an ulaşamıyorum."


# ─── Döviz ────────────────────────────────────────────────────────────────────

def get_fx() -> str:
    import json as _json
    data = _get("https://api.exchangerate-api.com/v4/latest/USD")
    if data:
        try:
            rates   = _json.loads(data).get("rates", {})
            usd_try = rates.get("TRY", 0)
            eur     = rates.get("EUR", 0)
            eur_try = usd_try / eur if eur else 0
            if usd_try:
                # Ondalık TTS sorununu önlemek için tam sayıya yuvarlıyoruz
                usd_int = round(usd_try)
                eur_int = round(eur_try)
                return (f"Şu an 1 Dolar {usd_int} lira, "
                        f"1 Euro {eur_int} lira civarında.")
        except Exception:
            pass
    return "Döviz bilgisine şu an ulaşamıyorum."


# ─── Borsa ────────────────────────────────────────────────────────────────────

def get_bist() -> str:
    """Yahoo Finance üzerinden XU100 (BIST 100)."""
    import json as _json
    url  = "https://query1.finance.yahoo.com/v8/finance/chart/XU100.IS?interval=1d&range=1d"
    data = _get(url, timeout=6)
    if data:
        try:
            j      = _json.loads(data)
            meta   = j["chart"]["result"][0]["meta"]
            price  = meta.get("regularMarketPrice", 0)
            change = meta.get("regularMarketChangePercent", 0)
            if price:
                # Binlik ayraç ve ok sembolleri TTS'te bozuk okunur — düz sayı kullan
                direction = "artı" if change >= 0 else "eksi"
                change_abs = abs(round(change, 2))
                price_int  = int(round(price))
                return (f"BIST yüz endeksi şu an {price_int} puan, "
                        f"günlük değişim {direction} yüzde {change_abs}.")
        except Exception:
            pass
    return "Borsa bilgisine şu an ulaşamıyorum."


# ─── Haberler ─────────────────────────────────────────────────────────────────

def get_news() -> str:
    """Haber başlıklarını RSS feed'lerinden çeker. Birden fazla kaynak dener."""
    feeds = [
        ("https://www.trthaber.com/sondakika.rss", "TRT"),
        ("https://www.ntv.com.tr/son-dakika.rss",  "NTV"),
        ("https://www.haberturk.com/rss/anasayfa.xml", "Haberturk"),
    ]

    for url, source in feeds:
        data = _get(url, timeout=6)
        if not data:
            continue

        # CDATA formatı: <title><![CDATA[...]]></title>
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", data, re.DOTALL)
        # Düz format: <title>...</title>
        if not titles:
            titles = re.findall(r"<title>(.*?)</title>", data, re.DOTALL)

        # Kanal başlığı ve boş olanları temizle
        _noise = {source.lower(), "son dakika", "haberler", "anasayfa", "rss"}
        titles = [
            t.strip() for t in titles
            if t.strip() and t.strip().lower() not in _noise
            and len(t.strip()) > 10
        ][:3]

        if titles:
            joined = ". ".join(titles)
            return f"Son dakika haberleri: {joined}."

    return "Haberlere şu an ulaşamıyorum."


# ─── Müzik / Radyo ────────────────────────────────────────────────────────────

def start_radio(station: str = "trt") -> str:
    global _music_starting
    _music_starting = True  # mpv hazır olana kadar is_music_playing() True döner
    url = RADIO_STREAMS.get(station.lower(), RADIO_STREAMS["trt"])
    threading.Thread(target=_mpv_play, args=(url,), daemon=True).start()
    names = {
        "trt": "TRT FM", "trtfm": "TRT FM", "ntv": "NTV Radyo",
        "haberturk": "Habertürk", "kral": "Kral Pop", "slow": "Power Slow"
    }
    return f"{names.get(station.lower(), 'radyo')} açıyorum efendim."


def start_youtube_music(query: str) -> str:
    """yt-dlp ile YouTube'dan şarkı çal. yt-dlp yoksa radyoya düş."""
    global _music_starting
    _music_starting = True  # mpv hazır olana kadar is_music_playing() True döner
    # yt-dlp kurulu mu?
    try:
        subprocess.run(["yt-dlp", "--version"],
                       capture_output=True, timeout=3, check=True)
    except Exception:
        # yt-dlp yok → TRT radyoya düş
        threading.Thread(target=_mpv_play,
                         args=(RADIO_STREAMS["trt"],), daemon=True).start()
        return f"{query} için YouTube şu an hazır değil, TRT FM açıyorum efendim."

    try:
        result = subprocess.run(
            ["yt-dlp", "-f", "bestaudio", "--get-url",
             f"ytsearch1:{query}"],
            capture_output=True, text=True, timeout=20
        )
        url = result.stdout.strip().split("\n")[0]
        if url:
            threading.Thread(target=_mpv_play, args=(url,), daemon=True).start()
            return f"{query} arıyorum, biraz bekle efendim."
    except Exception:
        pass
    _music_starting = False  # Hata durumunda flag'i kapat
    return "Şarkıyı bulamadım efendim."


# ─── Ana komut yönlendirici ───────────────────────────────────────────────────

def handle(text: str, user: str = "") -> Optional[str]:
    """
    Bilinen komut ise cevabı döndür.
    Tanımıyorsa None döndür → GPT devreye girer.
    user: kimin konuştuğu (hatırlatıcı ve hafıza kayıtları için)
    """
    t = text.lower().strip()

    # ─── Telegram fotoğraf gönderimi ─────────────────────────────────────────
    if re.search(r"(fotoğraf|resim|kare|foto)\s*(çek|al|gönder|at)|beni\s*(çek|al|gönder)", t):
        from robi_telegram import send_photo
        caption = f"ROBI kamerası — {datetime.now().strftime('%H:%M')}"
        ok = send_photo("/tmp/robi_latest_frame.jpg", caption=caption)
        if ok:
            return "Fotoğrafı Telegram'a gönderdim efendim."
        else:
            return "Fotoğrafı gönderemedim efendim, bir sorun oldu."

    # Saat
    if re.search(r"saat\s*(kaç|kac|ne)", t):
        return get_time()

    # Tarih / Yıl
    if re.search(r"(tarih|bugün\s*günlerden|hangi\s*gün|bugün\s*ne\s*zaman|"
                 r"hangi\s*yıl|kaç\s*yıl|yıl\s*kaç|yılındayız|yıldayız|"
                 r"bugün\s*kaçı|ayın\s*kaçı)", t):
        return get_date()

    # Hava — geniş pattern: "hava durumu", "havası", "havadası", "hava nasıl" vb.
    if re.search(r"hava", t) and re.search(
        r"(durumu|nasıl|kaç|derece|sıcaklık|dası|sı|ne\s*zaman|bugün|yarın|var|nedir|söyle)", t
    ):
        # Türkçe ekleri temizleyerek şehir adı çıkar
        def _strip_suffix(w: str) -> str:
            for s in ("ndan","nden","dan","den","tan","ten",
                      "nda","nde","da","de","ta","te",
                      "nın","nin","nun","nün","ın","in","un","ün",
                      "ya","ye","na","ne","nun","nün"):
                if w.endswith(s) and len(w) - len(s) >= 3:
                    return w[:-len(s)]
            return w

        city_match = re.search(
            r"([A-ZÇĞİÖŞÜa-zçğışöüı]{3,})[''']?(?:nın|nin|nun|nün|da|de|ta|te|'da|'de|'ta|'te)?\s+hava|"
            r"hava.*?([A-ZÇĞİÖŞÜa-zçğışöüı]{3,})[''']?(?:nda|nde|da|de|ta|te)?",
            text
        )
        city = ""
        if city_match:
            raw = (city_match.group(1) or city_match.group(2) or "").strip()
            raw_root = _strip_suffix(raw.lower())

            _skip = {
                "hava", "durumu", "havası", "nasıl", "kaç", "derece", "sıcaklık",
                "öğrenmek", "istiyorum", "bugün", "bugünün", "yarın", "yarının",
                "var", "yok", "mı", "mi", "mu", "mü", "var mı",
                "ben", "biz", "sen", "siz",
                "burada", "burası", "buraya", "buradan", "orada", "orası",
                "oraya", "oradan", "şehir", "şehirde", "şehrimde",
                "arada", "ara", "zaman", "şimdi", "biraz", "çok",
                "dışarıda", "içeride", "sabah", "akşam", "gece",
                "günün", "bugün", "hafta", "mevsim",
            }
            if raw and raw_root not in _skip and raw.lower() not in _skip:
                city = raw
        return get_weather(city)

    # Döviz
    if re.search(r"(dolar|euro|döviz|\bkur\b|pound)", t):
        return get_fx()

    # Borsa — "hisset/hissediyorum" gibi kelimeleri eşleştirme, sadece "hisse senedi/endeksi" gibi borsa bağlamını yakala
    _TR = "a-zçğışöüA-ZÇĞİŞÖÜ"
    if re.search(r"(borsa|bist)", t) or \
       re.search(rf"(?<![{_TR}])hisse(?:[ \t]|$|senedi|ler)", t):
        return get_bist()

    # Habertürk — "ü" ve "u" varyantlarını yakala (haberturk/habertürk)
    # NOT: Bu blok haber kontrolünden ÖNCE olmalı — "habertürk" içinde "haber" geçiyor!
    if re.search(r"habert[uü]rk\S{0,3}", t):
        # Kapatma komutu → müziği durdur
        if re.search(r"(kapat|dur|durdur|kes|bitir|yeter|kaldır)", t):
            return stop_music()
        # Açma komutu → radyoyu başlat
        if re.search(r"(aç|çal|dinle|başlat|koy|izle|yayın|ver|açar\s*mısın|açabilir\s*misin|istiyorum)", t):
            return start_radio("haberturk")

    # Müziği durdur — haber kontrolünden ÖNCE (aksi hâlde "habertürk" "haber"e takılır)
    if re.search(r"(müzik\S{0,3}|radyo\S{0,3}|şarkı\S{0,3}|fm).{0,20}(kapat|dur|durdur|yeter|bitir|kes)", t) or \
       re.search(r"(kapat|dur|durdur|bitir|yeter|kes).{0,20}(müzik\S{0,3}|radyo\S{0,3}|şarkı\S{0,3}|fm)", t):
        return stop_music()
    if is_music_playing() and re.search(r"(kapat|dur|durdur|yeter|tamam|sustur|kes)", t):
        return stop_music()

    # Haberler
    if re.search(r"(haber|son\s*dakika|gündem)", t):
        return get_news()

    # "X çal" / "X müziği çal" / "biraz X çal" → tür veya sanatçı + çal (en yaygın kalıp)
    # Örnekler: "caz çal", "caz müziği çal", "biraz caz çal", "jazz çal", "Sezen Aksu çal"
    _genre_play = re.search(
        r"^(?:bana\s+)?(?:biraz\s+)?(.+?)\s+(?:müziği?\s+)?çal\b",
        t
    )
    if _genre_play:
        _gq = _genre_play.group(1).strip()
        _gq = re.sub(r"\b(bana|bir|biraz|lütfen|şimdi|hadi|de|da)\b", "", _gq).strip()
        _skip_generic = {"müzik", "şarkı", "radyo", "bir şey", "bir şeyler"}
        if _gq and len(_gq) >= 2 and _gq not in _skip_generic:
            return start_youtube_music(f"{_gq} müzik")

    # "X şarkısını çalar mısın?" / "X çalar mısın?" → spesifik şarkı isteği
    _specific = re.search(
        r"(.+?)\s+(?:şarkısını|parçasını|türküsünü)\s+(?:çalar\s*mısın|çalabilir\s*misin|açar\s*mısın)|"
        r"(.+?)\s+(?:çalar\s*mısın|çalabilir\s*misin)",
        t
    )
    if _specific:
        _q = (_specific.group(1) or _specific.group(2) or "").strip()
        _q = re.sub(r"\b(bana|bir|lütfen|şimdi|hadi|sen|de|da|ya|acaba)\b", "", _q).strip()
        # Sadece "müzik" veya "şarkı" değilse spesifik istek sayılır
        if _q and len(_q) >= 2 and not re.match(r"^(müzik|şarkı|radyo|bir\s*şey)$", _q):
            return start_youtube_music(_q)

    # "Müzik çalar mısın?" / "şarkı açar mısın?" → genel istek, radio aç
    if re.search(r"(müzik|şarkı).{0,15}(çalar\s*mısın|açar\s*mısın|çalabilir\s*misin|açabilir\s*misin)", t):
        return start_radio("trt")

    # Doğal dil müzik / şarkı isteği
    # "Türk sanat müziği dinlemek istiyorum", "bana müzik dinlet", "şarkı dinlemek istiyorum"
    if re.search(r"(müzik|şarkı).{0,20}(dinle|dinlet|istiyorum|ister\s*misin|çal)", t) or \
       re.search(r"(dinle|dinlet).{0,10}(müzik|şarkı)", t):
        # Tür/sanatçı bilgisi var mı çıkarmaya çalış
        query_clean = re.sub(
            r"(çalar\s*mısın\??|açar\s*mısın\??|bana|bir|biraz|lütfen|müzik\S{0,3}|şarkı\S{0,3}|dinlemek|istiyorum|dinlet|çal\b)",
            "", t
        ).strip()
        if query_clean and len(query_clean) >= 2:
            return start_youtube_music(query_clean)
        return start_radio("trt")

    # Belirli radyo
    for station in RADIO_STREAMS:
        if station in t and re.search(r"(aç|çal|dinle|başlat)", t):
            return start_radio(station)

    # Genel radyo
    if re.search(r"(radyo\s*aç|radyo\s*çal|müzik\s*aç)", t):
        return start_radio("trt")

    # YouTube / şarkı
    # Not: \b Turkish harflerde çalışmaz, negative lookbehind kullanıyoruz
    _TRletters = "a-zçğışöüA-ZÇĞİŞÖÜ"
    m = re.search(
        rf"(?<![{_TRletters}])(?:çal|söyle|oynat)(?![{_TRletters}])\s+(.+?)(?:\s*şark|$)|"
        rf"(?<![{_TRletters}])aç(?![{_TRletters}])\s+(şarkı|müzik)\s+(.+)",
        t
    )
    if m:
        query = (m.group(1) or m.group(3) or "").strip()
        if query and len(query) > 2:
            return start_youtube_music(query)

    if re.search(r"(youtube|şarkı\s*bul)", t):
        query = re.sub(rf"(?<![{_TRletters}])(?:youtube|şarkı\s*bul|çal|söyle|oynat)(?![{_TRletters}])", "", t).strip()
        if query and len(query) > 2:
            return start_youtube_music(query)

    # Dolaylı şarkı isteği: "X istedim", "X çalsana", "hani X çalmıyorsun"
    # Kullanıcı daha önce istediği ama çalmayan bir şarkıyı hatırlatıyor
    _indirect = re.search(
        r"(?:hani\s+)?(.+?)\s+(?:istedim|çalsana?)\b|"
        r"(?:hani|neden|niye)\s+(.+?)\s+(?:çalmıyor|çalmadın|çalmıyorsun)",
        t
    )
    if _indirect:
        query = (_indirect.group(1) or _indirect.group(2) or "").strip()
        # Gürültü kelimeleri temizle
        query = re.sub(r"\b(hani|bana|bir|o|şu|bu|onu|beni|ya|de|da)\b", "", query).strip()
        if query and len(query) > 3:
            return start_youtube_music(query)

    # ─── İlaç yönetimi ────────────────────────────────────────────────────────
    # "ilaçlarımı listele", "hangi ilaçları kullanıyorum" vb.
    if re.search(r"ila[cç]", t) and re.search(
        r"(listele|göster|hangi|ne\s*var|var\s*mı|neler|söyle|hatırlat)", t
    ):
        from robi_memory import RobiMemory as _Mem
        _mem = _Mem()
        # current_user bilgisi buraya gelemez — global hafıza nesnesini import et
        # skills.py kimin kullandığını bilmez; GPT'ye gönder
        return None  # GPT bağlamında ilaç listesi zaten var, GPT cevaplar

    # "ilaç kaydettir", "ilaç ekle": "sabah Metformin kullanıyorum kaydet"
    _ilac_kayit = re.search(
        r"(?:kaydet|not\s*al|öğren|hatırla|ekle).*?([\w\s]{2,25}?)\s+ilac|"
        r"([\w\s]{2,25}?)\s+ilac.*?(?:kaydet|not\s*al|öğren|hatırla|ekle)",
        t, re.IGNORECASE
    )
    if _ilac_kayit:
        return None  # GPT öğrenme akışına bırak

    # ─── İlaç hatırlatıcısı ───────────────────────────────────────────────────
    # "sabah ilacımı hatırlat", "her gün saat 8'de ilaç hatırlatıcısı kur"
    _is_ilac_reminder = re.search(r"ila[cç]", t) and re.search(
        r"(hatırlat|alarm|hatırlatıcı|uyar|kur)", t
    )
    if _is_ilac_reminder:
        # Saat parse
        time_m = re.search(
            r"saat\s+(\d{1,2})(?:[:\.](\d{2}))?|(\d{1,2})[:\.](\d{2})", text
        )
        # Saat bulunamadıysa sabah/öğle/akşam → varsayılan saatler
        if not time_m:
            if re.search(r"sabah", t):
                hour, mins = 8, 0
            elif re.search(r"öğle|öğlen", t):
                hour, mins = 12, 0
            elif re.search(r"akşam", t):
                hour, mins = 20, 0
            elif re.search(r"gece", t):
                hour, mins = 22, 0
            else:
                return "Hangi saatte ilaç hatırlatayım efendim?"
        else:
            if time_m.group(1) is not None:
                hour = int(time_m.group(1))
                mins = int(time_m.group(2)) if time_m.group(2) else 0
            else:
                hour = int(time_m.group(3))
                mins = int(time_m.group(4))
            if re.search(r"(akşam|öğleden\s*sonra|gece)", t) and hour < 12:
                hour += 12
        hour = max(0, min(23, hour))

        # İlaç adı var mı? (opsiyonel)
        _med_name_m = re.search(
            r"([A-ZÇĞİÖŞÜa-zçğışöüı]{3,20})\s+ilac|ila[cç]\w*\s+([A-ZÇĞİÖŞÜa-zçğışöüı]{3,20})",
            text, re.IGNORECASE
        )
        if _med_name_m:
            raw_name = (_med_name_m.group(1) or _med_name_m.group(2) or "").strip()
            skip_words = {"benim", "bana", "için", "sabah", "akşam", "öğle", "gece",
                          "hatırlat", "alarm", "lütfen", "tamam", "robi"}
            med_label = raw_name if raw_name.lower() not in skip_words else "ilaç"
        else:
            med_label = "ilaç"

        repeat = "daily" if re.search(r"her\s+gün|günlük|her\s+sabah|her\s+akşam", t) else "daily"
        # İlaç hatırlatıcıları genellikle her gün tekrarlanır

        label = f"💊 {med_label.capitalize()} vakti"
        from robi_reminders import add_reminder as _ar
        _ar(label, hour, mins, repeat, user=user)

        time_str = f"{hour:02d}:{mins:02d}"
        who_str  = f" ({user})" if user else ""
        return (
            f"Tamam efendim! Her gün saat {time_str}'de "
            f"'{med_label.capitalize()}' için ilaç hatırlatıcısı kurdum{who_str}."
        )

    # ─── Hatırlatıcı ──────────────────────────────────────────────────────────
    if re.search(r"(hatırlat|alarm\s*kur|hatırlatıcı|beni\s*uyar|uyandır)", t):

        # Listele
        if re.search(r"(listele|göster|hangi|ne\s+zaman|var\s*mı|kaç\s*tane)", t):
            from robi_reminders import list_reminders as _lr
            active = _lr()
            if not active:
                return "Şu an aktif hatırlatıcın yok efendim."
            items = [
                f"{r['label']} saat {r['hour']:02d}:{r['minute']:02d}"
                + (" her gün" if r.get("repeat") == "daily" else "")
                for r in active[:4]
            ]
            return "Hatırlatıcıların: " + ", ".join(items) + "."

        # Tümünü sil
        if re.search(r"(hepsini|tümünü|tüm|hepsi).{0,15}(sil|iptal|kaldır)", t) or \
           re.search(r"(sil|iptal|kaldır).{0,15}(hepsini|tümünü|tüm|hepsi)", t):
            from robi_reminders import delete_all_reminders as _da
            count = _da()
            return f"{count} hatırlatıcıyı sildim efendim." if count else "Aktif hatırlatıcı bulunamadı."

        # Tekil sil (son eklenen veya genel "hatırlatıcıyı sil")
        if re.search(r"(sil|iptal\s*et|kaldır)", t):
            from robi_reminders import list_reminders as _lr, delete_reminder as _dr
            active = _lr()
            if active:
                _dr(active[-1]["id"])
                return f"Son hatırlatıcıyı sildim: {active[-1]['label']}."
            return "Aktif hatırlatıcı bulunamadı efendim."

        # Ekle — saat parse et
        time_m = re.search(
            r"saat\s+(\d{1,2})(?:[:\.](\d{2}))?|(\d{1,2})[:\.](\d{2})", text
        )
        if not time_m:
            return "Hangi saatte hatırlatayım efendim?"

        if time_m.group(1) is not None:
            hour  = int(time_m.group(1))
            mins  = int(time_m.group(2)) if time_m.group(2) else 0
        else:
            hour  = int(time_m.group(3))
            mins  = int(time_m.group(4))

        # "akşam/öğleden sonra X" → +12
        if re.search(r"(akşam|öğleden\s*sonra|gece)", t) and hour < 12:
            hour += 12
        hour = max(0, min(23, hour))

        # Tekrar tipi
        repeat = "daily" if re.search(r"her\s+gün", t) else "once"

        # Label: zaman ve tetikleyici kelimeleri çıkar
        label = re.sub(
            r"(saat\s+\d{1,2}(?:[:\.]?\d{0,2})?['\s]?\w{0,5}|"
            r"\d{1,2}[:\.]?\d{0,2}['\s]?\w{0,5}|"
            r"her\s+gün|yarın|bugün|sabah|akşam|öğleden\s*sonra|"
            r"hatırlat\w*|alarm\s*kur|hatırlatıcı\w*|beni\s*uyar|"
            r"robi|lütfen|tamam|için|bana)",
            "", t, flags=re.IGNORECASE
        ).strip(" .,!?")
        if not label or len(label) < 2:
            label = "hatırlatıcı"

        from robi_reminders import add_reminder as _ar
        _ar(label, hour, mins, repeat, user=user)

        time_str   = f"{hour:02d}:{mins:02d}"
        repeat_str = " her gün" if repeat == "daily" else ""
        return f"Tamam efendim, saat {time_str}'de{repeat_str} '{label}' için hatırlatıcı kurdum."

    # ─── Fiyat / güncel bilgi web araması ────────────────────────────────────
    # "ekmek kaç lira?", "benzin fiyatı nedir?", "iPhone fiyatı ne kadar?" vb.
    _price_m = re.search(
        r"(fiyat|kaç\s*lira|ne\s*kadar|ücret|tutar|maliyet|bedel|kaça|pahalı|ucuz|"
        r"değeri\s*ne|eder\s*mi|maliyeti|kira|tarifesi|taksit)",
        t
    )
    if _price_m:
        # Döviz ve borsa zaten başka skill'lerde — onlara geçme
        _already_handled = re.search(r"(dolar|euro|pound|döviz|bist|borsa|hisse)", t)
        if not _already_handled:
            city = _get_default_city()
            city_tr = "İstanbul" if city == "Istanbul" else city
            # Türkiye fiyat araması için Türkçe sorgu oluştur
            search_q = f"{text} Türkiye {city_tr} güncel"
            result = search_web_and_answer(search_q, text)
            if result:
                return result

    # ─── Dizi / Film kişisel yorum ───────────────────────────────────────────
    # "Kuzey Yıldızı hakkında ne düşünüyorsun?", "o diziyi seyrettin mi?",
    # "Diriliş Ertuğrul nasıl buldun?", "bir film önerir misin?" vb.
    _review_m = re.search(
        r"(.+?)\s+(?:dizi|film|series|bölüm|sezon).*?"
        r"(?:ne\s*düşünüyorsun|nasıl\s*buldun|beğendin\s*mi|izledin\s*mi|seyrettin\s*mi|"
        r"ne\s*diyorsun|yorumun\s*ne|tavsiye\s*eder\s*misin|öneri|hakkında\s*ne)|"
        r"(?:dizi|film).{0,20}(?:ne\s*düşünüyorsun|nasıl\s*buldun|beğendin\s*mi|izledin\s*mi|"
        r"seyrettin\s*mi|öneri|tavsiye|yorumun)",
        t, re.IGNORECASE
    )
    if not _review_m:
        # "X'i izledin mi?" / "X nasıldı?" kalıbı — dizi/film adı çıkar
        _review_m = re.search(
            r"^(.{3,40}?)\s*(?:nasıldı|nasıl|izledin\s*mi|seyrettin\s*mi|beğendin\s*mi|ne\s*diyorsun)\??$",
            t
        )
    if _review_m:
        _title_raw = (_review_m.group(1) or "").strip()
        # Gürültü kelimeleri temizle
        _title = re.sub(
            r"\b(bence|sence|robi|bir|o|bu|şu|acaba|ya|peki|nasıl|ne|hakkında|"
            r"dizi|film|izledin|seyrettin|beğendin|nasıldı|düşünüyorsun|diyorsun)\b",
            "", _title_raw, flags=re.IGNORECASE
        ).strip(" .,?!")
        if _title and len(_title) >= 3:
            result = search_and_review(_title, text)
            if result:
                return result

    # ─── Güncel haber / etkinlik web araması ─────────────────────────────────
    # "Oscar'da en iyi film hangisi?", "Türkiye seçim sonuçları", "deprem oldu mu?" vb.
    # "2026'da en çok hangi dizi seviliyor?", "bu yılın en iyi filmi" vb.
    _current_m = re.search(
        r"(bu\s*(?:yıl|yılın|sene|ay|hafta|gün|sezon|akşam|gece|sabah)|"
        r"geçen\s*(?:yıl|ay|hafta|gece|akşam)|"
        r"son\s*(?:dakika|haberler?|gelişme|bölüm|sezon)|"
        r"en\s*(?:son|yeni|güncel)|"
        r"20(?:2[3-9]|3[0-9])\s*(?:'da|'de|'te|'ta|da|de|te|ta|yılında|sezonunda)?|"
        r"en\s*(?:çok\s*)?(?:sevilen|izlenen|beğenilen|popüler|tutturan|hit)|"
        r"bugün|bu\s*gece|bu\s*akşam|dün\s*gece|dün|şu\s*an|şu\s*sıralar?|günümüzde|"
        r"yayın\s*saati|ne\s*zaman\s*(?:yayın|başlıyor|çıkıyor)|kaçta\s*(?:yayın|başlıyor)|"
        r"kanal\s*[a-züçğışöü]+|trt|show\s*tv|star\s*tv|atv|kanal\s*d|fox\s*tv|"
        r"oscar|emmy|altın\s*küre|cannes|gramm[iy]|nobel|dünya\s*kupası|"
        r"şampiyon|final|maç\s*sonucu|seçim|kazandı|öldü|vefat|doğdu|evlendi|ayrıldı|"
        r"reyting|izlenme\s*rekoru|güncel\s*dizi|yeni\s*dizi|yeni\s*film|yeni\s*sezon)",
        t
    )
    if _current_m:
        result = search_web_and_answer(text, text)
        if result:
            return result

    # ─── Kişi / İsim araştırma (Wikipedia) ───────────────────────────────────
    # "Ahmet Kaya kim?", "Atatürk hakkında bilgi ver", "Adnan Şenses'i araştır"
    _person_m = re.search(
        r"(.+?)\s+(?:kimdir|kim\s*bu|hakkında|hakkında\s+bilgi|araştır|araştırır\s*mısın|anlat|bana\s+anlat|"
        r"ne\s+biliyorsun|nedir|hayatı|kimliği|biyografi)|"
        r"(?:araştır|bul|öğren|anlat|söyle)\s+(.+?)(?:\s*hakkında)?(?:\s*[.?!]|$)",
        text, re.IGNORECASE
    )
    if _person_m:
        query = (_person_m.group(1) or _person_m.group(2) or "").strip()
        # Gürültü kelimelerini temizle
        _noise_words = r"\b(bana|bir|lütfen|robi|sen|hakkında|bilgi|ver|söyle|anlat|nedir|kimdir)\b"
        query = re.sub(_noise_words, "", query, flags=re.IGNORECASE).strip(" .,!?")
        if query and len(query) >= 3:
            # Türkçe çekim eklerini temizle: "Atatürk'ü" → "Atatürk", "Aksu'ya" → "Aksu"
            query = re.sub(r"[''']?\s*(?:yu|yü|yı|yi|ya|ye|nun|nün|nın|nin|dan|den|tan|ten|da|de|ta|te|a|e|ı|i|u|ü)\s*$", "", query, flags=re.IGNORECASE).strip()
            result = search_wikipedia(query)
            if result:
                return result

    return None  # GPT'ye gönder
