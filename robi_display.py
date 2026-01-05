from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi
from luma.core.render import canvas
import time
import threading

# -----------------------------
# LED Matrix Kurulumu
# -----------------------------
serial = spi(port=0, device=0)
device = max7219(serial, cascaded=1, block_orientation=90, rotate=0)

# Animasyon kontrol
_stop_flag = False
_anim_thread = None


def _run_animation(frames, delay=0.15):
    """Frame listesi ile animasyonu çalıştırır."""
    global _stop_flag

    for frame in frames:
        if _stop_flag:
            return
        with canvas(device) as draw:
            for y, row in enumerate(frame):
                for x, pixel in enumerate(row):
                    if pixel:
                        draw.point((x, y), fill=1)
        time.sleep(delay)


def _start(frames, delay=0.15, loop=False):
    """Her animasyon kendi thread'inde çalışır."""
    global _stop_flag, _anim_thread

    _stop_flag = True  # önceki animasyonu durdur
    time.sleep(0.05)
    _stop_flag = False

    def anim():
        while not _stop_flag:
            _run_animation(frames, delay)
            if not loop:
                break

    _anim_thread = threading.Thread(target=anim, daemon=True)
    _anim_thread.start()

# -----------------------------
# FRAME TANIMLARI
# -----------------------------

def _zeros():
    return [[0]*8 for _ in range(8)]

# Dinleme animasyonu
listen_frames = [
    [
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,1,1,0,0,0,0],
        [0,1,1,1,1,0,0,0],
        [0,1,1,1,1,0,0,0],
        [0,0,1,1,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
    ],
    [
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,1,1,0,0,0],
        [0,0,1,1,1,1,0,0],
        [0,0,1,1,1,1,0,0],
        [0,0,0,1,1,0,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
    ]
]

# Konuşma animasyonu
speak_frames = [
    [
        [0,0,0,0,0,0,0,0],
        [0,0,1,1,1,1,0,0],
        [0,1,0,0,0,0,1,0],
        [1,0,0,0,0,0,0,1],
        [1,0,0,0,0,0,0,1],
        [0,1,0,0,0,0,1,0],
        [0,0,1,1,1,1,0,0],
        [0,0,0,0,0,0,0,0],
    ],
    [
        [0,0,0,0,0,0,0,0],
        [0,0,0,1,1,0,0,0],
        [0,1,0,0,0,0,1,0],
        [0,1,0,0,0,0,1,0],
        [0,1,0,0,0,0,1,0],
        [0,0,1,1,1,1,0,0],
        [0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0],
    ]
]

# Düşünme (processing) animasyonu
think_frames = [
    [
        [1,0,0,0,0,0,0,0],
        [0,1,0,0,0,0,0,0],
        [0,0,1,0,0,0,0,0],
        [0,0,0,1,0,0,0,0],
        [0,0,0,0,1,0,0,0],
        [0,0,0,0,0,1,0,0],
        [0,0,0,0,0,0,1,0],
        [0,0,0,0,0,0,0,1],
    ]
]

# Idle göz kırpma animasyonu
idle_frames = [
    [
        [0,0,1,1,1,1,0,0],
        [0,1,0,0,0,0,1,0],
        [1,0,1,0,0,1,0,1],
        [1,0,0,1,1,0,0,1],
        [1,0,1,0,0,1,0,1],
        [0,1,0,0,0,0,1,0],
        [0,0,1,1,1,1,0,0],
        [0,0,0,0,0,0,0,0],
    ],
    _zeros()
]

# -----------------------------
# PUBLIC API
# -----------------------------

def listening():
    _start(listen_frames, delay=0.25, loop=True)

def speaking():
    _start(speak_frames, delay=0.15, loop=True)

def thinking():
    _start(think_frames, delay=0.05, loop=True)

def idle():
    _start(idle_frames, delay=0.5, loop=True)

def clear():
    _start([_zeros()], loop=False)
