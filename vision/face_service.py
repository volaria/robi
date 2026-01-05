# vision/face_service.py

from typing import Optional

# robi_perception içinde güncellenecek global değerler
last_confirmed_name = None
last_confirm_time = 0.0

FACE_LOCK_SECONDS = 8.0


def update_confirmed_person(name: str, ts: float):
    global last_confirmed_name, last_confirm_time
    last_confirmed_name = name
    last_confirm_time = ts


def get_current_person(now: float) -> Optional[str]:
    if not last_confirmed_name:
        return None

    if (now - last_confirm_time) > FACE_LOCK_SECONDS:
        return None

    return last_confirmed_name
