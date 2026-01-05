import time
import RPi.GPIO as GPIO

SERVO_PIN = 13
SERVO_MIN = 30
SERVO_MAX = 170
CENTER_ANGLE = 100

_pwm = None
_inited = False

def _angle_to_duty(angle: float) -> float:
    return 2.5 + (angle / 180.0) * 10.0

def _clamp(a: int) -> int:
    return max(SERVO_MIN, min(SERVO_MAX, int(a)))

def servo_init():
    global _pwm, _inited
    if _inited:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(SERVO_PIN, GPIO.OUT)
    _pwm = GPIO.PWM(SERVO_PIN, 50)
    _pwm.start(_angle_to_duty(CENTER_ANGLE))
    time.sleep(0.4)
    _pwm.ChangeDutyCycle(0)
    _inited = True

def servo_goto(angle: int, hold: float = 0.30):
    global _pwm
    if not _inited:
        servo_init()
    angle = _clamp(angle)
    _pwm.ChangeDutyCycle(_angle_to_duty(angle))
    time.sleep(hold)
    _pwm.ChangeDutyCycle(0)

def servo_center():
    servo_goto(CENTER_ANGLE, 0.35)

def servo_cleanup():
    global _pwm, _inited
    try:
        if _pwm:
            _pwm.stop()
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
    _pwm = None
    _inited = False

def servo_search_pattern():
    """
    İnsan gibi etrafına bakar.
    """
    steps = [
        (100, 0.25),
        (80, 0.25),
        (120, 0.25),
        (90, 0.2),
    ]
    for angle, delay in steps:
        servo_goto(angle, delay)
