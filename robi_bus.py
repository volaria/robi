"""
robi_bus.py — ROBI Pub/Sub mesaj otobüsü
Tek sorumluluk: publisher'lardan mesaj al, tüm subscriber'lara ilet.

Kullanım:
  python3 robi_bus.py          ← sunucu olarak başlat
  from robi_bus import BusClient  ← client olarak kullan
"""

import json
import os
import socket
import threading
from typing import Optional

from config import BUS_SOCKET


# ─── Sunucu tarafı ────────────────────────────────────────────────────────────

_subscribers: set  = set()
_sub_lock          = threading.Lock()


def _safe_close(conn: socket.socket) -> None:
    try: conn.shutdown(socket.SHUT_RDWR)
    except Exception: pass
    try: conn.close()
    except Exception: pass


def _broadcast(line: bytes) -> None:
    dead = []
    with _sub_lock:
        for s in list(_subscribers):
            try:
                s.sendall(line)
            except Exception:
                dead.append(s)
        for s in dead:
            _subscribers.discard(s)
            _safe_close(s)


def _handle_client(conn: socket.socket) -> None:
    role = "pub"
    try:
        # İlk satır: "PUB\n" veya "SUB\n"
        header = b""
        while b"\n" not in header and len(header) < 64:
            chunk = conn.recv(64)
            if not chunk:
                return
            header += chunk

        if header.startswith(b"SUB"):
            role = "sub"
            with _sub_lock:
                _subscribers.add(conn)
            # Bağlantıyı açık tut
            while True:
                if not conn.recv(1024):
                    break
            return

        # Publisher: header'ın geri kalanı ilk mesaj olabilir
        buf = header[header.index(b"\n") + 1:] if b"\n" in header else b""

        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    _broadcast(line + b"\n")

    except OSError:
        pass
    finally:
        if role == "sub":
            with _sub_lock:
                _subscribers.discard(conn)
        _safe_close(conn)


def main() -> None:
    if os.path.exists(BUS_SOCKET):
        os.remove(BUS_SOCKET)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(BUS_SOCKET)
    os.chmod(BUS_SOCKET, 0o666)
    srv.listen(32)

    print(f"[BUS] 🚌 online → {BUS_SOCKET}")

    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle_client, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        _safe_close(srv)
        try: os.remove(BUS_SOCKET)
        except Exception: pass
        print("[BUS] offline")


# ─── Client tarafı ────────────────────────────────────────────────────────────

class BusClient:
    """
    Thread-safe değil — her thread kendi BusClient'ını oluştursun.
    """

    def __init__(self, sock_path: str = BUS_SOCKET):
        self.sock_path = sock_path
        self._pub  = self._connect("PUB")
        self._sub  = self._connect("SUB")
        self._buf  = b""

    def _connect(self, role: str) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.sendall((role + "\n").encode())
        return s

    def publish(self, ev: dict) -> None:
        line = (json.dumps(ev, ensure_ascii=False) + "\n").encode()
        try:
            self._pub.sendall(line)
        except Exception as e:
            print(f"[BUS][PUB] hata: {e}")

    def recv(self, timeout: float = 0.1) -> Optional[dict]:
        """
        Mesaj varsa dict döndürür, yoksa None.
        timeout saniye kadar bekler.
        """
        self._sub.settimeout(timeout)
        try:
            chunk = self._sub.recv(4096)
            if chunk:
                self._buf += chunk
        except socket.timeout:
            pass
        except Exception:
            pass
        finally:
            try: self._sub.settimeout(None)
            except Exception: pass

        if b"\n" not in self._buf:
            return None

        line, self._buf = self._buf.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            return None

    def poll(self) -> Optional[dict]:
        """Non-blocking recv (timeout=0)."""
        return self.recv(timeout=0.0)


if __name__ == "__main__":
    main()
