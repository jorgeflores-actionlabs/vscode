from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SQLSERVER_DIR = BASE_DIR.parent
APP_DIR = SQLSERVER_DIR / "app"

# Mapping de comandos por action y turno.
#
# Cada comando acepta:
# - script: ruta del .py a ejecutar.
# - args: parametros fijos o placeholders del payload.
# - cwd: carpeta desde donde se ejecuta el comando.
#
# Placeholders soportados:
# - {run}
# - {action}
# - {version}
# - {updatedAt}
# - cualquier llave simple que venga en el payload.
#
# Ejemplo de parametro desde API:
#   "dealerId": 8701
# puede usarse como:
#   "--dealer-id", "{dealerId}"
COMMAND_MAPPING = {
    "schedule": {
        "night": [
            {
                "script": APP_DIR / "dummy.py",
                "args": ["1234"],
                "cwd": APP_DIR,
            },
        ],
        "afternoon": [
            {
                "script": APP_DIR / "dummy.py",
                "args": ["1234"],
                "cwd": APP_DIR,
            },
        ],
    },
}
