# ROBI_MASTER_STATE.md

## 🎯 ROBİ – Amaç
ROBI, yaşlı bir kadına akıllı, sempatik, esprili bir arkadaş olacak.
Bir cihaz gibi değil, evin içinden biri gibi davranır.

---

## 🧠 Temel Davranışlar

### 🔊 Uyanma & Sohbet
- “ROBİ” dendiğinde uyanır
- Uyanınca uzun süre awake kalır (hemen IDLE’a düşmez)
- Wake sonrası:
  - sempatik
  - esprili
  - doğal sohbet kurar
- ChatGPT değil ama ChatGPT hissi verir

---

### 📰 Bilgi & Medya
- Günün önemli haberlerini özetler
- Hava durumu ve borsa bilgisi verir
- Müzik çalar (türe göre)
- Radyo / TV kanallarına erişebilir
- Fıkra anlatabilir

---

### 👀 Görme & Tepki
- IDLE modda:
  - ses duyarsa etrafı kontrol eder
  - yüz yakalarsa konuşmaya başlar
  - yüz yoksa: “Biri var sandım…”
- Sabah:
  - birini görürse wake beklemeden “Günaydın” der

---

### 🧍‍♀️ İnsan Tanıma
- Tanımlı insanlara adıyla hitap eder
- Tanımadığı biriyle tanışır
- 2. fazda yeni tanıştıklarını da hatırlar

---

### 🧠 Hafıza
- Kişiye özel bilgiler tutar
- Sevdiği / sevmediği şeyleri bilir
- Zamanla daha kişisel bir arkadaş olur

---

## 🧩 Mimari Gerçekler

### Python Ortamları
- venv → audio / vosk / webrtcvad
- venv311 → torch / vision / perception
- Sebep: Torch yeni Python’da yok

---

## 🛣️ Yol Haritası

### FAZ 1 – DAVRANIŞ
- AWAKE state
- Wake sonrası uzun dinleme
- Hızlı IDLE yok

### FAZ 2 – BEDEN
- LED
- Servo
- Kamera
- Yüz tanıma

### FAZ 3 – ZEKA
- GPT entegrasyonu
- Kişisel hafıza
- Karakter derinliği

---

## 📜 Altın Kurallar
- Çalışanı bozma
- Tek seferde tek sistem
- Hardware olmadan ruh olmaz
- ROBİ oyuncak değil, ev arkadaşıdır

---

## 🔑 Yeni Sohbet Kuralı
Yeni sohbet şu cümleyle başlar:

“ROBI_MASTER_STATE.md’e göre devam ediyoruz.
Şu an FAZ 1 – AWAKE state’teyiz.”

---

## 🧠 Core Architecture Principles (Non-Negotiable)

- GPT / LLM logic ONLY lives in `RobiBrain`
- `RobiCore` never generates text or calls LLM
- `RobiAudio` never thinks, decides, or responds
- Audio → text
- Core → decision
- Brain → meaning + language + personality
- User input is NEVER echoed back
- All conversational memory belongs to Brain

---

## 🧩 Reasoning

This project evolved from v1 → v11 as a monolithic loop.
When split into Brain / Core / Audio, the most critical rule is:

"Conversation intelligence must remain centralized."

If intelligence leaks into Core or Audio, ROBI becomes unstable,
repeats the user, or enters dead states.

---

## 🧠 ROBI Consciousness Rules (Hard Rules)

- ROBI has exactly ONE brain: RobiBrain
- GPT / LLM calls live ONLY in RobiBrain
- RobiCore NEVER generates text
- RobiCore ONLY manages state transitions
- RobiAudio NEVER decides or responds
- User input is NEVER echoed back
- Conversation memory lives ONLY in RobiBrain (messages[])
- Wake → Listen → Think → Speak → Idle is the only valid loop

Violating these rules causes:
- echoing the user
- dead states
- wake-only behavior
- half-alive ROBI
