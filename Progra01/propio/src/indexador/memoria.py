"""Lectura de la memoria del proceso para limitar los bloques SPIMI y medirlos.

SPIMI necesita saber cuánta memoria está ocupando para decidir cuándo volcar un
bloque a disco, y el benchmark necesita medir el pico de cada política por
separado. Ambas cosas se resuelven leyendo ``/proc/self/status`` en Linux, con
``resource.getrusage`` como respaldo en otras plataformas.
"""

from __future__ import annotations

import resource
import sys
from pathlib import Path

_RUTA_ESTADO: Path = Path("/proc/self/status")
_RUTA_CLEAR_REFS: Path = Path("/proc/self/clear_refs")


def _leer_campo_proc(nombre: str) -> float | None:
    """Lee un campo de ``/proc/self/status`` y lo devuelve en megabytes."""
    try:
        with open(_RUTA_ESTADO, encoding="ascii") as archivo:
            for linea in archivo:
                if linea.startswith(f"{nombre}:"):
                    return float(linea.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def memoria_residente_mb() -> float:
    """Memoria residente actual del proceso en megabytes."""
    residente = _leer_campo_proc("VmRSS")
    if residente is not None:
        return residente
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    return float(rusage.ru_maxrss / 1024.0)


def memoria_pico_mb() -> float:
    """Pico máximo de memoria residente alcanzado por el proceso, en megabytes."""
    pico = _leer_campo_proc("VmHWM")
    if pico is not None:
        return pico
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    return float(rusage.ru_maxrss / 1024.0)


def reiniciar_memoria_pico() -> bool:
    """Reinicia la marca de pico de RSS del proceso.

    ``VmHWM`` y ``ru_maxrss`` son marcas acumulativas: sin reiniciarlas, la
    segunda política medida hereda el pico de la primera y la comparación queda
    inservible. El núcleo permite reiniciar la marca escribiendo ``5`` en
    ``/proc/self/clear_refs``. Devuelve ``False`` cuando la plataforma no lo
    permite.
    """
    if sys.platform != "linux":
        return False
    try:
        _RUTA_CLEAR_REFS.write_text("5", encoding="ascii")
    except OSError:
        return False
    return True
