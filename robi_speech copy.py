# robi_speech.py
# Tek işi: ROBI'yi konuşturmak + LED yüz senkronu

import time
import subprocess

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

client = OpenAI() if OpenAI else None


import os
print("OPENAI_API_KEY in env:", bool(os.getenv("OPENAI_API_KEY")))

USE_HW = False

if USE_HW:
    from robi_hw import face_thinking, face_speaking, face_listening, anim_transition
else:
    def face_thinking(): pass
    def face_speaking(): pass
    def face_listening(): pass
    def anim_transition(*a, **k): pass

is_speaking = False
stop_speaking_flag = False
tts_process = None

def speaking_now():
    return False

def speak(text: str):
    global is_speaking, stop_speaking_flag, tts_process

    text = (text or "").strip()
    if not text:
        return

    if client is None:
        print(f"[TTS:FALLBACK] {text}")
        return

    stop_speaking_flag = False
    is_speaking = True

    try:
        # animasyon senkronu
        face_thinking()
        anim_transition()
        face_speaking()

        print(f"\n[ROBI SES] {text}\n")

        # TTS wav üret
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="verse",
            input=text,
            response_format="wav",
        ) as response:
            response.stream_to_file("tts.wav")

        # çal
        try:
            tts_process = subprocess.Popen(["aplay", "tts.wav"])
        except Exception as e:
            print("TTS PLAY ERROR:", e)
            return

        while tts_process.poll() is None:
            if stop_speaking_flag:
                try:
                    tts_process.terminate()
                except Exception:
                    pass
                break
            time.sleep(0.05)

    finally:
        # ✅ NE OLURSA OLSUN: TALKING'de takılma + mic lock temizle
        is_speaking = False
        try:
            if os.path.exists("/tmp/robi_mic.lock"):
                os.remove("/tmp/robi_mic.lock")
        except Exception:
            pass

        # yüzü dinlemeye al
        try:
            face_listening()
        except Exception:
            pass

        time.sleep(0.15)

def _fallback_say(text: str):
    # terminale yazmak yerine gerçekten ses ver
    try:
        # espeak-ng -> wav -> aplay
        subprocess.run(["espeak-ng", "-v", "tr", "-s", "155", text], check=True)
        return True
    except Exception:
        pass
    try:
        subprocess.run(["espeak", "-v", "tr", "-s", "155", text], check=True)
        return True
    except Exception:
        pass
    print(f"[TTS:FALLBACK] {text}")
    return False

def stop_speaking():
    global is_speaking, stop_speaking_flag, tts_process
    stop_speaking_flag = True
    is_speaking = False
    try:
        if tts_process and tts_process.poll() is None:
            tts_process.terminate()
    except Exception as e:
        print("TTS terminate error:", e)
    face_listening()
