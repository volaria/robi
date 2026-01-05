# vision/face_recognize.py
import os
import cv2
import numpy as np

from vision.face_detect import detect_faces

# -----------------------------------------------------
# CONFIG
# -----------------------------------------------------
BASE_DIR = os.path.dirname(__file__)
FACES_DIR = os.path.join(BASE_DIR, "faces")
MODEL_DIR = os.path.join(BASE_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "face_model.yml")
LABELS_PATH = os.path.join(MODEL_DIR, "labels.txt")

FACE_SIZE = (200, 200)

# LBPH: confidence küçükse daha iyi (0-50 çok iyi, 50-100 orta, 100+ çoğu zaman zayıf)
CONFIDENCE_THRESHOLD = 120

# Log ayarı
VERBOSE_RAW = False   # True yaparsan her predict'te label/conf basar
VERBOSE_DECISION = False  # True yaparsan ACCEPT/REJECT basar

# -----------------------------------------------------
# GLOBALS
# -----------------------------------------------------
recognizer = cv2.face.LBPHFaceRecognizer_create()
label_map: dict[int, str] = {}
trained = False


def is_ready() -> bool:
    return trained and len(label_map) > 0


# -----------------------------------------------------
# TRAIN / LOAD
# -----------------------------------------------------
def train(force: bool = False) -> bool:
    """
    vision/faces/ klasöründen modeli eğitir.
    Model varsa ve force=False ise model+labels yükler.
    """
    global trained, label_map, label_map_rev

    if not os.path.exists(FACES_DIR):
        print("⚠️ FaceRec: faces/ folder not found")
        return False

    os.makedirs(MODEL_DIR, exist_ok=True)

    # -----------------------------
    # LOAD EXISTING MODEL
    # -----------------------------
    if os.path.exists(MODEL_PATH) and os.path.exists(LABELS_PATH) and not force:
        try:
            recognizer.read(MODEL_PATH)
            _load_label_map()
            trained = True
            print(f"🧠 FaceRec: model loaded ({len(label_map)} persons)")
            return True
        except Exception as e:
            print(f"⚠️ FaceRec: model load failed, retraining ({e})")

    # -----------------------------
    # TRAIN FROM SCRATCH
    # -----------------------------
    images = []
    labels = []
    label_map.clear()
    label_map_rev = {}

    current_label = 0
    total_imgs = 0

    for person_name in sorted(os.listdir(FACES_DIR)):
        person_dir = os.path.join(FACES_DIR, person_name)
        if not os.path.isdir(person_dir):
            continue

        label_map[current_label] = person_name
        label_map_rev[person_name] = current_label

        for file in sorted(os.listdir(person_dir)):
            path = os.path.join(person_dir, file)

            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue

            h, w = img.shape[:2]
            if w > 800:
                scale = 800 / w
                img = cv2.resize(img, None, fx=scale, fy=scale)

            img = cv2.GaussianBlur(img, (5, 5), 0)

            faces = detect_faces(img)
            if len(faces) == 0:
                continue

            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])

            H, W = img.shape[:2]
            x2 = min(x + w, W)
            y2 = min(y + h, H)
            roi = img[y:y2, x:x2]

            if roi.size == 0:
                continue

            roi = cv2.resize(roi, FACE_SIZE)

            images.append(roi)
            labels.append(current_label)
            total_imgs += 1

            print(f"🧪 TRAIN img: {person_name}/{file}")

        current_label += 1

    if not images:
        print("⚠️ FaceRec: no face images found")
        trained = False
        return False

    recognizer.train(images, np.array(labels))
    recognizer.save(MODEL_PATH)
    _save_label_map()

    trained = True

    # -----------------------------
    # TRAIN SUMMARY (NET TEŞHİS)
    # -----------------------------
    print("\n🧪 TRAIN SUMMARY")
    print(f"Total face crops: {len(images)}")
    print(f"Total persons: {len(label_map)}")

    print("Label map:")
    for lbl, name in label_map.items():
        print(f"  {lbl} -> {name}")

    from collections import Counter
    cnt = Counter(labels)
    print("Samples per person:")
    for lbl, c in cnt.items():
        print(f"  {label_map[lbl]}: {c}")

    print("----\n")

    return True

# -----------------------------------------------------
# RECOGNIZE
# -----------------------------------------------------
def recognize(gray, faces):
    """
    gray  : grayscale frame (blur'suz önerilir)
    faces : Haar detectMultiScale çıktısı
    return: (name, confidence) or (None, None)
    """
    if not is_ready() or faces is None or len(faces) == 0:
        return None, None

    x, y, w, h = _largest_face(faces)

    # hafif içten kırpma (saç/arka plan etkisini azaltır)
    pad = int(0.08 * w)
    x1 = max(0, x + pad)
    y1 = max(0, y + pad)
    x2 = max(0, x + w - pad)
    y2 = max(0, y + h - pad)

    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None, None

    roi = cv2.resize(roi, FACE_SIZE)

    label, best_conf = recognizer.predict(roi)

    # ikinci en yakın mesafeyi yaklaşıkla
    # (LBPH tek model olduğu için pratik çözüm)
    second_conf = best_conf + 999  # default çok büyük

    # basit trick: threshold yakınındaysa güvensiz say
    CONF_GAP = 20  # 🔑 10–20 arası ayarlanır

    if VERBOSE_RAW:
        print(f"🧪 FaceRec raw: label={label}, conf={best_conf:.1f}")

    # 1) klasik threshold
    if best_conf >= CONFIDENCE_THRESHOLD:
        if VERBOSE_DECISION:
            print("❌ FaceRec: REJECTED (threshold)")
        return None, best_conf

    # 2) gap kuralı
    # threshold’a çok yakınsa kararsız say
    if (CONFIDENCE_THRESHOLD - best_conf) < CONF_GAP:
        if VERBOSE_DECISION:
            print("❌ FaceRec: REJECTED (gap)")
        return None, best_conf

    if VERBOSE_DECISION:
        print("✅ FaceRec: ACCEPTED")

    return label_map.get(label), best_conf

def _largest_face(faces):
    # faces: [(x,y,w,h), ...]
    return max(faces, key=lambda r: r[2] * r[3])


def _save_label_map():
    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        for k, v in label_map.items():
            f.write(f"{k}:{v}\n")


def _load_label_map():
    label_map.clear()
    if not os.path.exists(LABELS_PATH):
        return
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            k, v = line.strip().split(":", 1)
            label_map[int(k)] = v
