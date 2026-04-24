"""
robi_reminders.py — ROBI Hatırlatıcı Servisi
Zamanlı hatırlatmaları kontrol eder, Bus'a REMINDER eventi yayar.

Veri dosyası: reminders.json (proje kökünde)
Bus eventi: {"type": "REMINDER", "label": "ilaç vakti", "ts": ...}
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from config import BUS_SOCKET
from robi_bus import BusClient


REMINDERS_FILE = Path(__file__).resolve().parent / "reminders.json"
CHECK_INTERVAL  = 30   # saniye — her dakikayı en az bir kez yakalar


# ─── Veri yönetimi ────────────────────────────────────────────────────────────

def _load() -> list:
    try:
        if REMINDERS_FILE.exists():
            return json.loads(REMINDERS_FILE.read_text("utf-8"))
    except Exception:
        pass
    return []


def _save(reminders: list) -> None:
    try:
        REMINDERS_FILE.write_text(
            json.dumps(reminders, ensure_ascii=False, indent=2), "utf-8"
        )
    except Exception as e:
        print(f"[REMIND] ❌ Kaydetme hatası: {e}")


def add_reminder(label: str, hour: int, minute: int,
                 repeat: str = "once",
                 on_date: Optional[str] = None,
                 user: str = "") -> dict:
    """
    Yeni hatırlatıcı ekle.
    repeat : "once" (tek seferlik) | "daily" (her gün)
    on_date: "YYYY-MM-DD"  — sadece repeat="once" için (varsayılan: bugün)
    user   : kimin hatırlatıcısı olduğunu belirtir (boş = herkese)
    """
    reminders = _load()
    rid   = uuid.uuid4().hex[:8]
    entry = {
        "id":     rid,
        "label":  label,
        "hour":   hour,
        "minute": minute,
        "repeat": repeat,
        "date":   on_date or (date.today().isoformat() if repeat == "once" else None),
        "active": True,
        "user":   user,   # kimin için kurulduğu
    }
    reminders.append(entry)
    _save(reminders)
    who = f" ({user})" if user else ""
    print(f"[REMIND] ✅ Eklendi: {label} @ {hour:02d}:{minute:02d} ({repeat}){who}")
    return entry


def list_reminders() -> list:
    """Aktif hatırlatıcıları döndür."""
    return [r for r in _load() if r.get("active")]


def delete_reminder(rid: str) -> bool:
    """ID'ye göre tek hatırlatıcı sil."""
    reminders = _load()
    for r in reminders:
        if r["id"] == rid:
            r["active"] = False
            _save(reminders)
            return True
    return False


def delete_all_reminders() -> int:
    """Tüm aktif hatırlatıcıları sil. Silinen sayısını döndür."""
    reminders = _load()
    count = sum(1 for r in reminders if r.get("active"))
    for r in reminders:
        r["active"] = False
    _save(reminders)
    return count


# ─── Servis ───────────────────────────────────────────────────────────────────

class RobiReminders:

    def __init__(self):
        self.bus    = BusClient(BUS_SOCKET)
        self._fired: set = set()   # "id_YYYY-MM-DD_HH:MM" — bu dakika tetiklenenleri tutar

    def _check(self) -> None:
        now  = datetime.now()
        slot = f"{now.date().isoformat()}_{now.hour:02d}:{now.minute:02d}"

        for r in list_reminders():
            if r["hour"] != now.hour or r["minute"] != now.minute:
                continue

            key = f"{r['id']}_{slot}"
            if key in self._fired:
                continue   # Bu dakika zaten tetiklendi

            # Tek seferlik → sadece doğru günde tetikle
            if r["repeat"] == "once":
                if r.get("date") and r["date"] != now.date().isoformat():
                    continue
                # Tetiklendikten sonra deaktif et
                delete_reminder(r["id"])

            self._fired.add(key)

            # Eski kayıtları temizle (bellek tasarrufu — 2 dakikadan eskiler)
            cutoff = f"{now.date().isoformat()}_{(now.hour * 60 + now.minute - 2) % (24*60):04d}"
            self._fired = {k for k in self._fired if k >= cutoff}

            print(f"[REMIND] 🔔 Tetiklendi: {r['label']} (kullanıcı: {r.get('user') or 'herkese'})")
            self.bus.publish({
                "type":  "REMINDER",
                "label": r["label"],
                "user":  r.get("user", ""),   # kimin için
                "ts":    time.time(),
            })

    def run(self) -> None:
        print("[REMIND] 🔔 Hatırlatıcı servisi online")
        try:
            while True:
                try:
                    self._check()
                except Exception as e:
                    print(f"[REMIND] ⚠ Kontrol hatası: {e}")
                time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            pass
        finally:
            print("[REMIND] offline")


def main() -> None:
    RobiReminders().run()


if __name__ == "__main__":
    main()
