#!/usr/bin/env python3
"""
cam_color_test.py — Kamera renk kazancını test eder.
Farklı R/B değerleriyle fotoğraf çekip karşılaştırma için kaydeder.

Kullanım:
    python3 cam_color_test.py
    Sonra: scp robi:~/ai-robot/color_test_*.jpg ~/Desktop/
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from picamera2 import Picamera2
except ImportError:
    print("picamera2 yok")
    sys.exit(1)

from config import CAM_WIDTH, CAM_HEIGHT, CAM_FPS

# Test edilecek (R_gain, B_gain) kombinasyonları
TEST_GAINS = [
    (2.0, 1.8),   # hafif sıcak
    (2.5, 1.5),   # orta sıcak (varsayılan)
    (3.0, 1.3),   # çok sıcak
    (1.8, 2.0),   # soğuk (referans — ne kadar kötü olduğu görülür)
]

print("=" * 50)
print("Kamera Renk Kazancı Testi")
print("=" * 50)
print(f"Kamera önüne geç. Her fotoğraf için 4 saniye verilecek.\n")

for r_gain, b_gain in TEST_GAINS:
    print(f"Çekiliyor: R={r_gain} B={b_gain} ...")
    cam = Picamera2()
    cfg = cam.create_preview_configuration(
        main={"size": (CAM_WIDTH, CAM_HEIGHT), "format": "BGR888"},
        controls={
            "FrameRate": CAM_FPS,
            "AwbEnable": False,
            "ColourGains": (r_gain, b_gain),
        }
    )
    cam.configure(cfg)
    cam.start()
    time.sleep(4)  # kameranın stabilize olması için bekle
    frame = cam.capture_array()
    cam.stop()
    cam.close()

    import cv2
    filename = f"/home/volkan/ai-robot/color_test_R{r_gain}_B{b_gain}.jpg"
    cv2.imwrite(filename, frame)
    print(f"  ✅ Kaydedildi: {filename}")
    time.sleep(0.5)

print("\nTüm testler tamamlandı!")
print("Mac'te görmek için:")
print("  scp robi:~/ai-robot/color_test_*.jpg ~/Desktop/")
print("\nEn doğal görüneni config.py'de CAM_COLOR_GAIN_R / CAM_COLOR_GAIN_B olarak ayarla.")
