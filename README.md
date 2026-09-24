# API Runner

Worker Python para consultar periódicamente:

- `/lv/api/night`
- `/lv/api/afternoon`

La respuesta actual de ambas APIs contiene, entre otros, `run` y `action`.
El worker despacha la función correspondiente usando `action`.

## Instalación (Windows)

Descomprime esta carpeta dentro de:

`C:\Users\JorgeLuisPerezFlores\OneDrive - Accionlabs\Documents\VSCODE\sqlserver\api`

Luego abre PowerShell/CMD en esa carpeta:

```powershell
python -m pip install -r requirements.txt
python runner.py start
```

También puedes ejecutar `install.bat` y luego `start.bat`.

## Administración

```powershell
python runner.py start
python runner.py status
python runner.py stop
python runner.py restart
```

También existen `start.bat`, `stop.bat`, `restart.bat` y `status.bat`.

## Background

`start` lanza `api_worker.py` como proceso desacoplado en Windows y guarda su PID
en `.api_runner.pid`.

## Logs

Los logs se separan por tipo de Run:

- `logs/night.log`
- `logs/afternoon.log`

Para verlos en terminal en tiempo real desde PowerShell:

```powershell
Get-Content .\logs\night.log -Wait
Get-Content .\logs\afternoon.log -Wait
```

## Acciones

Edita `actions.py`. Cada valor posible de `action` debe apuntar a una función:

```python
def mi_accion(payload):
    print(payload)

ACTIONS = {
    "no_action": no_action,
    "mi_accion": mi_accion,
}
```

Las acciones desconocidas se registran como warning y no detienen el worker.

## Mapping de comandos

`command_mapping.py` define el mapping por `action` y turno (`run`).

Actualmente no hay comandos predefinidos. La estructura queda lista para agregar
comandos despues:

```python
COMMAND_MAPPING = {
    "schedule": {
        "night": [],
        "afternoon": [],
    },
}
```

Cada comando puede aceptar parametros fijos o placeholders del payload:

```python
{
    "script": APP_DIR / "mi_script.py",
    "args": ["--dealer-id", "{dealerId}", "--run", "{run}"],
    "cwd": APP_DIR,
}
```

Placeholders soportados: `{run}`, `{action}`, `{version}`, `{updatedAt}` y
cualquier llave simple que venga en el JSON de la API.

## Configuración

En `config.py` puedes cambiar:

- endpoints
- `POLL_INTERVAL_SECONDS`
- timeout HTTP

Por defecto consulta ambas APIs cada 5 segundos. El worker compara `action`,
`version` y `updatedAt`; si no cambiaron, registra `SIN_CAMBIOS` y no vuelve a
ejecutar la accion.
