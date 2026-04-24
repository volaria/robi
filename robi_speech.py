"""
robi_speech.py — ROBI TTS Servisi
En büyük hız kazanımı burada:
  OpenAI TTS PCM formatında stream eder → doğrudan aplay pipe'ına yazar.
  Dosya yazma/okuma yoktur → ilk ses ~500ms içinde çıkar.

speak(text)       — senkron konuşur (thread içinde çağırılmalı)
stop_speaking()   — o anki konuşmayı keser
speaking_now()    — True/False
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional

from openai import OpenAI

from config import (
    AUDIO_PLAYBACK_DEVICE, BUS_SOCKET,
    TTS_FORMAT, TTS_MODEL, TTS_SAMPLE_RATE, TTS_VOICE,
)
from robi_bus import BusClient

# ─── Konuşma bitti sinyali ────────────────────────────────────────────────────

def play_end_chime() -> None:
    """
    ROBI konuşmasını bitirince kısa 'tık' sesi çalar ve LED'i flaşlatır.
    ~180ms sürer, bloklar (speak() içinden çağrılır).
    """
    import numpy as _np

    # 180ms, 880Hz (A5) — fade-in/out ile yumuşatılmış
    _sr       = TTS_SAMPLE_RATE   # 24000 Hz
    _dur      = 0.18
    _freq     = 880
    _vol      = 0.25              # 0..1 arası ses seviyesi
    t         = _np.linspace(0, _dur, int(_sr * _dur), endpoint=False)
    wave      = _np.sin(2 * _np.pi * _freq * t) * _vol
    fade      = int(_sr * 0.025)  # 25ms fade-in/out
    wave[:fade]  *= _np.linspace(0, 1, fade)
    wave[-fade:] *= _np.linspace(1, 0, fade)
    pcm = (wave * 32767).astype(_np.int16).tobytes()

    # Ses çal
    try:
        p = subprocess.Popen(
            ["aplay", "-f", "S16_LE", "-r", str(_sr), "-c", "1",
             "-D", AUDIO_PLAYBACK_DEVICE, "-"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        p.stdin.write(pcm)
        p.stdin.close()
        p.wait(timeout=1)
    except Exception:
        pass

    # LED flash (opsiyonel — display yoksa sessizce geç)
    try:
        import robi_display as _disp
        _disp.done()
    except Exception:
        pass


# ─── State ────────────────────────────────────────────────────────────────────

_lock      = threading.Lock()
_speaking  = False
_stop_flag = False
_proc: Optional[subprocess.Popen] = None

# ─── Bus yayıncısı (best-effort) ──────────────────────────────────────────────
# Speech modülü brain ile aynı process'te çalışabileceğinden
# bus bağlantısı tembel (lazy) açılır.

_bus: Optional[BusClient] = None
_bus_lock = threading.Lock()


def _get_bus() -> Optional[BusClient]:
    global _bus
    with _bus_lock:
        if _bus is None:
            try:
                _bus = BusClient(BUS_SOCKET)
            except Exception:
                pass
    return _bus


def _pub(ev: dict) -> None:
    b = _get_bus()
    if b:
        try:
            b.publish(ev)
        except Exception:
            pass


# ─── OpenAI istemcisi ─────────────────────────────────────────────────────────

_oai: Optional[OpenAI] = None
_oai_lock = threading.Lock()


def _get_oai() -> OpenAI:
    global _oai
    with _oai_lock:
        if _oai is None:
            _oai = OpenAI()
    return _oai


# ─── Public API ───────────────────────────────────────────────────────────────

def speaking_now() -> bool:
    with _lock:
        return _speaking


def stop_speaking() -> None:
    global _stop_flag, _proc
    _stop_flag = True
    with _lock:
        p = _proc
    if p and p.poll() is None:
        try: p.stdin.close()
        except Exception: pass
        try: p.terminate()
        except Exception: pass


def speak(text: str) -> None:
    """
    Senkron TTS. Bitene kadar bloklar.
    Brain içinde her zaman ayrı bir thread'den çağırılmalı.
    """
    global _speaking, _stop_flag, _proc

    text = (text or "").strip()
    if not text:
        return

    with _lock:
        if _speaking:
            return           # başka bir konuşma sürüyorsa atla
        _speaking  = True
        _stop_flag = False

    _pub({"type": "TTS_START", "ts": time.time()})

    try:
        # aplay: stdin'den raw PCM oku → hoparlöre çal
        aplay_cmd = [
            "aplay",
            "-f", "S16_LE",
            "-r", str(TTS_SAMPLE_RATE),
            "-c", "1",
            "-D", AUDIO_PLAYBACK_DEVICE,
            "-",             # stdin'den oku
        ]
        proc = subprocess.Popen(
            aplay_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        with _lock:
            _proc = proc

        client = _get_oai()

        with client.audio.speech.with_streaming_response.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            response_format=TTS_FORMAT,   # "pcm" → raw 24kHz 16-bit mono
        ) as response:
            for chunk in response.iter_bytes(chunk_size=4096):
                if _stop_flag:
                    break
                try:
                    proc.stdin.write(chunk)
                except BrokenPipeError:
                    break

        try:
            proc.stdin.close()
        except Exception:
            pass

        proc.wait()

    except Exception as e:
        print(f"[SPEECH] ⚠ TTS hatası: {e}")
        # Fallback: espeak
        _fallback(text)

    finally:
        with _lock:
            _speaking  = False
            _stop_flag = False
            _proc      = None
        _pub({"type": "TTS_END", "ts": time.time()})


def _fallback(text: str) -> None:
    """OpenAI çalışmazsa espeak ile konuş."""
    for cmd in (
        ["espeak-ng", "-v", "tr", "-s", "145", text],
        ["espeak",    "-v", "tr", "-s", "145", text],
    ):
        try:
            subprocess.run(cmd, check=True, timeout=15)
            return
        except Exception:
            continue
    print(f"[SPEECH][FALLBACK] {text}")
