"""Pruebas de integración contra los índices reales almacenados en ``salida/``.

Verifican que los tres archivos ``.idx`` (que ``scripts/regenerar_todo.sh``
deja en disco sin versionarlos) sigan siendo coherentes con su encabezado y que
una consulta funcione igual de bien con cualquiera de las tres políticas de
normalización, que es precisamente lo que se rompía cuando la consulta se
normalizaba con una configuración fija.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from indexador.buscador import BuscadorVectorial
from indexador.lector_binario import LectorIndiceBinario

POLITICAS = ["minimo", "estandar", "avanzado"]
DIRECTORIO_SALIDA = Path("salida")

pytestmark = pytest.mark.skipif(
    not all((DIRECTORIO_SALIDA / f"indice_{p}.idx").is_file() for p in POLITICAS),
    reason="Los índices de salida no están construidos",
)


@pytest.mark.parametrize("politica", POLITICAS)
def test_estructura_del_indice_es_consistente(politica: str) -> None:
    with LectorIndiceBinario(DIRECTORIO_SALIDA / f"indice_{politica}.idx") as lector:
        assert lector.verificar_estructura() == []


@pytest.mark.parametrize("politica", POLITICAS)
def test_consulta_devuelve_resultados_en_cada_politica(politica: str) -> None:
    """La consulta debe encontrar términos aunque la política no lematice."""
    with BuscadorVectorial.desde_archivo(
        DIRECTORIO_SALIDA / f"indice_{politica}.idx"
    ) as buscador:
        respuesta = buscador.buscar("peliculas del director", top=5)

    assert respuesta.resultados, f"la política {politica} no recuperó documentos"
    assert respuesta.resultados[0].similitud > 0
    similitudes = [r.similitud for r in respuesta.resultados]
    assert similitudes == sorted(similitudes, reverse=True)


@pytest.mark.parametrize("politica", POLITICAS)
def test_normalizacion_declarada_coincide_con_la_politica(politica: str) -> None:
    esperados = {
        "minimo": {"stopwords": False, "stemming": False},
        "estandar": {"stopwords": True, "stemming": True},
        "avanzado": {"stopwords": True, "stemming": True},
    }[politica]

    with LectorIndiceBinario(DIRECTORIO_SALIDA / f"indice_{politica}.idx") as lector:
        normalizacion = lector.normalizacion

    assert normalizacion.stopwords is esperados["stopwords"]
    assert normalizacion.stemming is esperados["stemming"]
    if politica == "avanzado":
        assert normalizacion.expandir_sinonimos is True
        assert normalizacion.normalizar_numeros is True


def test_las_tres_politicas_comparten_documentos() -> None:
    """El mismo doc_id debe apuntar al mismo hash en las tres políticas."""
    hashes = {}
    for politica in POLITICAS:
        with LectorIndiceBinario(
            DIRECTORIO_SALIDA / f"indice_{politica}.idx"
        ) as lector:
            hashes[politica] = [d.hash_url for d in lector.iterar_documentos()]

    assert hashes["minimo"] == hashes["estandar"] == hashes["avanzado"]
