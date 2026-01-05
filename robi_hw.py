# ROBI Hardware / LED Face Engine - v9.2
# Cleaned, stable, fixed draw-pattern usage, corrected speeds.

import time
import threading
import random
import lgpio
from luma.core.interface.serial import spi, noop
from luma.led_matrix.device import max7219
from PIL import Image

GPIO_ENABLED = True

# ------------------------------
# Low-level LED drawing
# ------------------------------

def draw_pattern(pattern):
    """Draw 8x8 bitmap pattern onto MAX7219."""
    img = Image.new("1", (8, 8))
    for y in range(8):
        for x in range(8):
            img.putpixel((x, y), pattern[y][x])
    matrix.display(img)

# ------------------------------
# GPIO BUTTON INIT
# ------------------------------
CHIP = 0
BUTTON_PIN = 21  # BCM21

h = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_input(h, BUTTON_PIN, lgpio.SET_PULL_UP)

# ------------------------------
# LED MATRIX INIT
# ------------------------------
serial = spi(
    port=0,
    device=0,
    gpio=noop()   # 🔴 RPi.GPIO'yu tamamen devre dışı bırakır
)

matrix = max7219(serial, cascaded=1, block_orientation=90, rotate=0)
matrix.contrast(40)

# Alias to avoid confusion
draw = draw_pattern

# ------------------------------
# FACE BASE SHAPES
# ------------------------------

FACE_IDLE = [
    [0,0,0,0,0,0,0,0],
    [0,1,0,0,0,0,1,0],
    [0,1,0,0,0,0,1,0],
    [0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0],
    [0,0,1,0,0,1,0,0],
    [0,0,1,0,0,1,0,0],
    [0,0,0,0,0,0,0,0],
]

FACE_LISTENING = [
    [0,0,0,0,0,0,0,0],
    [0,1,0,0,0,0,1,0],
    [0,1,0,0,0,0,1,0],
    [0,0,0,0,0,0,0,0],
    [0,0,0,1,1,0,0,0],
    [0,0,0,1,1,0,0,0],
    [0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0],
]

current_state = "idle"
anim_stop = False

# ------------------------------
# ANIMATIONS
# ------------------------------

def anim_transition():
    """Konusma oncesi kisa enerji toplama animasyonu (0.8s)."""
    # 3 halka seklinde kuculup buyuyen efekt
    rings = [3, 2, 1, 2]
    for r in rings:
        frame = [[0]*8 for _ in range(8)]
        start = 4 - r
        end   = 4 + r - 1

        # ust ve alt
        for x in range(start, end+1):
            frame[start][x] = 1
            frame[end][x] = 1

        # sol ve sag
        for y in range(start, end+1):
            frame[y][start] = 1
            frame[y][end] = 1

        draw_pattern(frame)
        time.sleep(0.12)

def anim_idle():
    """Shrinking/expanding box cycle for standby mode."""
    layers = [0,1,2,3,2,1]

    while current_state == "idle" and not anim_stop:
        for L in layers:
            if current_state != "idle" or anim_stop:
                break
            frame = [[0]*8 for _ in range(8)]

            # top
            for x in range(L, 8-L):
                frame[L][x] = 1
            # bottom
            for x in range(L, 8-L):
                frame[7-L][x] = 1
            # left
            for y in range(L, 8-L):
                frame[y][L] = 1
            # right
            for y in range(L, 8-L):
                frame[y][7-L] = 1

            draw(frame)
            time.sleep(0.14)


def anim_listening():
    """Shrinking box: 8→6→4→2 then repeat."""
    size_steps = [8, 6, 4, 2]
    idx = 0

    while current_state == "listening" and not anim_stop:
        size = size_steps[idx]
        frame = [[0]*8 for _ in range(8)]
        off = (8 - size) // 2

        # top + bottom
        for x in range(off, off + size):
            frame[off][x] = 1
            frame[off + size - 1][x] = 1

        # left + right
        for y in range(off, off + size):
            frame[y][off] = 1
            frame[y][off + size - 1] = 1

        draw(frame)

        # speed tuning
        if size == 2:
            time.sleep(0.33)
        else:
            time.sleep(0.22)

        idx = (idx + 1) % len(size_steps)


def anim_thinking():
    """3-pixel spinning trail around border."""
    path = [
        (0,0),(1,0),(2,0),(3,0),(4,0),(5,0),(6,0),(7,0),
        (7,1),(7,2),(7,3),(7,4),(7,5),(7,6),(7,7),
        (6,7),(5,7),(4,7),(3,7),(2,7),(1,7),(0,7),
        (0,6),(0,5),(0,4),(0,3),(0,2),(0,1),
    ]

    idx = 0
    while current_state == "thinking" and not anim_stop:
        frame = [[0]*8 for _ in range(8)]

        for k in range(3):
            x, y = path[(idx - k) % len(path)]
            frame[y][x] = 1

        draw(frame)
        idx = (idx + 1) % len(path)
        time.sleep(0.06)


def anim_speaking():
    """Random mouth height for natural speech flicker."""
    while current_state == "speaking" and not anim_stop:
        mouth_h = random.randint(2, 5)
        start_row = (8 - mouth_h) // 2

        frame = []
        for y in range(8):
            if start_row <= y < start_row + mouth_h:
                frame.append([1]*8)
            else:
                frame.append([0]*8)

        draw(frame)
        time.sleep(0.06)


# ------------------------------
# THREAD CONTROL
# ------------------------------

def _run_animation(name):
    if name == "idle": anim_idle()
    elif name == "listening": anim_listening()
    elif name == "thinking": anim_thinking()
    elif name == "speaking": anim_speaking()


def set_face(state):
    """Stop previous anim, start new one."""
    global current_state, anim_stop
    anim_stop = True
    time.sleep(0.02)

    current_state = state
    anim_stop = False
    threading.Thread(target=_run_animation, args=(state,), daemon=True).start()


# ------------------------------
# BUTTON
# ------------------------------
button_callback = None

def _button_loop():
    while True:
        if lgpio.gpio_read(h, BUTTON_PIN) == 0:
            if button_callback:
                button_callback()
            time.sleep(0.25)
        time.sleep(0.01)

threading.Thread(target=_button_loop, daemon=True).start()

def on_button_press(func):
    global button_callback
    button_callback = func

# ------------------------------
# PUBLIC FACE API
# ------------------------------
def face_idle(): set_face("idle")
def face_listening(): set_face("listening")
def face_thinking(): set_face("thinking")
def face_speaking(): set_face("speaking")

def cleanup():
    global anim_stop
    anim_stop = True
    matrix.clear()
    lgpio.gpiochip_close(h)
