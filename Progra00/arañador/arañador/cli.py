"""Comando único del arañador.

Uso:
    uv run arañador                    # cosecha y muestra progreso automático
    uv run arañador informe            # estadísticas del repositorio y Zipf
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from arañador.entorno import RAIZ_PROYECTO, leer_entero, leer_texto


def _preparar_settings() -> Any:
    """Construye settings de Scrapy y aplica lo declarado en ``.env``."""

    os.environ.setdefault(
        "SCRAPY_SETTINGS_MODULE",
        "arañador.implementacion.configuracion",
    )
    from scrapy.utils.project import get_project_settings

    settings = get_project_settings()
    archivo_log = leer_texto("LOG_FILE", "bitacoras/cosecha.log")
    ruta_log = Path(archivo_log).expanduser()
    if not ruta_log.is_absolute():
        ruta_log = RAIZ_PROYECTO / ruta_log
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    settings.set("LOG_FILE", str(ruta_log), priority="command")
    if nivel := leer_texto("LOG_LEVEL"):
        settings.set("LOG_LEVEL", nivel.upper(), priority="command")
    return settings


def _resumen_progreso() -> str:
    """Devuelve un resumen legible del estado actual del corpus."""

    from arañador.guiones.monitorear_progreso import (
        RUTA_BASE_DATOS_PREDETERMINADA,
        RUTA_REPOSITORIO_PREDETERMINADA,
        ErrorMonitoreo,
        formatear_instantanea,
        obtener_instantanea,
    )

    try:
        instantanea = obtener_instantanea(
            RUTA_BASE_DATOS_PREDETERMINADA,
            RUTA_REPOSITORIO_PREDETERMINADA,
        )
    except ErrorMonitoreo as error:
        return f"Progreso no disponible todavía: {error}"
    return formatear_instantanea(instantanea)


def _bucle_progreso(detener: threading.Event, intervalo: int) -> None:
    """Imprime el progreso cada ``intervalo`` segundos hasta terminar."""

    while not detener.is_set():
        print("\n" + _resumen_progreso(), flush=True)
        detener.wait(intervalo)


def ejecutar_cosecha() -> int:
    """Ejecuta la araña completa y muestra progreso mientras corre."""

    from scrapy.crawler import CrawlerProcess

    settings = _preparar_settings()
    proceso = CrawlerProcess(settings)
    proceso.crawl("arañador")

    detener = threading.Event()
    intervalo = max(1, leer_entero("INTERVALO_PROGRESO", 15))
    hilo = threading.Thread(
        target=_bucle_progreso,
        args=(detener, intervalo),
        daemon=True,
    )
    hilo.start()

    try:
        proceso.start()
    finally:
        detener.set()
        hilo.join(timeout=5)

    print("\nCosecha finalizada. Resumen final:\n", flush=True)
    print(_resumen_progreso(), flush=True)
    return 0


def ejecutar_informe() -> int:
    """Genera las estadísticas del repositorio y la curva de la ley de Zipf.

    Son dos cálculos internos con costos distintos: las estadísticas leen
    SQLite y el tamaño de los archivos; Zipf recorre todo el texto. Para el
    usuario son una sola tarea del informe.
    """

    from arañador.guiones.calcular_zipf import main as generar_zipf
    from arañador.guiones.exportar_estadisticas import main as exportar_estadisticas

    codigo_estadisticas = exportar_estadisticas([])
    codigo_zipf = generar_zipf([])
    return 0 if codigo_estadisticas == 0 and codigo_zipf == 0 else 1


def construir_parser() -> argparse.ArgumentParser:
    """Construye el analizador del comando principal."""

    parser = argparse.ArgumentParser(
        prog="arañador",
        description="Ejecuta el arañador y muestra su progreso automáticamente.",
    )
    parser.add_argument("--version", action="store_true", help="muestra la versión")
    subcomandos = parser.add_subparsers(dest="comando", metavar="{informe}")
    subcomandos.add_parser(
        "informe",
        help="genera estadísticas del repositorio y la curva de Zipf",
    )
    return parser


def main(argumentos: Sequence[str] | None = None) -> int:
    """Punto de entrada del comando ``arañador``."""

    parser = construir_parser()
    opciones, _ = parser.parse_known_args(argumentos)

    if opciones.version:
        from arañador import __version__

        print(__version__)
        return 0

    if opciones.comando == "informe":
        return ejecutar_informe()
    if opciones.comando is not None:
        parser.error(f"subcomando desconocido: {opciones.comando}")

    return ejecutar_cosecha()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
