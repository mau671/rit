"""Casos texto -> tokens de casos_prueba/normalizacion.json.

El mismo archivo lo debe pasar el indexador Lucene (ver scripts/verificar_casos.py),
así que aquí se fija el contrato de normalización de las tres políticas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from indexador.configuracion import ConfiguracionIndexador
from indexador.normalizador import Normalizador

RAIZ = Path(__file__).resolve().parents[1]
DATOS = json.loads((RAIZ / "casos_prueba" / "normalizacion.json").read_text(encoding="utf-8"))
CASOS = [(c["id"], p, c) for c in DATOS["casos"] for p in DATOS["politicas"]]


@pytest.mark.parametrize(("caso_id", "politica", "caso"), CASOS, ids=[f"{i}-{p}" for i, p, _ in CASOS])
def test_caso_compartido(caso_id: str, politica: str, caso: dict) -> None:
    # La config sale de configuraciones/<politica>.conf, la misma que usa el indexador real.
    cfg = ConfiguracionIndexador.cargar_desde_archivo(RAIZ / "configuraciones" / f"{politica}.conf")
    assert Normalizador(cfg.normalizacion).normalizar_texto(caso["texto"]) == caso["tokens"][politica]


def test_conf_y_defecto_coinciden() -> None:
    """crear_defecto() (usado por el buscador) debe normalizar igual que el .conf."""
    for politica in DATOS["politicas"]:
        a = ConfiguracionIndexador.cargar_desde_archivo(RAIZ / "configuraciones" / f"{politica}.conf")
        b = ConfiguracionIndexador.crear_defecto(politica)
        assert a.normalizacion == b.normalizacion


def test_recursos_sin_ambiguos() -> None:
    """Las entradas descartadas por ambiguas no deben volver a colarse al mapa."""
    from indexador.normalizador import TESAURO_AUDIOVISUAL

    for ambiguo in ("star", "stars", "score", "short", "feature", "editor", "composer"):
        assert ambiguo not in TESAURO_AUDIOVISUAL
