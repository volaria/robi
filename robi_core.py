"""
ROBI Core (Product v1)

Event-driven, state-based core.
Legacy reference: legacy/robi_v11_reference.py
"""

import time
from enum import Enum, auto
from dataclasses import dataclass
from typing import Any, Optional


# ---- Core actions (CORE -> Brain) ----
class CoreAction(Enum):
    NONE = auto()
    SAY_ACK = auto()
    START_LISTEN = auto()
    RESPOND_TEXT = auto()
    START_THINKING = auto()
    ASK_IDENTITY = auto()
    SAY_SLEEP = auto()


# ---- States (Core internal) ----
class State(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()
    AUTO_LISTEN = auto()
    WAITING_FOR_NAME = auto()


# ---- Events (Bus -> Core) ----
class EventType(Enum):
    WAKE_WORD = auto()
    AUDIO_TEXT = auto()
    RESPONSE_READY = auto()
    SPEAK_DONE = auto()
    TIMEOUT = auto()
    PERSON_DETECTED = auto()
    UNKNOWN_PERSON = auto()


@dataclass
class Event:
    type: EventType
    payload: Optional[Any] = None


# ---- Core ----
class RobiCore:
    AUTO_LISTEN_TIMEOUT = 60.0  # saniye

    def __init__(self):
        self.state = State.IDLE
        self.auto_listen_started_at: Optional[float] = None
        self.auto_listen_listening = False
        print(f"[CORE] init state={self.state.name}")

    # -----------------------------
    # Memory (Phase-1)
    # -----------------------------
    def memory_remember_person(self, name: str):
        with open("known_people.txt", "a", encoding="utf-8") as f:
            f.write(name + "\n")

    # -----------------------------
    # Main state machine
    # -----------------------------
    def handle_event(self, event: Event) -> CoreAction:
        print(f"[CORE] event={event.type.name} state={self.state.name}")

        # ⛔ WAKE yalnızca BUSY iken yok sayılır
        if event.type == EventType.WAKE_WORD:
            if self.state in (State.LISTENING, State.SPEAKING):
                print("[CORE] WAKE ignored (busy)")
                return CoreAction.NONE
            # IDLE ve AUTO_LISTEN için aşağı akacak

        # =========================
        # IDLE
        # =========================
        if self.state == State.IDLE:
            if event.type == EventType.WAKE_WORD:
                self.state = State.LISTENING
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.SAY_ACK
            return CoreAction.NONE

        # =========================
        # LISTENING
        # =========================
        if self.state == State.LISTENING:
            if event.type == EventType.SPEAK_DONE:
                print("[CORE] LISTENING -> start listen after ack")
                return CoreAction.START_LISTEN

            if event.type == EventType.AUDIO_TEXT:
                text = (event.payload or {}).get("text", "").strip()
                if not text:
                    self.state = State.AUTO_LISTEN
                    self.auto_listen_started_at = time.time()
                    self.auto_listen_listening = False
                    print(f"[CORE] -> state={self.state.name} (empty text)")
                    return CoreAction.START_LISTEN

                self.state = State.THINKING
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.START_THINKING

            if event.type == EventType.TIMEOUT:
                self.state = State.AUTO_LISTEN
                self.auto_listen_started_at = time.time()
                self.auto_listen_listening = False
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.START_LISTEN

            return CoreAction.NONE

        # =========================
        # SPEAKING
        # =========================
        if self.state == State.SPEAKING:
            if event.type == EventType.SPEAK_DONE:
                self.state = State.AUTO_LISTEN
                self.auto_listen_started_at = time.time()
                self.auto_listen_listening = False
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.START_LISTEN

            return CoreAction.NONE

        # =========================
        # THINKING
        # =========================
        if self.state == State.THINKING:
            if event.type == EventType.RESPONSE_READY:
                self.state = State.SPEAKING
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.RESPOND_TEXT
            return CoreAction.NONE

        # =========================
        # AUTO_LISTEN (pasif ama mic açık)
        # =========================
        if self.state == State.AUTO_LISTEN:
            # AUTO_LISTEN sırasında WAKE gelirse: ACK yok, sadece yeniden LISTEN
            if event.type == EventType.WAKE_WORD:
                self.auto_listen_listening = False
                print("[CORE] AUTO_LISTEN -> re-listen after WAKE")
                return CoreAction.START_LISTEN

            # Kullanıcı konuştu
            if event.type == EventType.AUDIO_TEXT:
                text = (event.payload or {}).get("text", "").strip()
                if not text:
                    self.auto_listen_listening = False
                    print("[CORE] AUTO_LISTEN -> re-listen after empty text")
                    return CoreAction.START_LISTEN

                self.state = State.THINKING
                print(f"[CORE] -> state={self.state.name}")
                return CoreAction.START_THINKING

            # Tek seans dinleme time-out olduysa tekrar LISTEN
            if event.type == EventType.TIMEOUT:
                self.auto_listen_listening = False
                print("[CORE] AUTO_LISTEN -> re-listen after TIMEOUT")
                return CoreAction.START_LISTEN

            # Mic'in açık olduğundan emin ol (bir kere)
            if not self.auto_listen_listening:
                self.auto_listen_listening = True
                print("[CORE] AUTO_LISTEN -> ensure LISTEN")
                return CoreAction.START_LISTEN

            # Uzun sessizlik → uyku mesajı
            if (
                self.auto_listen_started_at
                and time.time() - self.auto_listen_started_at >= self.AUTO_LISTEN_TIMEOUT
            ):
                self.state = State.SPEAKING
                print(f"[CORE] -> state={self.state.name} (sleep message)")
                return CoreAction.SAY_SLEEP

            return CoreAction.NONE

        # =========================
        # WAITING_FOR_NAME
        # =========================
        if self.state == State.WAITING_FOR_NAME:
            if event.type == EventType.AUDIO_TEXT:
                name = (event.payload or {}).get("text", "").strip()
                if name:
                    self.memory_remember_person(name)
                    print(f"[CORE] remembered name={name}")
                self.state = State.IDLE
                print(f"[CORE] -> state={self.state.name}")
            return CoreAction.NONE

        return CoreAction.NONE
