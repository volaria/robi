#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading
from robi_constants import BUS_SOCKET

subscribers = set()
sub_lock = threading.Lock()

# --- BusClient (brain/audio kullanacak) ---
import socket, json

class BusClient:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.pub = self._connect("PUB")
        self.sub = self._connect("SUB")
        self._buf = b""

    def _connect(self, role: str):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.sendall((role + "\n").encode())
        return s

    def publish(self, ev: dict):
        self.pub.sendall((json.dumps(ev, ensure_ascii=False) + "\n").encode())

    def recv(self, timeout: float = 0.2):
        self.sub.settimeout(timeout)
        try:
            chunk = self.sub.recv(4096)
            if not chunk:
                return None
            self._buf += chunk
        except socket.timeout:
            return None
        except BlockingIOError:
            return None

        if b"\n" not in self._buf:
            return None

        line, self._buf = self._buf.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            return None

def safe_close(conn):
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass

def broadcast(line: bytes):
    dead = []
    with sub_lock:
        for s in list(subscribers):
            try:
                s.sendall(line)
            except Exception:
                dead.append(s)
        for s in dead:
            subscribers.discard(s)
            safe_close(s)

def handle_client(conn: socket.socket):
    role = "pub"
    try:
        # First line can be "SUB\n" or "PUB\n"
        first = b""
        while b"\n" not in first and len(first) < 64:
            chunk = conn.recv(64)
            if not chunk:
                return
            first += chunk

        if first.startswith(b"SUB"):
            role = "sub"
        elif first.startswith(b"PUB"):
            role = "pub"
        else:
            # Not a role header → treat as first message from publisher
            role = "pub"
            broadcast(first)

        if role == "sub":
            with sub_lock:
                subscribers.add(conn)
            # Keep socket open
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
            return

        # publisher
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    broadcast(line + b"\n")

    except OSError:
        # client died
        pass
    finally:
        if role == "sub":
            with sub_lock:
                subscribers.discard(conn)
        safe_close(conn)

def main():
    if os.path.exists(BUS_SOCKET):
        os.remove(BUS_SOCKET)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(BUS_SOCKET)
    os.chmod(BUS_SOCKET, 0o666)
    srv.listen(32)

    print("[BUS] 🚌 ROBI Bus online:", BUS_SOCKET)

    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print("[BUS][ERR]", e)
    finally:
        safe_close(srv)
        try:
            os.remove(BUS_SOCKET)
        except Exception:
            pass
        print("[BUS] 🚌 ROBI Bus offline")

if __name__ == "__main__":
    main()
