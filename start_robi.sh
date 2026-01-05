#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# venv'ler
VENV_AUDIO="$BASE_DIR/venv"
VENV_BRAIN="$BASE_DIR/venv311"

# modeller
WAKE_MODEL="$BASE_DIR/models/vosk-model-small-en-us-0.15"
STT_MODEL="$BASE_DIR/models/vosk-model-small-tr-0.3"

# BUS + BRAIN (venv311)
source "$VENV_BRAIN/bin/activate"
python robi_bus.py &
sleep 0.3
python robi_brain.py &
sleep 0.3

# AUDIO (venv)
source "$VENV_AUDIO/bin/activate"
python robi_audio.py \
  --wake-model "$WAKE_MODEL" \
  --stt-model "$STT_MODEL"
