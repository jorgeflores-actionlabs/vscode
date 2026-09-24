import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from command_mapping import COMMAND_MAPPING

logger = logging.getLogger("api_runner.actions")


def no_action(payload: dict[str, Any]) -> None:
    """La API no solicita ninguna accion."""
    logger.info("action=no_action | No hay operacion pendiente.")


def example_action(payload: dict[str, Any]) -> None:
    """
    Ejemplo. Reemplaza o agrega funciones aqui y registralas en ACTIONS.
    """
    logger.info("action=example_action | Ejecutando funcion de ejemplo.")


def _format_arg(value: Any, payload: dict[str, Any]) -> str:
    if isinstance(value, Path):
        return str(value)
    if not isinstance(value, str):
        return str(value)

    replacements = {
        "run": payload.get("run", ""),
        "action": payload.get("action", ""),
        "version": payload.get("version", ""),
        "updatedAt": payload.get("updatedAt", ""),
        **payload,
    }

    try:
        safe_replacements = {
            key: "" if val is None else val for key, val in replacements.items()
        }
        return value.format_map(safe_replacements)
    except KeyError as exc:
        raise ValueError(f"Parametro usa placeholder no disponible: {exc}") from exc


def run_mapped_commands(payload: dict[str, Any]) -> None:
    action = str(payload.get("action") or "no_action")
    run = str(payload.get("run") or "")
    run_commands = COMMAND_MAPPING.get(action, {}).get(run, [])

    if not run_commands:
        logger.warning("action=%s run=%s | No hay comandos configurados.", action, run)
        return

    for index, command in enumerate(run_commands, start=1):
        script = Path(command["script"])
        args = [_format_arg(arg, payload) for arg in command.get("args", [])]
        cwd = Path(command.get("cwd") or script.parent)
        cmd = [sys.executable, str(script), *args]

        logger.info(
            "action=%s run=%s | Ejecutando comando %s/%s: %s",
            action,
            run,
            index,
            len(run_commands),
            " ".join(cmd),
        )

        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )

        if completed.stdout:
            logger.info("STDOUT | %s", completed.stdout.strip())
        if completed.stderr:
            logger.warning("STDERR | %s", completed.stderr.strip())
        if completed.returncode != 0:
            raise RuntimeError(
                f"Comando fallo con exit code {completed.returncode}: {' '.join(cmd)}"
            )


ACTIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "no_action": no_action,
    "example_action": example_action,
    "schedule": run_mapped_commands,
}


def execute_action(action: str, payload: dict[str, Any]) -> None:
    handler = ACTIONS.get(action)
    if handler is None:
        logger.warning("action=%s | Accion no registrada; se ignora.", action)
        return
    handler(payload)
