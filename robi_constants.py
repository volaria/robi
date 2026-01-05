from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# IPC
BUS_SOCKET = "/tmp/robi_bus.sock"

# Models
MODELS_DIR = ROOT_DIR / "models"
VOSK_EN_MODEL = MODELS_DIR / "vosk-model-small-en-us-0.15"
VOSK_TR_MODEL = MODELS_DIR / "vosk-model-small-tr-0.3"

# Audio
DEFAULT_AUDIO_DEVICE = None  # override from CLI if needed
