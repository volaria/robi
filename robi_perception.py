#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import math
import struct
import os
import subprocess
from collections import deque

import cv2

from picamera2 import Picamera2

from vision.face_recognize import train as face_train, recognize
from vision.face_detect import detect_faces
from vision.face_service import update_confirmed_person

from collections import deque, Counter
from robi_bus import BusClient
from robi_constants import BUS_SOCKET

FACE_VOTE_WINDOW = 7
FACE_VOTE_MIN_HITS = 4
FACE_LOCK_SECONDS = 6
MIC_LOCK_PATH = "/tmp/robi_mic.lock"

face_vote_buffer = deque(maxlen=FACE_VOTE_WINDOW)
last_confirmed_name = None
last_confirm_time = 0.0

arecord = None

def on_person_detected(person_id=None):
    bus.publish({
        "type": "PERSON_DETECTED",
        "payload": {"id": person_id},
        "ts": time.time()
    })
# =====================================================
# EVENT SYSTEM (callback + JSONL output)
# =====================================================
import json

event_callback = None
EVENT_LOG_PATH = "/tmp/robi_events.jsonl"

def on_event(func):
    global event_callback
    event_callback = func

def emit(event: dict):
    # 1) callback varsa (aynı process için)
    if event_callback:
        event_callback(event)

    # 2) her zaman dosyaya yaz (brain buradan okur)
    try:
        event["_ts"] = time.time()
        with open(EVENT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print("EVENT WRITE ERROR:", e)

# =====================================================
# CONFIG: FACE CONSENSUS (anti-flicker)
# =====================================================
FACE_WINDOW = 12


# =====================================================
# CAMERA
# =====================================================
FRAME_SIZE = (640, 480)

picam2 = Picamera2()
picam2.configure(
    picam2.create_preview_configuration(
        main={"format": "RGB888", "size": FRAME_SIZE}
    )
)
picam2.start()
time.sleep(1.0)

def get_gray() -> "cv2.Mat":
    frame = picam2.capture_array()
    return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

def get_gray_blur() -> "cv2.Mat":
    g = get_gray()
    return cv2.GaussianBlur(g, (21, 21), 0)

# =====================================================
# FACE DETECTION (HAAR)
# =====================================================
FACE_CASCADE_PATH = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(FACE_CASCADE_PATH)
if face_cascade.empty():
    raise RuntimeError(f"Face cascade not loaded: {FACE_CASCADE_PATH}")

def detect_faces(gray):
    # Blur kullanmıyoruz (LBPH + Haar için daha iyi)
    return face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(40, 40),
        flags=cv2.CASCADE_SCALE_IMAGE
    )

# =====================================================
# MOTION
# =====================================================
MOTION_THRESH = 17000
MOTION_CONFIRM = 2
MOTION_COOLDOWN = 8.0

prev_motion = get_gray_blur()
motion_hits = 0
last_motion_time = 0.0

def motion_score(g1, g2) -> int:
    diff = cv2.absdiff(g1, g2)
    _, thr = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(thr)

# =====================================================
# SOUND (AUTO DEVICE FALLBACK)
# =====================================================
RATE = 16000
CHUNK_MS = 50
SOUND_THRESH = 0.005
SOUND_DELTA = 200
SOUND_COOLDOWN = 5.0

chunk_frames = int(RATE * CHUNK_MS / 1000)
chunk_bytes = chunk_frames * 2

AUDIO_DEVICE_CANDIDATES = [
    "plughw:CARD=sndrpigooglevoi,DEV=0",
]

# arecord = None

def _start_arecord(dev: str):
    return subprocess.Popen(
        ["arecord", "-D", dev, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=chunk_bytes * 6
    )

def init_audio():
    global arecord
    for dev in AUDIO_DEVICE_CANDIDATES:
        p = _start_arecord(dev)
        time.sleep(0.15)
        if p.poll() is None:
            arecord = p
            print(f"🎤 Audio device OK: {dev}")
            return True
    print("⚠️ Audio init failed (arecord device not found)")
    return False

last_rms = 0.0
last_sound_time = 0.0
rms_smooth = 0.0

def read_rms() -> float:
    global arecord

    # Brain mic kullanıyorsa → perception mic'i bırakır
    if os.path.exists("/tmp/robi_mic.lock"):
        if arecord and arecord.poll() is None:
            arecord.terminate()
            arecord = None
        return 0.0

    # arecord kapalıysa → aç
    if arecord is None or arecord.poll() is not None:
        if not init_audio():
            return 0.0

    data = arecord.stdout.read(chunk_bytes)
    if not data or len(data) != chunk_bytes:
        return 0.0

    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
    s2 = 0
    for v in samples:
        s2 += v * v
    return math.sqrt(s2 / len(samples))


# =====================================================
# INIT
# =====================================================
if face_train():
    print("✅ Face recognition ready")
else:
    print("⚠️ Face recognition NOT ready")

print("🤖 ROBI | Perception system started (face integrated)")

# =====================================================
# MAIN LOOP
# =====================================================
UNKNOWN_COOLDOWN = 2.0
UNKNOWN_MIN_FRAMES = 8  # ~8 frame boyunca tanıyamazsa unknown say
unknown_streak = 0
last_unknown_emit = 0.0

try:
    while True:
        now = time.time()

        # ---- FRAME (face + motion use different gray) ----
        gray = get_gray()
        faces = detect_faces(gray)

        # ---- Face recognize (single result) ----
        name = None
        if len(faces) > 0:
            print("👁️ FACE DETECTED (main loop)")
            name, conf = recognize(gray, faces)

            now = time.time()

            # 1) Tanıma yoksa: streak artır, hemen UNKNOWN basma
            if name is None:
                unknown_streak += 1

                # Eğer yakın zamanda CONFIRMED olmuş biri varsa, hiç unknown basma
                if last_confirmed_name and (now - last_confirm_time) < FACE_LOCK_SECONDS:
                    pass
                else:
                    if unknown_streak >= UNKNOWN_MIN_FRAMES and (now - last_unknown_emit) > UNKNOWN_COOLDOWN:
                        emit({
                            "type": "UNKNOWN_FACE",
                            "source": "main"
                        })
                        last_unknown_emit = now

                # burada buffer'ı her seferinde yakma (confirmation'ı öldürüyor)
                # face_vote_buffer.clear()  # KALDIR
            else:
                unknown_streak = 0

                face_vote_buffer.append(name)
                print(f"🧠 FACE VOTE (main): {name} conf={conf:.1f} buf={list(face_vote_buffer)}")

                counts = Counter(face_vote_buffer)
                winner, hits = counts.most_common(1)[0]

                if hits >= FACE_VOTE_MIN_HITS:
                    if winner != last_confirmed_name:
                        print(f"😄 FACE CONFIRMED: {winner}")
                        last_confirmed_name = winner
                        last_confirm_time = now
                        update_confirmed_person(winner, time.time())

                        emit({
                            "type": "FACE_CONFIRMED",
                            "name": winner,
                            "source": "main",
                            "hits": hits,
                            "confidence": conf
                        })

                    face_vote_buffer.clear()

        else:
            # Yüz yoksa streak sıfırla (unknown birikmesin)
            unknown_streak = 0

        # ---- MOTION (blurred) ----
        motion_g = cv2.GaussianBlur(gray, (21, 21), 0)
        ms = motion_score(prev_motion, motion_g)
        prev_motion = motion_g

        motion_hits = motion_hits + 1 if ms > MOTION_THRESH else 0
        net_motion = motion_hits >= MOTION_CONFIRM

        # ---- SOUND ----
        # ---- SOUND (GEÇİCİ OLARAK KAPALI) ----
        time.sleep(0.02)

        # # --- MIC LOCK watchdog (BURAYA) ---
        # if os.path.exists(MIC_LOCK_PATH):
        #     age = time.time() - os.path.getmtime(MIC_LOCK_PATH)
        #     if age > 5.0:
        #         print("⚠️ MIC LOCK STALE → force release")
        #         try:
        #             os.remove(MIC_LOCK_PATH)
        #         except Exception:
        #             pass
        #
        # # mevcut gate (aynı kalsın)
        # if os.path.exists(MIC_LOCK_PATH):
        #     # Brain konuşma/dinleme yaparken perception mikrofonu KULLANMAYACAK
        #     time.sleep(0.02)
        #     continue
        #
        #
        # raw = read_rms()
        # rms_smooth = 0.7 * rms_smooth + 0.3 * raw
        # rms = rms_smooth
        # delta = rms - last_rms
        # last_rms = rms

        # print(f"🔊 SOUND rms={rms:.0f} Δ={delta:.0f}")

        # ---- SOUND TRIGGER ----
        # if (
        #         rms > SOUND_THRESH
        #         and (now - last_sound_time) > SOUND_COOLDOWN
        # ):
        #     last_sound_time = now
        #     rms_smooth = 0
        #
        #     print(f"🔊 SOUND rms={int(rms)} Δ={int(delta)}")
        #
        #     emit({
        #         "type": "SOUND_DETECTED",
        #         "rms": int(rms),
        #         "delta": int(delta)
        #     })
        #
        # time.sleep(0.02)

except KeyboardInterrupt:
    print("\n👋 Perception stopped")

finally:
    try:
        picam2.stop()
    except Exception:
        pass
    try:
        if arecord is not None:
            arecord.terminate()
    except Exception:
        pass

