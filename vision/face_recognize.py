"""
vision/face_recognize.py — YuNet + SFace tabanlı yüz tanıma

Tespit  : YuNet (OpenCV Zoo, ~228KB) — Haar'dan çok daha hızlı ve doğru
Tanıma  : SFace (ArcFace tabanlı, ~37MB) — ışık/açı değişikliklerine dayanıklı
Eşleşme : Cosine similarity (0.363 eşiği — OpenCV önerisi)

Eğitim  : vision/faces/<İsim>/*.jpg  → embeddings.pkl (otomatik cache)
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ─── Yollar ───────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
MODELS_DIR      = BASE_DIR / "models"
FACES_DIR       = BASE_DIR / "faces"
YUNET_MODEL     = str(MODELS_DIR / "face_detection_yunet_2023mar.onnx")
SFACE_MODEL     = str(MODELS_DIR / "face_recognition_sface_2021dec.onnx")
EMBEDDINGS_FILE = str(MODELS_DIR / "embeddings.pkl")

# ─── Parametreler ─────────────────────────────────────────────────────────────
COSINE_THRESHOLD = 0.363   # OpenCV önerisi; yükseltilirse daha katı eşleşme
YUNET_SCORE      = 0.40    # YuNet tespit güven eşiği (0.55→0.40: hafif açılı yüzler için)
YUNET_NMS        = 0.3     # Non-maximum suppression

# ─── Global state ──────────────────────────────────────────────────────────────
_detector:   Optional[cv2.FaceDetectorYN]   = None
_recognizer: Optional[cv2.FaceRecognizerSF] = None
_embeddings: Dict[str, List[np.ndarray]]    = {}
trained = False


# ─── Model yükleme ────────────────────────────────────────────────────────────

def _load_models() -> bool:
    global _detector, _recognizer
    for path, label in [(YUNET_MODEL, "YuNet"), (SFACE_MODEL, "SFace")]:
        if not os.path.exists(path):
            print(f"[FaceRec] ❌ Model bulunamadı: {label} → {path}")
            return False
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            YUNET_MODEL, "", (320, 240),
            score_threshold=YUNET_SCORE,
            nms_threshold=YUNET_NMS,
            top_k=5,
        )
    if _recognizer is None:
        _recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL, "")
    return True


def is_ready() -> bool:
    return trained and bool(_embeddings)


# ─── Eğitim ───────────────────────────────────────────────────────────────────

def train(force: bool = False) -> bool:
    """
    vision/faces/<İsim>/*.jpg fotoğraflarından SFace embedding'leri oluşturur.
    Mevcut embeddings.pkl cache varsa ve force=False ise direkt yükler.
    """
    global trained, _embeddings

    if not _load_models():
        return False

    # Cache yükle
    if not force and os.path.exists(EMBEDDINGS_FILE):
        try:
            with open(EMBEDDINGS_FILE, "rb") as f:
                _embeddings = pickle.load(f)
            trained = True
            info = {k: len(v) for k, v in _embeddings.items()}
            print(f"[FaceRec] ✅ Cache yüklendi: {info}")
            return True
        except Exception as e:
            print(f"[FaceRec] ⚠ Cache bozuk, yeniden eğitiliyor: {e}")

    # Sıfırdan eğit
    _embeddings.clear()

    if not FACES_DIR.exists():
        print(f"[FaceRec] ❌ Yüz klasörü bulunamadı: {FACES_DIR}")
        trained = False
        return False

    for person_dir in sorted(FACES_DIR.iterdir()):
        if not person_dir.is_dir():
            continue
        name = person_dir.name
        vecs: List[np.ndarray] = []

        _IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        img_paths = sorted(
            p for p in person_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _IMG_EXTS
        )
        for img_path in img_paths:
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            feat = _extract_feature(img)
            if feat is not None:
                vecs.append(feat)

        if vecs:
            _embeddings[name] = vecs
            print(f"[FaceRec]   {name}: {len(vecs)}/{len(img_paths)} embedding ✅")
        else:
            print(f"[FaceRec]   {name}: hiç yüz bulunamadı ⚠")

    if not _embeddings:
        print("[FaceRec] ❌ Hiç embedding oluşturulamadı")
        trained = False
        return False

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_FILE, "wb") as f:
        pickle.dump(_embeddings, f)

    trained = True
    total = sum(len(v) for v in _embeddings.values())
    print(f"[FaceRec] ✅ Eğitim tamamlandı — {total} embedding, kişiler: {list(_embeddings.keys())}")
    return True


# ─── Tespit & Tanıma ──────────────────────────────────────────────────────────

def detect_faces(frame_bgr: np.ndarray) -> np.ndarray:
    """
    YuNet ile yüz tespit et.
    Döndürür: (N, 15) array [x,y,w,h, 10 landmark, score] veya boş array.
    """
    if not _load_models():
        return np.empty((0, 15), dtype=np.float32)
    h, w = frame_bgr.shape[:2]
    _detector.setInputSize((w, h))
    _, faces = _detector.detect(frame_bgr)
    return faces if faces is not None else np.empty((0, 15), dtype=np.float32)


def recognize(frame_bgr: np.ndarray, face_row: np.ndarray) -> Tuple[Optional[str], float]:
    """
    Tek bir yüzü tanı.
    frame_bgr : tam BGR frame
    face_row  : detect_faces() çıktısından tek satır (shape 15,)
    Döndürür  : (isim, cosine_score)  — tanınamazsa (None, score)
    """
    if not is_ready() or _recognizer is None:
        return None, 0.0

    try:
        aligned = _recognizer.alignCrop(frame_bgr, face_row)
        feat    = _recognizer.feature(aligned)
    except Exception as e:
        print(f"[FaceRec] ⚠ Feature hatası: {e}")
        return None, 0.0

    best_name  = None
    best_score = -1.0

    for name, vecs in _embeddings.items():
        for ref in vecs:
            score = float(_recognizer.match(feat, ref, 0))  # 0 = FR_COSINE
            if score > best_score:
                best_score = score
                best_name  = name

    if best_score >= COSINE_THRESHOLD:
        return best_name, best_score
    return None, best_score


# ─── Yardımcılar ──────────────────────────────────────────────────────────────

def _extract_feature(img_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Görüntüden en büyük yüzü bul, SFace feature'ı döndür."""
    h, w = img_bgr.shape[:2]
    _detector.setInputSize((w, h))
    _, faces = _detector.detect(img_bgr)

    if faces is not None and len(faces) > 0:
        best = faces[np.argmax(faces[:, 2] * faces[:, 3])]
        try:
            aligned = _recognizer.alignCrop(img_bgr, best)
            return _recognizer.feature(aligned)
        except Exception:
            pass

    # YuNet bulamazsa Haar cascade fallback (eğitim fotoğrafları için)
    return _haar_fallback_feature(img_bgr)


def _haar_fallback_feature(img_bgr: np.ndarray) -> Optional[np.ndarray]:
    """YuNet yüz bulamazsa Haar cascade ile dene."""
    # cv2.data bazı build'lerde yok; bilinen yolları dene
    _CASCADE_CANDIDATES = [
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    ]
    if hasattr(cv2, "data"):
        _CASCADE_CANDIDATES.insert(0, cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cascade_path = next((p for p in _CASCADE_CANDIDATES if os.path.exists(p)), None)
    if cascade_path is None:
        return None
    cascade = cv2.CascadeClassifier(cascade_path)
    gray  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.1, 4, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    # YuNet formatına çevir (x,y,w,h + 10 landmark placeholder + score)
    face_row = np.array(
        [float(x), float(y), float(w), float(h),
         float(x + w//3),   float(y + h//3),    # sol göz
         float(x + 2*w//3), float(y + h//3),    # sağ göz
         float(x + w//2),   float(y + h//2),    # burun
         float(x + w//3),   float(y + 2*h//3),  # sol ağız
         float(x + 2*w//3), float(y + 2*h//3),  # sağ ağız
         1.0],
        dtype=np.float32
    )
    try:
        aligned = _recognizer.alignCrop(img_bgr, face_row)
        return _recognizer.feature(aligned)
    except Exception:
        return None


# ─── Doğrudan çalıştırma: python3 vision/face_recognize.py ───────────────────
if __name__ == "__main__":
    print("=== YuNet + SFace Eğitim ===")
    ok = train(force=True)
    if ok:
        print("\nEmbedding özeti:")
        for name, vecs in _embeddings.items():
            print(f"  {name}: {len(vecs)} vektör")
    else:
        print("Eğitim başarısız.")
