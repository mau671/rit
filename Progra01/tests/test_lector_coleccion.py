"""Prueba de lectura sobre la colección real de Progra00."""

from pathlib import Path

from indexador.lector_coleccion import LectorColeccion


def test_lector_coleccion_lectura_real():
    ruta_bd = Path("../Progra00/almacenamiento/metadatos_araña.db")
    ruta_repo = Path("../Progra00/almacenamiento/repositorio")

    lector = LectorColeccion(ruta_bd, ruta_repo)
    total_db = lector.contar_documentos()
    assert total_db > 0

    # Probar iteración de 5 documentos
    docs = list(lector.iterar_documentos(limite=5))
    assert len(docs) == 5
    for i, doc in enumerate(docs):
        assert doc.doc_id == i
        assert len(doc.hash_url) == 64
        assert len(doc.texto) > 0
        assert doc.tamano_bytes > 0
