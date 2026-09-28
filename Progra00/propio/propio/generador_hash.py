# Copia literal de arañador/arañador/implementacion/utilidades/generador_hash.py.
# Se duplica (en vez de importarla) para que ambas arañas calculen exactamente
# las mismas URL canónicas y huellas SHA-256 sin que propio dependa de Scrapy.
"""Huellas SHA-256 reproducibles para deduplicación.

Las huellas de URL se calculan sobre la URL canónica producida por
:func:`normalizar_url`.  Las huellas de contenido normalizan Unicode NFC,
eliminan NUL, unifican saltos de línea y reducen el espacio horizontal; así
una descarga equivalente en Unicode o con espaciado HTML no se almacena dos
veces.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .normalizador_url import normalizar_url

__all__ = [
    "calcular_hash_contenido",
    "calcular_hash_url",
    "calcular_sha256_contenido",
    "calcular_sha256_url",
    "generar_hash_contenido",
    "generar_hash_url",
    "hash_contenido",
    "hash_texto",
    "hash_url",
    "normalizar_contenido_para_hash",
]

_ESPACIOS_HORIZONTALES = re.compile(r"[^\S\n]+")
_ESPACIOS_ALREDEDOR_DE_SALTOS = re.compile(r" *\n *")
_SALTOS_REPETIDOS = re.compile(r"\n{3,}")


def _decodificar(contenido: bytes) -> str:
    # Los bytes son texto de extracción, no un canal de acceso a archivos.
    # ``replace`` hace que una descarga con una cabecera imperfecta sea
    # procesable en vez de abortar todo el lote.
    return contenido.decode("utf-8", errors="replace")


def normalizar_contenido_para_hash(contenido: str | bytes) -> str:
    """Normaliza texto para que la huella sea estable y reproducible.

    La transformación es deliberadamente conservadora: no cambia mayúsculas,
    puntuación ni palabras.  Sí unifica ``CRLF``/``CR`` a ``LF``, convierte
    espacios Unicode horizontales a un espacio ASCII, recorta espacio en los
    extremos de cada línea, limita las líneas vacías a dos y elimina NUL.
    """

    if isinstance(contenido, bytes):
        texto = _decodificar(contenido)
    elif isinstance(contenido, str):
        texto = contenido
    else:
        raise TypeError("contenido debe ser str o bytes")

    texto = unicodedata.normalize("NFC", texto).replace("\x00", "")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = _ESPACIOS_HORIZONTALES.sub(" ", texto)
    texto = _ESPACIOS_ALREDEDOR_DE_SALTOS.sub("\n", texto)
    texto = _SALTOS_REPETIDOS.sub("\n\n", texto)
    return texto.strip()


def hash_url(url: str, *, normalizar: bool = True) -> str:
    """Devuelve el SHA-256 hexadecimal de la URL canónica.

    Args:
        url: URL de origen.
        normalizar: Si es ``False``, se calcula sobre la cadena recibida sin
            canonicalización ni recorte.  El valor por defecto es el seguro
            para deduplicación.
    """

    if not isinstance(url, str):
        raise TypeError("url debe ser str")
    if not isinstance(normalizar, bool):
        raise TypeError("normalizar debe ser bool")
    valor = normalizar_url(url) if normalizar else url
    return hashlib.sha256(valor.encode("utf-8", errors="strict")).hexdigest()


def hash_contenido(contenido: str | bytes, *, normalizar: bool = True) -> str:
    """Devuelve el SHA-256 hexadecimal del contenido normalizado.

    Con ``normalizar=True`` (valor predeterminado) se usa Unicode NFC, se
    eliminan NUL, se convierten CRLF/CR en LF, se reduce el espacio Unicode
    horizontal y se recortan los espacios exteriores.  Con ``False`` sólo se
    decodifican bytes como UTF-8 (reemplazando errores) y se calcula la huella
    del texto resultante sin esas transformaciones.
    """

    if not isinstance(normalizar, bool):
        raise TypeError("normalizar debe ser bool")
    if normalizar:
        valor = normalizar_contenido_para_hash(contenido)
    elif isinstance(contenido, bytes):
        valor = _decodificar(contenido)
    elif isinstance(contenido, str):
        valor = contenido
    else:
        raise TypeError("contenido debe ser str o bytes")
    return hashlib.sha256(valor.encode("utf-8", errors="strict")).hexdigest()


# Nombres descriptivos adicionales; se mantienen los nombres cortos para el
# código de tuberías y los alias largos para una API más explícita.
generar_hash_url = hash_url
generar_hash_contenido = hash_contenido
calcular_hash_url = hash_url
calcular_hash_contenido = hash_contenido
calcular_sha256_url = hash_url
calcular_sha256_contenido = hash_contenido
hash_texto = hash_contenido
