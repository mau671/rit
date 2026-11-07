"""Motor de indexación invertida utilizando SPIMI (Single-Pass In-Memory Indexing).

Implementa la construcción escalable del índice invertido en dos etapas:
1. Segmentación en bloques temporales ordenados en memoria y volcados a disco.
2. Fusión K-way (Merge) externa calculando DF, IDF y normas euclidianas exactas,
   con streaming directo de postings para no agotar la memoria RAM.
"""

from __future__ import annotations

import heapq
import math
import struct
import tempfile
import time
from array import array
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .configuracion import ConfiguracionIndexador
from .escritor_binario import EscritorIndiceBinario
from .formato_binario import EntradaDocumento
from .lector_coleccion import LectorColeccion
from .normalizador import Normalizador


@dataclass(slots=True)
class MetadatoDocTemporal:
    """Información del documento recolectada durante la primera fase de SPIMI."""

    doc_id: int
    hash_url: bytes
    num_tokens: int


class LectorBloqueTemporal:
    """Iterador que lee secuencialmente un archivo temporal binario de un bloque SPIMI.

    Formato binario de cada entrada en el bloque temporal:
    - len_termino: uint16 (2B)
    - termino_utf8: bytes
    - doc_id: uint32 (4B)
    - tf: uint32 (4B)
    """

    def __init__(self, ruta_archivo: Path) -> None:
        self.ruta_archivo = ruta_archivo
        self._archivo: BinaryIO | None = open(ruta_archivo, "rb")  # noqa: SIM115
        self.termino_actual: str = ""
        self.doc_id_actual: int = -1
        self.tf_actual: int = 0
        self.activo: bool = True
        self.avanzar()

    def avanzar(self) -> None:
        """Lee el siguiente registro del bloque temporal."""
        if not self._archivo or not self.activo:
            self.activo = False
            return

        tam_bytes = self._archivo.read(2)
        if not tam_bytes:
            self.cerrar()
            return

        (len_termino,) = struct.unpack("<H", tam_bytes)
        termino_bytes = self._archivo.read(len_termino)
        self.termino_actual = termino_bytes.decode("utf-8", errors="replace")

        datos_resto = self._archivo.read(8)  # doc_id (4B) + tf (4B)
        if len(datos_resto) < 8:
            self.cerrar()
            return

        self.doc_id_actual, self.tf_actual = struct.unpack("<II", datos_resto)

    def cerrar(self) -> None:
        """Cierra el archivo y marca el iterador como inactivo."""
        self.activo = False
        if self._archivo and not self._archivo.closed:
            self._archivo.close()
            self._archivo = None

    def __lt__(self, other: LectorBloqueTemporal) -> bool:
        if self.termino_actual != other.termino_actual:
            return self.termino_actual < other.termino_actual
        return self.doc_id_actual < other.doc_id_actual


class MotorIndexadorSPIMI:
    """Motor principal de indexación SPIMI con streaming a bajo nivel."""

    def __init__(
        self,
        directorio_temporal: Path | str | None = None,
        tamano_bloque_documentos: int = 25000,
        esquema_tf: str = "logaritmico",
        esquema_idf: str = "estandar",
    ) -> None:
        self.directorio_temporal = (
            Path(directorio_temporal) if directorio_temporal else Path(tempfile.gettempdir()) / "spimi_encuadre"
        )
        self.tamano_bloque_documentos = tamano_bloque_documentos
        self.esquema_tf = esquema_tf
        self.esquema_idf = esquema_idf

    def _calcular_peso_tf(self, tf: int) -> float:
        """Calcula el peso TF del término según el esquema configurado."""
        if tf <= 0:
            return 0.0
        if self.esquema_tf in {"logaritmico", "sublineal"}:
            return 1.0 + math.log(tf)
        if self.esquema_tf == "binario":
            return 1.0
        return float(tf)

    def _calcular_idf(self, n_docs: int, df: int) -> float:
        """Calcula el valor IDF del término según el esquema configurado."""
        if df <= 0 or n_docs <= 0:
            return 0.0
        if self.esquema_idf == "estandar":
            return float(math.log((n_docs / df) + 1.0))
        if self.esquema_idf == "probabilistico":
            ratio = (n_docs - df) / df
            return float(max(0.0, math.log(ratio + 1.0)))
        return 1.0

    def _escribir_bloque_spimi(
        self,
        indice_memoria: dict[str, list[tuple[int, int]]],
        num_bloque: int,
    ) -> Path:
        """Ordena el diccionario en memoria y lo vuelca a un archivo binario temporal."""
        self.directorio_temporal.mkdir(parents=True, exist_ok=True)
        ruta_bloque = self.directorio_temporal / f"bloque_{num_bloque:04d}.spimi"

        terminos_ordenados = sorted(indice_memoria.keys())
        buffer = bytearray()
        tamano_buffer = 128 * 1024

        with open(ruta_bloque, "wb") as f:
            for term in terminos_ordenados:
                term_bytes = term.encode("utf-8")
                len_term = len(term_bytes)
                if len_term > 65535:
                    continue

                prefijo_term = struct.pack("<H", len_term) + term_bytes
                postings = indice_memoria[term]
                postings.sort(key=lambda p: p[0])

                for doc_id, tf in postings:
                    buffer.extend(prefijo_term)
                    buffer.extend(struct.pack("<II", doc_id, tf))
                    if len(buffer) >= tamano_buffer:
                        f.write(buffer)
                        buffer.clear()

            if buffer:
                f.write(buffer)
                buffer.clear()

        return ruta_bloque

    def indexar_coleccion(
        self,
        iterador_documentos: Iterator[tuple[int, str, list[str]]],
        ruta_salida_indice: Path | str,
        flags_normalizacion: int = 0,
        callback_progreso: Callable[[int, str], None] | None = None,
    ) -> dict[str, float | int]:
        """Ejecuta el pipeline completo SPIMI y genera el archivo binario .idx final."""
        inicio_total = time.perf_counter()
        ruta_salida = Path(ruta_salida_indice)
        self.directorio_temporal.mkdir(parents=True, exist_ok=True)

        # Fase 1: Segmentación en bloques temporales SPIMI
        bloques_temporales: list[Path] = []
        documentos_metadatos: list[MetadatoDocTemporal] = []

        indice_memoria: dict[str, list[tuple[int, int]]] = defaultdict(list)
        docs_en_bloque = 0
        total_docs = 0
        num_bloque = 0

        for doc_id, hash_url_str, tokens in iterador_documentos:
            total_docs += 1
            docs_en_bloque += 1

            try:
                hash_bytes = bytes.fromhex(hash_url_str) if len(hash_url_str) == 64 else hash_url_str.encode("ascii")[:32]
            except ValueError:
                hash_bytes = hash_url_str.encode("ascii", errors="ignore")[:32].ljust(32, b"\x00")

            documentos_metadatos.append(
                MetadatoDocTemporal(
                    doc_id=doc_id,
                    hash_url=hash_bytes,
                    num_tokens=len(tokens),
                )
            )

            conteo_terminos: dict[str, int] = defaultdict(int)
            for t in tokens:
                conteo_terminos[t] += 1

            for term, tf in conteo_terminos.items():
                indice_memoria[term].append((doc_id, tf))

            if docs_en_bloque >= self.tamano_bloque_documentos:
                ruta_b = self._escribir_bloque_spimi(indice_memoria, num_bloque)
                bloques_temporales.append(ruta_b)
                num_bloque += 1
                indice_memoria.clear()
                docs_en_bloque = 0
                if callback_progreso:
                    callback_progreso(total_docs, f"Bloque {num_bloque} volcado a disco")

        # Volcar remanente si existe
        if indice_memoria:
            ruta_b = self._escribir_bloque_spimi(indice_memoria, num_bloque)
            bloques_temporales.append(ruta_b)
            num_bloque += 1
            indice_memoria.clear()

        tiempo_fase1 = time.perf_counter() - inicio_total
        if callback_progreso:
            callback_progreso(
                total_docs,
                f"Fase 1 terminada: {total_docs} docs en {len(bloques_temporales)} bloques ({tiempo_fase1:.2f}s)",
            )

        # Fase 2: K-Way Merge con streaming de postings a disco
        N = total_docs
        if N == 0:
            with EscritorIndiceBinario(ruta_salida) as escritor:
                escritor.flags_normalizacion = flags_normalizacion
                escritor.finalizar()
            return {"total_docs": 0, "total_terminos": 0, "total_postings": 0, "tiempo_segundos": 0.0}

        normas_cuadradas = array("f", [0.0] * N)

        lectores: list[LectorBloqueTemporal] = []
        heap: list[LectorBloqueTemporal] = []

        for ruta_b in bloques_temporales:
            lector = LectorBloqueTemporal(ruta_b)
            if lector.activo:
                lectores.append(lector)
                heapq.heappush(heap, lector)

        ruta_postings_tmp = self.directorio_temporal / "postings_merge.tmp"
        terminos_meta: list[tuple[str, int, float, int]] = []
        total_postings_global = 0

        termino_actual: str | None = None
        postings_del_termino: list[tuple[int, int]] = []

        with open(ruta_postings_tmp, "wb") as f_post_tmp:
            buffer_post = bytearray()
            tamano_buffer_post = 128 * 1024

            def procesar_termino_acumulado(term: str, pares_doc_tf: list[tuple[int, int]]) -> None:
                nonlocal total_postings_global
                df = len(pares_doc_tf)
                if df == 0:
                    return

                idf = self._calcular_idf(N, df)

                for d_id, tf_crudo in pares_doc_tf:
                    peso_tf = self._calcular_peso_tf(tf_crudo)
                    peso_tfidf = float(peso_tf * idf)

                    normas_cuadradas[d_id] += peso_tfidf * peso_tfidf

                    # Escribir posting binario: <IIf (12 bytes)
                    buffer_post.extend(struct.pack("<IIf", d_id, tf_crudo, peso_tfidf))
                    if len(buffer_post) >= tamano_buffer_post:
                        f_post_tmp.write(buffer_post)
                        buffer_post.clear()

                total_postings_global += df
                terminos_meta.append((term, df, idf, df))

            while heap:
                menor = heapq.heappop(heap)
                t = menor.termino_actual
                d_id = menor.doc_id_actual
                tf = menor.tf_actual

                if termino_actual is None:
                    termino_actual = t
                    postings_del_termino.append((d_id, tf))
                elif t == termino_actual:
                    postings_del_termino.append((d_id, tf))
                else:
                    procesar_termino_acumulado(termino_actual, postings_del_termino)
                    termino_actual = t
                    postings_del_termino = [(d_id, tf)]

                menor.avanzar()
                if menor.activo:
                    heapq.heappush(heap, menor)

            if termino_actual is not None and postings_del_termino:
                procesar_termino_acumulado(termino_actual, postings_del_termino)

            if buffer_post:
                f_post_tmp.write(buffer_post)
                buffer_post.clear()

        # Cerrar y eliminar lectores de bloques
        for l in lectores:
            l.cerrar()
        for ruta_b in bloques_temporales:
            ruta_b.unlink(missing_ok=True)

        # Preparar tabla de documentos con sus normas euclidianas definitivas
        entradas_documentos: list[EntradaDocumento] = []
        for meta in documentos_metadatos:
            d_id = meta.doc_id
            norma = math.sqrt(normas_cuadradas[d_id])
            entradas_documentos.append(
                EntradaDocumento(
                    doc_id=d_id,
                    hash_url=meta.hash_url,
                    num_tokens=meta.num_tokens,
                    norma_vectorial=norma,
                )
            )

        # Fase 3: Escribir archivo binario final .idx
        with EscritorIndiceBinario(ruta_salida) as escritor:
            escritor.flags_normalizacion = flags_normalizacion
            escritor.escribir_documentos(entradas_documentos)
            escritor.escribir_diccionario_y_anexar_postings_streaming(
                terminos_meta,
                ruta_postings_tmp,
            )
            escritor.finalizar(flags_normalizacion=flags_normalizacion)

        # Eliminar archivo temporal de postings
        ruta_postings_tmp.unlink(missing_ok=True)

        tiempo_total = time.perf_counter() - inicio_total
        return {
            "total_docs": N,
            "total_terminos": len(terminos_meta),
            "total_postings": total_postings_global,
            "tiempo_segundos": tiempo_total,
            "tamano_bytes": ruta_salida.stat().st_size,
        }

    @classmethod
    def indexar_desde_configuracion(
        cls,
        config: ConfiguracionIndexador,
        limite_documentos: int = 0,
        callback_progreso: Callable[[int, str], None] | None = None,
    ) -> dict[str, float | int]:
        """Orquesta la indexación completa a partir de una configuración dada."""
        lector = LectorColeccion(
            ruta_bd=config.rutas.base_datos,
            ruta_repositorio=config.rutas.repositorio,
        )
        normalizador = Normalizador(config.normalizacion)

        def generador_tokens() -> Iterator[tuple[int, str, list[str]]]:
            for doc in lector.iterar_documentos(limite=limite_documentos):
                tokens = normalizador.normalizar_texto(doc.texto)
                yield doc.doc_id, doc.hash_url, tokens

        motor = cls(
            directorio_temporal=config.rutas.directorio_spimi,
            tamano_bloque_documentos=config.spimi.docs_por_bloque,
            esquema_tf=config.modelo.esquema_tf,
            esquema_idf=config.modelo.esquema_idf,
        )

        flags = config.normalizacion.calcular_flags_bitmask()
        return motor.indexar_coleccion(
            iterador_documentos=generador_tokens(),
            ruta_salida_indice=config.rutas.indice_salida,
            flags_normalizacion=flags,
            callback_progreso=callback_progreso,
        )
