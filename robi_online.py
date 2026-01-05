# robi_online.py
# Robi'nin internetle ilgili tüm yetenekleri burada toplanır.
# Haber, hava, müzik vs. ileride buradan büyür.

import socket
import datetime
import random
import requests
import re
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

TR_TZ = ZoneInfo("Europe/Istanbul")
TR_DAYS = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]

TR_MONTHS = [
    "Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
    "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"
]

def get_time_tr() -> str:
    now = datetime.now(TR_TZ)
    return f"Saat {now.strftime('%H:%M')}."

def get_date_tr() -> str:
    now = datetime.now(TR_TZ)
    day = TR_DAYS[now.weekday()]
    month = TR_MONTHS[now.month - 1]
    return f"Bugün {now.day} {month} {now.year}, günlerden {day}."

def normalize_for_speech(text: str) -> str:
    if not text:
        return text

    # 12.345 -> 12345
    text = re.sub(r'(\d+)\.(\d{3})', r'\1\2', text)

    # 12,34 -> 12 virgül 34
    text = re.sub(r'(\d+),(\d+)', r'\1 virgül \2', text)

    # %5 -> yüzde 5
    text = re.sub(r'%\s*(\d+)', r'yüzde \1', text)

    # 18:06 -> 18 06
    text = re.sub(r'(\d{1,2}):(\d{2})', r'\1 \2', text)

    return text

# -------------------------------------------------
# INTERNET VAR MI?
# -------------------------------------------------
def has_internet(timeout=3) -> bool:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        return True
    except OSError:
        return False


# -------------------------------------------------
# SABAH MI?
# -------------------------------------------------
def is_morning():
    now = datetime.datetime.now()
    return 6 <= now.hour <= 11


# -------------------------------------------------
# SABAH SELAMLAMASI
# -------------------------------------------------
MORNING_GREETINGS = [
    "Günaydın efendim.",
    "Günaydın, umarım güzel bir gün olur.",
    "Günaydın, bugün sakin bir gün gibi.",
]


def say_good_morning(speak):
    speak(random.choice(MORNING_GREETINGS))


# -------------------------------------------------
# HABER ÇEK (ÇOK SADE)
# -------------------------------------------------
def fetch_simple_news():
    """
    Çok basit, başlık bazlı haber çeker.
    Şimdilik RSS kullanıyoruz (API anahtarı yok).
    """
    try:
        url = "https://feeds.bbci.co.uk/turkce/rss.xml"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return []

        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)

        titles = []
        for item in root.findall(".//item")[:3]:
            title = item.find("title")
            if title is not None:
                titles.append(title.text)

        return titles

    except Exception:
        return []


# -------------------------------------------------
# SABAH KISA ÖZET
# -------------------------------------------------
def morning_brief(speak):
    """
    Anne-dostu sabah özeti:
    - Selam
    - 1–2 yumuşak haber
    """
    say_good_morning(speak)

    if not has_internet():
        speak("İnternet yok gibi görünüyor ama sorun değil.")
        return

    news = fetch_simple_news()
    if not news:
        speak("Bugün dikkat çeken bir haber yok gibi.")
        return

    speak("İstersen kısaca haberlerden bahsedebilirim.")

    for title in news[:2]:
        speak(title)

# -------------------------------------------------
# HAVA DURUMU
# -------------------------------------------------

def get_weather_izmir() -> str:
    """
    İzmir için anlık hava (Open-Meteo).
    API key yok. :contentReference[oaicite:1]{index=1}
    """
    lat, lon = 38.4237, 27.1428  # ✅ İzmir
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current_weather=true"
        "&timezone=Europe%2FIstanbul"
    )

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        cw = data.get("current_weather") or {}
        temp = cw.get("temperature")
        wind = cw.get("windspeed")
        code = cw.get("weathercode")

        desc = weathercode_tr(code)

        parts = []
        if temp is not None:
            parts.append(f"İzmir’de şu an {int(round(temp))} derece")
        if desc:
            parts.append(desc)
        if wind is not None:
            parts.append(f"Rüzgar {int(round(wind))} kilometre")
        return ". ".join(parts) + "."

    except Exception:
        return "Hava durumunu şu an çekemedim. İnternet var ama hava servisine ulaşamadım."

def weathercode_tr(code: int | None) -> str:
    # Basit, yeterli bir TR mapping (istersen sonra genişletiriz)
    m = {
        0: "açık",
        1: "çoğunlukla açık",
        2: "parçalı bulutlu",
        3: "kapalı",
        45: "sisli",
        48: "kırağılı sis",
        51: "hafif çiseleme",
        53: "çiseleme",
        55: "yoğun çiseleme",
        61: "hafif yağmur",
        63: "yağmur",
        65: "şiddetli yağmur",
        71: "hafif kar",
        73: "kar",
        75: "yoğun kar",
        80: "sağanak",
        95: "gök gürültülü fırtına",
    }
    return m.get(code, "")


def get_market_tr() -> str:
    # Yahoo Finance quote endpoint (pratik)
    symbols = "USDTRY=X,EURTRY=X,XU100.IS"
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"

    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))

        rows = data.get("quoteResponse", {}).get("result", [])
        bysym = {x.get("symbol"): x for x in rows}

        def px(sym):
            x = bysym.get(sym, {})
            return x.get("regularMarketPrice")

        usd = px("USDTRY=X")
        eur = px("EURTRY=X")
        xu100 = px("XU100.IS")

        parts = []
        if xu100 is not None:
            parts.append(f"BIST yüz endeksi {int(xu100)} puan")
        if usd is not None:
            parts.append(f"Dolar {usd:.2f} lira")
        if eur is not None:
            parts.append(f"Euro {eur:.2f} lira")

        if not parts:
            return "Piyasa verisini bulamadım."
        return " / ".join(parts) + "."
    except Exception:
        return "Piyasa verisini şu an çekemedim."

def get_fx_tr():
    try:
        url = "https://www.tcmb.gov.tr/kurlar/today.xml"
        with urllib.request.urlopen(url, timeout=5) as r:
            xml = r.read()
        root = ET.fromstring(xml)

        usd = root.find(".//Currency[@Kod='USD']/BanknoteSelling").text
        eur = root.find(".//Currency[@Kod='EUR']/BanknoteSelling").text

        usd = float(usd.replace(",", "."))
        eur = float(eur.replace(",", "."))

        return f"Dolar {usd:.2f} lira, Euro {eur:.2f} lira."
    except Exception:
        return "Döviz bilgisini şu an alamıyorum."

def get_bist100():
    try:
        url = "https://api.investing.com/api/financialdata/indices/3491"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))

        val = data["data"][0]["last"]
        return f"BIST yüz endeksi {int(float(val))} puan."
    except Exception:
        return "BIST 100 bilgisini şu an alamıyorum."
