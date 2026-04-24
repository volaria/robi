"""
robi_vision.py — ROBI Kamera, Yüz Tanıma, Servo

Özellikler:
  • picamera2 ile 640x480 @ 10fps görüntü akışı
  • YuNet (tespit) + SFace (tanıma) — ışık/açı dayanıklı, dlib gerektirmez
  • Servo ile yüz takibi (yumuşatılmış hareket)
  • Tanınan yüz  → bus'a FACE_SEEN  {name}
  • Tanınmayan yüz → bus'a FACE_UNKNOWN
  • Vote buffer: anti-flicker (N frame içinde M kez görülmeli)

Bağımlılıklar:
  picamera2 : sudo apt install -y python3-picamera2
  opencv    : zaten kurulu (4.10+)
  YuNet/SFace ONNX modelleri : vision/models/ klasöründe
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Dict, Optional

import cv2
import numpy as np

from config import (
    BUS_SOCKET, CAM_FPS, CAM_HEIGHT, CAM_WIDTH, CAM_AWB_ENABLE,
    FACE_GREET_WAIT,
    SERVO_CENTER, SERVO_MAX, SERVO_MIN, SERVO_PIN, SERVO_STEP,
)
from robi_bus import BusClient
from vision.face_recognize import detect_faces, is_ready, recognize, train

# ─── Opsiyonel bağımlılıklar ──────────────────────────────────────────────────

try:
    from picamera2 import Picamera2
    HAS_PICAM = True
except ImportError:
    HAS_PICAM = False
    print("[VISION] ⚠ picamera2 yok — sudo apt install python3-picamera2")

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    print("[VISION] ⚠ RPi.GPIO yok — servo devre dışı")

# ─── Vote parametreleri ────────────────────────────────────────────────────────
VOTE_WINDOW   = 7   # son N frame içinde
VOTE_MIN_HITS = 4   # en az M kez görülmeli (anti-flicker)


# ─── Servo ────────────────────────────────────────────────────────────────────

class Servo:
    """Yumuşatılmış tek eksenli servo."""

    def __init__(self):
        self._angle  = float(SERVO_CENTER)
        self._target = float(SERVO_CENTER)
        self._pwm    = None
        self._lock   = threading.Lock()

        if HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(SERVO_PIN, GPIO.OUT)
            self._pwm = GPIO.PWM(SERVO_PIN, 50)
            self._pwm.start(self._duty(SERVO_CENTER))
            threading.Thread(target=self._smooth_loop, daemon=True).start()

    @staticmethod
    def _duty(angle: float) -> float:
        return 2.5 + (angle / 180.0) * 10.0

    def set_target(self, angle: float) -> None:
        angle = max(SERVO_MIN, min(SERVO_MAX, angle))
        with self._lock:
            self._target = angle

    def center(self) -> None:
        self.set_target(SERVO_CENTER)

    def _smooth_loop(self) -> None:
        _stable_since: float = 0.0
        _pwm_off = False
        while True:
            with self._lock:
                diff = self._target - self._angle
            if abs(diff) > 0.5:
                # Harekete geç: PWM'i aç, konumu güncelle
                if _pwm_off and self._pwm:
                    self._pwm.ChangeDutyCycle(self._duty(self._angle))
                    _pwm_off = False
                step = min(abs(diff), SERVO_STEP) * (1 if diff > 0 else -1)
                with self._lock:
                    self._angle += step
                if self._pwm:
                    self._pwm.ChangeDutyCycle(self._duty(self._angle))
                _stable_since = time.time()
            else:
                # Hedefe ulaşıldı — 1.5 sn sonra PWM'i kapat (titreme önleme)
                if not _pwm_off and time.time() - _stable_since > 1.5:
                    if self._pwm:
                        self._pwm.ChangeDutyCycle(0)
                    _pwm_off = True
            time.sleep(0.02)

    def cleanup(self) -> None:
        if self._pwm:
            self._pwm.stop()
        if HAS_GPIO:
            GPIO.cleanup()


# ─── Ana servis ───────────────────────────────────────────────────────────────

class RobiVision:

    def __init__(self):
        self.bus   = BusClient(BUS_SOCKET)
        self.servo = Servo()

        # YuNet + SFace modellerini ve embedding cache'ini yükle
        ok = train(force=False)
        if ok:
            print("[VISION] ✅ Yüz tanıma hazır")
        else:
            print("[VISION] ⚠ Yüz tanıma yüklenemedi — sadece tespit çalışır")

        # Greet cooldown per kişi
        self._greeted_at: Dict[str, float] = {}
        self._last_unknown_at: float       = 0.0

        # Vote buffer: anti-flicker
        self._vote_buf: deque = deque(maxlen=VOTE_WINDOW)
        self._last_confirmed: Optional[str] = None

        # Kamera
        self._cam: Optional[Picamera2] = None
        self._frame_count = 0

        # Uyku modu — BRAIN_SLEEP gelince True, BRAIN_WAKE gelince False
        self._brain_sleeping: bool = False



    # ── Kamera ────────────────────────────────────────────────────────────────

    def _start_camera(self) -> bool:
        if not HAS_PICAM:
            return False
        try:
            cam = Picamera2()
            cam_controls: dict = {"FrameRate": CAM_FPS, "AwbEnable": CAM_AWB_ENABLE}
            cfg = cam.create_preview_configuration(
                main={"size": (CAM_WIDTH, CAM_HEIGHT), "format": "BGR888"},
                controls=cam_controls,
            )
            cam.configure(cfg)
            cam.start()
            self._cam = cam
            print(f"[VISION] 📷 Kamera açıldı: {CAM_WIDTH}x{CAM_HEIGHT}@{CAM_FPS}fps")
            return True
        except Exception as e:
            print(f"[VISION] ❌ Kamera hatası: {e}")
            return False

    def _stop_camera(self) -> None:
        if self._cam:
            try:
                self._cam.stop()
            except Exception:
                pass
            self._cam = None

    # ── Yüz işleme ────────────────────────────────────────────────────────────

    def _process_frame(self, frame_bgr: np.ndarray) -> None:
        """YuNet ile yüz tespit et, SFace ile tanı, vote sonucunu yayınla."""
        faces = detect_faces(frame_bgr)

        if len(faces) == 0:
            self._vote_buf.append(None)
            self.servo.center()
            return


        # Yüz seçimi: tek yüz varsa direkt al; birden fazlaysa mevcut kişiyi öncelendir
        if len(faces) == 1 or not is_ready():
            best_face = faces[np.argmax(faces[:, 2] * faces[:, 3])]
        else:
            best_face = self._pick_best_face(frame_bgr, faces)

        # Servo takip
        self._track_face(best_face)

        # Tanıma (embedding hazırsa)
        if is_ready():
            name, score = recognize(frame_bgr, best_face)
            label = name if name else "UNKNOWN"
            self._vote_buf.append(label)
            # Tanıma debug: her 15 işlenmiş frame'de bir log
            if self._frame_count % 45 == 0:
                status = f"✅ {name} ({score:.3f})" if name else f"❓ UNKNOWN ({score:.3f})"
                print(f"[VISION] 🔍 {status} — buf={list(self._vote_buf)[-4:]}")
                # Debug frame kaydet (son tanıma anını görmek için)
                try:
                    debug_frame = frame_bgr.copy()
                    x, y, w, h = int(best_face[0]), int(best_face[1]), int(best_face[2]), int(best_face[3])
                    color = (0, 255, 0) if name else (0, 165, 255)
                    cv2.rectangle(debug_frame, (x, y), (x+w, y+h), color, 2)
                    label = f"{name} {score:.3f}" if name else f"? {score:.3f}"
                    cv2.putText(debug_frame, label, (x, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                    cv2.imwrite("/tmp/robi_face_debug.jpg", debug_frame)
                except Exception:
                    pass
        else:
            self._vote_buf.append("UNKNOWN")

        # Vote değerlendir
        self._evaluate_vote()

    def _evaluate_vote(self) -> None:
        """VOTE_WINDOW içinde en az VOTE_MIN_HITS olan kişiyi onayla."""
        if len(self._vote_buf) < VOTE_WINDOW:
            return

        counts = Counter(v for v in self._vote_buf if v is not None)
        if not counts:
            return

        winner, hits = counts.most_common(1)[0]
        if hits < VOTE_MIN_HITS:
            return

        now = time.time()

        if winner == "UNKNOWN":
            if now - self._last_unknown_at > FACE_GREET_WAIT:
                self._last_unknown_at = now
                self._vote_buf.clear()
                self.bus.publish({"type": "FACE_UNKNOWN", "ts": now})
        else:
            last = self._greeted_at.get(winner, 0)
            if now - last > FACE_GREET_WAIT:
                self._greeted_at[winner] = now
                self._vote_buf.clear()
                print(f"[VISION] 👋 {winner} tanındı ve selamlandı")
                self.bus.publish({"type": "FACE_SEEN", "name": winner, "ts": now})

    # ── Çoklu yüz seçimi ─────────────────────────────────────────────────────

    def _pick_best_face(self, frame_bgr: np.ndarray, faces: np.ndarray) -> np.ndarray:
        """
        Birden fazla yüz varken hangisini işleyeceğimizi seçer.
        Öncelik sırası:
          1. Vote buffer'da zaten görünen (onaylı) kişi tekrar görünüyorsa o yüzü seç.
          2. Yoksa en büyük yüzü kullan.
        Bu sayede iki kişi aynı anda kameradayken ROBI sürekli kişi değiştirmez.
        """
        # Son vote buffer'dan en sık görünen kişiyi bul
        recent = [v for v in self._vote_buf if v and v != "UNKNOWN"]
        anchor = Counter(recent).most_common(1)[0][0] if recent else None

        if anchor:
            # Tüm yüzleri tara — anchor kişiyle eşleşen en yüksek scorelu yüzü bul
            best_face  = None
            best_score = -1.0
            for face in faces:
                name, score = recognize(frame_bgr, face)
                if name == anchor and score > best_score:
                    best_score = score
                    best_face  = face
            if best_face is not None:
                return best_face

        # Anchor bulunamadı veya hiçbiri eşleşmedi → en büyük yüzü seç
        return faces[np.argmax(faces[:, 2] * faces[:, 3])]

    # ── Servo takip ───────────────────────────────────────────────────────────

    def _track_face(self, face_row: np.ndarray) -> None:
        """YuNet face_row [x,y,w,h,...] ile yüzü merkeze al."""
        x, y, w, h = face_row[:4]
        face_cx  = x + w / 2.0
        frame_cx = CAM_WIDTH / 2.0
        # Normalize hata: -1 (tam sol) .. +1 (tam sağ)
        error = (face_cx - frame_cx) / frame_cx
        # Deadzone %15: küçük titremeleri yoksay
        if abs(error) < 0.15:
            return
        # Yüz pozisyonunu direkt açıya dönüştür (incremental değil, absolute)
        # error > 0 → yüz sağda → servo sola döner (açı azalır)
        target = SERVO_CENTER - error * (SERVO_MAX - SERVO_CENTER) * 0.6
        self.servo.set_target(target)

    # ── Ana döngü ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        if not self._start_camera():
            print("[VISION] Kamera başlatılamadı, vision devre dışı.")
            return

        print("[VISION] 👁 Vision döngüsü başladı")

        try:
            while True:
                if self._cam is None:
                    break

                # Bus mesajlarını kontrol et (BRAIN_SLEEP / BRAIN_WAKE)
                ev = self.bus.poll()
                if ev:
                    t = ev.get("type", "")
                    if t == "BRAIN_SLEEP":
                        self._brain_sleeping = True
                        self._vote_buf.clear()   # eski oylama temizle
                        print("[VISION] 💤 Brain uyudu — yavaş tarama moduna geçildi")
                    elif t == "BRAIN_WAKE":
                        self._brain_sleeping = False
                        print("[VISION] 👁 Brain uyandı — normal tarama moduna geçildi")

                # BRAIN uyuyorsa: 2 saniyede bir kare al, sadece yüz kontrolü yap
                if self._brain_sleeping:
                    time.sleep(2.0)
                    frame = self._cam.capture_array()
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    self._frame_count += 1
                    self._process_frame(frame)  # Yüz görülürse FACE_SEEN → brain uyanır
                    continue

                frame = self._cam.capture_array()
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)  # picamera2 RGB verir, OpenCV BGR ister
                self._frame_count += 1

                # En güncel kareyi kaydet (Telegram fotoğraf komutu için)
                if self._frame_count % 10 == 0:
                    try:
                        cv2.imwrite("/tmp/robi_latest_frame.jpg", frame)
                    except Exception:
                        pass

                # YuNet her 3 saniyede bir çalışır (30 frame @ 10fps)
                # Basit ve öngörülebilir — karmaşık mantık yok
                if self._frame_count % 30 == 0:
                    self._process_frame(frame)

                time.sleep(1.0 / CAM_FPS)

        except KeyboardInterrupt:
            pass
        finally:
            self._stop_camera()
            self.servo.center()
            time.sleep(0.3)
            self.servo.cleanup()
            print("[VISION] offline")


def main() -> None:
    RobiVision().run()


if __name__ == "__main__":
    main()
