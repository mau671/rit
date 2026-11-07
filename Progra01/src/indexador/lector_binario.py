"""Lector binario de bajo nivel para el índice invertido manual (.idx).

Permite:
- Validar magic bytes b"ENCUADRE" y versión del encabezado.
- Acceder a cualquier documento por doc_id en tiempo O(1) usando seek.
- Cargar o consultar términos del diccionario (con soporte para búsqueda binaria O(log |V|)).
- Leer postings directamente con seek sin cargar el índice completo a memoria.
- Inspeccionar el archivo con un resumen detallado y direcciones hexadecimales.
"""

from __future__ import annotations

import io
from bisect import bisect_left
from pathlib import Path
from typing import BinaryIO, Self

from .formato_binario import (
    MAGIC_BYTES,
    TAMANO_DOCUMENTO,
    TAMANO_ENCABEZADO,
    TAMANO_POSTING,
    VERSION_FORMATO_ACTUAL,
    EncabezadoIndice,
    EntradaDocumento,
    EntradaTermino,
    Posting,
)


class LectorIndiceBinario:
    """Lector eficiente y de bajo nivel para archivos de índice invertido .idx."""

    def __init__(self, ruta_archivo: str | Path) -> None:
        self.ruta_archivo = Path(ruta_archivo)
        self._archivo: BinaryIO | None = None
        self._encabezado: EncabezadoIndice | None = None

        # Caché opcional de diccionario (lista ordenada de EntradaTermino)
        self._diccionario_en_memoria: list[EntradaTermino] | None = None
        self._terminos_claves: list[str] | None = None

    def __enter__(self) -> Self:
        self.abrir()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.cerrar()

    def abrir(self) -> None:
        """Abre el archivo en modo binario de sólo lectura y valida el encabezado."""
        if not self.ruta_archivo.is_file():
            raise FileNotFoundError(f"Archivo de índice no encontrado: {self.ruta_archivo}")

        self._archivo = open(self.ruta_archivo, "rb")  # noqa: SIM115
        self._leer_encabezado()

    def cerrar(self) -> None:
        """Cierra el descriptor de archivo."""
        if self._archivo and not self._archivo.closed:
            self._archivo.close()
            self._archivo = None

    def _asegurar_abierto(self) -> BinaryIO:
        if self._archivo is None or self._archivo.closed:
            raise RuntimeError("El archivo de índice no está abierto.")
        return self._archivo

    def _leer_encabezado(self) -> EncabezadoIndice:
        f = self._asegurar_abierto()
        f.seek(0, io.SEEK_SET)
        buffer = f.read(TAMANO_ENCABEZADO)
        if len(buffer) < TAMANO_ENCABEZADO:
            raise ValueError(f"Archivo truncado: no contiene los {TAMANO_ENCABEZADO} bytes de encabezado.")

        self._encabezado = EncabezadoIndice.desempaquetar(buffer)
        if self._encabezado.magic != MAGIC_BYTES:
            raise ValueError(f"Magic bytes inválidos: {self._encabezado.magic!r} != {MAGIC_BYTES!r}")
        if self._encabezado.version != VERSION_FORMATO_ACTUAL:
            raise ValueError(
                f"Versión de formato no soportada: {self._encabezado.version} "
                f"(esperada: {VERSION_FORMATO_ACTUAL})"
            )
        return self._encabezado

    @property
    def encabezado(self) -> EncabezadoIndice:
        """Retorna el encabezado leído del índice."""
        if self._encabezado is None:
            return self._leer_encabezado()
        return self._encabezado

    # --------------------------------------------------------------------------
    # Acceso a Documentos y Normas
    # --------------------------------------------------------------------------
    def leer_documento(self, doc_id: int) -> EntradaDocumento:
        """Obtiene la información y norma de un documento en tiempo O(1) vía seek."""
        f = self._asegurar_abierto()
        enc = self.encabezado

        if doc_id < 0 or doc_id >= enc.total_docs:
            raise IndexError(f"doc_id {doc_id} fuera de rango [0, {enc.total_docs - 1}]")

        offset_doc = enc.offset_docs + (doc_id * TAMANO_DOCUMENTO)
        f.seek(offset_doc, io.SEEK_SET)
        buffer = f.read(TAMANO_DOCUMENTO)
        if len(buffer) < TAMANO_DOCUMENTO:
            raise ValueError(f"No se pudieron leer los {TAMANO_DOCUMENTO} bytes del doc_id {doc_id}")

        return EntradaDocumento.desempaquetar(buffer)

    def iterar_documentos(self) -> list[EntradaDocumento]:
        """Itera todos los documentos de la tabla."""
        f = self._asegurar_abierto()
        enc = self.encabezado
        f.seek(enc.offset_docs, io.SEEK_SET)

        bytes_a_leer = enc.total_docs * TAMANO_DOCUMENTO
        buffer = f.read(bytes_a_leer)

        docs: list[EntradaDocumento] = []
        for i in range(enc.total_docs):
            pos = i * TAMANO_DOCUMENTO
            docs.append(EntradaDocumento.desempaquetar(buffer[pos : pos + TAMANO_DOCUMENTO]))
        return docs

    # --------------------------------------------------------------------------
    # Acceso a Diccionario / Vocabulario
    # --------------------------------------------------------------------------
    def cargar_diccionario(self) -> list[EntradaTermino]:
        """Lee el vocabulario completo a memoria y lo almacena en caché."""
        if self._diccionario_en_memoria is not None:
            return self._diccionario_en_memoria

        f = self._asegurar_abierto()
        enc = self.encabezado
        f.seek(enc.offset_diccionario, io.SEEK_SET)

        bytes_diccionario = enc.offset_postings - enc.offset_diccionario
        buffer = f.read(bytes_diccionario)

        terminos: list[EntradaTermino] = []
        pos = 0
        while pos < len(buffer) and len(terminos) < enc.total_terminos:
            entrada, tamano = EntradaTermino.desempaquetar_desde_bytes(buffer, offset=pos)
            terminos.append(entrada)
            pos += tamano

        self._diccionario_en_memoria = terminos
        self._terminos_claves = [t.termino for t in terminos]
        return self._diccionario_en_memoria

    def buscar_termino(self, termino: str) -> EntradaTermino | None:
        """Busca un término en el vocabulario en O(log |V|) usando búsqueda binaria."""
        if self._diccionario_en_memoria is None:
            self.cargar_diccionario()

        assert self._terminos_claves is not None
        assert self._diccionario_en_memoria is not None

        idx = bisect_left(self._terminos_claves, termino)
        if idx < len(self._terminos_claves) and self._terminos_claves[idx] == termino:
            return self._diccionario_en_memoria[idx]
        return None

    # --------------------------------------------------------------------------
    # Acceso a Postings
    # --------------------------------------------------------------------------
    def leer_postings(self, entrada_termino: EntradaTermino) -> list[Posting]:
        """Lee los postings de un término saltando directamente con seek a su offset."""
        f = self._asegurar_abierto()
        f.seek(entrada_termino.offset_postings, io.SEEK_SET)

        buffer = f.read(entrada_termino.bytes_postings)
        if len(buffer) < entrada_termino.bytes_postings:
            raise ValueError(
                f"Postings incompletos para {entrada_termino.termino}: esperados "
                f"{entrada_termino.bytes_postings}, leídos {len(buffer)}"
            )

        num_postings = entrada_termino.bytes_postings // TAMANO_POSTING
        postings: list[Posting] = []
        for i in range(num_postings):
            pos = i * TAMANO_POSTING
            postings.append(Posting.desempaquetar(buffer[pos : pos + TAMANO_POSTING]))

        return postings

    def obtener_postings_termino(self, termino: str) -> list[Posting]:
        """Obtiene la lista de postings para un término por su nombre de texto."""
        entrada = self.buscar_termino(termino)
        if entrada is None:
            return []
        return self.leer_postings(entrada)

    # --------------------------------------------------------------------------
    # Utilidad de Inspección y Diagnóstico
    # --------------------------------------------------------------------------
    @classmethod
    def inspeccionar(cls, ruta_archivo: str | Path, num_muestras: int = 5) -> str:
        """Imprime y retorna un reporte detallado del índice y sus direcciones hex."""
        lineas: list[str] = []
        with cls(ruta_archivo) as lector:
            enc = lector.encabezado
            tamano_total = lector.ruta_archivo.stat().st_size

            lineas.append("=" * 70)
            lineas.append(f"INSPECCIÓN DEL ÍNDICE BINARIO: {lector.ruta_archivo.name}")
            lineas.append("=" * 70)
            lineas.append(f"Tamaño total de archivo : {tamano_total} bytes (0x{tamano_total:08X})")
            lineas.append(f"Magic Number             : {enc.magic.decode('ascii', errors='replace')} (OK)")
            lineas.append(f"Versión de formato       : {enc.version}")
            lineas.append(f"Flags de normalización   : 0x{enc.flags_normalizacion:04X} ({enc.flags_normalizacion:016b})")
            lineas.append(f"Total Documentos (N)     : {enc.total_docs}")
            lineas.append(f"Total Vocabulario (|V|)  : {enc.total_terminos}")
            lineas.append(f"Total Postings           : {enc.total_postings}")
            lineas.append("-" * 70)
            lineas.append("DISTRIBUCIÓN DE SECCIONES EN DISCO:")
            lineas.append(f"  [0x00000000 - 0x{TAMANO_ENCABEZADO:08X}] : Encabezado ({TAMANO_ENCABEZADO} bytes)")
            lineas.append(
                f"  [0x{enc.offset_docs:08X} - 0x{enc.offset_diccionario:08X}] : "
                f"Tabla Documentos ({enc.offset_diccionario - enc.offset_docs} bytes, "
                f"{enc.total_docs} docs x {TAMANO_DOCUMENTO} B)"
            )
            lineas.append(
                f"  [0x{enc.offset_diccionario:08X} - 0x{enc.offset_postings:08X}] : "
                f"Diccionario / Vocabulario ({enc.offset_postings - enc.offset_diccionario} bytes, "
                f"{enc.total_terminos} términos)"
            )
            lineas.append(
                f"  [0x{enc.offset_postings:08X} - 0x{tamano_total:08X}] : "
                f"Listas de Postings ({tamano_total - enc.offset_postings} bytes, "
                f"{enc.total_postings} postings x {TAMANO_POSTING} B)"
            )
            lineas.append("-" * 70)

            # Muestra de documentos
            lineas.append(f"MUESTRA DE DOCUMENTOS (primeros {min(num_muestras, enc.total_docs)}):")
            for i in range(min(num_muestras, enc.total_docs)):
                doc = lector.leer_documento(i)
                hash_hex = doc.hash_url.hex()
                lineas.append(
                    f"  Doc #{doc.doc_id:03d} | tokens: {doc.num_tokens:5d} | "
                    f"norma: {doc.norma_vectorial:8.4f} | hash: {hash_hex[:16]}...{hash_hex[-8:]}"
                )

            lineas.append("-" * 70)
            # Muestra de vocabulario y postings
            dicc = lector.cargar_diccionario()
            lineas.append(f"MUESTRA DE VOCABULARIO Y POSTINGS (primeros {min(num_muestras, len(dicc))}):")
            for t in dicc[:num_muestras]:
                lineas.append(
                    f"  Término: '{t.termino}' | df={t.df} | idf={t.idf:.4f} | "
                    f"offset=0x{t.offset_postings:08X} | bytes={t.bytes_postings}"
                )
                postings = lector.leer_postings(t)
                detalles_post = [f"(d:{p.doc_id}, tf:{p.tf}, w:{p.peso_tfidf:.3f})" for p in postings[:4]]
                if len(postings) > 4:
                    detalles_post.append("...")
                lineas.append(f"    -> Postings: {', '.join(detalles_post)}")

            lineas.append("=" * 70)

        reporte = "\n".join(lineas)
        return reporte
