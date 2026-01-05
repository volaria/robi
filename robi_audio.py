#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import json
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, List

import webrtcvad
from vosk import Model, KaldiRecognizer  # type: ignore
from robi_constants import BUS_SOCKET, VOSK_EN_MODEL, VOSK_TR_MODEL
from robi_events import make_event

# -----------------------------
# Bus client
# -----------------------------
class BusClient:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.pub = self._connect(role="PUB")
        self.sub = self._connect(role="SUB")
        self._sub_buf = b""

    def _connect(self, role: str) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.sendall((role + "\n").encode())
        return s

    def publish(self, ev: dict):
        line = (json.dumps(ev, ensure_ascii=False) + "\n").encode()
        self.pub.sendall(line)

    def poll(self) -> Optional[dict]:
        self.sub.settimeout(0.0)
        try:
            chunk = self.sub.recv(4096)
            if not chunk:
                return None
            self._sub_buf += chunk
        except BlockingIOError:
            return None
        except Exception:
            return None
        finally:
            try:
                self.sub.settimeout(None)
            except Exception:
                pass

        if b"\n" not in self._sub_buf:
            return None
        line, self._sub_buf = self._sub_buf.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            return None


# -----------------------------
# Config
# -----------------------------
@dataclass
class AudioCfg:
    arecord_device: str
    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 20
    vad_mode: int = 1

    # wake
    wake_grammar: Optional[List[str]] = None
    wake_accept: Optional[List[str]] = None
    wake_cooldown: float = 1.2

    # listening
    listen_max_sec: float = 8.0
    end_silence_ms: int = 700
    min_speech_ms: int = 400

    debug: bool = False


def now_ts() -> float:
    return time.time()


# -----------------------------
# VAD segmenter (frame -> utterance)
# -----------------------------
class Segmenter:
    def __init__(self, cfg: AudioCfg, max_sec: float):
        self.cfg = cfg
        self.vad = webrtcvad.Vad(cfg.vad_mode)

        self.frame_bytes = int(cfg.sample_rate * (cfg.frame_ms / 1000.0) * 2)
        self.end_silence_frames = max(1, int(cfg.end_silence_ms / cfg.frame_ms))
        self.min_speech_frames = max(1, int(cfg.min_speech_ms / cfg.frame_ms))
        self.max_frames = max(1, int((max_sec * 1000) / cfg.frame_ms))

        self.reset()

    def reset(self):
        self.in_speech = False
        self.buf: List[bytes] = []
        self.sil = 0
        self.speech = 0

    def push(self, frame: bytes) -> Optional[bytes]:
        if len(frame) != self.frame_bytes:
            return None

        is_speech = self.vad.is_speech(frame, self.cfg.sample_rate)

        if not self.in_speech:
            if is_speech:
                self.in_speech = True
                self.buf = [frame]
                self.speech = 1
                self.sil = 0
            return None

        self.buf.append(frame)
        if is_speech:
            self.speech += 1
            self.sil = 0
        else:
            self.sil += 1

        if len(self.buf) >= self.max_frames:
            out = b"".join(self.buf)
            self.reset()
            return out

        if self.sil >= self.end_silence_frames:
            out = b"".join(self.buf) if self.speech >= self.min_speech_frames else None
            self.reset()
            return out

        return None


# -----------------------------
# Recognizers
# -----------------------------
class WakeRecognizer:
    def __init__(self, model: Model, cfg: AudioCfg):
        grammar = cfg.wake_grammar or ["robi", "roby", "robby", "rubi"]
        self.accept = cfg.wake_accept or ["robi", "roby", "robby", "rubi"]
        self.rec = KaldiRecognizer(model, cfg.sample_rate, json.dumps(grammar, ensure_ascii=False))
        self.rec.SetWords(True)

    def detect(self, utt: bytes) -> Optional[dict]:
        self.rec.Reset()
        for i in range(0, len(utt), 4000):
            self.rec.AcceptWaveform(utt[i:i + 4000])

        data = json.loads(self.rec.FinalResult() or "{}")
        text = (data.get("text") or "").strip().lower()
        if not text:
            return None
        if not any(t in text for t in self.accept):
            return None

        conf = None
        words = data.get("result")
        if isinstance(words, list) and words:
            confs = [w.get("conf") for w in words if isinstance(w, dict) and isinstance(w.get("conf"), (int, float))]
            if confs:
                conf = sum(confs) / len(confs)

        return {"heard": text, "confidence": conf}


class SttRecognizer:
    def __init__(self, model: Model, cfg: AudioCfg):
        self.rec = KaldiRecognizer(model, cfg.sample_rate)
        self.rec.SetWords(False)

    def transcribe(self, utt: bytes) -> str:
        self.rec.Reset()
        for i in range(0, len(utt), 4000):
            self.rec.AcceptWaveform(utt[i:i + 4000])

        data = json.loads(self.rec.FinalResult() or "{}")
        return (data.get("text") or "").strip()


# -----------------------------
# Main audio service (single mic owner)
# -----------------------------
class RobiAudio:
    STATE_IDLE = "IDLE"
    STATE_LISTENING = "LISTENING"

    def __init__(self, cfg: AudioCfg, wake_model_path: str, stt_model_path: str):
        self.cfg = cfg

        # 🔊 MODELLER (AYRI)
        self.wake_model = Model(wake_model_path)   # EN wake
        self.stt_model = Model(stt_model_path)     # TR STT

        self.wake = WakeRecognizer(self.wake_model, cfg)
        self.stt = SttRecognizer(self.stt_model, cfg)

        self.bus = BusClient(BUS_SOCKET)

        self.state = self.STATE_IDLE
        self.cooldown_until = 0.0
        self.listen_continuous = False

        self.seg_wake = Segmenter(cfg, max_sec=2.2)
        self.seg_listen = Segmenter(cfg, max_sec=cfg.listen_max_sec)

        self.frame_bytes = int(cfg.sample_rate * (cfg.frame_ms / 1000.0) * 2)
        self._arecord = None

    def _start_arecord(self):
        cmd = [
            "arecord",
            "-D", self.cfg.arecord_device,
            "-f", "S16_LE",
            "-r", str(self.cfg.sample_rate),
            "-c", str(self.cfg.channels),
            "-t", "raw",
            "--buffer-size=32768",
        ]
        if self.cfg.debug:
            print("[AUDIO] 🎙️ arecord:", " ".join(cmd))
        self._arecord = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self.frame_bytes * 50
        )

    def _stop_arecord(self):
        p = self._arecord
        self._arecord = None
        if not p:
            return
        try:
            p.terminate()
        except Exception:
            pass

    def _publish(self, typ: str, **payload):
        # 1) Legacy bus event (Brain/Core bunu bekliyor)
        ev = {"type": typ, "ts": now_ts()}
        ev.update(payload)
        self.bus.publish(ev)

        # 2) New unified event (sadece konuşma metni varsa)
        if typ == "UTTERANCE":
            text = payload.get("text", "")
            self.bus.publish(
                make_event(
                    type="speech.heard",
                    source="audio",
                    payload={"text": text, "lang": "tr"},
                )
            )

    def run(self):
        print("[AUDIO] 🎧 ROBI Audio online")
        print("[AUDIO]   device:", self.cfg.arecord_device)
        self._start_arecord()

        try:
            while True:
                ev = self.bus.poll()

                # Brain dinle dedi
                if ev and ev.get("type") == "LISTEN":
                    if self.cfg.debug:
                        print("[AUDIO] 🎧 Audio got LISTEN -> LISTENING")
                    self.state = self.STATE_LISTENING
                    self.listen_continuous = ev.get("mode") == "auto"
                    self.seg_listen.reset()
                    self.seg_wake.reset()  # ⛔️ wake buffer tamamen sıfırlansın
                    self._listen_started_at = now_ts()
                    continue

                # TTS başladıysa mic sustur ama STATE DEĞİŞTİRME
                if ev and ev.get("type") == "TTS_START":
                    if self.cfg.debug:
                        print("[AUDIO] 🔇 Audio got TTS_START (mic muted)")
                    self.seg_wake.reset()
                    continue

                # Brain iş bitti dedi
                if ev and ev.get("type") == "DONE":
                    if self.cfg.debug:
                        print("[AUDIO] 🟦 Audio got DONE -> IDLE")
                    self.cooldown_until = now_ts() + 3
                    self.state = self.STATE_IDLE
                    self.seg_wake.reset()
                    self.seg_listen.reset()
                    continue

                # 🔇 TTS sırasında mic tamamen kapalı: kendi sesini dinleme
                if os.path.exists("/tmp/robi_mic.lock"):
                    self.seg_wake.reset()
                    self.seg_listen.reset()
                    continue

                data = self._arecord.stdout.read(self.frame_bytes)
                if not data or len(data) != self.frame_bytes:
                    continue

                # -------- IDLE: Wake bekle --------
                if self.state == self.STATE_IDLE:
                    if now_ts() < self.cooldown_until:
                        continue

                    utt = self.seg_wake.push(data)
                    if not utt:
                        continue

                    hit = self.wake.detect(utt)
                    if hit:
                        # ⛔️ cooldown süresince WAKE BASMA
                        if now_ts() < self.cooldown_until:
                            continue

                        if self.cfg.debug:
                            print("[AUDIO] ✅ WAKE", hit)

                        self._publish("WAKE", heard=hit["heard"], confidence=hit["confidence"])
                        self.cooldown_until = now_ts() + self.cfg.wake_cooldown

                        # ⚠️ Burada otomatik LISTENING'e GEÇMİYORUZ.
                        # Brain "Efendim" deyip sonra LISTEN yollayacak.
                        self.cooldown_until = now_ts() + self.cfg.wake_cooldown

                # -------- LISTENING: STT --------
                elif self.state == self.STATE_LISTENING:
                    utt = self.seg_listen.push(data)
                    if not utt:
                        # ✅ TIMEOUT kontrolü (hiç konuşma gelmediyse)
                        if now_ts() - getattr(self, "_listen_started_at", now_ts()) >= self.cfg.listen_max_sec:
                            if self.cfg.debug:
                                print("[AUDIO] ⏱️ LISTEN timeout -> publish TIMEOUT")
                            self._publish("TIMEOUT")
                            if self.listen_continuous:
                                self._listen_started_at = now_ts()
                                self.seg_listen.reset()
                            else:
                                self.cooldown_until = now_ts() + 0.8
                                self.state = self.STATE_IDLE
                                self.seg_wake.reset()
                                self.seg_listen.reset()
                        continue

                    # 🔒 text HER ZAMAN tanımlı
                    text = ""

                    try:
                        text = self.stt.transcribe(utt) or ""
                        print("[AUDIO][STT]", repr(text))
                        if self.cfg.debug:
                            print("[AUDIO] 🗣 STT:", repr(text))
                    except Exception as e:
                        print("[AUDIO][ERR] STT failed:", e)

                    # ✅ UTTERANCE'ı mutlaka publish et (boş bile olsa)
                    self._publish("UTTERANCE", text=text)

                    if self.listen_continuous:
                        self._listen_started_at = now_ts()
                        self.seg_listen.reset()
                    else:
                        # ✅ tek seferlik dinleme bitti: tekrar WAKE moduna dön
                        self.cooldown_until = now_ts() + 0.8
                        self.state = self.STATE_IDLE
                        self.seg_wake.reset()
                        self.seg_listen.reset()
                    continue

        finally:
            self._stop_arecord()
            print("[AUDIO] \n🎧 ROBI Audio offline")


# -----------------------------
# CLI555
# -----------------------------
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wake-model", default=str(VOSK_EN_MODEL), help="Vosk model folder for wake-word (EN)")
    ap.add_argument("--stt-model", default=str(VOSK_TR_MODEL), help="Vosk model folder for STT (TR)")
    ap.add_argument(
        "--device",
        default="plughw:CARD=sndrpigooglevoi,DEV=0",
        help="arecord -D device"
    )
    ap.add_argument("--debug", action="store_true")
    return ap.parse_args()

def main():
    try:
        args = parse_args()
        cfg = AudioCfg(
            arecord_device=args.device,
            debug=args.debug,
            wake_grammar=["robi", "roby", "robby", "rubi"],
            wake_accept=["robi", "roby", "robby", "rubi"],
        )
        RobiAudio(
            cfg,
            wake_model_path=args.wake_model,
            stt_model_path=args.stt_model
        ).run()

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("[AUDIO][ERR]", e)

if __name__ == "__main__":
    main()