#!/usr/bin/env python3..
# -*- coding: utf-8 -*-
"""
ROBI Wake Word Service (stable, wake-only)
- Runs on Raspberry Pi
- Audio-only process (separate from perception/brain)
- Uses:
  - sounddevice (ALSA) for mic stream
  - webrtcvad for speech segmentation (VAD)
  - Vosk with GRAMMAR for robust "Robi" detection (no full-STT)

Writes events to JSONL:
  /tmp/robi_events.jsonl

Event example:
  {"type":"WAKE_WORD","word":"robi","confidence":0.86,"_ts":1730000000.123}

Install:
  pip install sounddevice webrtcvad vosk

Vosk model:
  Download and unzip a model folder, e.g.
   - vosk-model-small-tr-0.3
   - vosk-model-small-en-us-0.15
  Then pass --model /path/to/model

Run:
  python3 robi_wake.py --model /home/pi/vosk-model-small-tr-0.3

Tips:
  - Use `python3 robi_wake.py --list-devices` to find correct mic device index/name
  - If wake triggers too easily, increase --vad-mode or tighten --grammar / --cooldown
"""

from __future__ import annotations

import audioop
import subprocess
import argparse
import json
import os
import queue
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional, List

import sounddevice as sd
import webrtcvad
from vosk import Model, KaldiRecognizer  # type: ignore
import socket, json, time

MIC_LOCK_PATH = "/tmp/robi_mic.lock"

EVENTBUS_SOCK = "/tmp/robi_eventbus.sock"

def send_event(event: dict):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(EVENTBUS_SOCK)
        s.sendall((json.dumps(event) + "\n").encode())
        s.close()
    except Exception as e:
        print("[WAKE] ❌ EventBus send failed:", e)


DEFAULT_EVENTS_PATH = "/tmp/robi_events.jsonl"

# -----------------------------
# Utilities
# -----------------------------
def now_ts() -> float:
    return time.time()


def write_jsonl(path: str, obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


# -----------------------------
# Wake configuration
# -----------------------------
@dataclass
class WakeConfig:
    sample_rate: int = 16000
    arecord_device: str = "plughw:CARD=sndrpigooglevoi,DEV=0"
    channels: int = 1
    frame_ms: int = 20               # 10/20/30 ms supported by webrtcvad
    vad_mode: int = 0                # 0..3 (3 = most aggressive / less false positives)
    max_utterance_sec: float = 2.2   # hard cap for a single utterance
    min_speech_ms: int = 200         # ignore ultra-short noises
    end_silence_ms: int = 350        # consider speech ended after this much silence
    pre_roll_ms: int = 200           # keep a little audio before speech start
    cooldown_sec: float = 1.2        # ignore new wake for a moment after a trigger

    events_path: str = DEFAULT_EVENTS_PATH
    device: Optional[str] = None     # device name or index as string for sounddevice

    # Grammar: limit recognition to wake word variants.
    # Vosk "grammar" expects JSON array of phrases.
    grammar_phrases: List[str] = None  # set in main if None

    # Confidence heuristic thresholds (since small models can be noisy):
    accept_if_contains: List[str] = None  # tokens to accept if detected in result text
    debug: bool = False
    beep_on_wake: bool = False


# -----------------------------
# VAD-based segmenter
# -----------------------------
class SpeechSegmenter:
    def __init__(self, cfg: WakeConfig):
        self.cfg = cfg
        self.vad = webrtcvad.Vad(clamp(cfg.vad_mode, 0, 3))

        self.frame_bytes = int(cfg.sample_rate * (cfg.frame_ms / 1000.0) * 2)  # int16 mono => 2 bytes
        if cfg.frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20, or 30 for webrtcvad")

        self.pre_roll_frames = max(1, int(cfg.pre_roll_ms / cfg.frame_ms))
        self.end_silence_frames = max(1, int(cfg.end_silence_ms / cfg.frame_ms))
        self.min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
        self.max_frames = max(1, int(cfg.max_utterance_sec * 1000 / cfg.frame_ms))

        self._pre_roll = []  # list[bytes]
        self._in_speech = False
        self._speech_frames = []
        self._silence_count = 0
        self._speech_count = 0

    def reset(self) -> None:
        self._pre_roll.clear()
        self._in_speech = False
        self._speech_frames.clear()
        self._silence_count = 0
        self._speech_count = 0

    def push_frame(self, frame: bytes) -> Optional[bytes]:
        """
        Feed one frame. Returns a complete utterance (bytes) when speech ends, else None.
        """
        if len(frame) != self.frame_bytes:
            # Drop malformed frames
            return None

        is_speech = self.vad.is_speech(frame, self.cfg.sample_rate)

        # Maintain pre-roll buffer always
        self._pre_roll.append(frame)
        if len(self._pre_roll) > self.pre_roll_frames:
            self._pre_roll.pop(0)

        if not self._in_speech:
            if is_speech:
                # start speech
                self._in_speech = True
                self._speech_frames = list(self._pre_roll)  # include pre-roll
                self._silence_count = 0
                self._speech_count = 1
            return None

        # in speech
        self._speech_frames.append(frame)
        if is_speech:
            self._speech_count += 1
            self._silence_count = 0
        else:
            self._silence_count += 1

        # cap utterance length
        if len(self._speech_frames) >= self.max_frames:
            utt = b"".join(self._speech_frames)
            self.reset()
            return utt

        # end speech if enough trailing silence
        if self._silence_count >= self.end_silence_frames:
            if self._speech_count >= self.min_speech_frames:
                utt = b"".join(self._speech_frames)
            else:
                utt = None
            self.reset()
            return utt

        return None


# -----------------------------
# Wake detector (Vosk grammar)
# -----------------------------
class WakeDetector:
    def __init__(self, model_path: str, cfg: WakeConfig):
        self.cfg = cfg
        self.model = Model(model_path)

        grammar = cfg.grammar_phrases or ["robi"]
        grammar_json = json.dumps(grammar, ensure_ascii=False)

        self.rec = KaldiRecognizer(self.model, cfg.sample_rate, grammar_json)
        self.rec.SetWords(True)

    def detect(self, audio_bytes: bytes) -> Optional[dict]:
        """
        Returns dict with detection details if wake found.
        """
        self.rec.Reset()
        # Feed in chunks to recognizer
        chunk = 4000
        for i in range(0, len(audio_bytes), chunk):
            self.rec.AcceptWaveform(audio_bytes[i:i + chunk])

        result = self.rec.FinalResult()
        try:
            data = json.loads(result)
        except Exception:
            return None

        text = (data.get("text") or "").strip().lower()
        if self.cfg.debug:
            print(f"[VOSK] text='{text}' raw={data}")

        if not text:
            return None

        # Heuristic: accept if any target token appears in recognized text
        tokens = self.cfg.accept_if_contains or ["robi", "roby", "robby", "rubi"]
        hit = any(t in text for t in tokens)
        if not hit:
            return None

        # Confidence: Vosk sometimes provides 'result' word list with conf
        conf = None
        words = data.get("result")
        if isinstance(words, list) and words:
            # average confidence of returned words
            confs = [w.get("conf") for w in words if isinstance(w, dict) and isinstance(w.get("conf"), (int, float))]
            if confs:
                conf = sum(confs) / len(confs)

        return {
            "type": "WAKE_WORD",
            "word": "robi",
            "heard": text,
            "confidence": conf,
            "_ts": now_ts(),
        }


# -----------------------------
# Audio stream runner
# -----------------------------
class WakeService:
    def __init__(self, cfg: WakeConfig, model_path: str):
        self.cfg = cfg
        self.model_path = model_path

        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=200)
        self._stop = False
        self._cooldown_until = 0.0

        self.segmenter = SpeechSegmenter(cfg)
        self.detector = WakeDetector(model_path, cfg)

    def stop(self):
        self._stop = True

    def _audio_cb(self, indata, frames, time_info, status):
        if status and self.cfg.debug:
            print("[AUDIO STATUS]", status, file=sys.stderr)

        # indata is bytes in RawInputStream
        try:
            self._q.put_nowait(bytes(indata))
        except queue.Full:
            # drop if overloaded
            pass

    def run(self):
        # Make sure events path dir exists
        os.makedirs(os.path.dirname(self.cfg.events_path), exist_ok=True)

        # Print header
        print("🤖 [WAKE] ROBI Wake | online")
        print(f"🔊 [WAKE] device={self.cfg.device or 'default'} sr={self.cfg.sample_rate} frame={self.cfg.frame_ms}ms vad={self.cfg.vad_mode}")
        print(f"🧠 [WAKE] vosk_model={self.model_path}")
        print(f"📝 [WAKE] events={self.cfg.events_path}")
        if self.cfg.debug:
            print(f"[WAKE] 🧪 grammar={self.cfg.grammar_phrases} accept_tokens={self.cfg.accept_if_contains}")

        # arecord üzerinden RAW PCM okuyacağız (eski çalışan yöntem)
        frame_bytes = int(self.cfg.sample_rate * (self.cfg.frame_ms / 1000.0) * 2)  # int16 mono
        cmd = [
            "arecord",
            "-D", self.cfg.arecord_device,
            "-f", "S16_LE",
            "-r", str(self.cfg.sample_rate),
            "-c", str(self.cfg.channels),
            "-t", "raw",
        ]

        if self.cfg.debug:
            print("[WAKE] 🎙️ arecord cmd:", " ".join(cmd))

        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=frame_bytes * 50,
        )

        try:
            while not self._stop:
                data = p.stdout.read(frame_bytes)
                data = audioop.mul(data, 2, 2.5)  # 2 byte sample, gain x2.5

                # print("audio frame", len(data))

                if not data or len(data) != frame_bytes:
                    continue

                # 🔇 Brain konuşuyor/dinliyor → wake durmalı
                if os.path.exists(MIC_LOCK_PATH):
                    if self.cfg.debug:
                        print("[WAKE] 🔇 MIC locked by brain, wake paused")
                    continue

                utt = self.segmenter.push_frame(data)
                if utt is None:
                    continue

                # cooldown
                t = now_ts()
                if t < self._cooldown_until:
                    continue

                det = self.detector.detect(utt)
                if det:
                    print("[WAKE] ✅ WAKE:", det["heard"], det["confidence"])

                    write_jsonl(self.cfg.events_path, det)

                    send_event({
                        "type": "WAKE_WORD",
                        "source": "wake",
                        "payload": {
                            "word": det["word"],
                            "confidence": det["confidence"],
                            "heard": det["heard"],
                        },
                        "_ts": det["_ts"],
                    })

                    self._cooldown_until = now_ts() + self.cfg.cooldown_sec

                    if self.cfg.beep_on_wake:
                        sys.stdout.write("\a")
                        sys.stdout.flush()
        finally:
            try:
                p.terminate()
            except Exception:
                pass

            while not self._stop:
                try:
                    frame = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue

                utt = self.segmenter.push_frame(frame)
                if utt is None:
                    continue

                # cooldown
                t = now_ts()
                if t < self._cooldown_until:
                    continue

                det = self.detector.detect(utt)
                if det:
                    write_jsonl(self.cfg.events_path, det)
                    if self.cfg.debug:
                        print("[WAKE] ✅ WAKE:", det)

                    self._cooldown_until = now_ts() + self.cfg.cooldown_sec

                    if self.cfg.beep_on_wake:
                        # simple terminal bell (works on some setups)
                        sys.stdout.write("\a")
                        sys.stdout.flush()


# -----------------------------
# CLI
# -----------------------------
def list_devices() -> None:
    print(sd.query_devices())


def parse_args():
    p = argparse.ArgumentParser(description="ROBI Wake Word Service (VAD + Vosk grammar)")

    p.add_argument("--model", required=False, default=os.environ.get("ROBI_VOSK_MODEL"),
                   help="Path to Vosk model folder (or set ROBI_VOSK_MODEL)")
    p.add_argument("--events", default=DEFAULT_EVENTS_PATH, help="JSONL event bus path")
    p.add_argument("--device", default=None, help="Sounddevice input device (index or name). Use --list-devices")
    p.add_argument("--list-devices", action="store_true", help="List audio devices and exit")

    p.add_argument("--sr", type=int, default=16000, help="Sample rate (default 16000)")
    p.add_argument("--frame-ms", type=int, default=20, choices=[10, 20, 30], help="Frame size for VAD (10/20/30)")
    p.add_argument("--vad-mode", type=int, default=2, choices=[0, 1, 2, 3], help="webrtcvad aggressiveness")

    p.add_argument("--max-sec", type=float, default=2.2, help="Max utterance seconds")
    p.add_argument("--min-speech-ms", type=int, default=200, help="Ignore speech shorter than this")
    p.add_argument("--end-silence-ms", type=int, default=350, help="Speech end silence threshold")
    p.add_argument("--pre-roll-ms", type=int, default=200, help="Pre-roll to include before speech start")
    p.add_argument("--cooldown", type=float, default=1.2, help="Cooldown after wake trigger")

    p.add_argument("--grammar", default="robi,roby,robby,rubi",
                   help="Comma-separated grammar phrases for Vosk (wake variants)")
    p.add_argument("--accept", default="robi,roby,robby,rubi",
                   help="Comma-separated tokens; if any appears in recognized text => wake")

    p.add_argument("--debug", action="store_true", help="Verbose logging")
    p.add_argument("--beep", action="store_true", help="Beep on wake trigger")

    return p.parse_args()


def main():
    args = parse_args()

    if args.list_devices:
        list_devices()
        return 0

    if not args.model:
        print("[WAKE] ❌ Missing --model (or ROBI_VOSK_MODEL env).", file=sys.stderr)
        return 2

    cfg = WakeConfig(
        sample_rate=args.sr,
        frame_ms=args.frame_ms,
        vad_mode=args.vad_mode,
        max_utterance_sec=args.max_sec,
        min_speech_ms=args.min_speech_ms,
        end_silence_ms=args.end_silence_ms,
        pre_roll_ms=args.pre_roll_ms,
        cooldown_sec=args.cooldown,
        events_path=args.events,
        device=args.device,
        grammar_phrases=[s.strip() for s in args.grammar.split(",") if s.strip()],
        accept_if_contains=[s.strip() for s in args.accept.split(",") if s.strip()],
        debug=args.debug,
        beep_on_wake=args.beep,
    )

    svc = WakeService(cfg, model_path=args.model)

    def _sig_handler(signum, frame):
        svc.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        svc.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        # Crash-safe log to event bus (so brain can see wake service died)
        write_jsonl(cfg.events_path, {"type": "WAKE_SERVICE_ERROR", "error": str(e), "_ts": now_ts()})
        raise
    finally:
        print("[WAKE] \n👋 ROBI Wake | offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
