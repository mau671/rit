"""Pruebas unitarias para el motor de indexación SPIMI."""

from __future__ import annotations

from pathlib import Path

from indexador.lector_binario import LectorIndiceBinario
from indexador.spimi import MotorIndexadorSPIMI


def test_spimi_indexar_pequeno(tmp_path: Path) -> None:
    ruta_idx = tmp_path / "test_spimi.idx"
    dir_tmp = tmp_path / "spimi_tmp"

    # 3 documentos de prueba
    docs = [
        (0, "a" * 64, ["cine", "pelicula", "accion", "cine"]),
        (1, "b" * 64, ["pelicula", "drama", "actor"]),
        (2, "c" * 64, ["cine", "drama", "accion", "cine", "guion"]),
    ]

    motor = MotorIndexadorSPIMI(
        directorio_temporal=dir_tmp,
        tamano_bloque_documentos=2,  # Provocar división en bloques
        esquema_tf="sublineal",
    )

    stats = motor.indexar_coleccion(
        iterador_documentos=iter(docs),
        ruta_salida_indice=ruta_idx,
        flags_normalizacion=0b0001,
    )

    assert stats["total_docs"] == 3
    assert stats["total_terminos"] == 6  # accion, actor, cine, drama, guion, pelicula
    assert stats["total_postings"] > 0
    assert ruta_idx.is_file()

    # Inspeccionar con LectorIndiceBinario
    with LectorIndiceBinario(ruta_idx) as lector:
        assert lector.encabezado.total_docs == 3
        assert lector.encabezado.total_terminos == stats["total_terminos"]
        assert lector.encabezado.flags_normalizacion == 0b0001

        # Verificar búsqueda de término 'cine'
        termino_cine = lector.buscar_termino("cine")
        assert termino_cine is not None
        assert termino_cine.df == 2  # aparece en doc 0 y doc 2

        postings_cine = lector.leer_postings(termino_cine)
        assert len(postings_cine) == 2
        assert postings_cine[0].doc_id == 0
        assert postings_cine[0].tf == 2
        assert postings_cine[1].doc_id == 2
        assert postings_cine[1].tf == 2

        # Verificar norma euclidiana del doc 0
        doc0 = lector.leer_documento(0)
        assert doc0 is not None
        assert doc0.norma_vectorial > 0.0
