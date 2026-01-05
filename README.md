# ROBI

ROBI is my home-assistant robot project (Raspberry Pi + ESP32/ESP8266).
Current status: active development; features and structure change frequently.
Hardware: Raspberry Pi 4 (main), ESP32/ESP8266 peripherals, LED matrix, mic (INMP441), speaker/amp.
Python: 3.11 (local venv, not committed).
Note: This repo is a snapshot; refactor/cleanup will happen gradually.

## Vision

ROBI is equipped with a Pİ camera and supports face detection and recognition.
It can recognize known people (e.g. family members) and use this information
as part of its decision-making process (context-aware behavior).

Vision is treated as a separate perception layer and communicates with the core
via events (e.g. PERSON_DETECTED).
