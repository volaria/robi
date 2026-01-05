# vision/face_detect.py
import cv2

FACE_CASCADE_PATH = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(FACE_CASCADE_PATH)
if face_cascade.empty():
    raise RuntimeError(f"Face cascade not loaded: {FACE_CASCADE_PATH}")

def detect_faces(gray):
    # robi_perception.py ile aynı ayarlar
    return face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(40, 40),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
