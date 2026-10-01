"""Pruebas del subcomando ``informe`` del CLI.

Se aíslan las dos fases con espías sobre los ``main`` de sus módulos, de modo
que las pruebas no dependen de que ``calcular_zipf`` esté modificado ni tocan
el corpus real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arañador import cli
from arañador.guiones import calcular_zipf as modulo_zipf
from arañador.guiones import exportar_estadisticas as modulo_estadisticas


class _Espia:
    """Registra los argumentos recibidos y simula una ejecución exitosa."""

    def __init__(self) -> None:
        self.llamadas: list[list[str]] = []

    def __call__(self, argumentos: Any) -> int:
        self.llamadas.append(list(argumentos))
        return 0


def _instalar_espia(monkeypatch: pytest.MonkeyPatch, modulo: Any) -> _Espia:
    """Sustituye ``main`` del módulo dado y devuelve el espía."""

    espia = _Espia()
    monkeypatch.setattr(modulo, "main", espia)
    return espia


def test_ejecutar_informe_llama_las_dos_fases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambas fases se ejecutan y el resultado global es exitoso."""

    espia_zipf = _instalar_espia(monkeypatch, modulo_zipf)
    espia_estadisticas = _instalar_espia(monkeypatch, modulo_estadisticas)

    assert cli.ejecutar_informe() == 0
    assert len(espia_estadisticas.llamadas) == 1
    assert len(espia_zipf.llamadas) == 1
    assert espia_estadisticas.llamadas[0] == []
    assert espia_zipf.llamadas[0] == []


def test_ejecutar_informe_imprime_cabeceras(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """El usuario recibe una cabecera antes de cada fase."""

    _instalar_espia(monkeypatch, modulo_zipf)
    _instalar_espia(monkeypatch, modulo_estadisticas)

    cli.ejecutar_informe()
    salida = capsys.readouterr().out
    assert "Fase 1/2" in salida
    assert "Fase 2/2" in salida


def test_ejecutar_informe_reenvia_trabajadores_y_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``trabajadores`` y ``cache`` llegan como banderas a Zipf."""

    espia_zipf = _instalar_espia(monkeypatch, modulo_zipf)
    _instalar_espia(monkeypatch, modulo_estadisticas)

    assert cli.ejecutar_informe(trabajadores=4, cache=Path("/tmp/x")) == 0
    assert espia_zipf.llamadas[0] == ["--cache", "/tmp/x", "--trabajadores", "4"]


def test_ejecutar_informe_sin_progreso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``progreso=False`` añade ``--sin-progreso`` al argv de Zipf."""

    espia_zipf = _instalar_espia(monkeypatch, modulo_zipf)
    _instalar_espia(monkeypatch, modulo_estadisticas)

    assert cli.ejecutar_informe(progreso=False) == 0
    assert espia_zipf.llamadas[0] == ["--sin-progreso"]


def test_main_sin_cache_no_reenvia_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--sin-cache`` evita pasar ``--cache`` a Zipf."""

    espia_zipf = _instalar_espia(monkeypatch, modulo_zipf)
    _instalar_espia(monkeypatch, modulo_estadisticas)

    assert cli.main(["informe", "--sin-cache", "--sin-progreso"]) == 0
    argv = espia_zipf.llamadas[0]
    assert "--cache" not in argv
    assert "--sin-progreso" in argv
