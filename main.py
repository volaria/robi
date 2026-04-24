"""
main.py — ROBI Giriş Noktası
Tüm servisleri başlatır ve Ctrl+C ile temiz kapatır.

Servisler (her biri ayrı subprocess):
  1. robi_bus.py    — mesaj otobüsü (önce başlamalı)
  2. robi_audio.py  — mikrofon (wake + STT)
  3. robi_brain.py  — AI beyin
  4. robi_vision.py — kamera + yüz tanıma (opsiyonel)

Kullanım:
  python3 main.py           # tüm servisler
  python3 main.py --no-vision  # vision olmadan (test için)
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / "venv" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _start(script: str, label: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [PYTHON, str(ROOT / script)],
        cwd=str(ROOT),
    )
    print(f"[MAIN] ▶ {label} başlatıldı (pid={proc.pid})")
    return proc


def _stop_all(procs: List[subprocess.Popen]) -> None:
    print("\n[MAIN] ⛔ Kapatılıyor...")
    for p in reversed(procs):
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    # Bekle
    deadline = time.time() + 4.0
    for p in procs:
        remaining = max(0.1, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            p.kill()
    print("[MAIN] ✅ Tüm servisler kapatıldı.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ROBI başlatıcı")
    parser.add_argument("--no-vision", action="store_true",
                        help="Vision servisini başlatma")
    args = parser.parse_args()

    procs: List[subprocess.Popen] = []

    def _sig(signum, frame):
        _stop_all(procs)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _sig)
    signal.signal(signal.SIGTERM, _sig)

    # 1. Bus önce başlar
    procs.append(_start("robi_bus.py", "Bus"))
    time.sleep(0.8)  # bus'un socket'i açmasını bekle

    # 2. Audio
    procs.append(_start("robi_audio.py", "Audio"))
    time.sleep(0.3)

    # 3. Brain
    procs.append(_start("robi_brain.py", "Brain"))
    time.sleep(0.3)

    # 4. Reminders
    procs.append(_start("robi_reminders.py", "Reminders"))
    time.sleep(0.2)

    # 5. Vision (opsiyonel)
    if not args.no_vision:
        procs.append(_start("robi_vision.py", "Vision"))

    print("\n[MAIN] 🤖 ROBI hazır. Çıkmak için Ctrl+C.\n")

    # Servisleri izle — biri çökerse yeniden başlat (max 3 deneme)
    RESTARTABLE = [
        ("robi_audio.py", "Audio"),
        ("robi_brain.py", "Brain"),
    ]
    # proc listesindeki indeksler: 0=Bus, 1=Audio, 2=Brain, 3=Vision
    RESTART_IDX = {1: RESTARTABLE[0], 2: RESTARTABLE[1]}
    restart_counts: dict = {}

    while True:
        time.sleep(5)
        for idx, (script, label) in RESTART_IDX.items():
            if idx >= len(procs):
                continue
            if procs[idx].poll() is not None:
                count = restart_counts.get(script, 0)
                if count >= 3:
                    print(f"[MAIN] ❌ {label} 3 kez çöktü, artık yeniden başlatılmıyor.")
                    print(f"[MAIN]    Log için: python3 {script}")
                    continue
                print(f"[MAIN] ⚠ {label} çöktü (deneme {count+1}/3), 10sn sonra yeniden başlatılıyor...")
                time.sleep(10)
                procs[idx] = _start(script, label)
                restart_counts[script] = count + 1


if __name__ == "__main__":
    main()
