# radio.py
import subprocess
import time

RADIOS = {
    "haberturk": {
        "type": "youtube_live",
        "url": "https://www.youtube.com/watch?v=RNVNlJSUFoE"
    }
}

current_radio_process = None
LAST_VOLUME = 80
FADE_STEPS = [80, 60, 40, 20, 10, 0]


def _set_volume(vol):
    subprocess.call(
        ["amixer", "sset", "Master", f"{vol}%"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def fade_out():
    for v in FADE_STEPS:
        _set_volume(v)
        time.sleep(0.05)


def fade_in():
    for v in reversed(FADE_STEPS):
        _set_volume(v)
        time.sleep(0.05)


def stop_radio(fade=True):
    global current_radio_process

    if current_radio_process:
        if fade:
            fade_out()

        current_radio_process.terminate()
        current_radio_process = None


def play_radio(name):
    global current_radio_process

    stop_radio(fade=False)

    radio = RADIOS.get(name)
    if not radio:
        print("Radio not found")
        return

    cmd = [
        "mpv",
        "--no-video",
        "--ytdl-format=bestaudio",
        "--cache=yes",
        "--quiet",
        radio["url"]
    ]

    current_radio_process = subprocess.Popen(cmd)
    fade_in()
