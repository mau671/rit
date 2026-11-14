"""Pruebas del buscador vectorial sobre índices sintéticos."""

from __future__ import annotations

from pathlib import Path

import pytest

from indexador.buscador import BuscadorVectorial
from indexador.escritor_binario import EscritorIndiceBinario
from indexador.formato_binario import EntradaDocumento, Posting


def _construir_indice(
    ruta: Path,
    documentos: list[tuple[str, int, float]],
    terminos: list[tuple[str, int, float, list[Posting]]],
    flags: int = 0b0000_0011,
) -> None:
    """Escribe un índice mínimo con postings explícitos y offsets calculados."""
    with EscritorIndiceBinario(ruta) as escritor:
        escritor.flags_normalizacion = flags
        escritor.escribir_documentos(
            [
                EntradaDocumento(
                    doc_id=i,
                    hash_url=hash_hex,
                    num_tokens=tokens,
                    norma_vectorial=norma,
                )
                for i, (hash_hex, tokens, norma) in enumerate(documentos)
            ]
        )
        escritor.escribir_diccionario_y_postings(
            [(termino, df, idf, postings) for termino, df, idf, postings in terminos]
        )
        escritor.finalizar(flags_normalizacion=flags)


@pytest.fixture
def indice(tmp_path: Path) -> Path:
    ruta = tmp_path / "prueba.idx"
    _construir_indice(
        ruta,
        documentos=[
            ("0" * 64, 5, 10.0),
            ("1" * 64, 4, 8.0),
            ("2" * 64, 3, 6.0),
        ],
        terminos=[
            ("cine", 2, 1.0, [Posting(0, 2, 3.0), Posting(2, 1, 1.5)]),
            ("guion", 2, 1.0, [Posting(1, 3, 4.0), Posting(2, 1, 1.0)]),
            ("drama", 1, 2.0, [Posting(0, 1, 2.0)]),
        ],
    )
    return ruta


def test_busqueda_ordena_por_similitud(indice: Path) -> None:
    with BuscadorVectorial.desde_archivo(indice) as buscador:
        respuesta = buscador.buscar("cine guion", top=3)

    assert respuesta.tokens == ["cine", "guion"]
    assert respuesta.terminos_en_indice == ["cine", "guion"]
    # doc 1 (guion, w=4.0, ‖d‖=8) > doc 2 (cine+guion = 2.5, ‖d‖=6) > doc 0 (cine, w=3.0, ‖d‖=10)
    assert [r.doc_id for r in respuesta.resultados] == [1, 2, 0]
    similitudes = [r.similitud for r in respuesta.resultados]
    assert similitudes == sorted(similitudes, reverse=True)
    assert similitudes[0] > similitudes[1] > 0


def test_termino_ausente_se_reporta(indice: Path) -> None:
    with BuscadorVectorial.desde_archivo(indice) as buscador:
        respuesta = buscador.buscar("cine inexistente", top=5)

    assert respuesta.terminos_ausentes == ["inexistente"]
    assert respuesta.resultados


def test_sin_coincidencias_sugiere_prefijos(indice: Path) -> None:
    with BuscadorVectorial.desde_archivo(indice) as buscador:
        respuesta = buscador.buscar("guionistas", top=5)

    assert respuesta.resultados == []
    assert "guion" in respuesta.sugerencias


def test_consulta_vacia(indice: Path) -> None:
    with BuscadorVectorial.desde_archivo(indice) as buscador:
        respuesta = buscador.buscar("!!! ???", top=5)

    assert respuesta.tokens == []
    assert respuesta.resultados == []


def test_hash_de_url_se_serializa_completo(indice: Path) -> None:
    with BuscadorVectorial.desde_archivo(indice) as buscador:
        respuesta = buscador.buscar("drama", top=1)

    assert respuesta.resultados[0].hash_url == "0" * 64
