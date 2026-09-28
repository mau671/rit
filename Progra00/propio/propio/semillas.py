"""Lectura de las semillas compartidas con la versión con Scrapy.

Las reglas son las mismas que ``arana_semillas.py``: una URL HTTP(S) absoluta
por línea, comentarios con ``#``, duplicados descartados por su forma canónica
y hosts permitidos derivados de las semillas (sin ``www.``). Ningún dominio se
escribe en el código.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .configuracion import RUTA_SEMILLAS
from .normalizador_url import es_url_http, normalizar_url, obtener_host_origen


def interpretar_semillas(contenido: str) -> tuple[str, ...]:
    """Convierte el texto de ``semillas.txt`` en una tupla de URL únicas."""

    semillas: list[str] = []
    vistas: set[str] = set()
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        candidata = linea.split("#", 1)[0].strip()
        if not candidata:
            continue
        if not es_url_http(candidata):
            raise ValueError(f"Línea {numero} de semillas no es una URL HTTP(S): {candidata!r}")
        clave = normalizar_url(candidata)
        if clave in vistas:
            continue
        vistas.add(clave)
        semillas.append(candidata)
    return tuple(semillas)


def cargar_semillas(ruta: Path = RUTA_SEMILLAS) -> tuple[str, ...]:
    """Lee las semillas del archivo compartido."""

    if not ruta.is_file():
        raise FileNotFoundError(f"No se encontró el archivo de semillas: {ruta}")
    semillas = interpretar_semillas(ruta.read_text(encoding="utf-8"))
    if not semillas:
        raise ValueError(f"{ruta} no declara ninguna URL semilla")
    return semillas


def derivar_hosts(semillas: Iterable[str]) -> tuple[str, ...]:
    """Hosts permitidos, en orden de aparición y sin repetir."""

    hosts: list[str] = []
    for semilla in semillas:
        try:
            host = obtener_host_origen(semilla)
        except (TypeError, ValueError):
            continue
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def host_permitido(host: str, permitidos: Iterable[str]) -> bool:
    """``True`` si ``host`` es uno de los permitidos o un subdominio suyo."""

    host = host.casefold().removeprefix("www.")
    return any(host == p or host.endswith("." + p) for p in permitidos)
