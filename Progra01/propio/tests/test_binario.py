"""Pruebas unitarias y de integración para el formato binario manual (.idx).

Verifica:
- Serialización y deserialización a bajo nivel con struct y Little-Endian.
- Roundtrip completo (escritura -> lectura) con documentos, diccionario y postings sintéticos.
- Acceso O(1) a documentos y búsqueda binaria O(log |V|) en el diccionario.
- Compatibilidad e inspección con `hexdump -C`:
  * Cabecera exacta de 64 bytes con `|ENCUADRE|` en la posición 0x00000000.
  * Legibilidad del vocabulario UTF-8 en la columna ASCII de hexdump.
  * Coincidencia precisa de offsets de disco.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

from indexador.escritor_binario import EscritorIndiceBinario
from indexador.formato_binario import (
    MAGIC_BYTES,
    EncabezadoIndice,
    EntradaDocumento,
    EntradaTermino,
    Posting,
)
from indexador.lector_binario import LectorIndiceBinario


def test_encabezado_tamano_y_empaquetado():
    """Valida que el encabezado mida exactamente 64 bytes y soporte ida y vuelta."""
    enc = EncabezadoIndice(
        flags_normalizacion=0b0000_1101,
        total_docs=100,
        total_terminos=500,
        total_postings=2048,
        offset_docs=64,
        offset_diccionario=64 + (100 * 44),
        offset_postings=64 + (100 * 44) + 12000,
    )
    datos = enc.empaquetar()
    assert len(datos) == 64
    assert datos.startswith(b"ENCUADRE")

    desempaquetado = EncabezadoIndice.desempaquetar(datos)
    assert desempaquetado.magic == b"ENCUADRE"
    assert desempaquetado.version == 1
    assert desempaquetado.flags_normalizacion == 0b0000_1101
    assert desempaquetado.total_docs == 100
    assert desempaquetado.total_terminos == 500
    assert desempaquetado.total_postings == 2048
    assert desempaquetado.offset_docs == 64
    assert desempaquetado.offset_diccionario == 64 + (100 * 44)
    assert desempaquetado.offset_postings == 64 + (100 * 44) + 12000


def test_documento_tamano_y_empaquetado():
    """Valida que la entrada de documento mida exactamente 44 bytes."""
    hash_hex = "a" * 64
    doc = EntradaDocumento(
        doc_id=7,
        hash_url=bytes.fromhex(hash_hex),
        num_tokens=150,
        norma_vectorial=3.14159,
    )
    datos = doc.empaquetar()
    assert len(datos) == 44

    desemp = EntradaDocumento.desempaquetar(datos)
    assert desemp.doc_id == 7
    assert desemp.hash_url == bytes.fromhex(hash_hex)
    assert desemp.num_tokens == 150
    assert math.isclose(desemp.norma_vectorial, 3.14159, rel_tol=1e-5)


def test_termino_y_postings_empaquetado():
    """Valida empaquetado de términos variables y postings fijos de 12 bytes."""
    term = EntradaTermino(
        termino="computacion",
        df=12,
        idf=2.456,
        offset_postings=1024,
        bytes_postings=12 * 12,
    )
    datos_term = term.empaquetar()
    # 2 bytes len + 11 bytes "computacion" + 20 bytes sufijo = 33 bytes
    assert len(datos_term) == 2 + 11 + 20

    desemp_term, consumidos = EntradaTermino.desempaquetar_desde_bytes(datos_term)
    assert consumidos == len(datos_term)
    assert desemp_term.termino == "computacion"
    assert desemp_term.df == 12
    assert math.isclose(desemp_term.idf, 2.456, rel_tol=1e-5)
    assert desemp_term.offset_postings == 1024
    assert desemp_term.bytes_postings == 144

    post = Posting(doc_id=3, tf=5, peso_tfidf=4.125)
    datos_post = post.empaquetar()
    assert len(datos_post) == 12
    desemp_post = Posting.desempaquetar(datos_post)
    assert desemp_post.doc_id == 3
    assert desemp_post.tf == 5
    assert math.isclose(desemp_post.peso_tfidf, 4.125, rel_tol=1e-5)


def test_roundtrip_indice_completo(tmp_path: Path):
    """Test completo de escritura y lectura con LectorIndiceBinario."""
    ruta_idx = tmp_path / "indice_prueba.idx"

    # 1. Crear 5 documentos sintéticos
    docs = [
        EntradaDocumento(
            doc_id=i,
            hash_url=(f"{i:02x}" * 32).encode("ascii")[:32],  # 32 bytes exactos
            num_tokens=100 + i * 20,
            norma_vectorial=math.sqrt(10.0 + i),
        )
        for i in range(5)
    ]

    # 2. Crear vocabulario y postings sintéticos
    # Términos en desorden para comprobar que el escritor los ordena lexicográficamente
    vocabulario = [
        ("algoritmo", 2, 1.25, [Posting(0, 2, 2.5), Posting(2, 1, 1.25)]),
        ("busqueda", 3, 0.85, [Posting(1, 1, 0.85), Posting(3, 3, 2.55), Posting(4, 1, 0.85)]),
        ("datos", 1, 2.10, [Posting(0, 5, 10.5)]),
        ("informacion", 4, 0.40, [Posting(0, 1, 0.4), Posting(1, 2, 0.8), Posting(2, 1, 0.4), Posting(3, 1, 0.4)]),
    ]

    # 3. Escribir con EscritorIndiceBinario
    with EscritorIndiceBinario(ruta_idx) as escritor:
        escritor.escribir_documentos(docs)
        escritor.escribir_diccionario_y_postings(vocabulario)
        enc_final = escritor.finalizar(flags_normalizacion=0b1011)

    assert enc_final.total_docs == 5
    assert enc_final.total_terminos == 4
    assert enc_final.total_postings == 2 + 3 + 1 + 4  # 10 postings
    assert enc_final.offset_docs == 64
    assert enc_final.offset_diccionario == 64 + (5 * 44)  # 64 + 220 = 284
    assert enc_final.offset_postings > enc_final.offset_diccionario

    # 4. Leer con LectorIndiceBinario
    with LectorIndiceBinario(ruta_idx) as lector:
        enc = lector.encabezado
        assert enc.magic == MAGIC_BYTES
        assert enc.version == 1
        assert enc.flags_normalizacion == 0b1011
        assert enc.total_docs == 5
        assert enc.total_terminos == 4
        assert enc.total_postings == 10

        # Verificar documentos individuales O(1)
        for i in range(5):
            doc_rec = lector.leer_documento(i)
            assert doc_rec.doc_id == i
            assert doc_rec.hash_url == docs[i].hash_url
            assert doc_rec.num_tokens == docs[i].num_tokens
            assert math.isclose(doc_rec.norma_vectorial, docs[i].norma_vectorial, rel_tol=1e-5)

        # Verificar iterar_documentos
        docs_todos = lector.iterar_documentos()
        assert len(docs_todos) == 5

        # Verificar búsqueda de términos
        terminos_esperados = sorted([t[0] for t in vocabulario])
        dicc = lector.cargar_diccionario()
        assert [t.termino for t in dicc] == terminos_esperados

        for termino_str, df_esp, idf_esp, postings_esp in vocabulario:
            entrada_t = lector.buscar_termino(termino_str)
            assert entrada_t is not None
            assert entrada_t.termino == termino_str
            assert entrada_t.df == df_esp
            assert math.isclose(entrada_t.idf, idf_esp, rel_tol=1e-5)

            # Verificar postings leídos mediante seek directo
            postings_rec = lector.leer_postings(entrada_t)
            assert len(postings_rec) == len(postings_esp)
            for p_rec, p_esp in zip(postings_rec, postings_esp, strict=True):
                assert p_rec.doc_id == p_esp.doc_id
                assert p_rec.tf == p_esp.tf
                assert math.isclose(p_rec.peso_tfidf, p_esp.peso_tfidf, rel_tol=1e-5)

            # Verificar conveniencia obtener_postings_termino
            assert len(lector.obtener_postings_termino(termino_str)) == len(postings_esp)

        # Término inexistente
        assert lector.buscar_termino("inexistente") is None
        assert lector.obtener_postings_termino("inexistente") == []


def test_hexdump_compatibilidad_y_legibilidad(tmp_path: Path):
    """Verifica compatibilidad con el comando del sistema `hexdump -C`."""
    ruta_idx = tmp_path / "test_hexdump.idx"

    docs = [
        EntradaDocumento(
            doc_id=0,
            hash_url=b"A" * 32,
            num_tokens=50,
            norma_vectorial=1.5,
        )
    ]
    vocabulario = [
        ("palabra_legible", 1, 0.5, [Posting(0, 1, 0.5)]),
    ]

    with EscritorIndiceBinario(ruta_idx) as escritor:
        escritor.escribir_documentos(docs)
        escritor.escribir_diccionario_y_postings(vocabulario)
        escritor.finalizar(flags_normalizacion=1)

    # Ejecutar hexdump -C
    resultado = subprocess.run(
        ["hexdump", "-C", str(ruta_idx)],
        capture_output=True,
        text=True,
        check=True,
    )
    salida_hex = resultado.stdout
    lineas = salida_hex.strip().splitlines()

    # 1. Comprobar que la primera línea comience en offset 00000000 y contenga |ENCUADRE
    assert lineas[0].startswith("00000000")
    assert "|ENCUADRE" in lineas[0]

    # 2. Comprobar que los bytes del magic number coincidan en hex (45 4e 43 55 41 44 52 45)
    assert "45 4e 43 55 41 44 52 45" in lineas[0]

    # 3. Comprobar que las cadenas de texto del diccionario sean legibles en la columna ASCII
    # Extraer la columna ASCII delimitada por '|' de cada línea de hexdump
    columna_ascii_total = "".join(line.split("|")[1] for line in lineas if line.count("|") >= 2)
    assert "palabra_legible" in columna_ascii_total

    # Verificar que los offsets en el encabezado coincidan con las posiciones reales
    with LectorIndiceBinario(ruta_idx) as lector:
        enc = lector.encabezado
        assert enc.offset_docs == 64
        assert enc.offset_diccionario == 64 + 44  # 108 == 0x6C
        assert enc.offset_postings == 108 + (2 + len(b"palabra_legible") + 20)  # 145 == 0x91
        tamano_archivo = ruta_idx.stat().st_size
        assert tamano_archivo == enc.offset_postings + (1 * 12)  # 157 == 0x9D

    # La última línea de hexdump marca exactamente la longitud total del archivo (0000009d)
    assert f"{tamano_archivo:08x}" in lineas[-1]

    # 4. Probar método de inspección
    reporte = LectorIndiceBinario.inspeccionar(ruta_idx)
    assert "ENCUADRE" in reporte
    assert "palabra_legible" in reporte
    assert "MUESTRA DE DOCUMENTOS" in reporte
