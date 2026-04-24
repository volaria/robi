"""
robi_audio.py — ROBI Ses Servisi
Tek mikrofon sahibi. Wake word tespiti ve STT.

Self-listening fix:
  TTS_START gelince arecord tamamen durdurulur.
  TTS_END gelince TTS_RESUME_DELAY sonra arecord temiz başlatılır.
  Böylece aplay buffer'ında ROBI'nin kendi sesi kesinlikle kalmaz.

Bus'a yayılan eventler:
  WAKE      — wake word tespit edildi
  UTTERANCE — kullanıcı konuşması transkribe edildi {text, confidence}
  TIMEOUT   — dinleme süresi doldu, ses gelmedi

Bus'tan dinlenen eventler:
  LISTEN    — Brain: dinlemeye geç {mode: "once"|"auto"}
  TTS_START — TTS başladı, mic'i kapat
  TTS_END   — TTS bitti, mic'i aç
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

import webrtcvad
from vosk import KaldiRecognizer, Model

from config import (
    AUDIO_CAPTURE_DEVICE, AUDIO_INPUT_CHANNELS, AUDIO_INPUT_FORMAT,
    CHANNELS, FRAME_MS, LISTEN_END_SIL, LISTEN_MAX_SEC,
    LISTEN_MIN_SPK, SAMPLE_RATE, TTS_RESUME_DELAY, VAD_MODE,
    VOSK_WAKE_MODEL, WAKE_ACCEPT, WAKE_COOLDOWN,
    WAKE_GRAMMAR, WAKE_MAX_SEC, WAKE_VAD_MODE, WAKE_RMS_THRESHOLD,
    BUS_SOCKET,
    WHISPER_MIN_RMS, WHISPER_MIN_RMS_QUIET, WHISPER_MIN_RMS_MUSIC,
    WHISPER_HALLUCINATIONS, WHISPER_HALLUCINATION_FRAGMENTS,
    WHISPER_HALLUCINATION_FRAGMENTS_LONG,
)
from robi_bus import BusClient


# ─── VAD Segmenter ────────────────────────────────────────────────────────────

class Segmenter:
    """VAD tabanlı ses parçacığı ayırıcı. Frame → utterance.

    vad_mode: webrtcvad hassasiyet (0=en geniş, 3=en katı)
      Wake word için 1 kullan — uyku sonrası kullanıcı sesi kaçmasın.
      STT dinleme için 2 kullan — hışırtı/gürültü azaltsın.
    """

    def __init__(self, max_sec: float, end_sil_ms: int = LISTEN_END_SIL,
                 min_spk_ms: int = LISTEN_MIN_SPK, vad_mode: int = VAD_MODE):
        self.vad         = webrtcvad.Vad(vad_mode)
        self.frame_bytes = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * 2)
        self.max_frames  = max(1, int(max_sec * 1000 / FRAME_MS))
        self.end_sil_f   = max(1, int(end_sil_ms / FRAME_MS))
        self.min_spk_f   = max(1, int(min_spk_ms / FRAME_MS))
        self._reset()

    def _reset(self):
        self._buf: List[bytes] = []
        self._in   = False
        self._sil  = 0
        self._spk  = 0

    def reset(self):
        self._reset()

    def push(self, frame: bytes, force_speech: bool = False) -> Optional[bytes]:
        """frame: S16_LE mono PCM. force_speech: RMS yüksekse VAD'ı bypass et."""
        if len(frame) != self.frame_bytes:
            return None

        # VAD kararı — force_speech ile RMS fallback desteklenir
        is_speech = self.vad.is_speech(frame, SAMPLE_RATE) or force_speech

        if not self._in:
            if is_speech:
                self._in  = True
                self._buf = [frame]
                self._spk = 1
                self._sil = 0
            return None

        self._buf.append(frame)
        if is_speech:
            self._spk += 1
            self._sil  = 0
        else:
            self._sil += 1

        if len(self._buf) >= self.max_frames:
            out = b"".join(self._buf)
            self._reset()
            return out

        if self._sil >= self.end_sil_f:
            out = b"".join(self._buf) if self._spk >= self.min_spk_f else None
            self._reset()
            return out

        return None


# ─── Vosk tanıyıcılar ─────────────────────────────────────────────────────────

class WakeRecognizer:
    def __init__(self, model: Model):
        grammar = json.dumps(WAKE_GRAMMAR, ensure_ascii=False)
        self.rec = KaldiRecognizer(model, SAMPLE_RATE, grammar)
        self.rec.SetWords(True)

    def detect(self, utt: bytes) -> Optional[dict]:
        self.rec.Reset()
        for i in range(0, len(utt), 4000):
            self.rec.AcceptWaveform(utt[i:i + 4000])

        data = json.loads(self.rec.FinalResult() or "{}")
        text = (data.get("text") or "").strip().lower()

        # Vosk bir şey duydu ama wake word değilse teşhis için logla
        if text and not any(t in text for t in WAKE_ACCEPT):
            print(f"[AUDIO] 👂 IDLE-duydu (wake değil): '{text}'")

        if not text or not any(t in text for t in WAKE_ACCEPT):
            return None

        conf = None
        words = data.get("result")
        if isinstance(words, list) and words:
            cs = [w.get("conf") for w in words
                  if isinstance(w, dict) and isinstance(w.get("conf"), (int, float))]
            conf = sum(cs) / len(cs) if cs else None

        return {"heard": text, "confidence": conf}


class WhisperSttRecognizer:
    """
    OpenAI Whisper API ile STT.
    Vosk'tan çok daha doğru, Türkçe için mükemmel.
    ~1 saniyelik API gecikmesi var ama karşılığı değer.
    """

    def __init__(self):
        from openai import OpenAI as _OAI
        self._client = _OAI(timeout=8.0)   # 8s'de cevap gelmezse hata ver

    def transcribe(self, pcm_bytes: bytes, min_rms: int = WHISPER_MIN_RMS) -> dict:
        import io, wave

        # ── 1. RMS enerji kontrolü ────────────────────────────────────────────
        # Ses yeterince güçlü değilse Whisper'a hiç gönderme — hem API tasarrufu
        # hem de halüsinasyon önlemi.
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(arr ** 2))) if len(arr) > 0 else 0.0
        if rms < min_rms:
            print(f"[AUDIO] ⬇ RMS={rms:.0f} < {min_rms} → Whisper atlandı")
            return {"text": "", "confidence": None}

        # ── 2. Raw PCM → WAV container ────────────────────────────────────────
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)           # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_bytes)
        buf.seek(0)
        buf.name = "audio.wav"           # OpenAI dosya adı bekliyor

        try:
            result = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=buf,
                language="tr",
                response_format="text",
            )
            text = (result or "").strip()

            # ── 3. Bilinen halüsinasyonları filtrele ──────────────────────────
            text_lower = text.lower()
            is_hallucination = text_lower in WHISPER_HALLUCINATIONS
            # Kısa metinlerde genel fragment kontrolü (<=8 kelime)
            if not is_hallucination and len(text_lower.split()) <= 8:
                is_hallucination = any(f in text_lower for f in WHISPER_HALLUCINATION_FRAGMENTS)
            # YouTube kapanış kalıpları: kelime sayısına bakılmaksızın her zaman filtrele
            if not is_hallucination:
                is_hallucination = any(f in text_lower for f in WHISPER_HALLUCINATION_FRAGMENTS_LONG)
            if is_hallucination:
                print(f"[AUDIO] 🚫 Whisper halüsinasyon filtrelendi: {repr(text)}")
                return {"text": "", "confidence": None}

            return {"text": text, "confidence": 1.0}
        except Exception as e:
            print(f"[AUDIO] ⚠ Whisper hatası: {e}")
            return {"text": "", "confidence": None}


# ─── Ana servis ───────────────────────────────────────────────────────────────

# Durumlar
IDLE      = "IDLE"       # wake word bekleniyor
LISTENING = "LISTENING"  # kullanıcı konuşması bekleniyor (STT)
MUTED     = "MUTED"      # TTS çalıyor, mic tamamen kapalı


class RobiAudio:

    def __init__(self):
        print("[AUDIO] Vosk wake modeli yükleniyor...")
        self.wake_model = Model(VOSK_WAKE_MODEL)
        print("[AUDIO] Whisper STT hazırlanıyor...")
        self.wake_rec   = WakeRecognizer(self.wake_model)
        self.stt_rec    = WhisperSttRecognizer()
        print("[AUDIO] Hazır. (Wake: Vosk EN | STT: Whisper TR)")

        # Wake word segmenter: kısa kelimeler için hassas ayar.
        # LISTEN_MIN_SPK (400ms) → "Robi" gibi ~300ms'lik kelimeleri filtreler!
        # Wake için: min 120ms konuşma ve 300ms sessizlik yeterli.
        # WAKE_VAD_MODE=1: uyku sonrası sesi kaçırmamak için daha toleranslı VAD.
        self.seg_wake   = Segmenter(max_sec=WAKE_MAX_SEC,
                                    end_sil_ms=300,
                                    min_spk_ms=120,
                                    vad_mode=WAKE_VAD_MODE)
        self.seg_listen = Segmenter(max_sec=LISTEN_MAX_SEC,
                                    vad_mode=VAD_MODE)

        # frame_bytes: VAD/Segmenter için S16_LE mono frame boyutu (640 byte @ 16kHz 20ms)
        self.frame_bytes     = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * 2)
        # frame_bytes_raw: arecord'dan okunacak ham S32_LE stereo frame boyutu (2560 byte)
        self.frame_bytes_raw = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * 4 * AUDIO_INPUT_CHANNELS)
        self._proc: Optional[subprocess.Popen] = None

        self.state           = IDLE
        self.listen_mode     = "once"   # "once" | "auto"
        self._listen_at      = 0.0
        self._wake_cd_until  = 0.0
        self._muted_next     = IDLE     # TTS bitince geçilecek durum
        self._low_threshold  = False    # Müzik pauselanmışken düşük RMS eşiği
        self._music_mode     = False    # Müzik çalarken yüksek RMS eşiği

        # IDLE diagnostic sayaçlar
        self._idle_frames       = 0     # IDLE'da okunan toplam frame
        self._idle_speech_f     = 0     # VAD/RMS speech olarak işaretlenen frame
        self._idle_diag_next    = 0.0   # sonraki diagnostic log zamanı
        self._idle_last_frame_t = 0.0   # IDLE'da son başarılı frame zamanı (watchdog)
        self._muted_since       = 0.0   # MUTED'a girilen zaman (watchdog)
        self._warmup_until      = 0.0   # bu zamana kadar IDLE frame'lerini atla (startup pop önlemi)

        self.bus = BusClient(BUS_SOCKET)

    # ── arecord yönetimi ──────────────────────────────────────────────────────

    def _start_arecord(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        # Taze arecord başlıyor — ilk 500ms atla (donanım init gürültüsü)
        self._warmup_until = time.time() + 0.5
        cmd = [
            "arecord",
            "-D", AUDIO_CAPTURE_DEVICE,
            "-f", AUDIO_INPUT_FORMAT,          # S32_LE (INMP441 ham çıkışı)
            "-r", str(SAMPLE_RATE),
            "-c", str(AUDIO_INPUT_CHANNELS),   # 2 kanal (stereo I2S)
            "-t", "raw",
            "--buffer-size=4096",   # küçük buffer → düşük gecikme
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self.frame_bytes_raw * 4,
        )

    def _convert_raw(self, raw: bytes) -> bytes:
        """S32_LE stereo → S16_LE mono, yazılım kazancı.

        INMP441, I2S üzerinden 32-bit stereo veri üretir.
        Sol kanalı alıp int16 aralığına ölçekliyoruz (/ 2^16),
        ardından yazılım kazancı uyguluyoruz.

        SW_GAIN_DB: ortama göre ayarla.
          25 dB → RMS ~3000-8000 (normal)
          35 dB → RMS düşükse (reboot sonrası DMIC gain sıfırlandıysa)
          40 dB → mikrofon uzakta veya çok sessiz ortam
        """
        SW_GAIN_DB = 30.0   # 25→30: uzaktan sesi güçlendir (TV kapalıyken bg RMS ~98, eşiklerin altında)
        arr  = np.frombuffer(raw, dtype=np.int32)
        mono = arr[0::2].astype(np.float64)          # Sol kanal
        gain = 10.0 ** (SW_GAIN_DB / 20.0)
        mono = mono * gain / 65536.0                  # int32 → int16 ölçeği + gain
        mono = np.clip(mono, -32768, 32767).astype(np.int16)
        return mono.tobytes()

    def _stop_arecord(self) -> None:
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try: p.terminate()
            except Exception: pass
            try: p.wait(timeout=1.0)
            except Exception: pass

    # ── bus event işleme ──────────────────────────────────────────────────────

    def _handle_bus(self, ev: dict) -> None:
        t = ev.get("type")

        if t == "TTS_START":
            # Mikrofonu tamamen kapat — self-listening önlemi
            self._stop_arecord()
            self._muted_next  = self.state   # nereye döneceksin hatırla
            self.state        = MUTED
            self._muted_since = time.time()  # watchdog için giriş zamanını kaydet
            self.seg_wake.reset()
            self.seg_listen.reset()

        elif t == "TTS_END":
            # TTS bitti; kısa bekle (yankı/geri-besleme önlemi) sonra mic aç
            time.sleep(TTS_RESUME_DELAY)
            self.state = self._muted_next
            self.seg_wake.reset()
            self.seg_listen.reset()
            # LISTENING'e geçiyorsak timer'ı sıfırla — yoksa eski _listen_at
            # nedeniyle anında TIMEOUT tetiklenir
            if self.state == LISTENING:
                self._listen_at = time.time()
            self._start_arecord()

        elif t == "STOP_LISTEN":
            # Brain sessizlik/IDLE moduna geçti — wake word moduna dön
            if self.state != MUTED:
                self.state = IDLE
                self.seg_wake.reset()
                self.seg_listen.reset()
                # Arecord'u sadece çalışmıyorsa başlat (zorla yeniden başlatma YOK).
                # Zorla restart → her geçişte ~12000 RMS startup gürültüsü → Vosk'a
                # anlamsız ses gönderir, wake detection gecikmesine yol açar.
                # Zaten çalışıyorsa warmup_until ile ilk 500ms atla (stale buffer temizlenir).
                self._start_arecord()
                self._warmup_until = time.time() + 0.5   # ilk 500ms atla
                self._idle_last_frame_t = time.time()
                proc_ok = self._proc and self._proc.poll() is None
                print(f"[AUDIO] 👂 IDLE → wake word moduna geçildi ({'✅' if proc_ok else '❌'})")
            else:
                # TTS çalıyor, bitince IDLE'a geç
                self._muted_next = IDLE

        elif t == "LISTEN":
            mode = ev.get("mode", "once")
            self._low_threshold = ev.get("low_threshold", False)
            self._music_mode    = ev.get("music_mode", False)
            if self.state == MUTED:
                # TTS henüz bitmedi — TTS_END gelince LISTENING'e geç
                self._muted_next  = LISTENING
                self.listen_mode  = mode
            else:
                self.state       = LISTENING
                self.listen_mode = mode
                self._listen_at  = time.time()
                self.seg_listen.reset()
                self.seg_wake.reset()

    # ── ana döngü ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        print(f"[AUDIO] 🎧 online — cihaz: {AUDIO_CAPTURE_DEVICE}")
        self._start_arecord()

        try:
            while True:
                # Bus eventlerini önce kontrol et (non-blocking)
                ev = self.bus.poll()
                if ev:
                    self._handle_bus(ev)

                # Mic kapalıysa audio okuma
                if self.state == MUTED:
                    # Watchdog: MUTED > 30s → TTS_END hiç gelmedi (bağlantı hatası/race condition)
                    # Simüle edilmiş TTS_END ile kurtarma yap.
                    if self._muted_since > 0 and time.time() - self._muted_since > 30.0:
                        print("[AUDIO] ⚠ MUTED watchdog: 30s TTS_END gelmedi → zorla kurtarılıyor")
                        self._muted_since = 0.0
                        self._handle_bus({"type": "TTS_END", "ts": time.time()})
                    else:
                        time.sleep(0.01)
                    continue

                # arecord çalışmıyorsa yeniden başlat
                if not self._proc or self._proc.poll() is not None:
                    self._start_arecord()

                data = self._proc.stdout.read(self.frame_bytes_raw)
                if not data or len(data) != self.frame_bytes_raw:
                    # IDLE watchdog: frame gelmiyor → arecord stuck olabilir
                    if self.state == IDLE and self._idle_last_frame_t > 0:
                        if time.time() - self._idle_last_frame_t > 3.0:
                            print("[AUDIO] ⚠ IDLE: 3s frame yok → arecord yeniden başlatılıyor")
                            self._stop_arecord()
                            self._start_arecord()
                            self._idle_last_frame_t = time.time()
                    continue

                # Başarılı frame alındı → watchdog zamanlayıcısını güncelle
                if self.state == IDLE:
                    self._idle_last_frame_t = time.time()

                # S32_LE stereo → S16_LE mono + 25 dB yazılım kazancı (INMP441)
                data = self._convert_raw(data)

                # ── IDLE: wake word ───────────────────────────────────────────
                if self.state == IDLE:
                    now_t = time.time()
                    if now_t < self._wake_cd_until:
                        continue

                    # Warmup: arecord başladıktan sonra ilk 500ms donanım gürültüsünü atla
                    # (startup pop ~12000 RMS — Vosk'a anlamsız ses gönderilmesin)
                    if now_t < self._warmup_until:
                        continue

                    # ── RMS hesapla (VAD fallback + diagnostic için) ───────────
                    _arr_w = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    _rms   = float(np.sqrt(np.mean(_arr_w ** 2))) if len(_arr_w) > 0 else 0.0

                    # RMS fallback: VAD katı olsa bile yüksek sesli konuşmayı yakala
                    _force_speech = _rms > WAKE_RMS_THRESHOLD

                    # IDLE diagnostic: her 5 saniyede bir durum logu
                    self._idle_frames += 1
                    if _force_speech:
                        self._idle_speech_f += 1
                    if now_t >= self._idle_diag_next:
                        print(
                            f"[AUDIO] 🔎 IDLE: {self._idle_frames} frame okudu, "
                            f"RMS-speech={self._idle_speech_f}, şu_an_rms={_rms:.0f} "
                            f"(eşik={WAKE_RMS_THRESHOLD})"
                        )
                        self._idle_frames    = 0
                        self._idle_speech_f  = 0
                        self._idle_diag_next = now_t + 5.0

                    utt = self.seg_wake.push(data, force_speech=_force_speech)
                    if utt is None:
                        continue

                    # Vosk'a gönderilmeden önce: utterance yakalandı mı bildir
                    _arr_utt = np.frombuffer(utt, dtype=np.int16).astype(np.float32)
                    _rms_utt = float(np.sqrt(np.mean(_arr_utt ** 2))) if len(_arr_utt) > 0 else 0
                    print(f"[AUDIO] 🔍 Wake-utt {len(utt)//32}ms RMS={_rms_utt:.0f} → Vosk'a gönderiliyor")

                    hit = self.wake_rec.detect(utt)
                    if hit:
                        print(f"[AUDIO] ✅ WAKE → '{hit['heard']}' conf={hit['confidence']:.2f}" if hit['confidence'] else f"[AUDIO] ✅ WAKE → '{hit['heard']}'")
                        self._wake_cd_until = time.time() + WAKE_COOLDOWN
                        self.bus.publish({
                            "type": "WAKE",
                            "heard": hit["heard"],
                            "confidence": hit["confidence"],
                            "ts": time.time(),
                        })

                # ── LISTENING: STT ────────────────────────────────────────────
                elif self.state == LISTENING:
                    utt = self.seg_listen.push(data)

                    if utt is None:
                        # Timeout kontrolü
                        elapsed = time.time() - self._listen_at
                        if elapsed >= LISTEN_MAX_SEC:
                            print("[AUDIO] ⏱ TIMEOUT")
                            self.bus.publish({"type": "TIMEOUT", "ts": time.time()})
                            if self.listen_mode == "auto":
                                self._listen_at = time.time()
                                self.seg_listen.reset()
                            else:
                                self.state = IDLE
                                self.seg_wake.reset()
                        continue

                    # Utterance alındı → transkribe et
                    # RMS eşiği: müzik çalarken yüksek, pause'dayken düşük, normalde orta
                    if self._low_threshold:
                        _rms_thr = WHISPER_MIN_RMS_QUIET
                    elif self._music_mode:
                        _rms_thr = WHISPER_MIN_RMS_MUSIC
                    else:
                        _rms_thr = WHISPER_MIN_RMS
                    # Teşhis: utterance geldiğini ve RMS'i logla
                    _arr_dbg = np.frombuffer(utt, dtype=np.int16).astype(np.float32)
                    _rms_dbg = float(np.sqrt(np.mean(_arr_dbg ** 2))) if len(_arr_dbg) > 0 else 0
                    print(f"[AUDIO] 📦 Utterance {len(utt)//32} ms, RMS={_rms_dbg:.0f} (eşik={_rms_thr})")
                    result = self.stt_rec.transcribe(utt, min_rms=_rms_thr)
                    text   = (result.get("text") or "").strip()
                    conf   = result.get("confidence")

                    if text and len(text) < 2:
                        text = ""

                    print(f"[AUDIO] 🗣 STT: {repr(text) if text else '(boş)'}")

                    self.bus.publish({
                        "type":       "UTTERANCE",
                        "text":       text,
                        "confidence": conf,
                        "ts":         time.time(),
                    })

                    if self.listen_mode == "auto":
                        self._listen_at = time.time()
                        self.seg_listen.reset()
                    else:
                        self.state = IDLE
                        self.seg_wake.reset()

        except KeyboardInterrupt:
            pass
        finally:
            self._stop_arecord()
            print("[AUDIO] offline")


def main() -> None:
    RobiAudio().run()


if __name__ == "__main__":
    main()
