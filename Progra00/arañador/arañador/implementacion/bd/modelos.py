"""Modelos de dominio inmutables para la persistencia audiovisual.

Los modelos no dependen de Scrapy.  De esta forma los repositorios pueden
reutilizarse desde una tubería, un middleware o un guion sin importar el
marco de arañado.  Todas las fechas se normalizan a UTC al construir el
objeto y se serializan como texto ISO-8601 al guardarlas.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

type PathLike = str | os.PathLike[str]
"""Ruta relativa o absoluta aceptada por los modelos."""

_PATRON_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def ahora_utc() -> datetime:
    """Devuelve el instante actual como ``datetime`` con zona horaria UTC."""
    return datetime.now(UTC)


def normalizar_datetime_utc(valor: datetime | str) -> datetime:
    """Normaliza un instante a ``datetime`` UTC.

    Un valor ``str`` se interpreta con :func:`datetime.fromisoformat`, lo que
    permite leer datos exportados por otros procesos.  Los valores naive se
    interpretan como UTC, una convención explícita para no convertir por
    accidente una fecha ya almacenada en una zona local desconocida.

    Args:
        valor: Fecha horaria o cadena ISO-8601.

    Returns:
        Fecha equivalente en UTC.

    Raises:
        TypeError: Si el valor no es una fecha o una cadena.
        ValueError: Si la cadena no representa una fecha ISO válida.
    """
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.endswith("Z") or texto.endswith("z"):
            texto = texto[:-1] + "+00:00"
        try:
            valor = datetime.fromisoformat(texto)
        except ValueError as exc:
            raise ValueError(f"Fecha ISO inválida: {valor!r}") from exc
    if not isinstance(valor, datetime):
        raise TypeError("Se esperaba datetime o una cadena ISO-8601.")
    if valor.tzinfo is None:
        return valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC)


def timestamp_utc_iso(valor: datetime | str) -> str:
    """Serializa una fecha como ISO-8601 UTC con desplazamiento ``+00:00``."""
    return normalizar_datetime_utc(valor).isoformat()


def datetime_desde_timestamp(valor: str | datetime) -> datetime:
    """Convierte un timestamp almacenado en SQLite a ``datetime`` UTC."""
    return normalizar_datetime_utc(valor)


def _validar_cadena(valor: Any, nombre: str, *, permitir_vacio: bool = False) -> str:
    """Valida y normaliza una cadena de texto no vacía."""
    if not isinstance(valor, str):
        raise TypeError(f"{nombre} debe ser una cadena.")
    normalizada = valor.strip()
    if not permitir_vacio and not normalizada:
        raise ValueError(f"{nombre} no puede estar vacío.")
    return normalizada


def _validar_sha256(valor: Any, nombre: str) -> str:
    """Valida una huella SHA-256 hexadecimal de 64 caracteres."""

    normalizada = _validar_cadena(valor, nombre).lower()
    if _PATRON_SHA256.fullmatch(normalizada) is None:
        raise ValueError(f"{nombre} debe ser un SHA-256 hexadecimal de 64 caracteres.")
    return normalizada


def _validar_codigo_http(codigo_http: int | None) -> int | None:
    """Valida un código HTTP, permitiendo ``None`` para errores de red."""
    if codigo_http is None:
        return None
    if isinstance(codigo_http, bool) or not isinstance(codigo_http, int):
        raise TypeError("codigo_http debe ser un entero o None.")
    if codigo_http < 0:
        raise ValueError("codigo_http no puede ser negativo.")
    return codigo_http


def _validar_ruta_relativa(valor: PathLike) -> str:
    """Valida que la ruta de un documento sea relativa y no atraviese ``..``."""
    if isinstance(valor, (PurePosixPath, PureWindowsPath)):
        texto = str(valor)
    elif isinstance(valor, str):
        texto = valor
    else:
        if not isinstance(valor, os.PathLike):
            raise TypeError("ruta_relativa debe ser str o pathlib.Path.")
        texto = str(valor)
    texto = texto.strip()
    if not texto:
        raise ValueError("ruta_relativa no puede estar vacía.")
    # PureWindowsPath detecta rutas como C:\\... aunque el proceso corra en Linux.
    ruta_windows = PureWindowsPath(texto)
    if PurePosixPath(texto).is_absolute() or ruta_windows.is_absolute() or ruta_windows.drive:
        raise ValueError("ruta_relativa debe ser relativa a la raíz de almacenamiento.")
    partes = PurePosixPath(texto.replace("\\", "/")).parts
    if not partes:
        raise ValueError("ruta_relativa debe identificar un archivo o carpeta.")
    if ".." in partes:
        raise ValueError("ruta_relativa no puede contener segmentos '..'.")
    return PurePosixPath(*partes).as_posix()


@dataclass(frozen=True, slots=True)
class MetadatoDocumento:
    """Metadatos de un documento limpio listo para persistir.

    La clase sigue el contrato documentado del proyecto. ``codigo_http`` admite ``None``
    para una operación que falló antes de recibir una respuesta HTTP; para los
    documentos descargados con éxito se recomienda un entero entre 100 y 599.
    ``ruta_relativa`` siempre es una ruta relativa al árbol de almacenamiento.
    """

    url: str
    hash_url: str
    dominio: str
    categoria: str
    ruta_relativa: str
    tamano_texto_bytes: int
    hash_contenido: str
    codigo_http: int | None
    profundidad: int
    fecha_descarga: datetime
    fecha_revisitacion: datetime | None = None

    def __post_init__(self) -> None:
        """Normaliza y valida los valores del modelo inmutable."""
        object.__setattr__(self, "url", _validar_cadena(self.url, "url"))
        object.__setattr__(self, "hash_url", _validar_sha256(self.hash_url, "hash_url"))
        object.__setattr__(self, "dominio", _validar_cadena(self.dominio, "dominio"))
        object.__setattr__(self, "categoria", _validar_cadena(self.categoria, "categoria"))
        object.__setattr__(
            self,
            "ruta_relativa",
            _validar_ruta_relativa(self.ruta_relativa),
        )
        if isinstance(self.tamano_texto_bytes, bool) or not isinstance(
            self.tamano_texto_bytes, int
        ):
            raise TypeError("tamano_texto_bytes debe ser un entero.")
        if self.tamano_texto_bytes < 0:
            raise ValueError("tamano_texto_bytes no puede ser negativo.")
        object.__setattr__(
            self,
            "hash_contenido",
            _validar_sha256(self.hash_contenido, "hash_contenido"),
        )
        object.__setattr__(self, "codigo_http", _validar_codigo_http(self.codigo_http))
        if isinstance(self.profundidad, bool) or not isinstance(self.profundidad, int):
            raise TypeError("profundidad debe ser un entero.")
        if self.profundidad < 0:
            raise ValueError("profundidad no puede ser negativa.")
        object.__setattr__(self, "fecha_descarga", normalizar_datetime_utc(self.fecha_descarga))
        if self.fecha_revisitacion is not None:
            object.__setattr__(
                self,
                "fecha_revisitacion",
                normalizar_datetime_utc(self.fecha_revisitacion),
            )

    @property
    def fecha_revisitacion_sugerida(self) -> datetime | None:
        """Alias semántico de ``fecha_revisitacion`` usado por la hoja de ruta."""
        return self.fecha_revisitacion

    def a_timestamp(self) -> dict[str, str | int | None]:
        """Devuelve los campos primitivos necesarios para una inserción SQL."""
        return {
            "url": self.url,
            "hash_url": self.hash_url,
            "dominio": self.dominio,
            "categoria": self.categoria,
            "ruta_relativa": self.ruta_relativa,
            "tamano_texto_bytes": self.tamano_texto_bytes,
            "hash_contenido": self.hash_contenido,
            "codigo_http": self.codigo_http,
            "profundidad": self.profundidad,
            "fecha_descarga": timestamp_utc_iso(self.fecha_descarga),
            "fecha_revisitacion": (
                timestamp_utc_iso(self.fecha_revisitacion)
                if self.fecha_revisitacion is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RegistroBitacora:
    """Entrada inmutable de auditoría de una visita o de un error de red."""

    timestamp: datetime
    dominio: str
    url_origen: str | None
    url_destino: str
    tiempo_respuesta_ms: float
    codigo_http: int | None
    accion: str

    def __post_init__(self) -> None:
        """Normaliza y valida los datos de la bitácora."""
        object.__setattr__(self, "timestamp", normalizar_datetime_utc(self.timestamp))
        object.__setattr__(self, "dominio", _validar_cadena(self.dominio, "dominio"))
        if self.url_origen is not None:
            object.__setattr__(
                self,
                "url_origen",
                _validar_cadena(self.url_origen, "url_origen"),
            )
        object.__setattr__(
            self,
            "url_destino",
            _validar_cadena(self.url_destino, "url_destino"),
        )
        if isinstance(self.tiempo_respuesta_ms, bool) or not isinstance(
            self.tiempo_respuesta_ms, (int, float)
        ):
            raise TypeError("tiempo_respuesta_ms debe ser numérico.")
        tiempo = float(self.tiempo_respuesta_ms)
        if not math.isfinite(tiempo) or tiempo < 0:
            raise ValueError("tiempo_respuesta_ms debe ser finito y no negativo.")
        object.__setattr__(self, "tiempo_respuesta_ms", tiempo)
        object.__setattr__(self, "codigo_http", _validar_codigo_http(self.codigo_http))
        object.__setattr__(
            self,
            "accion",
            _validar_cadena(self.accion, "accion").upper(),
        )

    def a_timestamp(self) -> dict[str, str | float | int | None]:
        """Devuelve los campos primitivos necesarios para una inserción SQL."""
        return {
            "timestamp": timestamp_utc_iso(self.timestamp),
            "dominio": self.dominio,
            "url_origen": self.url_origen,
            "url_destino": self.url_destino,
            "tiempo_respuesta_ms": self.tiempo_respuesta_ms,
            "codigo_http": self.codigo_http,
            "accion": self.accion,
        }


__all__ = [
    "MetadatoDocumento",
    "RegistroBitacora",
    "UTC",
    "ahora_utc",
    "datetime_desde_timestamp",
    "normalizar_datetime_utc",
    "timestamp_utc_iso",
]
