"""
robi_brain.py — ROBI AI Beyin
Tüm konuşma logic'i burada. Non-blocking: API çağrıları worker thread'lerde.

State machine:
  SLEEP       — uyku modu (vision'dan gelecek yüz/hareket uyandırır)
  IDLE        — uyanık, wake word bekleniyor
  LISTENING   — kullanıcı konuşması bekleniyor
  THINKING    — GPT'ye istek gönderildi, cevap bekleniyor
  SPEAKING    — TTS çalıyor
  AUTO_LISTEN — konuşma bitti, wake word olmadan devam edebilir

Bus'tan dinlenen eventler:
  WAKE         — wake word tespit edildi
  UTTERANCE    — kullanıcı konuşması {text, confidence}
  TIMEOUT      — dinleme zaman aşımı
  TTS_END      — TTS bitti
  FACE_SEEN    — tanınan yüz {name}
  FACE_UNKNOWN — tanınmayan yüz

Bus'a yayılan eventler:
  LISTEN       — audio servisine: dinlemeye geç {mode}
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

from config import (
    AUTO_LISTEN_TIMEOUT, BUS_SOCKET,
    CONV_HISTORY_N, LLM_MAX_TOKENS, LLM_MODEL, SYSTEM_PROMPT,
)
from robi_bus    import BusClient
from robi_memory import RobiMemory
from robi_speech import speak, play_end_chime
import robi_skills as skills

# LED matrix — donanım yoksa sessizce devre dışı
try:
    import robi_display as display
    _DISPLAY_OK = True
except Exception as _e:
    print(f"[BRAIN] ⚠ LED matrix yüklenemedi: {_e}")
    _DISPLAY_OK = False

# Fiziksel buton (robi_hw) — donanım yoksa sessizce devre dışı
try:
    import robi_hw as _hw
    _HW_OK = True
except Exception as _e:
    print(f"[BRAIN] ⚠ robi_hw yüklenemedi: {_e}")
    _HW_OK = False


# ─── Durumlar ─────────────────────────────────────────────────────────────────

SLEEP       = "SLEEP"
IDLE        = "IDLE"
LISTENING   = "LISTENING"
THINKING    = "THINKING"
SPEAKING    = "SPEAKING"
AUTO_LISTEN = "AUTO_LISTEN"


def _greeting_by_time() -> str:
    h = datetime.now().hour
    if 5  <= h < 12: return "günaydın"
    if 12 <= h < 18: return "iyi günler"
    if 18 <= h < 22: return "iyi akşamlar"
    return "iyi geceler"


class RobiBrain:

    _GREET_COOLDOWN = 600   # aynı kişiyi 10 dakikada bir selamla (saniye)

    def __init__(self):
        self.bus      = BusClient(BUS_SOCKET)
        self.memory   = RobiMemory()
        self.oai      = OpenAI(timeout=12.0)   # 12s'de cevap gelmezse hata ver

        self.state      = IDLE
        self.state_lock = threading.Lock()

        self.current_user: Optional[str] = None   # tanınan kişi
        self._auto_since: float = 0.0              # AUTO_LISTEN başlangıcı
        self._pending_name: bool = False           # isim bekliyoruz mu?
        self._listening_since: float = 0.0         # LISTENING başlangıcı (stale TIMEOUT koruması)
        self._wake_triggered: bool = False         # True = wake word ile LISTENING'e girildi
        self._last_greeted: dict = {}              # name → son selamlama zamanı
        self._pending_greet: Optional[str] = None  # konuşma biterken yapılacak bekleyen selamlama
        self._vision_confirmed_at: float = 0.0    # son vision onayı zamanı (isim override koruması)
        self._sleep_companion: Optional[str] = None  # uyurken zaten orada olan kişi — tekrar uyanmasın
        self._pending_utterance: Optional[str] = None  # THINKING/SPEAKING sırasında gelen son utterance
        self._stop_speaking: bool = False          # True → mevcut konuşmayı kes (SUS komutu)
        self._last_proactive: float = 0.0          # son proaktif followup zamanı

        # En son bilinen kullanıcıyı hafızadan yükle
        known = self.memory.all_known_names()
        if known:
            self.current_user = known[0]
            print(f"[BRAIN] 👤 Hafızadan yüklendi: {self.current_user}")

        # Fiziksel buton callback'i kaydet
        if _HW_OK:
            _hw.on_button_press(self._on_button_press)
            print("[BRAIN] 🔘 Susturma butonu aktif (GPIO 21)")

        print("[BRAIN] 🧠 ROBI Brain online")

    # ── State yardımcıları ────────────────────────────────────────────────────

    def _set_state(self, new_state: str) -> None:
        with self.state_lock:
            old = self.state
            self.state = new_state
            # AUTO_LISTEN geçişinde sleep timer'ı state ile atomik güncelle.
            # Watchdog state_lock alarak okur → lock içinde set edilen _auto_since'ı
            # kesinlikle güncel görür. Race condition tamamen ortadan kalkar.
            if new_state == AUTO_LISTEN:
                self._auto_since = time.time()
        if old != new_state:
            print(f"[BRAIN] state: {old} → {new_state}")
            # Vision'a uyku/uyanış sinyali gönder
            if new_state == SLEEP:
                self.bus.publish({"type": "BRAIN_SLEEP", "ts": time.time()})
            elif old == SLEEP and new_state != SLEEP:
                self.bus.publish({"type": "BRAIN_WAKE", "ts": time.time()})
            if _DISPLAY_OK:
                try:
                    if   new_state == SLEEP:       display.clear()
                    elif new_state == IDLE:         display.idle()
                    elif new_state == LISTENING:    display.listening()
                    elif new_state == THINKING:     display.thinking()
                    elif new_state == SPEAKING:     display.speaking()
                    elif new_state == AUTO_LISTEN:  display.idle()
                except Exception as _de:
                    print(f"[BRAIN] ⚠ Display hatası: {_de}")

    def _get_state(self) -> str:
        with self.state_lock:
            return self.state

    # ── GPT mesaj geçmişi ─────────────────────────────────────────────────────

    def _build_messages(self, user_text: str) -> list:
        """System prompt + kişisel bağlam + konuşma geçmişi + yeni mesaj."""
        user = self.current_user or "unknown"

        # Güncel tarih/saat her çağrıda taze eklenir
        now = datetime.now()
        DAYS   = ["Pazartesi","Salı","Çarşamba","Perşembe","Cuma","Cumartesi","Pazar"]
        MONTHS = ["","Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
                  "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
        date_str = f"{DAYS[now.weekday()]}, {now.day} {MONTHS[now.month]} {now.year}"
        time_str = now.strftime("%H:%M")

        # Konum bilgisi (IP tabanlı, önbellekli)
        city = skills._get_default_city()

        # Kişisel bağlam system prompt'a eklenir
        context = self.memory.build_context(user)
        city_display = "İstanbul" if city == "Istanbul" else city
        sys_content = (SYSTEM_PROMPT
                       + f"\n\n[Sistem bilgisi] Bugün {date_str}, saat {time_str}."
                       + f" Kullanıcı {city_display}'da bulunuyor."
                       + f" Konum veya şehir sorulursa '{city_display}' olduğunu söyle."
                       + f" Not: Güncel bilgi gerektiğinde sistem web araması yaparak cevap sağlar;"
                       + f" 'bilmiyorum' veya 'ulaşamıyorum' demek yerine kısa ve net cevap ver.")
        if context:
            sys_content += f"\n\n[Kullanıcı hakkında bilgiler]\n{context}"

        messages = [{"role": "system", "content": sys_content}]
        messages += self.memory.get_history(user, n=CONV_HISTORY_N)
        messages.append({"role": "user", "content": user_text})

        return messages

    # ── GPT çağrısı ───────────────────────────────────────────────────────────

    def _ask_gpt(self, user_text: str) -> str:
        try:
            messages = self._build_messages(user_text)
            resp     = self.oai.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                max_tokens=LLM_MAX_TOKENS,
                temperature=0.8,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            print(f"[BRAIN] GPT hatası: {e}")
            return "Şu an düşünemiyorum efendim."

    # ── Konuşma akışı (thread'de çalışır) ────────────────────────────────────

    def _say(self, text: str) -> None:
        """Konuş ve bitince duruma göre geçiş yap.
        Müzik/radyo çalıyorsa → IDLE (TV sesi komutu tetiklemesin).
        Normal → AUTO_LISTEN (uyandırma kelimesi olmadan devam edilebilir).
        Eğer THINKING/SPEAKING sırasında gelen bekleyen utterance varsa, direkt işle.
        """
        self._set_state(SPEAKING)
        speak(text)                         # bloklar (kendi thread'inde)
        play_end_chime()                    # tüm konuşma bitti → bip + LED flash
        if skills.is_music_playing():
            self._pending_utterance = None
            if self.current_user:
                # Tanınan kullanıcı varsa AUTO_LISTEN kal — yüksek RMS eşiğiyle müzik gürültüsü filtrele
                self._auto_since = time.time()
                self._set_state(AUTO_LISTEN)
                self.bus.publish({"type": "LISTEN", "mode": "auto",
                                  "music_mode": True, "ts": time.time()})
            else:
                # Kimse tanınmıyorsa IDLE — sahipsiz radyo sesini komut olarak alma
                self._set_state(IDLE)
        else:
            # Konuşma bitti — bekleyen mesaj var mı?
            pending = self._pending_utterance
            self._pending_utterance = None
            if pending:
                print(f"[BRAIN] 📋 Bekleyen mesaj işleniyor: {repr(pending[:40])}")
                self._set_state(THINKING)
                self._think_async(pending)
            else:
                self._auto_since = time.time()
                self._set_state(AUTO_LISTEN)
                self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": time.time()})

                # Bekleyen yüz selamlaması var mı? (konuşma sırasında görülen kişi)
                pending_name = self._pending_greet
                if pending_name:
                    self._pending_greet = None
                    now = time.time()
                    if now - self._last_greeted.get(pending_name, 0) >= self._GREET_COOLDOWN:
                        self._last_greeted[pending_name] = now
                        greeting = _greeting_by_time()
                        specials = self._SPECIAL_GREETINGS.get(pending_name)
                        if specials:
                            idx = (self._special_greet_idx.get(pending_name, -1) + 1) % len(specials)
                            self._special_greet_idx[pending_name] = idx
                            greet_text = f"{greeting.capitalize()} {pending_name}! {specials[idx]}"
                        else:
                            greet_text = f"{greeting.capitalize()} {pending_name}! Nasılsın?"
                        print(f"[BRAIN] 👋 Bekleyen selamlama: {pending_name}")
                        threading.Thread(
                            target=self._greet_and_listen,
                            args=(greet_text,),
                            daemon=True,
                        ).start()
                        return

                # Proaktif sohbet başlatıcı — arka planda çalışır, koşullar sağlanırsa konuşur
                threading.Thread(target=self._proactive_followup, daemon=True).start()

    # ── Konuşmadan isim çıkarımı ─────────────────────────────────────────────

    # "benim adım X" / "adım X" / "ismim X" — sadece net isim ifadeleri
    # "ben dizi izleyeceğim" gibi cümleleri isim sanmaması için 'ben' çıkarıldı
    _NAME_RE = re.compile(
        r"(?:benim\s+adım|\badım|ismim)\s+([A-ZÇĞİÖŞÜa-zçğışöüı]{2,20})\b",
        re.IGNORECASE,
    )

    # GPT cümle sonu: nokta/soru/ünlem + boşluk (streaming split için)
    _SENT_END = re.compile(r'[.!?…]["\']?\s+')

    def _extract_name(self, text: str) -> Optional[str]:
        """Cümleden isim çıkarır. 'Benim adım Volkan' → 'Volkan'"""
        m = self._NAME_RE.search(text)
        if not m:
            return None
        name = m.group(1).strip().capitalize()
        # Kısa veya yaygın kelimelerle karışmasın
        _skip = {"bir", "bu", "şu", "ne", "de", "da", "ve", "ile", "robi"}
        return name if name.lower() not in _skip else None

    def _think_and_respond(self, user_text: str) -> None:
        """GPT stream → ilk cümle gelir gelmez TTS başlar (düşük gecikme)."""
        user = self.current_user or "unknown"

        # Konuşmadan isim öğren ("Benim adım Volkan" gibi)
        # ⚠ Vision son 90 saniyede kişiyi onayladıysa sözel isim iddiasına inanma
        _VISION_TRUST_SEC = 90.0
        detected_name = self._extract_name(user_text)
        if detected_name and detected_name != self.current_user:
            vision_recent = (time.time() - self._vision_confirmed_at) < _VISION_TRUST_SEC
            if vision_recent:
                print(f"[BRAIN] 👁 '{detected_name}' iddiası yoksayıldı — vision {self.current_user!r} onayladı")
            else:
                old_user = user
                self.current_user = detected_name
                user = detected_name
                self.memory.seen_person(detected_name)
                if old_user == "unknown":
                    self.memory.migrate_user("unknown", detected_name)
                print(f"[BRAIN] 👤 İsim öğrenildi: {detected_name}")

        self.memory.log(user, "user", user_text)
        self.memory.extract_and_save_prefs(user, user_text)

        # Uyuma komutu — "robi uyu", "uyu artık", "iyi geceler uyu" vb.
        if re.search(r"\b(uyu(?:artık|bakalım|hadi|şimdi)?|uyusana|uyuver)\b"
                     r"|uyu\s+artık|artık\s+uyu|iyi\s+geceler.*uyu|uyu.*iyi\s+geceler",
                     user_text.lower()):
            def _go_sleep():
                speak("Tamam, uyuyorum. İyi geceler!")
                time.sleep(0.3)
                self._sleep_companion = self.current_user
                self._set_state(SLEEP)
                self.bus.publish({"type": "STOP_LISTEN", "ts": time.time()})
                time.sleep(1.0)
                if self._get_state() == SLEEP:
                    self.bus.publish({"type": "STOP_LISTEN", "ts": time.time()})
            print("[BRAIN] 💤 Uyuma komutu alındı")
            threading.Thread(target=_go_sleep, daemon=True).start()
            return

        # Önce skills dene (hızlı, local) — kimin konuştuğunu da geçir
        reply = skills.handle(user_text, user=user)
        if reply:
            print(f"[BRAIN] 🤖 {reply}")
            self.memory.log(user, "assistant", reply)
            self._say(reply)
            return

        # GPT streaming — ilk cümle tamamlanınca hemen konuş
        messages     = self._build_messages(user_text)
        full_reply   = ""
        spoken_count = 0   # kaç cümle konuşuldu

        def _after_speak() -> None:
            """Tüm konuşma bitti → state geçişi."""
            play_end_chime()                # tüm streaming bitti → bip + LED flash
            if skills.is_music_playing():
                self._pending_utterance = None
                if self.current_user:
                    self._auto_since = time.time()
                    self._set_state(AUTO_LISTEN)
                    self.bus.publish({"type": "LISTEN", "mode": "auto",
                                      "music_mode": True, "ts": time.time()})
                else:
                    self._set_state(IDLE)
            else:
                pending = self._pending_utterance
                self._pending_utterance = None
                if pending:
                    print(f"[BRAIN] 📋 Bekleyen mesaj işleniyor: {repr(pending[:40])}")
                    self._set_state(THINKING)
                    self._think_async(pending)
                else:
                    self._auto_since = time.time()
                    self._set_state(AUTO_LISTEN)
                    self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": time.time()})
                    # GPT cevabından sonra proaktif sohbet başlatıcı
                    threading.Thread(target=self._proactive_followup, daemon=True).start()

        try:
            buffer = ""
            stream = self.oai.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                max_tokens=LLM_MAX_TOKENS,
                temperature=0.8,
                stream=True,
            )

            for chunk in stream:
                delta = (chunk.choices[0].delta.content or "")
                buffer    += delta
                full_reply += delta

                # Cümle sınırı bulduk mu?
                while True:
                    m = self._SENT_END.search(buffer)
                    if not m:
                        break
                    sentence = buffer[:m.end()].strip()
                    buffer   = buffer[m.end():]
                    if len(sentence) < 3:
                        continue
                    # SUS komutu geldi mi? → stream'i bırak
                    if self._stop_speaking:
                        self._stop_speaking = False
                        print("[BRAIN] 🤫 Konuşma kesildi (SUS)")
                        self.memory.log(user, "assistant", full_reply.strip() or "—")
                        self._auto_since = time.time()
                        self._set_state(AUTO_LISTEN)
                        self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": time.time()})
                        return
                    if spoken_count == 0:
                        # İlk cümle: THINKING → SPEAKING geçişi
                        self._set_state(SPEAKING)
                        print(f"[BRAIN] 🤖 {sentence}")
                    spoken_count += 1
                    speak(sentence)   # bloklar — ama GPT buffer'da bekler

            # Son kalan metin (cümle sonu yok)
            tail = buffer.strip()
            if len(tail) > 2:
                if spoken_count == 0:
                    self._set_state(SPEAKING)
                    print(f"[BRAIN] 🤖 {tail}")
                spoken_count += 1
                speak(tail)

            full_reply = full_reply.strip()

            if spoken_count == 0:
                # Hiç token gelmedi
                full_reply = "Şu an düşünemiyorum efendim."
                self._set_state(SPEAKING)
                print(f"[BRAIN] 🤖 {full_reply}")
                speak(full_reply)
            elif spoken_count > 1:
                # Birden fazla parça konuşulduysa tam metni logla
                print(f"[BRAIN] 🤖 (tam cevap) {full_reply}")

        except Exception as e:
            print(f"[BRAIN] GPT hatası: {e}")
            full_reply = "Şu an düşünemiyorum efendim."
            self._set_state(SPEAKING)
            speak(full_reply)

        self.memory.log(user, "assistant", full_reply or "—")

        # Arka planda öğrenme: kullanıcı mesajından sağlık/ilaç bilgisi çıkar
        threading.Thread(
            target=self._extract_health_info,
            args=(user, user_text),
            daemon=True,
        ).start()

        _after_speak()

    # ── GPT destekli öğrenme motoru ───────────────────────────────────────────

    # Son extraction zamanı — aynı mesajı tekrar işleme
    _last_extracted: dict = {}

    def _extract_health_info(self, user: str, user_text: str) -> None:
        """
        Kullanıcı mesajından GPT ile sağlık/ilaç/rutin bilgisi çıkarır,
        hafızaya kaydeder. Arka planda (daemon thread) çalışır.

        Sadece sağlıkla ilgili anahtar kelimeler geçiyorsa GPT'ye gider
        (gereksiz API çağrısını önlemek için).
        """
        _HEALTH_KEYWORDS = re.compile(
            r"ila[cç]|hap\b|tablet|doktor|hasta|ağrı|sancı|şeker|tansiyon|"
            r"kolesterol|kalp|böbrek|mide|baş\s*ağrısı|diz|bel|sırt|"
            r"alerji|astım|nefes|uyku|yorgun|bitkin|randevu|ameliyat|"
            r"röntgen|tahlil|kan|iğne|serum|vitamin|mineral|"
            r"sabah\s*kalk|yürüyüş|egzersiz|diyet|perhiz",
            re.IGNORECASE
        )

        if not _HEALTH_KEYWORDS.search(user_text):
            return  # Sağlıkla ilgisi yok → atla

        # Aynı metni kısa sürede iki kez işleme
        text_key = user_text[:60]
        last = self._last_extracted.get(text_key, 0)
        if time.time() - last < 60:
            return
        self._last_extracted[text_key] = time.time()

        try:
            prompt = (
                "Aşağıdaki Türkçe cümleden sağlıkla ilgili bilgileri çıkar.\n"
                "Sadece açıkça belirtilen bilgileri al; tahmin etme.\n"
                "JSON formatında yanıt ver — başka hiçbir şey yazma:\n"
                '{"medications": [{"name": "...", "detail": "..."}], '
                '"conditions": ["..."], '
                '"allergies": ["..."], '
                '"routines": [{"name": "...", "detail": "..."}]}\n\n'
                f"Cümle: {user_text}"
            )
            resp = self.oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0,
            )
            raw = (resp.choices[0].message.content or "").strip()

            import json as _json
            # JSON bloğunu ayıkla (GPT bazen ```json ... ``` wrapper ekler)
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not json_match:
                return
            data = _json.loads(json_match.group())

            saved_any = False
            for med in data.get("medications") or []:
                name   = (med.get("name")   or "").strip()
                detail = (med.get("detail") or "").strip()
                if name and len(name) > 1:
                    self.memory.add_health_note(user, "medication", name, detail)
                    saved_any = True

            for cond in data.get("conditions") or []:
                cond = cond.strip()
                if cond and len(cond) > 2:
                    self.memory.add_health_note(user, "condition", cond)
                    saved_any = True

            for allergy in data.get("allergies") or []:
                allergy = allergy.strip()
                if allergy and len(allergy) > 2:
                    self.memory.add_health_note(user, "allergy", allergy)
                    saved_any = True

            for routine in data.get("routines") or []:
                name   = (routine.get("name")   or "").strip()
                detail = (routine.get("detail") or "").strip()
                if name and len(name) > 2:
                    self.memory.add_health_note(user, "routine", name, detail)
                    saved_any = True

            if saved_any:
                print(f"[BRAIN] 🧠 Öğrenildi — {user}: {raw[:80]}")

        except Exception as e:
            print(f"[BRAIN] ⚠ Öğrenme hatası: {e}")

    def _say_async(self, text: str) -> None:
        threading.Thread(target=self._say, args=(text,), daemon=True).start()

    def _think_async(self, text: str) -> None:
        threading.Thread(target=self._think_and_respond, args=(text,), daemon=True).start()

    # ── Event handler'lar ─────────────────────────────────────────────────────

    def _on_wake(self) -> None:
        state = self._get_state()
        print(f"[BRAIN] 🔔 WAKE alındı (state={state})")
        if state in (LISTENING, SPEAKING, THINKING):
            print("[BRAIN] WAKE yok sayıldı (meşgul)")
            return
        # Müzik/radyo çalıyorken: mpv'yi SIGSTOP ile dondur, sonra dinle.
        # TV sesi kesilir → kullanıcı sesini temiz yakalarız.
        # Komut işlendikten sonra ya durdurulur ya da SIGCONT ile devam eder.
        if skills.is_music_playing():
            skills.pause_music()
        self._set_state(SPEAKING)
        threading.Thread(target=self._ack_and_listen, daemon=True).start()

    def _ack_and_listen(self) -> None:
        self._wake_triggered = True   # Wake word ile girildi → "sizi duyamadım" çalışsın
        ack = self._build_wake_ack()  # Saate & kişiye göre kişiselleştirilmiş selamlama
        speak(ack)
        time.sleep(0.3)   # TTS sonrası kalan ses buffer'ını temizle
        self._listening_since = time.time()
        self._set_state(LISTENING)
        # Müzik pauselanmışsa arka plan gürültüsü yok → düşük RMS eşiği yeterli
        low_thr = skills.is_music_playing()
        self.bus.publish({"type": "LISTEN", "mode": "once",
                          "low_threshold": low_thr, "ts": time.time()})

    # Durdurma komutları — wake sonrası müzik varken TV sesine karşı
    _STOP_RE = re.compile(r"\b(kapat|dur\b|durdur|kes\b|yeter|bitir|sustur)\b", re.IGNORECASE)

    def _on_utterance(self, text: str) -> None:
        self._auto_since = time.time()   # aktif konuşma → sleep timer sıfırla

        state = self._get_state()
        if state not in (LISTENING, AUTO_LISTEN):
            # IDLE modda: "robi ..." ile başlayan cümleleri direkt komut olarak işle.
            # Müzik çalarken wake word tetiklenemeyebilir — bu fallback'tir.
            if state == IDLE and re.search(r"^(robi|roby|rubi|robı)\b", text.lower()):
                clean = re.sub(r"^(robi|roby|rubi|robı)[,.\s]+", "", text, flags=re.IGNORECASE).strip()
                if clean:
                    print(f"[BRAIN] 🎙 IDLE komut (robi-prefix): {repr(clean)}")
                    self._set_state(THINKING)
                    self._think_async(clean)
            elif state in (THINKING, SPEAKING) and text:
                # ROBI düşünürken/konuşurken gelen utterance'ı kaydet — cevap bitince işle
                # Birden fazla mesaj gelirse birleştir (zincirleme GPT çağrısı önleme)
                if self._pending_utterance:
                    self._pending_utterance = self._pending_utterance + " " + text
                else:
                    self._pending_utterance = text
                print(f"[BRAIN] 📋 Bekleyen mesaj kaydedildi: {repr(self._pending_utterance[:60])}")
            elif state == SLEEP:
                # Audio stale LISTEN yüzünden LISTENING modunda takılı → Whisper üzerinden
                # "robi" gelirse direkt uyandır. Aksi halde STOP_LISTEN ile IDLE'a çek.
                if text and re.search(r"\b(robi|roby|rubi|robı)\b", text.lower()):
                    print(f"[BRAIN] 🔔 SLEEP'te 'Robi' algılandı (STT fallback) → uyanıyor")
                    self._on_wake()
                else:
                    print(f"[BRAIN] 💤 SLEEP'te utterance yok sayıldı, STOP_LISTEN yenileniyor")
                    self.bus.publish({"type": "STOP_LISTEN", "ts": time.time()})
            return

        # Müzik duraklatılmışken LISTENING modundayız (wake word sonrası).
        # Kullanıcının temiz sesini yakaladık — şimdi komutu işle.
        if skills.is_music_playing() and state == LISTENING:
            if not text:
                # Boş utterance → müziği devam ettir, IDLE'a dön (GPT tetikleme)
                skills.resume_music()
                self._set_state(IDLE)
                return
            if self._STOP_RE.search(text):
                # Durdurma komutu → müzik zaten durdurulacak, SIGCONT gerekmez
                print(f"[BRAIN] 🛑 Müzik-durdur komutu: {repr(text)}")
                self._set_state(THINKING)
                self._think_async(text)
            else:
                # Başka komut → müziği devam ettir, komutu işle
                print(f"[BRAIN] ▶ Müzik devam etti, komut işleniyor: {repr(text[:40])}")
                skills.resume_music()
                self._set_state(THINKING)
                self._think_async(text)
            return

        if not text:
            if state == AUTO_LISTEN:
                self._auto_since = time.time()
            elif state == LISTENING:
                # Audio boş utterance verdi ve IDLE'a döndü.
                # LISTEN tekrar gönder, yoksa brain LISTENING'de takılır.
                self.bus.publish({"type": "LISTEN", "mode": "once", "ts": time.time()})
            return

        # İsim bekliyorsak kaydet
        if self._pending_name:
            self._pending_name = False
            name = text.strip().split()[0].capitalize()
            self.current_user = name
            self.memory.seen_person(name)
            self._say_async(f"Memnun oldum {name}! Nasıl yardımcı olabilirim?")
            return

        self._set_state(THINKING)
        self._think_async(text)

    def _on_timeout(self) -> None:
        state = self._get_state()
        if state == LISTENING:
            # LISTENING'e yeni girilmişse eski AUTO_LISTEN session'ından
            # kalan stale TIMEOUT olabilir — 1 sn içindekileri yoksay
            if time.time() - self._listening_since < 1.0:
                return
            # Müzik duraklatılmışsa sessizce devam ettir, "duyamadım" deme
            if skills.is_music_playing():
                skills.resume_music()
                self._set_state(IDLE)
                return
            # "Sizi duyamadım" yalnızca wake word sonrası LISTENING'de söyle.
            # Yüz tanıma selamlaması sonrası timeout → sessizce IDLE'a dön.
            if not self._wake_triggered:
                self._set_state(IDLE)
                return
            threading.Thread(
                target=lambda: (speak("Sizi duyamadım efendim."),
                                self._set_state(IDLE)),
                daemon=True,
            ).start()
        elif state == AUTO_LISTEN:
            # Müzik çalıyorsa sayacı sıfırla — uyuma
            if skills.is_music_playing():
                self._auto_since = time.time()
                return
            # Sleep geçişini _sleep_watchdog yönetir — burada tekrar kontrol etme
        elif state == SLEEP:
            # Audio, uyku öncesi kuyruğa girmiş stale bir LISTEN event yüzünden
            # LISTENING modunda takılı kalmış olabilir (STOP_LISTEN'dan sonra gelen LISTEN).
            # TIMEOUT alıyorsak audio hâlâ LISTENING'de demektir → STOP_LISTEN tekrar gönder.
            print("[BRAIN] 💤 SLEEP'te TIMEOUT → stale LISTEN düzeltiliyor")
            self.bus.publish({"type": "STOP_LISTEN", "ts": time.time()})

    # Kişiye özel karşılama metinleri (zaman bazlı selamlama sonrasına eklenir)
    _SPECIAL_GREETINGS: dict = {
        "Selma": [
            "Ne güzel geldin! Sizi görmek ne kadar iyi.",
            "Aa, buyurun! Sizi bekliyordum.",
            "Hoş geldiniz! Bugün nasılsınız?",
            "Geldiniz mi! Çok iyi ettiniz.",
            "Harika! Sizi görmek güzel.",
        ],
        "Cemre": [
            "Aa, Cemre! Ne güzel sürpriz, hoş geldin!",
            "Cemre geldi, ev şenlendi! Nasılsın?",
            "Oh, Cemre! Seni görmek ne kadar güzel.",
            "Cemre! Tam zamanında geldin, özlemişim.",
            "Hayırlı geldin Cemre! Bugün nasılsın?",
        ],
    }
    _special_greet_idx: dict = {}  # kişi → son kullanılan index

    # ── Wake ack ────────────────────────────────────────────────────────────────

    def _build_wake_ack(self) -> str:
        """
        Wake word sonrası kişiselleştirilmiş uyandırma tepkisi.
        Saate, kişiye ve hafızadaki bağlama göre oluşturulur.
        GPT çağrısı YOK — şablon tabanlı, anlık.
        """
        import random
        # Vision son 60 saniyede kişiyi onayladı mı?
        # Onaylamadıysa hafızadaki ismi kullanma — kamera karşısında kim olduğunu bilmiyoruz.
        _VISION_MAX_AGE = 60.0
        vision_fresh = (time.time() - self._vision_confirmed_at) < _VISION_MAX_AGE
        user = self.current_user if vision_fresh else None
        name = user if user else None
        h    = datetime.now().hour

        # Kamera kimseyi görmüyorsa kısa yanıt — uzun TTS LISTENING fırsatını kaçırtıyor
        if not vision_fresh:
            opts = [
                "Buyurun?",
                "Evet?",
                "Dinliyorum.",
                "Efendim?",
            ]
            return random.choice(opts)

        # İlaç listesi (eğer varsa)
        meds = self.memory.get_medications(user) if user else []

        # ── Cemre: samimi ve coşkulu, "sen" hitabı ───────────────────────────
        if name == "Cemre":
            if 5 <= h < 11:
                opts = [
                    "Günaydın Cemre! Erken kalkmışsın, nasılsın?",
                    "Aa günaydın Cemre! Bugün nasıl hissediyorsun?",
                    "Günaydın! Cemre de burada, harika!",
                ]
            elif 11 <= h < 18:
                opts = [
                    "Evet Cemre, söyle bakalım?",
                    "Cemre! Ne var ne yok?",
                    "Buyur Cemre?",
                    "Evet, dinliyorum Cemre!",
                ]
            elif 18 <= h < 22:
                opts = [
                    "İyi akşamlar Cemre! Nasıl geçti günün?",
                    "Cemre geldi mi? Buyur!",
                    "Aa Cemre, iyi akşamlar! Ne istiyorsun?",
                ]
            else:
                opts = [
                    "Cemre? Bu saatte ne oldu?",
                    "Buyur Cemre, bir şey mi lazım?",
                ]
            return random.choice(opts)

        # ── Sabah (05-11) ─────────────────────────────────────────────────────
        if 5 <= h < 11:
            opts = []
            if name:
                opts = [
                    f"Günaydın {name}! Nasılsınız?",
                    f"Günaydın {name}!",
                    f"Aa günaydın {name}! Buyurun?",
                ]
                if meds:
                    med = meds[0]["content"]
                    opts.append(f"Günaydın {name}! {med} aldınız mı?")
            else:
                opts = ["Günaydın! Buyurun?", "Günaydın!"]
            return random.choice(opts)

        # ── Öğle (11-14) ──────────────────────────────────────────────────────
        elif 11 <= h < 14:
            opts = []
            if name:
                opts = [
                    f"Buyurun {name}?",
                    f"Evet {name}?",
                    f"Dinliyorum {name}.",
                ]
            else:
                opts = ["Buyurun?", "Evet?", "Dinliyorum."]
            return random.choice(opts)

        # ── İkindi (14-18) ────────────────────────────────────────────────────
        elif 14 <= h < 18:
            opts = []
            if name:
                opts = [
                    f"Buyurun {name}?",
                    f"Efendim, dinliyorum.",
                    f"Evet {name}?",
                    f"Buyurun, bir şey mi istiyorsunuz?",
                ]
                # Öğleden sonra %20 ihtimalle ilaç sorusu
                if meds and random.random() < 0.20:
                    return f"Buyurun {name}? Öğleden sonra ilacınızı aldınız mı?"
            else:
                opts = ["Buyurun efendim?", "Evet, dinliyorum."]
            return random.choice(opts)

        # ── Akşam (18-22) ─────────────────────────────────────────────────────
        elif 18 <= h < 22:
            opts = []
            if name:
                opts = [
                    f"İyi akşamlar {name}! Buyurun?",
                    f"Buyurun {name}?",
                    f"Evet {name}?",
                ]
            else:
                opts = ["İyi akşamlar! Buyurun?", "Efendim?"]
            return random.choice(opts)

        # ── Gece (22-05) ─────────────────────────────────────────────────────
        else:
            opts = []
            if name:
                opts = [
                    f"Buyurun {name}?",
                    f"Efendim?",
                    f"Evet {name}?",
                ]
            else:
                opts = ["Buyurun?", "Efendim?"]
            return random.choice(opts)

    def _on_button_press(self) -> None:
        """Fiziksel buton basıldı — konuşmayı/düşünmeyi anında kes, dinlemeye geç."""
        state = self._get_state()
        print(f"[BRAIN] 🔘 Buton basıldı (state={state})")
        if state in (THINKING, SPEAKING):
            self._stop_speaking = True
            self._pending_utterance = None
            # Konuşma thread'i en yakın checkpoint'te durduracak;
            # durduğunda state zaten AUTO_LISTEN'a geçecek.
        elif state == SLEEP:
            # Uyku modundayken butona basmak uyandırır
            self._auto_since = time.time()
            self._set_state(AUTO_LISTEN)
            self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": time.time()})
            print("[BRAIN] 🔘 Buton ile uyandı")

    def _on_face_seen(self, name: str) -> None:
        if not name:
            return
        self._auto_since = time.time()   # kişi görüldü → sleep timer sıfırla

        state = self._get_state()

        # Aktif konuşma sırasında farklı bir yüz görüldüğünde:
        # current_user'ı güncelle ama selamlamayı beklet.
        if state in (LISTENING, THINKING, SPEAKING):
            prev = self.current_user
            self.current_user = name
            self.memory.seen_person(name)
            if prev != name:
                # Konuşma bitince selamlanacak — _after_speak() kontrol eder
                now = time.time()
                if now - self._last_greeted.get(name, 0) >= self._GREET_COOLDOWN:
                    self._pending_greet = name
                    print(f"[BRAIN] 👤 {name} görüldü (meşgul) → selamlama beklemeye alındı")
            return

        prev_user = self.current_user
        self.current_user = name
        self.memory.seen_person(name)
        self._vision_confirmed_at = time.time()   # vision bu kişiyi onayladı

        # SLEEP modunda tanınan yüz → uyanma mantığı
        if state == SLEEP:
            now = time.time()
            last_seen = self._last_greeted.get(name, 0)
            if name == self._sleep_companion:
                # Uyurken yanında olan aynı kişi — 30 saniye sonra uyan
                # (Vosk wake word çalışmazsa vision devreye girer)
                if now - last_seen < 30:
                    return   # Henüz 30 saniye geçmedi → döngüye girme
                print(f"[BRAIN] 👁 SLEEP'te {name} (companion) tekrar aktif → uyanıyor")
            else:
                if now - last_seen < self._GREET_COOLDOWN:
                    return
                print(f"[BRAIN] 👁 SLEEP'te {name} görüldü (yeni kişi) → uyanıyor")
            self._sleep_companion = None
            self._last_greeted[name] = now
            self._on_wake()
            return

        # IDLE veya AUTO_LISTEN'da yüz değişiminde selamla.
        if state in (IDLE, AUTO_LISTEN) and prev_user != name:
            # Aynı kişiyi kısa sürede tekrar selamlama (vision titremesi önlemi)
            now = time.time()
            if now - self._last_greeted.get(name, 0) < self._GREET_COOLDOWN:
                return
            self._last_greeted[name] = now
            self._pending_greet = None  # bekleyen selamlama varsa iptal et (direkt selamlanacak)
            greeting = _greeting_by_time()

            # Kişiye özel karşılama varsa kullan
            specials = self._SPECIAL_GREETINGS.get(name)
            if specials:
                idx = (self._special_greet_idx.get(name, -1) + 1) % len(specials)
                self._special_greet_idx[name] = idx
                text = f"{greeting.capitalize()} {name}! {specials[idx]}"
            else:
                text = f"{greeting.capitalize()} {name}! Nasılsın?"

            self._set_state(SPEAKING)
            threading.Thread(
                target=self._greet_and_listen,
                args=(text,),
                daemon=True,
            ).start()

    def _greet_and_listen(self, text: str) -> None:
        self._wake_triggered = False  # Selamlama → timeout'ta "sizi duyamadım" deme
        speak(text)
        self._auto_since = time.time()
        self._set_state(AUTO_LISTEN)
        self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": time.time()})

    _UNKNOWN_COOLDOWN = 300   # "sizi tanıyamadım" arasındaki minimum süre (saniye)
    _last_unknown_asked: float = 0.0

    def _on_face_unknown(self) -> None:
        state = self._get_state()
        if state not in (SLEEP, IDLE):
            return
        # Zaten tanınan bir kullanıcı varsa ara kare belirsizliğine tepki verme
        if self.current_user:
            return
        # Çok sık sorma
        if time.time() - self._last_unknown_asked < self._UNKNOWN_COOLDOWN:
            return
        self._last_unknown_asked = time.time()
        self._pending_name = True
        self._set_state(SPEAKING)
        threading.Thread(target=self._ask_name_then_listen, daemon=True).start()

    def _ask_name_then_listen(self) -> None:
        speak("Sizi tanıyamadım efendim, adınız nedir?")
        self._set_state(LISTENING)
        self.bus.publish({"type": "LISTEN", "mode": "once", "ts": time.time()})

    def _on_reminder(self, label: str, reminder_user: str = "") -> None:
        """
        Hatırlatıcı zamanı geldi — her durumdan seslendir.
        reminder_user: bu hatırlatıcının kime ait olduğu (boş = herkese)
        """
        state = self._get_state()
        # Meşgulse (zaten konuşuyor/düşünüyor) birkaç saniye bekle
        if state in (SPEAKING, THINKING):
            def _delayed():
                time.sleep(4)
                self._on_reminder(label, reminder_user)
            threading.Thread(target=_delayed, daemon=True).start()
            return

        # Odadaki kişiyle uyumlu mu kontrol et
        current = self.current_user or ""
        if reminder_user and current and reminder_user != current:
            # Hatırlatıcı farklı kişiye ait — o kişinin adıyla hitap et
            msg = f"{reminder_user}, hatırlatıcın var: {label}."
        elif current:
            msg = f"{current}, {label}."
        else:
            msg = f"Hatırlatıcı: {label}."
        print(f"[BRAIN] 🔔 {msg}")
        # Telegram bildirimi gönder (arka planda, bloklamaması için thread)
        try:
            from robi_telegram import send_reminder as _tg_remind
            threading.Thread(
                target=_tg_remind,
                args=(label, reminder_user or current),
                daemon=True,
            ).start()
        except Exception as _te:
            print(f"[BRAIN] ⚠ Telegram hatırlatıcı gönderilemedi: {_te}")
        # Müzik çalıyorsa duraklat, hatırlatıcıyı söyle, devam et
        music_was_on = skills.is_music_playing()
        if music_was_on:
            skills.pause_music()
        self._set_state(SPEAKING)
        threading.Thread(
            target=lambda: (
                speak(msg),
                skills.resume_music() if music_was_on else None,
                self._set_state(IDLE if music_was_on else AUTO_LISTEN),
                None if music_was_on else setattr(self, "_auto_since", time.time()),
                None if music_was_on else self.bus.publish(
                    {"type": "LISTEN", "mode": "auto", "ts": time.time()}
                ),
            ),
            daemon=True,
        ).start()

    # ── Proaktif sohbet başlatıcı ────────────────────────────────────────────

    def _proactive_followup(self) -> None:
        """
        GPT ile kısa, doğal bir sohbet açıcı cümle üretir.
        Sadece belirli koşullar altında ve belirli bir ihtimalle tetiklenir:
          • Konuşma bitti, AUTO_LISTEN modundayız
          • Müzik çalmıyor
          • Son proaktif mesajdan 2+ dakika geçti
          • Rastgele %45 ihtimal
        3 saniyelik bekleme sonrası state hâlâ AUTO_LISTEN ise konuşur.
        """
        import random

        # Rastgele erken çıkış (%55)
        if random.random() > 0.45:
            return

        # Son proaktif mesajdan beri çok az zaman geçtiyse atla
        now = time.time()
        if now - self._last_proactive < 120.0:   # 2 dakika minimum aralık
            return

        # 3 saniye bekle — kullanıcı zaten konuşmaya başladıysa iptal
        time.sleep(3.0)

        if self._get_state() != AUTO_LISTEN:
            return
        if skills.is_music_playing():
            return

        user = self.current_user or "unknown"
        h    = datetime.now().hour

        # GPT'ye bağlam ver
        context      = self.memory.build_context(user)
        history_snip = self.memory.get_recent_user_text(user, n=4)
        meds         = self.memory.get_medications(user)

        time_hint = (
            "sabah"       if 5  <= h < 11 else
            "öğle vakti"  if 11 <= h < 14 else
            "öğleden sonra" if 14 <= h < 18 else
            "akşam"       if 18 <= h < 22 else
            "gece"
        )

        med_hint = ""
        if meds and h in range(8, 22):   # gündüz ilaç hatırlatması
            med_hint = (
                f" Kullanıcının {meds[0]['content']} ilacı var;"
                " eğer uygunsa ama çok tekrar etmeden bunu da hatırlatabilirsin."
            )

        prompt = (
            f"Sen ROBI'sin, yaşlı bir hanımın evindeki sevecen arkadaş robot. "
            f"Şu an {time_hint}. "
        )
        if context:
            prompt += f"Kullanıcı hakkında: {context}. "
        if history_snip:
            prompt += f"Son konuşmalardan özet: {history_snip}. "
        prompt += med_hint
        prompt += (
            " Şimdi SAMİMİ ve KISA (1 cümle, max 15 kelime) bir sohbet başlatıcı söyle. "
            "ASLA 'başka bir konuda yardımcı olabilir miyim' veya 'bir şey lazımsa söyle' DEME. "
            "Bunun yerine merak, sıcaklık veya hafıza kullan. "
            "Sadece o cümleyi yaz, başka hiçbir şey."
        )

        try:
            resp = self.oai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=35,
                temperature=0.95,
            )
            followup = (resp.choices[0].message.content or "").strip()
            # Tırnak işaretlerini temizle
            followup = followup.strip('"\'')

            if not followup or len(followup) < 5:
                return

            # Son kontrol — state hâlâ AUTO_LISTEN mı?
            if self._get_state() != AUTO_LISTEN:
                return

            print(f"[BRAIN] 💬 Proaktif: {repr(followup)}")
            self._last_proactive = time.time()
            self._say_async(followup)

        except Exception as e:
            print(f"[BRAIN] ⚠ Proaktif followup hatası: {e}")

    # ── Auto-sleep watchdog ───────────────────────────────────────────────────

    def _sleep_watchdog(self) -> None:
        """
        AUTO_LISTEN süresini watchdog'un kendi iç sayacıyla ölçer.

        Sessizlik süresi dolunca SLEEP'e DEĞİL, doğrudan IDLE'a geçer.
        Bu sayede:
          - Vision yavaş moda geçmez (BRAIN_SLEEP yayınlanmaz)
          - Audio wake-word moduna STOP_LISTEN ile geçer — startup ile özdeş durum
          - "Robi" denilince anında yanıt verir, SLEEP state karmaşıklığı yok
          - Sesli "Uyuyorum" anonsmanı kaldırıldı — sessiz geçiş daha doğal
        """
        _last_listen_pulse: float = 0.0
        _auto_start: float = 0.0   # bu watchdog döngüsünde AUTO_LISTEN'ı ilk gördüğümüz an

        while True:
            time.sleep(5)
            now = time.time()
            state = self._get_state()

            if state != AUTO_LISTEN:
                _auto_start = 0.0   # AUTO_LISTEN dışına çıkınca sayacı sıfırla
                continue

            # İlk kez AUTO_LISTEN görüyoruz — sayacı başlat
            if _auto_start == 0.0:
                _auto_start = now
                continue   # ilk tespitte hemen uyuma, bir sonraki döngüde karar ver

            # Müzik çalıyorsa uyuma
            if skills.is_music_playing():
                _auto_start = now
                continue

            elapsed = now - _auto_start
            if elapsed >= AUTO_LISTEN_TIMEOUT:
                print(f"[BRAIN] ⏱ Sessizlik ({elapsed:.0f}s) → IDLE (wake word bekleniyor)")
                _auto_start = 0.0
                # SLEEP'e geçme — IDLE'a geç, startup davranışıyla özdeş:
                #   • BRAIN_SLEEP yayınlanmaz → vision normal hızda çalışmaya devam eder
                #   • Audio STOP_LISTEN ile wake-word moduna geçer
                #   • "Robi" denilince _on_wake() hemen tetiklenir (IDLE state izin verir)
                self._set_state(IDLE)
                def _to_wake_mode():
                    time.sleep(0.1)
                    # Kısa "hazır" sinyali: kullanıcıya "artık 'Robi' diyebilirsin" bildir
                    play_end_chime()
                    self.bus.publish({"type": "STOP_LISTEN", "ts": time.time()})
                    print("[BRAIN] 👂 STOP_LISTEN → audio wake word modunda")
                threading.Thread(target=_to_wake_mode, daemon=True).start()
            else:
                # Her 15 saniyede LISTEN yeniden yayınla — audio senkronizasyonu
                if now - _last_listen_pulse >= 15.0:
                    self.bus.publish({"type": "LISTEN", "mode": "auto", "ts": now})
                    _last_listen_pulse = now

    # ── Ana döngü ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        threading.Thread(target=self._sleep_watchdog, daemon=True).start()
        print("[BRAIN] 👂 Bus dinleniyor...")

        while True:
            ev = self.bus.recv(timeout=0.1)
            if not ev:
                continue

            t = ev.get("type")

            if   t == "WAKE":         self._on_wake()
            elif t == "UTTERANCE":    self._on_utterance(ev.get("text", "").strip())
            elif t == "TIMEOUT":      self._on_timeout()
            elif t == "FACE_SEEN":    self._on_face_seen(ev.get("name", "").strip())
            elif t == "FACE_UNKNOWN": self._on_face_unknown()
            elif t == "REMINDER":     self._on_reminder(
                                          ev.get("label", "hatırlatıcı"),
                                          ev.get("user", ""),
                                      )


def main() -> None:
    try:
        RobiBrain().run()
    except KeyboardInterrupt:
        print("[BRAIN] offline")


if __name__ == "__main__":
    main()
