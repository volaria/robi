#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time

from robi_bus import BusClient
from robi_speech import speak, speaking_now
from robi_core import CoreAction, Event, EventType, RobiCore
from robi_constants import BUS_SOCKET
from robi_events import make_event  # ileride kullanacağız
from openai import OpenAI

client = OpenAI()

SYSTEM_PROMPT = (
    "Sen ROBİ adında bir ev robotusun. "
    "Kısa, doğal ve samimi cevaplar ver. "
    "Türkçe konuş. Gereksiz uzatma yapma."
)

class RobiBrain:
    def __init__(self):
        self.bus = BusClient(BUS_SOCKET)
        self.core = RobiCore()

        # 🧠 Conversational memory (v11 ruhu)
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        print("[BRAIN] 🧠 ROBI Brain online (bus)")
        print("[BRAIN] 👂 Brain listening...")

    # -----------------------------
    # Helpers
    # -----------------------------
    def _wait_tts_end(self):
        while speaking_now():
            time.sleep(0.05)

    # -----------------------------
    # Bus → Core mapping
    # -----------------------------
    def map_bus_event_to_core(self, ev: dict):
        if ev.get("type") == "speech.heard":
            return Event(
                EventType.SPEECH_HEARD,
                payload={
                    "text": ev.get("payload", {}).get("text", "")
                }
            )

        typ = ev.get("type")

        if typ == "DONE":
            return Event(EventType.SPEAK_DONE)

        if typ == "WAKE":
            return Event(
                EventType.WAKE_WORD,
                payload={
                    "heard": ev.get("heard"),
                    "confidence": ev.get("confidence"),
                },
            )

        if typ == "UTTERANCE":
            return Event(
                EventType.AUDIO_TEXT,
                payload={"text": ev.get("text", "")},
            )

        if typ == "TIMEOUT":
            return Event(EventType.TIMEOUT)

        if typ == "PERSON_DETECTED":
            return Event(
                EventType.PERSON_DETECTED,
                payload=ev.get("payload"),
            )

        if typ == "UNKNOWN_PERSON":
            return Event(
                EventType.UNKNOWN_PERSON,
                payload=ev.get("payload"),
            )

        # bilinmeyen event → core'a gitmez
        return None

    # -----------------------------
    # Apply action (Core → real world)
    # -----------------------------
    def apply_action(self, action: CoreAction, event_payload=None):
        if action == CoreAction.NONE:
            return

        if action == CoreAction.SAY_ACK:
            speak("Efendim")
            self._wait_tts_end()
            # konuşma bitti: core'a haber ver
            self.core.handle_event(Event(EventType.SPEAK_DONE))
            # audio'ya dinle komutu
            self.bus.publish({"type": "LISTEN", "ts": time.time()})
            return

        if action == CoreAction.ASK_IDENTITY:
            speak("Seni tanıyamadım, sen kimsin?")
            self._wait_tts_end()
            # burada SPEAK_DONE YOK: çünkü isim (UTTERANCE) bekleyeceğiz
            # audio zaten LISTEN modundaysa sorun yok; değilse dinlemeyi başlat:
            self.bus.publish({"type": "LISTEN", "ts": time.time()})
            return

        if action == CoreAction.START_LISTEN:
            self.bus.publish({"type": "LISTEN", "ts": time.time()})
            return

        if action == CoreAction.RESPOND_TEXT:
            user_text = (event_payload or {}).get("text", "").strip()
            if not user_text:
                return

            self.messages.append({"role": "user", "content": user_text})

            try:
                resp = client.responses.create(
                    model="gpt-5",
                    input=self.messages,
                )
                reply = (resp.output_text or "").strip()
            except Exception:
                reply = "Şu an düşünemiyorum."

            self.messages.append({"role": "assistant", "content": reply})

            speak(reply)
            self._wait_tts_end()

            self.core.handle_event(Event(EventType.SPEAK_DONE))
            return

        # bilmediğimiz action olursa sessiz kal
        return

    # -----------------------------
    # Event dispatch
    # -----------------------------
    def handle_bus_event(self, raw_ev: dict):
        core_event = self.map_bus_event_to_core(raw_ev)
        if not core_event:
            return

        action = self.core.handle_event(core_event)
        self.apply_action(action, core_event.payload)

    # -----------------------------
    # Main loop
    # -----------------------------
    def run(self):
        try:
            while True:
                ev = self.bus.recv(timeout=0.2)
                if not ev:
                    continue
                self.handle_bus_event(ev)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print("[BRAIN][ERR]", e)


if __name__ == "__main__":
    RobiBrain().run()
