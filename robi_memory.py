"""
robi_memory.py — ROBI SQLite Hafıza Sistemi
Kişi profilleri, tercihler, sağlık notları ve konuşma geçmişi.

Tablolar:
  profiles      — kişi profili (isim, kaç kez görüldü, vb.)
  preferences   — sevdikleri/sevmedikleri/ilgi alanları
  health_notes  — ilaçlar, hastalıklar, rutinler, kişisel notlar
  conversations — konuşma geçmişi (GPT bağlamı için)

Kullanım:
  mem = RobiMemory()
  mem.seen_person("Selma")
  mem.add_health_note("Selma", "medication", "Metformin", "sabah aç karna, 1 tablet")
  mem.add_health_note("Selma", "condition",  "Diyabet tip 2")
  context = mem.build_context("Selma")    # GPT system prompt'una eklenir
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from config import DB_PATH, CONV_HISTORY_N


# ─── Veritabanı şeması ────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    name            TEXT PRIMARY KEY,
    display_name    TEXT,
    first_seen      REAL,
    last_seen       REAL,
    greeting_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS preferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT    NOT NULL,
    category    TEXT    NOT NULL,   -- 'likes' | 'dislikes' | 'interests'
    item        TEXT    NOT NULL,
    sentiment   INTEGER DEFAULT 1,  -- 1=olumlu -1=olumsuz
    created_at  REAL
);

CREATE TABLE IF NOT EXISTS health_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    detail      TEXT    DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_at  REAL,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT    NOT NULL DEFAULT 'unknown',
    role        TEXT    NOT NULL,   -- 'user' | 'assistant'
    text        TEXT    NOT NULL,
    created_at  REAL
);
"""

# health_notes category değerleri
# 'medication' — ilaç (Metformin, Aspirin...)
# 'condition'  — hastalık/durum (diyabet, hipertansiyon...)
# 'routine'    — günlük rutin (sabah yürüyüşü, öğle uykusu...)
# 'allergy'    — alerji
# 'note'       — genel not


class RobiMemory:

    def __init__(self, db_path: Path = DB_PATH):
        self._path = str(db_path)
        self._init_db()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self._path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self):
        with self._conn() as con:
            con.executescript(_SCHEMA)

    # ── Profil ────────────────────────────────────────────────────────────────

    def seen_person(self, name: str) -> None:
        """Kişiyi gördüğümüzde çağır. Yoksa oluşturur, varsa günceller."""
        now = time.time()
        with self._conn() as con:
            existing = con.execute(
                "SELECT name FROM profiles WHERE name=?", (name,)
            ).fetchone()
            if existing:
                con.execute(
                    "UPDATE profiles SET last_seen=?, greeting_count=greeting_count+1 WHERE name=?",
                    (now, name)
                )
            else:
                con.execute(
                    "INSERT INTO profiles (name, display_name, first_seen, last_seen, greeting_count) VALUES (?,?,?,?,1)",
                    (name, name, now, now)
                )

    def get_profile(self, name: str) -> Optional[dict]:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM profiles WHERE name=?", (name,)
            ).fetchone()
            return dict(row) if row else None

    def all_known_names(self) -> List[str]:
        with self._conn() as con:
            rows = con.execute("SELECT name FROM profiles ORDER BY last_seen DESC").fetchall()
            return [r["name"] for r in rows]

    # ── Tercihler ─────────────────────────────────────────────────────────────

    def add_preference(self, user: str, category: str, item: str, sentiment: int = 1) -> None:
        """category: 'likes' | 'dislikes' | 'interests'"""
        item = item.strip().lower()
        if not item or len(item) < 2:
            return
        with self._conn() as con:
            existing = con.execute(
                "SELECT id FROM preferences WHERE user=? AND category=? AND item=?",
                (user, category, item)
            ).fetchone()
            if existing:
                con.execute(
                    "UPDATE preferences SET sentiment=?, created_at=? WHERE id=?",
                    (sentiment, time.time(), existing["id"])
                )
            else:
                con.execute(
                    "INSERT INTO preferences (user, category, item, sentiment, created_at) VALUES (?,?,?,?,?)",
                    (user, category, item, sentiment, time.time())
                )

    def get_preferences(self, user: str) -> dict:
        """{'likes': [...], 'dislikes': [...], 'interests': [...]}"""
        with self._conn() as con:
            rows = con.execute(
                "SELECT category, item FROM preferences WHERE user=? ORDER BY created_at DESC LIMIT 30",
                (user,)
            ).fetchall()
        result: dict = {"likes": [], "dislikes": [], "interests": []}
        for r in rows:
            cat = r["category"]
            if cat in result:
                result[cat].append(r["item"])
        return result

    # ── Sağlık notları ────────────────────────────────────────────────────────

    def add_health_note(self, user: str, category: str,
                        content: str, detail: str = "") -> None:
        """
        Sağlık notu ekle veya güncelle.
        category : 'medication' | 'condition' | 'routine' | 'allergy' | 'note'
        content  : kısa başlık  (örn. "Metformin", "Diyabet", "Sabah yürüyüşü")
        detail   : ek bilgi     (örn. "sabah aç karna 1 tablet", "tip 2")
        """
        content = content.strip()
        if not content or len(content) < 2:
            return
        now = time.time()
        with self._conn() as con:
            existing = con.execute(
                "SELECT id FROM health_notes WHERE user=? AND category=? AND lower(content)=lower(?)",
                (user, category, content)
            ).fetchone()
            if existing:
                con.execute(
                    "UPDATE health_notes SET detail=?, active=1, updated_at=? WHERE id=?",
                    (detail.strip(), now, existing["id"])
                )
            else:
                con.execute(
                    """INSERT INTO health_notes
                       (user, category, content, detail, active, created_at, updated_at)
                       VALUES (?,?,?,?,1,?,?)""",
                    (user, category, content, detail.strip(), now, now)
                )
        print(f"[MEMORY] 💾 Sağlık notu: [{category}] {content} — {detail}")

    def remove_health_note(self, user: str, content: str) -> bool:
        """İsme göre sağlık notunu pasif yap."""
        with self._conn() as con:
            cur = con.execute(
                "UPDATE health_notes SET active=0, updated_at=? WHERE user=? AND lower(content)=lower(?)",
                (time.time(), user, content)
            )
            return cur.rowcount > 0

    def get_health_notes(self, user: str) -> List[dict]:
        """Aktif sağlık notlarını döndür."""
        with self._conn() as con:
            rows = con.execute(
                """SELECT category, content, detail FROM health_notes
                   WHERE user=? AND active=1
                   ORDER BY category, created_at""",
                (user,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_medications(self, user: str) -> List[dict]:
        """Sadece ilaçları döndür."""
        with self._conn() as con:
            rows = con.execute(
                """SELECT content, detail FROM health_notes
                   WHERE user=? AND category='medication' AND active=1
                   ORDER BY created_at""",
                (user,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Konuşma geçmişi ───────────────────────────────────────────────────────

    def log(self, user: str, role: str, text: str) -> None:
        """Konuşmayı kaydet. role: 'user' | 'assistant'"""
        with self._conn() as con:
            con.execute(
                "INSERT INTO conversations (user, role, text, created_at) VALUES (?,?,?,?)",
                (user, role, text.strip(), time.time())
            )

    def get_history(self, user: str, n: int = CONV_HISTORY_N) -> List[dict]:
        """Son n mesajı [{role, content}, ...] formatında döndür."""
        with self._conn() as con:
            rows = con.execute(
                """SELECT role, text FROM conversations
                   WHERE user=?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (user, n)
            ).fetchall()
        return [{"role": r["role"], "content": r["text"]} for r in reversed(rows)]

    def get_recent_user_text(self, user: str, n: int = 6) -> str:
        """Son n kullanıcı mesajını tek string olarak döndür (öğrenme için)."""
        with self._conn() as con:
            rows = con.execute(
                """SELECT text FROM conversations
                   WHERE user=? AND role='user'
                   ORDER BY created_at DESC LIMIT ?""",
                (user, n)
            ).fetchall()
        return " | ".join(r["text"] for r in reversed(rows))

    # ── GPT bağlamı ───────────────────────────────────────────────────────────

    def build_context(self, user: str) -> str:
        """
        GPT system prompt'una eklenecek kişisel bağlam metni.
        Sağlık notları, tercihler, profil bilgisi içerir.
        """
        profile = self.get_profile(user)
        prefs   = self.get_preferences(user)
        health  = self.get_health_notes(user)

        parts = []

        if profile:
            parts.append(f"Kullanıcı adı: {profile['display_name'] or user}")
            if profile["greeting_count"] > 1:
                parts.append(f"Bu kişiyle daha önce {profile['greeting_count']} kez görüştün.")

        # Sağlık notları — öncelikli bağlam
        if health:
            meds       = [h for h in health if h["category"] == "medication"]
            conditions = [h for h in health if h["category"] == "condition"]
            allergies  = [h for h in health if h["category"] == "allergy"]
            routines   = [h for h in health if h["category"] == "routine"]
            notes      = [h for h in health if h["category"] == "note"]

            if meds:
                med_strs = []
                for m in meds:
                    s = m["content"]
                    if m["detail"]:
                        s += f" ({m['detail']})"
                    med_strs.append(s)
                parts.append("İlaçları: " + ", ".join(med_strs))

            if conditions:
                parts.append("Sağlık durumları: " + ", ".join(
                    f"{c['content']}{' — ' + c['detail'] if c['detail'] else ''}"
                    for c in conditions
                ))

            if allergies:
                parts.append("Alerjileri: " + ", ".join(a["content"] for a in allergies))

            if routines:
                parts.append("Günlük rutinleri: " + ", ".join(
                    f"{r['content']}{' (' + r['detail'] + ')' if r['detail'] else ''}"
                    for r in routines
                ))

            if notes:
                parts.append("Notlar: " + "; ".join(n["content"] for n in notes))

        # Genel tercihler
        if prefs["likes"]:
            parts.append("Sevdikleri: " + ", ".join(prefs["likes"][:6]))
        if prefs["dislikes"]:
            parts.append("Sevmedikleri: " + ", ".join(prefs["dislikes"][:4]))
        if prefs["interests"]:
            parts.append("İlgi alanları: " + ", ".join(prefs["interests"][:6]))

        return "\n".join(parts)

    def migrate_user(self, old_name: str, new_name: str) -> None:
        """'unknown' ismiyle kaydedilmiş verileri gerçek isme taşır."""
        with self._conn() as con:
            for table in ("conversations", "preferences", "health_notes"):
                con.execute(
                    f"UPDATE {table} SET user=? WHERE user=?", (new_name, old_name)
                )

    # ── Konuşmadan tercih çıkarımı (kural tabanlı, hızlı) ────────────────────

    def extract_and_save_prefs(self, user: str, text: str) -> None:
        """Kullanıcı cümlesinden kural-tabanlı tercih ve sağlık notu çıkarımı."""
        t = text.lower()

        # Olumlu ifadeler
        for kw in ("severim", "çok seviyorum", "bayılıyorum", "hoşlanıyorum"):
            if kw in t:
                item = t.split(kw)[0].strip().split()[-1] if t.split(kw)[0].strip() else ""
                if item and len(item) > 2:
                    self.add_preference(user, "likes", item, 1)

        # Olumsuz ifadeler
        for kw in ("sevmem", "sevmiyorum", "hiç sevmem", "nefret ederim"):
            if kw in t:
                item = t.split(kw)[0].strip().split()[-1] if t.split(kw)[0].strip() else ""
                if item and len(item) > 2:
                    self.add_preference(user, "dislikes", item, -1)

        # İlaç ifadeleri — basit kural
        # "ilacımı aldım", "ilacı içtim", "ilaç alıyorum" gibi
        import re
        m = re.search(
            r"(\w+(?:\s+\w+)?)\s+(?:ilacı?m?ı?|hapı?m?ı?|tabletim?i?)\s*"
            r"(?:aldım|içtim|alıyorum|içiyorum|kullanıyorum|kullanıyordum)",
            t, re.IGNORECASE
        )
        if m:
            med_name = m.group(1).strip()
            if len(med_name) > 2 and med_name not in {"benim", "onun", "kendi", "sabah", "akşam"}:
                self.add_health_note(user, "medication", med_name.capitalize())

        # Hastalık ifadeleri
        m2 = re.search(
            r"(?:var|var\s+bende|yaşıyorum|çekiyorum|hastayım)\s*[,.]?\s*"
            r"(\w+(?:\s+\w+)?)\s*(?:hastalığı?m?|rahatsızlığı?m?|sorunum?)",
            t, re.IGNORECASE
        )
        if m2:
            condition = m2.group(1).strip()
            if len(condition) > 2:
                self.add_health_note(user, "condition", condition.capitalize())

        # Alerji
        m3 = re.search(r"(\w+(?:\s+\w+)?)\s*(?:alerjim|alerjisi|alerjiye)", t, re.IGNORECASE)
        if m3:
            allergen = m3.group(1).strip()
            if len(allergen) > 2:
                self.add_health_note(user, "allergy", allergen.capitalize())
