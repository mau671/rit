"""Adaptadores hacia bibliotecas externas: extracción y SQLite."""

from .extraccion import (
    MINIMO_CARACTERES,
    UMBRAL_CARACTERES,
    UMBRAL_MINIMO_CARACTERES,
    EstadoExtraccion,
    ResultadoExtraccion,
    extract_text,
    extraer,
    extraer_texto,
    extraer_texto_limpio,
    normalizar_texto_extraido,
)
from .sqlite import ConexionSQLite

__all__ = [
    "ConexionSQLite",
    "EstadoExtraccion",
    "ResultadoExtraccion",
    "MINIMO_CARACTERES",
    "UMBRAL_CARACTERES",
    "UMBRAL_MINIMO_CARACTERES",
    "extraer",
    "extraer_texto",
    "extract_text",
    "extraer_texto_limpio",
    "normalizar_texto_extraido",
]
