"""Definición del formato binario para el índice invertido manual (.idx).

Este módulo define las estructuras, constantes y funciones de empaquetado/desempaquetado
a bajo nivel usando estrictamente `struct.pack` y `struct.unpack` con Little-Endian (`<`).
El diseño está optimizado para alineación perfecta y legibilidad con `hexdump -C`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

# ==============================================================================
# SECCIÓN 1: ENCABEZADO (Exactamente 64 bytes)
# ==============================================================================
# Campos:
#  - magic: 8 bytes ASCII (b"ENCUADRE", 45 4e 43 55 41 44 52 45)
#  - version: uint16 (2 bytes, Little-Endian) -> 1
#  - flags_normalizacion: uint16 (2 bytes, Little-Endian) -> bitmask
#  - total_docs (N): uint32 (4 bytes, Little-Endian)
#  - total_terminos (|V|): uint32 (4 bytes, Little-Endian)
#  - total_postings: uint64 (8 bytes, Little-Endian)
#  - offset_docs: uint64 (8 bytes, Little-Endian)
#  - offset_diccionario: uint64 (8 bytes, Little-Endian)
#  - offset_postings: uint64 (8 bytes, Little-Endian)
#  - reservado/padding: 12 bytes (ceros b"\x00" * 12)
#
# Total: 8 + 2 + 2 + 4 + 4 + 8 + 8 + 8 + 8 + 12 = 64 bytes.

MAGIC_BYTES: Final[bytes] = b"ENCUADRE"
VERSION_FORMATO_ACTUAL: Final[int] = 1

FORMATO_ENCABEZADO: Final[str] = "<8sHHIIQQQQ12s"
TAMANO_ENCABEZADO: Final[int] = struct.calcsize(FORMATO_ENCABEZADO)
assert TAMANO_ENCABEZADO == 64, f"El encabezado debe medir 64 bytes exactos, mide {TAMANO_ENCABEZADO}"


@dataclass(slots=True)
class EncabezadoIndice:
    """Metadatos globales del índice binario contenidos en los primeros 64 bytes."""

    magic: bytes = MAGIC_BYTES
    version: int = VERSION_FORMATO_ACTUAL
    flags_normalizacion: int = 0
    total_docs: int = 0
    total_terminos: int = 0
    total_postings: int = 0
    offset_docs: int = 64
    offset_diccionario: int = 0
    offset_postings: int = 0
    reservado: bytes = b"\x00" * 12

    def empaquetar(self) -> bytes:
        """Serializa el encabezado en exactamente 64 bytes."""
        return struct.pack(
            FORMATO_ENCABEZADO,
            self.magic,
            self.version,
            self.flags_normalizacion,
            self.total_docs,
            self.total_terminos,
            self.total_postings,
            self.offset_docs,
            self.offset_diccionario,
            self.offset_postings,
            self.reservado,
        )

    @classmethod
    def desempaquetar(cls, buffer: bytes) -> EncabezadoIndice:
        """Deserializa 64 bytes en una instancia de EncabezadoIndice."""
        if len(buffer) < TAMANO_ENCABEZADO:
            raise ValueError(
                f"Buffer insuficiente para encabezado: se esperaban {TAMANO_ENCABEZADO} bytes, "
                f"se recibieron {len(buffer)}"
            )
        (
            magic,
            version,
            flags,
            total_docs,
            total_terminos,
            total_postings,
            offset_docs,
            offset_dicc,
            offset_post,
            reservado,
        ) = struct.unpack(FORMATO_ENCABEZADO, buffer[:TAMANO_ENCABEZADO])

        if magic != MAGIC_BYTES:
            raise ValueError(f"Magic bytes inválidos: {magic!r}, esperado {MAGIC_BYTES!r}")

        return cls(
            magic=magic,
            version=version,
            flags_normalizacion=flags,
            total_docs=total_docs,
            total_terminos=total_terminos,
            total_postings=total_postings,
            offset_docs=offset_docs,
            offset_diccionario=offset_dicc,
            offset_postings=offset_post,
            reservado=reservado,
        )


# ==============================================================================
# SECCIÓN 2: TABLA DE DOCUMENTOS Y NORMAS (44 bytes fijos por doc)
# ==============================================================================
# Para cada documento (doc_id 0 .. N-1):
#  - doc_id: uint32 (4 bytes)
#  - hash_url: 32 bytes (raw bytes SHA-256 o 32 chars ASCII/bytes)
#  - num_tokens: uint32 (4 bytes)
#  - norma_vectorial: float32 (4 bytes, ||d|| = sqrt(sum w_{t,d}^2))
#
# Total: 4 + 32 + 4 + 4 = 44 bytes fijos por documento.

FORMATO_DOCUMENTO: Final[str] = "<I32sIf"
TAMANO_DOCUMENTO: Final[int] = struct.calcsize(FORMATO_DOCUMENTO)
assert TAMANO_DOCUMENTO == 44, f"Cada entrada de documento debe medir 44 bytes, mide {TAMANO_DOCUMENTO}"


@dataclass(slots=True)
class EntradaDocumento:
    """Información y norma vectorial de un documento indexado."""

    doc_id: int
    hash_url: bytes  # 32 raw bytes (o hash truncado/empaquetado a 32 bytes)
    num_tokens: int
    norma_vectorial: float

    def empaquetar(self) -> bytes:
        """Serializa la entrada de documento en exactamente 44 bytes."""
        hash_bytes = self.hash_url
        if len(hash_bytes) != 32:
            # Si viene como hex string de 64 caracteres, convertir a 32 raw bytes
            if len(hash_bytes) == 64:
                try:
                    hash_bytes = bytes.fromhex(hash_bytes.decode("ascii") if isinstance(hash_bytes, (bytes, bytearray)) else hash_bytes)
                except (ValueError, TypeError):
                    hash_bytes = hash_bytes[:32].ljust(32, b"\x00")
            elif len(hash_bytes) < 32:
                hash_bytes = hash_bytes.ljust(32, b"\x00")
            else:
                hash_bytes = hash_bytes[:32]

        return struct.pack(
            FORMATO_DOCUMENTO,
            self.doc_id,
            hash_bytes,
            self.num_tokens,
            self.norma_vectorial,
        )

    @classmethod
    def desempaquetar(cls, buffer: bytes) -> EntradaDocumento:
        """Deserializa 44 bytes en una EntradaDocumento."""
        if len(buffer) < TAMANO_DOCUMENTO:
            raise ValueError(
                f"Buffer insuficiente para documento: se esperaban {TAMANO_DOCUMENTO} bytes, "
                f"se recibieron {len(buffer)}"
            )
        doc_id, hash_bytes, num_tokens, norma = struct.unpack(
            FORMATO_DOCUMENTO, buffer[:TAMANO_DOCUMENTO]
        )
        return cls(
            doc_id=doc_id,
            hash_url=hash_bytes,
            num_tokens=num_tokens,
            norma_vectorial=norma,
        )


# ==============================================================================
# SECCIÓN 3: DICCIONARIO / VOCABULARIO (Tamaño variable por término)
# ==============================================================================
# Para cada término ordenado lexicográficamente:
#  - len_termino: uint16 (2 bytes)
#  - termino_utf8: bytes UTF-8 (len_termino bytes, visible en hexdump)
#  - df: uint32 (4 bytes, document frequency)
#  - idf: float32 (4 bytes, log(N / df))
#  - offset_postings: uint64 (8 bytes, offset absoluto del inicio de su lista)
#  - bytes_postings: uint32 (4 bytes, tamaño en bytes de su bloque de postings)
#
# Formato parte prefijo: "<H" (len_termino)
# Formato parte sufijo: "<IfQI" (df, idf, offset_postings, bytes_postings) -> 4 + 4 + 8 + 4 = 20 bytes

FORMATO_TERMINO_SUFIJO: Final[str] = "<IfQI"
TAMANO_TERMINO_SUFIJO: Final[int] = struct.calcsize(FORMATO_TERMINO_SUFIJO)
assert TAMANO_TERMINO_SUFIJO == 20


@dataclass(slots=True)
class EntradaTermino:
    """Metadatos de un término en el vocabulario."""

    termino: str
    df: int
    idf: float
    offset_postings: int
    bytes_postings: int

    def empaquetar(self) -> bytes:
        """Serializa la entrada de término (2 + len(utf8) + 20 bytes)."""
        term_bytes = self.termino.encode("utf-8")
        len_term = len(term_bytes)
        if len_term > 65535:
            raise ValueError(f"Término demasiado largo ({len_term} bytes): {self.termino[:30]}...")

        # <H {len}s IfQI
        prefijo = struct.pack("<H", len_term)
        sufijo = struct.pack(
            FORMATO_TERMINO_SUFIJO,
            self.df,
            self.idf,
            self.offset_postings,
            self.bytes_postings,
        )
        return prefijo + term_bytes + sufijo

    @classmethod
    def desempaquetar_desde_bytes(cls, buffer: bytes, offset: int = 0) -> tuple[EntradaTermino, int]:
        """Deserializa un término desde un buffer binario.
        
        Retorna la EntradaTermino y los bytes consumidos.
        """
        if len(buffer) - offset < 2:
            raise ValueError("Buffer insuficiente para leer longitud de término")
        (len_term,) = struct.unpack_from("<H", buffer, offset)
        pos = offset + 2

        tamano_total = 2 + len_term + TAMANO_TERMINO_SUFIJO
        if len(buffer) - offset < tamano_total:
            raise ValueError(
                f"Buffer insuficiente para término completo: requeridos {tamano_total}, "
                f"disponibles {len(buffer) - offset}"
            )

        term_bytes = buffer[pos : pos + len_term]
        termino = term_bytes.decode("utf-8", errors="replace")
        pos += len_term

        df, idf, offset_post, bytes_post = struct.unpack_from(FORMATO_TERMINO_SUFIJO, buffer, pos)
        return (
            cls(
                termino=termino,
                df=df,
                idf=idf,
                offset_postings=offset_post,
                bytes_postings=bytes_post,
            ),
            tamano_total,
        )


# ==============================================================================
# SECCIÓN 4: LISTAS DE POSTINGS (12 bytes fijos por posting)
# ==============================================================================
# Para cada posting:
#  - doc_id: uint32 (4 bytes)
#  - tf: uint32 (4 bytes)
#  - peso_tfidf: float32 (4 bytes)
# Total por posting: 12 bytes.

FORMATO_POSTING: Final[str] = "<IIf"
TAMANO_POSTING: Final[int] = struct.calcsize(FORMATO_POSTING)
assert TAMANO_POSTING == 12, f"Cada posting debe medir 12 bytes, mide {TAMANO_POSTING}"


@dataclass(slots=True)
class Posting:
    """Elemento individual de la lista invertida de ocurrencias."""

    doc_id: int
    tf: int
    peso_tfidf: float

    def empaquetar(self) -> bytes:
        """Serializa el posting en 12 bytes."""
        return struct.pack(FORMATO_POSTING, self.doc_id, self.tf, self.peso_tfidf)

    @classmethod
    def desempaquetar(cls, buffer: bytes) -> Posting:
        """Deserializa 12 bytes en un Posting."""
        if len(buffer) < TAMANO_POSTING:
            raise ValueError(
                f"Buffer insuficiente para posting: se esperaban {TAMANO_POSTING} bytes, "
                f"se recibieron {len(buffer)}"
            )
        doc_id, tf, peso = struct.unpack(FORMATO_POSTING, buffer[:TAMANO_POSTING])
        return cls(doc_id=doc_id, tf=tf, peso_tfidf=peso)
