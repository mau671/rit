"""Carga única de la configuración persistente desde ``.env``."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ_PROYECTO: Path = Path(__file__).resolve().parents[1]
ARCHIVO_ENV: Path = RAIZ_PROYECTO / ".env"

# ``override=True`` hace que el archivo local sea la fuente autoritativa para
# el proyecto. Los valores por defecto del código siguen existiendo únicamente
# para que las pruebas y una instalación limpia puedan ejecutarse sin secretos.
load_dotenv(dotenv_path=ARCHIVO_ENV, override=True, encoding="utf-8")


def leer_texto(nombre: str, predeterminado: str = "") -> str:
    """Lee un valor de configuración ya cargado desde ``.env``."""

    return os.getenv(nombre, predeterminado).strip()


def leer_entero(nombre: str, predeterminado: int) -> int:
    """Lee un entero y conserva el predeterminado si el valor es inválido."""

    valor = leer_texto(nombre)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return predeterminado


__all__ = ["ARCHIVO_ENV", "RAIZ_PROYECTO", "leer_entero", "leer_texto"]
