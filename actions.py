import logging
from typing import Any, Callable

logger = logging.getLogger("api_runner.actions")


def no_action(payload: dict[str, Any]) -> None:
    """La API no solicita ninguna acción."""
    logger.info("action=no_action | No hay operación pendiente.")


def example_action(payload: dict[str, Any]) -> None:
    """
    Ejemplo. Reemplaza o agrega funciones aquí y regístralas en ACTIONS.
    """
    logger.info("action=example_action | Ejecutando función de ejemplo.")


ACTIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "no_action": no_action,
    "example_action": example_action,
}


def execute_action(action: str, payload: dict[str, Any]) -> None:
    handler = ACTIONS.get(action)
    if handler is None:
        logger.warning("action=%s | Acción no registrada; se ignora.", action)
        return
    handler(payload)
