"""Ruta de cada documento en el repositorio de texto.

Mismo formato que la tubería de almacenamiento de la versión con Scrapy:

    {dominio}/{categoria}/{hash[:2]}/{hash}.txt

donde ``hash`` es el SHA-256 del contenido limpio. Así los guiones de
estadísticas y Zipf de ``arañador/`` funcionan sin cambios sobre este repositorio.
"""

from __future__ import annotations

import re
import unicodedata

_HASH = re.compile(r"^[0-9a-f]{64}$")


def componente_seguro(valor: str, predeterminado: str = "documento") -> str:
    """Sanea un nombre de carpeta: conserva letras, dígitos, ``.``, ``_`` y ``-``."""

    ascii_texto = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    limpio = "".join(c if c.isalnum() or c in "._-" else "-" for c in ascii_texto)
    limpio = re.sub(r"-+", "-", limpio)
    while ".." in limpio:
        limpio = limpio.replace("..", ".")
    limpio = limpio.strip(".-_")[:120].rstrip(".-_")
    return limpio or predeterminado


def ruta_relativa_documento(dominio: str, categoria: str, hash_contenido: str) -> str:
    """Devuelve la ruta POSIX relativa a la raíz del repositorio."""

    digest = hash_contenido.strip().lower()
    if not _HASH.fullmatch(digest):
        raise ValueError("hash_contenido debe ser un SHA-256 hexadecimal")
    return "/".join(
        (componente_seguro(dominio), componente_seguro(categoria), digest[:2], digest + ".txt")
    )
