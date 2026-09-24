import logging
import signal
import sys
import time
from pathlib import Path

import requests

from actions import execute_action
from config import API_ENDPOINTS, LOG_DIR, POLL_INTERVAL_SECONDS, REQUEST_TIMEOUT_SECONDS

RUNNING = True


class RunFilter(logging.Filter):
    def __init__(self, run_name: str):
        super().__init__()
        self.run_name = run_name

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "run", None) == self.run_name


def configure_logging() -> logging.Logger:
    Path(LOG_DIR).mkdir(exist_ok=True)
    logger = logging.getLogger("api_runner")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | RUN=%(run)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Un archivo por tipo de Run.
    for run_name in API_ENDPOINTS:
        handler = logging.FileHandler(
            Path(LOG_DIR) / f"{run_name}.log", encoding="utf-8"
        )
        handler.setFormatter(formatter)
        handler.addFilter(RunFilter(run_name))
        logger.addHandler(handler)

    return logger


LOGGER = configure_logging()


def log(run: str, level: int, message: str, *args) -> None:
    LOGGER.log(level, message, *args, extra={"run": run})


def stop_handler(signum, frame) -> None:
    global RUNNING
    RUNNING = False


def process_endpoint(session: requests.Session, expected_run: str, url: str) -> None:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()

        run = str(payload.get("run") or expected_run)
        action = str(payload.get("action") or "no_action")

        log(
            run,
            logging.INFO,
            "HTTP=%s | action=%s | version=%s | updatedAt=%s",
            response.status_code,
            action,
            payload.get("version"),
            payload.get("updatedAt"),
        )

        # Los logs de actions.py se mantienen generales; los resultados principales
        # quedan identificados aquí por RUN.
        try:
            execute_action(action, payload)
            log(run, logging.INFO, "action=%s | RESULT=OK", action)
        except Exception:
            log(run, logging.exception, "action=%s | RESULT=ERROR", action)

    except requests.RequestException as exc:
        log(expected_run, logging.ERROR, "REQUEST_ERROR | %s", exc)
    except ValueError as exc:
        log(expected_run, logging.ERROR, "INVALID_JSON | %s", exc)
    except Exception:
        log(expected_run, logging.exception, "UNEXPECTED_ERROR")


def main() -> None:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    log("night", logging.INFO, "Worker iniciado.")
    log("afternoon", logging.INFO, "Worker iniciado.")

    with requests.Session() as session:
        while RUNNING:
            started = time.monotonic()

            for run_name, url in API_ENDPOINTS.items():
                if not RUNNING:
                    break
                process_endpoint(session, run_name, url)

            elapsed = time.monotonic() - started
            wait_for = max(0.0, POLL_INTERVAL_SECONDS - elapsed)

            # Permite detener el worker sin esperar todo el intervalo.
            end = time.monotonic() + wait_for
            while RUNNING and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))

    log("night", logging.INFO, "Worker detenido.")
    log("afternoon", logging.INFO, "Worker detenido.")


if __name__ == "__main__":
    main()
