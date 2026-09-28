"""Comando de la araña propia.

Uso:
    uv run propio               # cosecha (Ctrl+C para detener; se puede reanudar)
    uv run propio verificar     # revisa semillas, hosts y base de datos
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from . import __version__, configuracion
from .arana import Arana
from .bd import BaseDatos
from .semillas import cargar_semillas, derivar_hosts


def configurar_log() -> None:
    """Bitácora de texto del recorrido en ``bitacoras/recorrido.log``.

    Complementa la tabla ``bitacora_recorrido``: aquí queda cada GET con su
    hilo, código, tiempo y profundidad, fácil de revisar con ``tail -f``.
    """

    configuracion.RUTA_LOG.parent.mkdir(parents=True, exist_ok=True)
    manejador = logging.FileHandler(configuracion.RUTA_LOG, encoding="utf-8")
    manejador.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    )
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, configuracion.NIVEL_LOG, logging.INFO))
    raiz.addHandler(manejador)


def ejecutar_verificacion() -> int:
    semillas = cargar_semillas()
    hosts = derivar_hosts(semillas)
    print(f"Semillas: {configuracion.RUTA_SEMILLAS}")
    print(f"  {len(semillas)} URL semilla, {len(hosts)} hosts permitidos:")
    for host in hosts:
        print(f"    - {host}")
    bd = BaseDatos()
    print(f"Base de datos: {bd.ruta}")
    print(f"  Resumen: {bd.resumen()}")
    bd.cerrar()
    print(f"Repositorio de texto: {configuracion.RUTA_REPOSITORIO}")
    print(f"Hilos configurados: {configuracion.HILOS}")
    return 0


def ejecutar_cosecha() -> int:
    configurar_log()
    bd = BaseDatos()
    arana = Arana(cargar_semillas(), bd)
    print(f"Cosechando con {arana.hilos} hilos. Log: {configuracion.RUTA_LOG}", flush=True)
    resumen = arana.ejecutar()
    print("\nCosecha finalizada.")
    print(arana.texto_progreso(resumen))
    bd.cerrar()
    return 0


def main(argumentos: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="propio", description="Arañador propio IC-8060")
    parser.add_argument("--version", action="store_true", help="muestra la versión")
    sub = parser.add_subparsers(dest="comando", metavar="{verificar}")
    sub.add_parser("verificar", help="revisa semillas, hosts y base de datos")
    opciones = parser.parse_args(argumentos)

    if opciones.version:
        print(__version__)
        return 0
    if opciones.comando == "verificar":
        return ejecutar_verificacion()
    return ejecutar_cosecha()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
