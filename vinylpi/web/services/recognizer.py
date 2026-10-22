import subprocess
import threading
import sys
import os
from typing import Optional
from vinylpi.paths import BASE_DIR
from vinylpi.integrations.home_assistant import send_vinyl_started

_recognizer_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()

def is_running() -> bool:
    global _recognizer_proc
    if _recognizer_proc is None:
        return False
    if _recognizer_proc.poll() is None:
        return True
    _recognizer_proc = None
    return False

def start(*, silence_output: bool, start_origin: str = "manual") -> bool:
    global _recognizer_proc
    with _lock:
        if is_running():
            return False

        cmd = [sys.executable, "-u", "-m", "vinylpi.main"]
        env = os.environ.copy()
        origin = "auto_wake" if start_origin == "auto_wake" else "manual"
        env["VINYLPI_START_ORIGIN"] = origin

        if silence_output:
            _recognizer_proc = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            _recognizer_proc = subprocess.Popen(cmd, cwd=BASE_DIR, env=env)

    # All entry points (dashboard, auto wake, legacy API) arrive here.
    # Notify only after spawning a NEW recognizer; repeated starts do not fire.
    # HA request failures do not prevent recognition from starting.
    send_vinyl_started(reason=origin)
    return True

def stop() -> bool:
    global _recognizer_proc
    with _lock:
        if not is_running():
            _recognizer_proc = None
            return False

        _recognizer_proc.terminate()
        try:
            _recognizer_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _recognizer_proc.kill()
        finally:
            _recognizer_proc = None
        return True
