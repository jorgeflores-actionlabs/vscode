import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from config import PID_FILE

BASE_DIR = Path(__file__).resolve().parent
PID_PATH = BASE_DIR / PID_FILE
WORKER = BASE_DIR / "api_worker.py"


def read_pid():
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def get_process():
    pid = read_pid()
    if not pid:
        return None
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return None
        cmdline = " ".join(process.cmdline()).lower()
        if "api_worker.py" not in cmdline:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def cleanup_pid():
    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        pass


def start():
    existing = get_process()
    if existing:
        print(f"RUNNER ya está activo | PID={existing.pid}")
        return 0

    cleanup_pid()

    creationflags = 0
    kwargs = {}

    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True

    # stdout/stderr van a DEVNULL porque el proceso está desacoplado.
    # Los detalles quedan en logs/night.log y logs/afternoon.log.
    process = subprocess.Popen(
        [sys.executable, str(WORKER)],
        cwd=str(BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        **kwargs,
    )

    PID_PATH.write_text(str(process.pid), encoding="utf-8")
    time.sleep(0.5)

    if process.poll() is not None:
        cleanup_pid()
        print("ERROR: el worker terminó durante el arranque.")
        return 1

    print(f"RUNNER iniciado en background | PID={process.pid}")
    print("Logs: logs\\night.log y logs\\afternoon.log")
    return 0


def stop():
    process = get_process()
    if not process:
        cleanup_pid()
        print("RUNNER no está activo.")
        return 0

    pid = process.pid
    print(f"Deteniendo RUNNER | PID={pid} ...")

    try:
        process.terminate()
        process.wait(timeout=10)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    finally:
        cleanup_pid()

    print("RUNNER detenido.")
    return 0


def restart():
    stop()
    time.sleep(1)
    return start()


def status():
    process = get_process()
    if process:
        print(f"RUNNER ACTIVO | PID={process.pid}")
        return 0

    cleanup_pid()
    print("RUNNER DETENIDO")
    return 1


def usage():
    print("Uso: python runner.py {start|stop|restart|status}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        usage()
        raise SystemExit(2)

    command = sys.argv[1].lower()
    commands = {
        "start": start,
        "stop": stop,
        "restart": restart,
        "status": status,
    }

    fn = commands.get(command)
    if fn is None:
        usage()
        raise SystemExit(2)

    raise SystemExit(fn())
