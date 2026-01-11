#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robi_speech.py
Tek işi: ROBI'yi konuşturmak (TTS) + (opsiyonel) LED yüz senkronu
- speaking_now() doğru çalışır
- mic lock yönetir: /tmp/robi_mic.lock
- bus'a TTS_START yayar (audio mic mute için)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Optional
from robi_constants import BUS_SOCKET


MIC_LOCK_PATH = "/tmp/robi_mic.lock"
TTS_WAV_PATH = "tts.wav"

# -----------------------------
# Optional HW face hooks
# -----------------------------
USE_HW = False

if USE_HW:
    try:
        from robi_hw import face_thinking, face_speaking, face_listening, anim_transition
    except Exception:
        USE_HW = False

if not USE_HW:
    def face_thinking(): ...
    def face_speaking(): ...
    def face_listening(): ...
    def anim_transition(*a, **k): ...


# -----------------------------
# Optional OpenAI TTS
# -----------------------------
try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

_client = None

def _get_openai_client():
    global _client
    if _client is None:
        if OpenAI is None:
            return None
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print("[SPEECH][DEBUG] OpenAI client created with key:",
              os.getenv("OPENAI_API_KEY")[:8], "...")
    return _client

print("[SPEECH] OPENAI_API_KEY in env:", bool(os.getenv("OPENAI_API_KEY")))


# -----------------------------
# Minimal bus publisher (best-effort)
# -----------------------------
class _BusPub:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self._sock: Optional[socket.socket] = None

    def _ensure(self):
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.sendall(b"PUB\n")
        self._sock = s

    def publish(self, ev: dict):
        try:
            if not os.path.exists(self.sock_path):
                return
            self._ensure()
            line = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
            self._sock.sendall(line)
        except Exception:
            # bus yoksa / koparsa konuşmayı engellemeyelim
            try:
                if self._sock:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None


_bus = _BusPub(BUS_SOCKET)


# -----------------------------
# State
# -----------------------------
_state_lock = threading.Lock()
_is_speaking = False
_stop_flag = False
_tts_process: Optional[subprocess.Popen] = None


def speaking_now() -> bool:
    with _state_lock:
        return _is_speaking


def _set_speaking(v: bool):
    global _is_speaking
    with _state_lock:
        _is_speaking = v


def _touch_mic_lock():
    try:
        with open(MIC_LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _clear_mic_lock():
    try:
        if os.path.exists(MIC_LOCK_PATH):
            os.remove(MIC_LOCK_PATH)
    except Exception:
        pass


def _fallback_say(text: str) -> bool:
    """
    OpenAI yoksa: espeak-ng / espeak ile gerçek ses.
    """
    text = (text or "").strip()
    if not text:
        return False

    # Önce espeak-ng
    for cmd in (
        ["espeak-ng", "-v", "tr", "-s", "155", text],
        ["espeak", "-v", "tr", "-s", "155", text],
    ):
        try:
            subprocess.run(cmd, check=True)
            return True
        except Exception:
            continue

    # Son çare: sadece log
    print(f"[SPEECH][TTS:FALLBACK] {text}")
    return False


def stop_speaking():
    global _stop_flag, _tts_process
    _stop_flag = True
    _set_speaking(False)

    try:
        if _tts_process and _tts_process.poll() is None:
            _tts_process.terminate()
    except Exception:
        pass

    _clear_mic_lock()
    try:
        face_listening()
    except Exception:
        pass


def speak(text: str):
    """
    Senkron konuşur: bittiğinde geri döner.
    """
    global _stop_flag, _tts_process

    text = (text or "").strip()
    if not text:
        return

    _stop_flag = False
    _set_speaking(True)

    # mic'i kilitle + audio mic mute
    _touch_mic_lock()
    _bus.publish({"type": "TTS_START", "ts": time.time()})

    try:
        # yüz animasyonu
        try:
            face_thinking()
            anim_transition()
            face_speaking()
        except Exception:
            pass

        # ---------- OpenAI TTS ----------
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY missing")

        client = _get_openai_client()
        if client is not None:
            try:
                with client.audio.speech.with_streaming_response.create(
                    model="gpt-4o-mini-tts",
                    voice="verse",
                    input=text,
                    response_format="wav",
                ) as response:
                    response.stream_to_file(TTS_WAV_PATH)

                _tts_process = subprocess.Popen(["aplay", TTS_WAV_PATH])

                while _tts_process.poll() is None:
                    if _stop_flag:
                        try:
                            _tts_process.terminate()
                        except Exception:
                            pass
                        break
                    time.sleep(0.05)

                return
            except Exception as e:
                print("[SPEECH] TTS(OpenAI) error:", e)


        # ---------- Fallback (espeak) ----------
        _fallback_say(text)

    finally:
        _set_speaking(False)
        _clear_mic_lock()
        _bus.publish({"type": "TTS_END", "ts": time.time()})

        try:
            face_listening()
        except Exception:
            pass

        time.sleep(0.05)
