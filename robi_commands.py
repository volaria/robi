# robi_commands.py
# Komut algılama yardımcıları

def normalize_cmd(text: str) -> str:
    if not text:
        return ""
    return text.lower().strip()


# -------------------------
# MÜZİK KOMUTLARI
# -------------------------
def is_music_start(text: str) -> bool:
    t = normalize_cmd(text)
    keywords = [
        "muzik ac",
        "müzik aç",
        "muzik cal",
        "müzik çal",
        "biraz muzik",
        "biraz müzik",
        "sarki ac",
        "şarkı aç",
        "sarki cal",
        "şarkı çal",
        "muzik baslat",
        "müzik başlat",
        "cal bakalim",
        "çal bakalım",
        "bir seyler cal",
        "bir şeyler çal",
        "muzik koy",
        "müzik koy",
    ]
    return any(k in t for k in keywords)


def is_music_stop(text: str) -> bool:
    t = normalize_cmd(text)
    keywords = [
        "muziği kapat",
        "müziği sustur",
        "muzik kapat",
        "müzik kapat",
        "muzigi durdur",
        "müziği durdur",
        "muzik durdur",
        "müzik durdur",
        "yeter",
        "kapat",
        "dur",
        "sus",
    ]
    return any(k in t for k in keywords)


# -------------------------
# SABAH / HABER
# -------------------------
def is_morning_brief(text: str) -> bool:
    t = normalize_cmd(text)
    keywords = [
        "haberler",
        "bugün ne var",
        "sabah haberleri",
        "gündem",
        "bugün neler oldu",
    ]
    return any(k in t for k in keywords)


# -------------------------
# İNTERNET KONTROL
# -------------------------
def is_internet_check(text: str) -> bool:
    t = normalize_cmd(text)
    keywords = [
        "internet var mı",
        "bağlantı var mı",
        "online mıyız",
    ]
    return any(k in t for k in keywords)
