"""Módulo para recorrer la colección documental desde la base de datos y el disco.

Lee la tabla `documentos` de metadatos_araña.db de Progra00 y carga el texto
correspondiente almacenado en el repositorio de archivos txt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DocumentoColeccion:
    """Representa un documento de la colección recuperado para indexación."""

    doc_id: int
    db_id: int
    url: str
    hash_url: str
    dominio: str
    ruta_relativa: str
    texto: str
    tamano_bytes: int


class LectorColeccion:
    """Iterador optimizado sobre la colección documental de Progra00."""

    def __init__(self, ruta_bd: str | Path, ruta_repositorio: str | Path) -> None:
        self.ruta_bd = Path(ruta_bd).resolve()
        self.ruta_repositorio = Path(ruta_repositorio).resolve()

        if not self.ruta_bd.is_file():
            raise FileNotFoundError(f"Base de datos no encontrada: {self.ruta_bd}")
        if not self.ruta_repositorio.is_dir():
            raise FileNotFoundError(f"Directorio de repositorio no encontrado: {self.ruta_repositorio}")

    def contar_documentos(self, limite: int = 0) -> int:
        """Retorna la cantidad total de documentos elegibles en la base de datos."""
        with sqlite3.connect(f"file:{self.ruta_bd}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            if limite > 0:
                cursor.execute("SELECT COUNT(*) FROM (SELECT id FROM documentos LIMIT ?)", (limite,))
            else:
                cursor.execute("SELECT COUNT(*) FROM documentos")
            resultado = cursor.fetchone()
            return int(resultado[0]) if resultado else 0

    def iterar_documentos(
        self,
        limite: int = 0,
        tamano_lote: int = 2000,
    ) -> Iterator[DocumentoColeccion]:
        """Itera secuencialmente los documentos leyendo texto de disco por lotes.

        Asigna un `doc_id` secuencial continuo [0, 1, ..., N-1] para el índice.
        """
        query = (
            "SELECT id, url, hash_url, dominio, ruta_relativa, tamano_texto_bytes "
            "FROM documentos ORDER BY id ASC"
        )
        if limite > 0:
            query += f" LIMIT {limite}"

        # Abrir SQLite en modo sólo lectura para no bloquear otros procesos
        with sqlite3.connect(f"file:{self.ruta_bd}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            cursor.execute(query)

            doc_id_secuencial = 0
            while True:
                filas = cursor.fetchmany(tamano_lote)
                if not filas:
                    break

                for db_id, url, hash_url, dominio, ruta_rel, tamano_bytes in filas:
                    ruta_archivo = self.ruta_repositorio / ruta_rel
                    if not ruta_archivo.is_file():
                        # Si un archivo registrado no se encuentra en disco, se omite con aviso
                        continue

                    try:
                        # Leer texto limpio utf-8 con reemplazo tolerante de errores de decodificación
                        texto = ruta_archivo.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue

                    yield DocumentoColeccion(
                        doc_id=doc_id_secuencial,
                        db_id=db_id,
                        url=url,
                        hash_url=hash_url,
                        dominio=dominio,
                        ruta_relativa=ruta_rel,
                        texto=texto,
                        tamano_bytes=tamano_bytes,
                    )
                    doc_id_secuencial += 1
