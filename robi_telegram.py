"""
robi_telegram.py — Telegram üzerinden mesaj ve fotoğraf gönderimi.

Kullanım:
    send_message("Merhaba!")
    send_photo("/tmp/robi_latest_frame.jpg", caption="İşte ben!")
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import json
from pathlib import Path
from typing import Optional

from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def _api(endpoint: str, data: dict) -> bool:
    """Telegram Bot API'ye istek gönder."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM] ❌ API hatası: {e}")
        return False


def send_message(text: str) -> bool:
    """Telegram'a metin mesajı gönder."""
    ok = _api("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })
    if ok:
        print(f"[TELEGRAM] ✅ Mesaj gönderildi: {text[:50]}")
    return ok


def send_reminder(label: str, user: str = "", with_photo: bool = False) -> bool:
    """
    Hatırlatıcı bildirimi gönder.
    label     : hatırlatıcı metni ("İlaç vakti: Metformin")
    user      : kime ait ("Selma", "Volkan" vb.) — boş bırakılabilir
    with_photo: True ise kamera fotoğrafını da ekle
    """
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")

    # Mesaj metni
    if user:
        text = f"🔔 <b>ROBI Hatırlatıcı</b> — {now}\n\n👤 {user}\n📌 {label}"
    else:
        text = f"🔔 <b>ROBI Hatırlatıcı</b> — {now}\n\n📌 {label}"

    ok = send_message(text)

    # İsteğe bağlı fotoğraf
    if with_photo:
        send_photo("/tmp/robi_latest_frame.jpg",
                   caption=f"{user or 'ROBI'} — {now}")

    return ok


def send_photo(image_path: str, caption: str = "") -> bool:
    """Telegram'a fotoğraf gönder (multipart/form-data)."""
    import urllib.error

    path = Path(image_path)
    if not path.exists():
        print(f"[TELEGRAM] ❌ Fotoğraf bulunamadı: {image_path}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    boundary = "----RobiBoundary7x3k"
    body = b""

    # chat_id field
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += f"{TELEGRAM_CHAT_ID}\r\n".encode()

    # caption field
    if caption:
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
        body += f"{caption}\r\n".encode()

    # photo field
    img_data = path.read_bytes()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'.encode()
    body += b"Content-Type: image/jpeg\r\n\r\n"
    body += img_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            if result.get("ok"):
                print(f"[TELEGRAM] ✅ Fotoğraf gönderildi: {path.name}")
                return True
            else:
                print(f"[TELEGRAM] ❌ API yanıtı: {result}")
                return False
    except Exception as e:
        print(f"[TELEGRAM] ❌ Fotoğraf gönderilemedi: {e}")
        return False
