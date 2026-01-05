import json
import os
import random
import re
from datetime import datetime


# ----------------------------------------------------------
#  TÜRKÇE KONTROL
# ----------------------------------------------------------
def is_valid_turkish(text: str) -> bool:
    """
    STT çıktısındaki çöp stringleri filtreler.
    En az bir Türkçe harf veya normal harf içeriyorsa True döner.
    """
    if not text or len(text.strip()) < 2:
        return False

    return bool(re.search(r"[a-zA-ZçÇğĞıİöÖşŞüÜ]", text))


# ----------------------------------------------------------
#  ROBI MEMORY CLASS
# ----------------------------------------------------------
class RobiMemory:
    def __init__(self, file_path="memory.json"):
        self.file_path = file_path
        self.data = self.load_memory()

    # ------------------------------------------------------
    # LOAD / SAVE
    # ------------------------------------------------------
    def load_memory(self):
        if not os.path.exists(self.file_path):
            return {
                "name": "",
                "name_locked": False,
                "likes": [],
                "dislikes": [],
                "topics": [],
                "last_interactions": [],
                "people": [],
            }

        with open(self.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # eski dosyalarda eksik alanları tamamla
        data.setdefault("name", "")
        data.setdefault("name_locked", False)
        data.setdefault("likes", [])
        data.setdefault("dislikes", [])
        data.setdefault("topics", [])
        data.setdefault("last_interactions", [])
        data.setdefault("people", [])

        return data

    def save_memory(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    # ------------------------------------------------------
    # USER NAME
    # ------------------------------------------------------
    def set_user_name(self, name: str, lock: bool = True):
        if not name:
            return

        name = name.strip()
        if not name:
            return

        # Tek kelime al
        name = name.split()[0]

        # İlk harfi büyük yap
        name = name[0].upper() + name[1:]

        self.data["name"] = name

        if lock:
            self.data["name_locked"] = True

        self.save_memory()

    def get_user_name(self) -> str:
        return self.data.get("name", "")

    # ------------------------------------------------------
    # LIKES / DISLIKES
    # ------------------------------------------------------
    def add_like(self, item):
        item = (item or "").strip()
        if not is_valid_turkish(item):
            return

        if item and item not in self.data["likes"]:
            self.data["likes"].append(item)
            self.save_memory()

    def add_dislike(self, item):
        item = (item or "").strip()
        if not is_valid_turkish(item):
            return

        if item and item not in self.data["dislikes"]:
            self.data["dislikes"].append(item)
            self.save_memory()

    def is_name_locked(self) -> bool:
        return bool(self.data.get("name_locked", False))

    def unlock_name(self):
        self.data["name_locked"] = False
        self.save_memory()

    def clear_name(self):
        self.data["name"] = ""
        self.save_memory()

    # ------------------------------------------------------
    # TOPICS
    # ------------------------------------------------------
    def remember_topic(self, text):
        text = (text or "").strip()
        if not is_valid_turkish(text):
            return

        self.data["topics"].append({
            "text": text,
            "timestamp": datetime.now().isoformat(),
        })

        # Son 20 kayıt
        self.data["topics"] = self.data["topics"][-20:]
        self.save_memory()

    # ------------------------------------------------------
    # INTERACTIONS
    # ------------------------------------------------------
    def log_interaction(self, text):
        text = (text or "").strip()
        if not text:
            return

        self.data["last_interactions"].append({
            "text": text,
            "timestamp": datetime.now().isoformat(),
        })

        # Son 30 kayıt
        self.data["last_interactions"] = self.data["last_interactions"][-30:]
        self.save_memory()

    # ------------------------------------------------------
    # RESET
    # ------------------------------------------------------
    def reset_memory(self, new_name="Selma"):
        self.data = {
            "name": new_name,
            "name_locked": False,
            "likes": [],
            "dislikes": [],
            "topics": [],
            "last_interactions": [],
            "people": [],
        }
        self.save_memory()

    # ------------------------------------------------------
    # PERSONAL HINT
    # ------------------------------------------------------
    def get_personal_hint(self):
        """
        Robi’nin araya doğal bir “hatırlatma” cümlesi sıkıştırmasını sağlar.
        Doğallık için %80 hiç bir şey söylemez.
        """
        if not self.data["likes"] and not self.data["topics"]:
            return ""

        # 80% sessiz
        if random.random() < 0.8:
            return ""

        # Like varsa önce onu söyle
        if self.data["likes"]:
            last_like = self.data["likes"][-1]
            return f" Bu arada en son {last_like} sevdiğini söylemiştin."

        # Yoksa topic
        if self.data["topics"]:
            last_topic = self.data["topics"][-1]["text"]
            short = " ".join(last_topic.split()[:5])
            return f" Geçen gün '{short}...' demiştin, aklımda."

        return ""
