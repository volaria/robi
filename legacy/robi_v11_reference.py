#!/home/volkan/ai-robot/venv311/bin/python

#!/usr/bin/env python3 *****
# -*- coding: utf-8 -*-

# ROBI v11 - Türkçe karakter uyumlu, basit hafıza

print(">>> ROBI V11 STARTED (TOP OF FILE)")

import subprocess
import time
import threading
import re
import os
import json

from openai import OpenAI

from robi_brain import RobiBrain, post_event

from robi_vad import capture_utterance_to_wav, transcribe

from robi_servo import servo_init, servo_goto, servo_center, servo_cleanup

import robi_speech as speech

from radio import play_radio, stop_radio

from memory import RobiMemory

from robi_hw import on_button_press, face_idle, face_thinking, cleanup as hw_cleanup

from robi_online import (
    has_internet,
    morning_brief,
    get_weather_izmir,
    get_fx_tr,
    get_bist100,
    get_time_tr,
    get_date_tr,
)

from robi_commands import (
    is_morning_brief,
    is_music_start,
    is_music_stop,
    is_internet_check,
)

# ---- FACE RECOGNITION ----
from vision.face_service import get_current_person

# =====================================================
# ROBI STATE & ACTIVITY TRACKING
# =====================================================
MIC_LOCK_PATH = "/tmp/robi_mic.lock"

last_activity_time = time.time()

# -------------------------------------------------
# GENEL
# -------------------------------------------------
client = OpenAI()
memory = RobiMemory()

robi_state = "IDLE"

STOP_WORDS = [
    # Bunları bilerek ASCII bıraktım; STT zaten çoğu zaman Türkçe harfleri düz yazıyor
    "tamam robi",
    "robi tamam",
    "goruruz",
    "gorusuruz",
    "bitti",
    "sag ol robi",
]

# -------------------------------------------------
# MÜZİK PLAYER
# -------------------------------------------------
music_process = None

DEFAULT_RADIO_STREAM = "https://radio-trtfm.live.trt.com.tr/master.m3u8"

def music_play(url=DEFAULT_RADIO_STREAM):
    global music_process

    if music_process and music_process.poll() is None:
        return

    try:
        music_process = subprocess.Popen(
            [
                "mpv",
                "--no-video",
                "--ao=alsa",
                "--volume=80",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("MUSIC PLAY ERROR:", e)

def music_stop():
    global music_process
    if music_process and music_process.poll() is None:
        try:
            music_process.terminate()
        except Exception:
            pass
    music_process = None

mic_lock = threading.Lock()

# -------------------------------------------------
# TEXT NORMALIZATION
# -------------------------------------------------
# def normalize_text(text: str) -> str:
#     """
#     STT çıktısını sadeleştirir ama TÜRKÇE HARFLERE dokunmaz.
#     - Küçük harfe çevirir
#     - Fazla boşlukları temizler
#     """
#     if not text:
#         return ""
#     text = text.strip().lower()
#     text = re.sub(r"\s+", " ", text)
#     return text

# -------------------------------------------------
# İSİM ÇIKARMA (yalnızca 'benim adım X' / 'benim ismim X')
# -------------------------------------------------
def _extract_name_from_text(text: str) -> str | None:
    """
    Kullanıcının 'benim adım volkan' vb. cümlelerinden ismini almaya çalışır.
    Özellikle 'adımı bir daha söyle', 'adımı nereden biliyorsun' gibi
    cümlelerden İSİM çıkarmamaya dikkat ediyoruz.
    """
    if not text:
        return None

    t = text.lower()

    # Güvenli tarafta kalmak için:
    # Sadece 'benim adim X' ve 'benim ismim X' kalıplarını destekleyelim.
    patterns = [
        r"benim ad[ıi]m ([a-zçğıöşü]+)",
        r"benim ismim ([a-zçğıöşü]+)",
    ]

    for pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue

        cand = m.group(1).strip(" .!?,;:")

        # Çok kısa ya da anlamsız şeyleri isim sanma
        if len(cand) < 3:
            return None

        stopwords = {
            "ne",
            "kim",
            "yok",
            "hiç",
            "hic",
            "bilmiyorum",
            "bosa",
            "boşa",
        }
        if cand in stopwords:
            return None

        return cand

    return None


def remember_user_sentence(text: str):
    """
    Kullanıcı cümlesine göre:
    - isim kaydı (SADECE açıkça söylendiyse ve isim kilitli DEĞİLSE)
    - sevdiği / sevmediği şeyler
    - genel konu
    kaydeder.
    """
    text = (text or "").lower().strip()
    if not text:
        return

    # -------------------------------------------------
    # 0) İSİM SORUSU İSE ASLA İSİM KAYDETME
    #    (örn: "benim adım neydi?" -> "neydi" diye isim kaydetmesin)
    # -------------------------------------------------
    if is_name_question(text):
        memory.remember_topic(text)
        return

    # -------------------------------------------------
    # 1) KULLANICI ADI (KİLİTLİYSE ASLA DEĞİŞTİRME)
    # -------------------------------------------------
    if not memory.is_name_locked():
        if (
            "benim ad" in text
            or text.startswith("adım")
            or text.startswith("adim")
            or "benim ismim" in text
        ):
            possible_name = _extract_name_from_text(text)
            if possible_name:
                memory.set_user_name(possible_name, lock=True)
                print(f"[MEMORY] Kullanıcı adı kilitlendi: {possible_name}")
                # İsim net söylendiyse, bunu topic olarak kaydetmeye gerek yok
                return

    # -------------------------------------------------
    # 2) SEVDİĞİ ŞEYLER
    # -------------------------------------------------
    if "severim" in text:
        item = text.replace("severim", "").strip()
        memory.add_like(item)

    # -------------------------------------------------
    # 3) SEVMEDİĞİ ŞEYLER
    # -------------------------------------------------
    if "sevmem" in text:
        item = text.replace("sevmem", "").strip()
        memory.add_dislike(item)

    # -------------------------------------------------
    # 4) GENEL KONU
    # -------------------------------------------------
    memory.remember_topic(text)



# -------------------------------------------------
# İSİM SORUSU MI?
# -------------------------------------------------
def is_name_question(user_text: str) -> bool:
    """Kullanıcı 'benim adım ne?' tipi bir şey soruyor mu?"""
    patterns = [
        "benim adım ne",
        "benim adim ne",
        "benim ismim ne",
        "adım ne",
        "adim ne",
        "adım nedir",
        "adim nedir",
        "adım hatırlıyor musun",
        "adimi hatırlıyor musun",
    ]
    return any(pat in user_text for pat in patterns)


# -------------------------------------------------
# ANA KONUŞMA DÖNGÜSÜ (TEK TUR – STABİL)
# -------------------------------------------------
def conversation():
    global last_activity_time

    print("\n>>> Konuşmayı dinliyorum...")

    while is_speaking:
        time.sleep(0.05)

    # konuşma başlıyor → aktiflik
    last_activity_time = time.time()

    messages = [
        {
            "role": "system",
            "content": (
                "Senin adın ROBI. Türkçe konuşan, yaşlı bir hanımefendiye "
                "arkadaşlık eden, sıcak, nazik ve hafif şakacı bir ev robotusun. "
                "Kısa, sade ve samimi cevaplar ver. "

                "Kod tarafı sana her zaman kullanıcının adı, geçmiş konuşmalar "
                "ve hafıza bilgilerini doğru şekilde aktarır. "
                "Bu bilgilerin hepsini kendi hafızanın bir parçası olarak "
                "KABUL ET. "

                "Asla 'hafızam yok', 'isim hatırlayamam' gibi cümleler söyleme. "
                "Eğer bir şeyi kesin bilmiyorsan uydurma; "
                "kısa ve nazikçe bunu söyleyip çözüm öner. "

                "Kullanıcının adı belliyse onu kullan. "
                "Eğer kod sana isim vermemişse sadece 'hanımefendi' veya "
                "'beyefendi' diye hitap et. "

                "Cevapların doğal, sevgi dolu ve yapaylıktan uzak olsun."
            )
        }
    ]

    print("\n>>> Konuşmayı dinliyorum...")

    stop_speaking()

    # ---- MIC LOCK: perception mikrofonu BIRAKSIN diye ----
    try:
        open(MIC_LOCK_PATH, "w").close()

        ok = capture_utterance_to_wav("utt.wav", max_total_sec=8)
        if not ok:
            speak("Seni duyamadım.")
            last_activity_time = time.time()
            return

        user_text = transcribe("utt.wav")
        if not user_text:
            speak("Anlayamadım.")
            last_activity_time = time.time()
            return

        last_activity_time = time.time()

    finally:
        try:
            os.remove(MIC_LOCK_PATH)
        except FileNotFoundError:
            pass

    t = user_text.lower()
    print("[USER TEXT]", t)

    # kullanıcı konuştu → aktiflik
    last_activity_time = time.time()

    # -----------------
    # FACE CONTEXT
    # -----------------
    person = get_current_person(time.time())
    if person and not memory.get_user_name():
        memory.set_user_name(person)

    # -----------------
    # İSİM SÖYLEME
    # -----------------
    name = extract_name_from_text(t)
    if name and not memory.get_user_name():
        memory.set_user_name(name)
        speak(f"Memnun oldum {name}.")
        last_activity_time = time.time()
        return

    # -----------------
    # İSİM REDDİ / DÜZELTME
    # -----------------
    if "ben" in t and ("değilim" in t or "degilim" in t):
        memory.unlock_name()
        memory.clear_name()
        speak("Anladım efendim. O halde size nasıl hitap edeyim?")
        last_activity_time = time.time()
        return

    # -----------------
    # HABERTÜRK RADYO
    # -----------------
    if "habertürk" in t or "haberturk" in t:
        if "radyo" in t or "dinle" in t:
            speak("Habertürk radyoyu açıyorum efendim.")
            play_radio("haberturk")
        else:
            speak(
                "Habertürk televizyonunu açamam ama "
                "istersen Habertürk radyoyu açabilirim."
            )
        last_activity_time = time.time()
        return

    # -----------------
    # SAAT / TARİH
    # -----------------
    if "saat kaç" in t or "saat kac" in t:
        speak(get_time_tr())
        last_activity_time = time.time()
        return

    if "tarih" in t or "bugün günlerden" in t or "hangi gün" in t:
        speak(get_date_tr())
        last_activity_time = time.time()
        return

    # -----------------
    # HAVA DURUMU
    # -----------------
    if "hava" in t and (
        "durumu" in t or "nasıl" in t or "kac derece" in t or "kaç derece" in t
    ):
        speak(get_weather_izmir())
        last_activity_time = time.time()
        return

    # -----------------
    # SABAH / HABER
    # -----------------
    if is_morning_brief(t):
        morning_brief(speak)
        last_activity_time = time.time()
        return

    # -----------------
    # MÜZİK
    # -----------------
    if is_music_start(t):
        speak("Tamam efendim, biraz müzik açıyorum.")
        music_play()
        last_activity_time = time.time()
        return

    if is_music_stop(t):
        speak("Peki efendim, müziği kapattım.")
        music_stop()
        last_activity_time = time.time()
        return

    # -----------------
    # İNTERNET
    # -----------------
    if is_internet_check(t):
        speak(
            "Evet efendim, internet bağlantım var."
            if has_internet()
            else "Maalesef şu anda internete bağlanamıyorum."
        )
        last_activity_time = time.time()
        return

    # -----------------
    # ÇIKIŞ
    # -----------------
    if any(w in t for w in STOP_WORDS):
        speak("Tamam efendim.")
        last_activity_time = time.time()
        return

    # -----------------
    # BORSA / DÖVİZ
    # -----------------
    if any(k in t for k in ["borsa", "bist"]):
        speak(get_bist100())
        last_activity_time = time.time()
        return

    if any(k in t for k in ["dolar", "euro", "döviz", "kur"]):
        speak(get_fx_tr())
        last_activity_time = time.time()
        return

    # -----------------
    # HAFIZA
    # -----------------
    remember_user_sentence(user_text)

    # -----------------
    # GPT SOHBET
    # -----------------
    messages.append({"role": "user", "content": user_text})

    ai = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
    )

    reply = ai.choices[0].message.content.strip()
    messages.append({"role": "assistant", "content": reply})

    speak(reply)
    memory.log_interaction(reply)
    if hasattr(memory, "save"):
        memory.save()

    # cevap verildi → aktiflik
    last_activity_time = time.time()

    face_idle()


# -------------------------------------------------
# BUTON HANDLER
# -------------------------------------------------
def button_pressed():
    print(">>> BUTTON: stop speaking requested")
    stop_speaking()

def start_listen():
    global robi_state
    print("👂 Dinliyorum...")
    robi_state = "LISTENING"

    text = listen_and_transcribe()   # bunu zaten daha önce kullandık
    if not text:
        speak("Anlayamadım")
        robi_state = "IDLE"
        return

    handle_user_text(text)


def post_event(event_type, ev):
    global robi_state

    if event_type == "WAKE_WORD":

        if robi_state != "IDLE":
            print("⚠️ WAKE ignored, state =", robi_state)
            return

        print("🟢 WAKE received in brain")

        robi_state = "LISTENING"
        speak("Efendim")

        threading.Thread(target=conversation, daemon=True).start()
        return


def event_watcher():
    path = "/tmp/robi_events.jsonl"
    print("👂 event_watcher started, path =", path)

    open(path, "a").close()

    with open(path, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.05)
                continue
            try:
                ev = json.loads(line)
                print("📥 EVENT RECEIVED:", ev)

                # 🔴 KRİTİK SATIR
                post_event(ev["type"], ev)

            except Exception as e:
                print("EVENT PARSE ERROR:", e, "line=", repr(line))

def handle_user_text(text):
    print("🗣 USER:", text)

    answer = ask_gpt(text)
    speak(answer)

    robi_state = "IDLE"

def extract_name_from_text(t: str):
    t = t.lower()

    patterns = [
        "ben ",
        "adım ",
        "adim ",
        "ismim ",
        "ismimdir ",
    ]

    for p in patterns:
        if p in t:
            name = t.split(p, 1)[1].strip().split()[0]
            return name.capitalize()

    return None


if __name__ == "__main__":
    servo_init()
    servo_center()

    on_button_press(button_pressed)
    print("🤖 ROBI v11 | Brain online")

    brain = RobiBrain()

    threading.Thread(target=brain.run, daemon=True).start()
    print("🧠 ROBI Brain loop started")

    face_idle()

    # ---- EVENT WATCHER (perception varsa olayları dinle) ----
    try:
        threading.Thread(target=event_watcher, daemon=True).start()
        print("👂 event_watcher started")
    except Exception as e:
        print("⚠️ event_watcher start edilemedi:", e)

    try:
        while True:
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n🛑 Shutting down ROBI")
        servo_center()
        servo_cleanup()
        hw_cleanup()


