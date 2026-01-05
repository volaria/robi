#robi_brain.py
import time

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from robi_bus import BusClient
from robi_speech import speak, speaking_now
from robi_core import CoreAction, Event, EventType, RobiCore, State
from robi_constants import BUS_SOCKET

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

    def _wait_tts_end(self):
        while speaking_now():
            time.sleep(0.05)

    # -----------------------------
    # Core → Real world
    # -----------------------------
    def apply_action(self, action: CoreAction, event_payload=None):
        if action == CoreAction.NONE:
            return

        if action == CoreAction.SAY_SLEEP:
            print("[ROBI] 🤖 İhtiyacın olursa buradayım.")
            speak("İhtiyacın olursa buradayım.")
            self._wait_tts_end()
            self.core.state = State.IDLE
            return

        if action == CoreAction.SAY_ACK:
            print("[ROBI] 🤖 Efendim")
            speak("Efendim")
            self._wait_tts_end()

            # konuşma bitti bilgisini Core'a ver
            action = self.core.handle_event(Event(EventType.SPEAK_DONE))
            self.apply_action(action)
            return

        if action == CoreAction.START_LISTEN:
            mode = "auto" if self.core.state == State.AUTO_LISTEN else "once"
            print(f"[BRAIN][DEBUG] START_LISTEN -> publish LISTEN ({mode})")
            self.bus.publish({"type": "LISTEN", "ts": time.time(), "mode": mode})
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

            print(f"[ROBI] 🤖 {reply}")

            speak(reply)
            self._wait_tts_end()

            action = self.core.handle_event(Event(EventType.SPEAK_DONE))
            self.apply_action(action)
            return

    # -----------------------------
    # Bus → Core
    # -----------------------------
    def handle_bus_event(self, ev: dict):
        core_event = None
        typ = ev.get("type")

        if typ == "WAKE":
            core_event = Event(EventType.WAKE_WORD)

        elif typ == "UTTERANCE":
            core_event = Event(
                EventType.AUDIO_TEXT,
                payload={"text": ev.get("text", "")},
            )

        elif typ == "DONE":
            core_event = Event(EventType.SPEAK_DONE)

        elif typ == "TIMEOUT":
            core_event = Event(EventType.TIMEOUT)

        if not core_event:
            return

        action = self.core.handle_event(core_event)
        self.apply_action(action, core_event.payload)

    # -----------------------------
    # Main loop
    # -----------------------------
    def run(self):
        while True:
            ev = self.bus.recv(timeout=0.2)
            if not ev:
                continue
            self.handle_bus_event(ev)


if __name__ == "__main__":
    RobiBrain().run()
