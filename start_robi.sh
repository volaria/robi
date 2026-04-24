#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── DMIC overlay gain (isteğe bağlı, /boot/config.txt'de dmic_gain yoksa) ──
# sndrpii2scard DMIC kartının hardware gain kontrolü yok;
# yazılım kazancı robi_audio.py içinde SW_GAIN_DB ile ayarlanır.

# venv
VENV="$BASE_DIR/venv"

# modeller
WAKE_MODEL="$BASE_DIR/vision/models/vosk-model-small-en-us-0.15"
STT_MODEL="$BASE_DIR/vision/models/vosk-model-small-tr-0.3"

source "$VENV/bin/activate"

# BUS + BRAIN (normal öncelik)
python robi_bus.py &
sleep 0.3
python robi_brain.py &
sleep 0.3

# VISION — düşük CPU önceliği (nice 10): audio ve brain önce alır, vision artan zamanda çalışır
nice -n 10 python robi_vision.py &
sleep 0.5

# AUDIO (foreground, yüksek öncelik — çıkınca script biter)
nice -n -5 python robi_audio.py
