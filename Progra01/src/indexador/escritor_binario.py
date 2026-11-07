"""Escritor binario de bajo nivel para el índice invertido manual (.idx).

Gestiona la creación y serialización eficiente en disco mediante streaming y buffers,
garantizando alineación exacta y actualización final del encabezado con offsets reales.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import BinaryIO, Self

from .formato_binario import (
    MAGIC_BYTES,
    TAMANO_ENCABEZADO,
    TAMANO_POSTING,
    VERSION_FORMATO_ACTUAL,
    EncabezadoIndice,
    EntradaDocumento,
    EntradaTermino,
    Posting,
)


class EscritorIndiceBinario:
    """Escritor a disco del archivo binario .idx con manejo eficiente de buffers.
    
    Flujo de escritura en archivo:
    1. `inicializar()`: Escribe un encabezado placeholder de 64 bytes.
    2. `escribir_documentos(documentos)`: Escribe secuencialmente la tabla de docs y normas.
    3. `escribir_postings_y_diccionario(...)`: O bien se escriben las listas de postings
       calculando los offsets de cada término y luego la sección del vocabulario.
    4. `finalizar()`: Vuelve a la posición 0 (seek) y sobreescribe el encabezado con
       las cantidades totales y offsets definitivos calculados.
    """

    def __init__(self, ruta_archivo: str | Path, tamano_buffer_kb: int = 64) -> None:
        self.ruta_archivo = Path(ruta_archivo)
        self.tamano_buffer_bytes = tamano_buffer_kb * 1024
        self._archivo: BinaryIO | None = None

        # Estado del índice
        self.flags_normalizacion: int = 0
        self.total_docs: int = 0
        self.total_terminos: int = 0
        self.total_postings: int = 0

        self.offset_docs: int = TAMANO_ENCABEZADO
        self.offset_diccionario: int = 0
        self.offset_postings: int = 0

    def __enter__(self) -> Self:
        self.abrir()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.cerrar()

    def abrir(self) -> None:
        """Crea o trunca el archivo binario y prepara el encabezado inicial."""
        self.ruta_archivo.parent.mkdir(parents=True, exist_ok=True)
        self._archivo = open(self.ruta_archivo, "wb+")  # noqa: SIM115
        # Escribir encabezado provisional de 64 bytes
        encabezado_vacio = EncabezadoIndice()
        self._archivo.write(encabezado_vacio.empaquetar())
        self._archivo.flush()

    def cerrar(self) -> None:
        """Cierra el archivo si sigue abierto."""
        if self._archivo and not self._archivo.closed:
            self._archivo.close()
            self._archivo = None

    def escribir_documentos(self, documentos: Iterable[EntradaDocumento]) -> int:
        """Escribe la tabla contigua de documentos y normas vectoriales.
        
        Retorna la cantidad de documentos escritos.
        """
        if not self._archivo:
            raise RuntimeError("El escritor no está abierto.")

        self.offset_docs = self._archivo.tell()
        assert self.offset_docs == TAMANO_ENCABEZADO, (
            f"La sección de documentos debe comenzar en el byte {TAMANO_ENCABEZADO}, "
            f"pero está en {self.offset_docs}"
        )

        buffer = bytearray()
        contador = 0
        for doc in documentos:
            buffer.extend(doc.empaquetar())
            contador += 1
            if len(buffer) >= self.tamano_buffer_bytes:
                self._archivo.write(buffer)
                buffer.clear()

        if buffer:
            self._archivo.write(buffer)
            buffer.clear()

        self.total_docs = contador
        return contador

    def escribir_diccionario_y_postings(
        self,
        terminos_con_postings: Iterable[tuple[str, int, float, Sequence[Posting]]],
    ) -> tuple[int, int]:
        """Escribe las secciones de Diccionario y Postings.
        
        Para mantener el diseño donde el diccionario apunta al offset_postings de cada término:
        Estrategia eficiente:
        1. Escribimos la sección de postings primero o segundo.
           En nuestro diseño de encabezado:
           - Encabezado: [0 .. 63]
           - Documentos: [offset_docs .. offset_diccionario - 1]
           - Diccionario: [offset_diccionario .. offset_postings - 1]
           - Postings:    [offset_postings .. fin]
        
        Para escribir el diccionario antes que los postings conociendo los offsets exactos de postings:
        ¡El tamaño en bytes de los postings de cada término se conoce de antemano!
        Cada posting mide exactamente 12 bytes (`TAMANO_POSTING`).
        
        Por lo tanto:
        `bytes_postings = len(postings) * 12`.
        
        Podemos:
        1) Calcular primero la sección de diccionario en memoria/buffer o pre-calcular offsets, O
        2) Escribir el diccionario a un buffer en memoria mientras calculamos los offsets acumulados
           de postings (partiendo de un offset estimado), o escribir los postings primero a un archivo
           temporal o directo al archivo y guardar el offset_postings.
        
        Dado que el encabezado tiene `offset_diccionario` y `offset_postings`:
        No hay restricción de que el diccionario deba estar antes físicamente que los postings o viceversa,
        pero siguiendo la convención de la especificación:
        Sección 3: Diccionario
        Sección 4: Postings
        
        Podemos calcular el tamaño total del diccionario pasando una vez, o escribir el diccionario
        después de calcular o usando un buffer. Como los términos caben en memoria o se pueden
        procesar de forma streaming:
        Veamos: si guardamos los postings en disco primero tras los documentos, tendríamos
        postings en sección 3 y diccionario en sección 4. Pero queremos que la sección 3 sea
        Diccionario y sección 4 Postings para que en `hexdump -C` aparezca:
        [Encabezado] -> [Documentos] -> [Diccionario legible en ASCII] -> [Postings binarios].
        
        Para que el Diccionario legible esté antes de los Postings:
        Calculamos la longitud del bloque del diccionario:
        Si `terminos_con_postings` se pasa como un generador o lista:
        Si es una lista o tuplas ordenadas:
        Calculamos la suma de bytes de cada entrada de diccionario: 2 + len(termino.encode('utf-8')) + 20.
        Con esto, conocemos exactamente `offset_diccionario` y `offset_postings`:
          `offset_diccionario = offset_actual`
          `offset_postings = offset_diccionario + tamano_total_diccionario`
        
        ¡Y podemos escribir el diccionario en streaming y luego los postings en streaming!
        """
        if not self._archivo:
            raise RuntimeError("El escritor no está abierto.")

        # Convertir a lista si es necesario o manejar colección
        terminos_lista = list(terminos_con_postings)
        terminos_lista.sort(key=lambda t: t[0])  # Orden lexicográfico obligatorio

        self.offset_diccionario = self._archivo.tell()

        # Calcular tamaño del diccionario para determinar offset_postings exacto
        tamano_diccionario = 0
        for termino, _df, _idf, _postings in terminos_lista:
            term_utf8_len = len(termino.encode("utf-8"))
            tamano_diccionario += 2 + term_utf8_len + 20

        self.offset_postings = self.offset_diccionario + tamano_diccionario

        # 1. Escribir Sección 3: Diccionario
        buffer_dicc = bytearray()
        offset_posting_actual = self.offset_postings
        total_postings_acumulado = 0

        entradas_terminos: list[EntradaTermino] = []
        for termino, df, idf, postings in terminos_lista:
            cant_postings = len(postings)
            bytes_postings = cant_postings * TAMANO_POSTING
            total_postings_acumulado += cant_postings

            entrada_term = EntradaTermino(
                termino=termino,
                df=df,
                idf=idf,
                offset_postings=offset_posting_actual,
                bytes_postings=bytes_postings,
            )
            entradas_terminos.append(entrada_term)
            buffer_dicc.extend(entrada_term.empaquetar())
            offset_posting_actual += bytes_postings

            if len(buffer_dicc) >= self.tamano_buffer_bytes:
                self._archivo.write(buffer_dicc)
                buffer_dicc.clear()

        if buffer_dicc:
            self._archivo.write(buffer_dicc)
            buffer_dicc.clear()

        # Verificar que el puntero quedó exactamente en offset_postings
        pos_postings_real = self._archivo.tell()
        assert pos_postings_real == self.offset_postings, (
            f"Desfase al terminar diccionario: esperado {self.offset_postings}, "
            f"actual {pos_postings_real}"
        )

        # 2. Escribir Sección 4: Listas de Postings
        buffer_post = bytearray()
        for _termino, _df, _idf, postings in terminos_lista:
            for posting in postings:
                buffer_post.extend(posting.empaquetar())
                if len(buffer_post) >= self.tamano_buffer_bytes:
                    self._archivo.write(buffer_post)
                    buffer_post.clear()

        if buffer_post:
            self._archivo.write(buffer_post)
            buffer_post.clear()

        self.total_terminos = len(terminos_lista)
        self.total_postings = total_postings_acumulado

        return self.total_terminos, self.total_postings

    def escribir_diccionario_y_anexar_postings_streaming(
        self,
        terminos_meta: Sequence[tuple[str, int, float, int]],  # (termino, df, idf, cant_postings)
        ruta_archivo_postings_tmp: Path | str,
    ) -> tuple[int, int]:
        """Escribe la sección de Diccionario y transfiere el archivo de postings en streaming.

        Permite indexar millones de postings sin cargarlos simultáneamente a la memoria RAM.
        """
        if not self._archivo:
            raise RuntimeError("El escritor no está abierto.")

        self.offset_diccionario = self._archivo.tell()

        # Calcular tamaño del diccionario para determinar offset_postings exacto
        tamano_diccionario = 0
        for termino, _df, _idf, _cant in terminos_meta:
            tamano_diccionario += 2 + len(termino.encode("utf-8")) + 20

        self.offset_postings = self.offset_diccionario + tamano_diccionario

        # 1. Escribir Sección 3: Diccionario
        buffer_dicc = bytearray()
        offset_posting_actual = self.offset_postings
        total_postings_acumulado = 0

        for termino, df, idf, cant_postings in terminos_meta:
            bytes_postings = cant_postings * TAMANO_POSTING
            total_postings_acumulado += cant_postings

            entrada_term = EntradaTermino(
                termino=termino,
                df=df,
                idf=idf,
                offset_postings=offset_posting_actual,
                bytes_postings=bytes_postings,
            )
            buffer_dicc.extend(entrada_term.empaquetar())
            offset_posting_actual += bytes_postings

            if len(buffer_dicc) >= self.tamano_buffer_bytes:
                self._archivo.write(buffer_dicc)
                buffer_dicc.clear()

        if buffer_dicc:
            self._archivo.write(buffer_dicc)
            buffer_dicc.clear()

        # Verificar que el puntero quedó exactamente en offset_postings
        pos_postings_real = self._archivo.tell()
        assert pos_postings_real == self.offset_postings, (
            f"Desfase al terminar diccionario: esperado {self.offset_postings}, actual {pos_postings_real}"
        )

        # 2. Transferir postings temporales a la Sección 4 del archivo definitivo
        ruta_postings = Path(ruta_archivo_postings_tmp)
        if ruta_postings.is_file():
            with open(ruta_postings, "rb") as f_post:
                shutil.copyfileobj(f_post, self._archivo, length=1024 * 1024)

        self.total_terminos = len(terminos_meta)
        self.total_postings = total_postings_acumulado

        return self.total_terminos, self.total_postings

    def finalizar(self, flags_normalizacion: int = 0) -> EncabezadoIndice:
        """Actualiza el encabezado al inicio del archivo con los offsets y metadatos finales."""
        if not self._archivo:
            raise RuntimeError("El escritor no está abierto.")

        self.flags_normalizacion = flags_normalizacion

        encabezado_final = EncabezadoIndice(
            magic=MAGIC_BYTES,
            version=VERSION_FORMATO_ACTUAL,
            flags_normalizacion=self.flags_normalizacion,
            total_docs=self.total_docs,
            total_terminos=self.total_terminos,
            total_postings=self.total_postings,
            offset_docs=self.offset_docs,
            offset_diccionario=self.offset_diccionario,
            offset_postings=self.offset_postings,
            reservado=b"\x00" * 12,
        )

        self._archivo.seek(0, io.SEEK_SET)
        self._archivo.write(encabezado_final.empaquetar())
        self._archivo.flush()

        return encabezado_final
