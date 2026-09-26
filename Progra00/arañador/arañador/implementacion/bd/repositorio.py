"""Repositorios SQLite parametrizados para documentos y bitácora.

Los repositorios no interpolan valores en SQL.  Toda consulta parametrizada
recibe un :class:`ConexionSQLite`, por lo que las tuberías e intermediarios
comparten una conexión explícita y pueden coordinar transacciones.  La
deduplicación de documentos se hace dentro de ``BEGIN IMMEDIATE``: la
comprobación y el ``INSERT`` son una sola operación lógica, incluso cuando hay
varios procesos de arañado escribiendo en el mismo archivo.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ...bibliotecas.sqlite import RUTA_POR_DEFECTO, ConexionSQLite
from .modelos import (
    MetadatoDocumento,
    RegistroBitacora,
    datetime_desde_timestamp,
    normalizar_datetime_utc,
    timestamp_utc_iso,
)

type PathLike = str | os.PathLike[str]
type FabricaConexion = Callable[[], ConexionSQLite]


class ResultadoInsercion(StrEnum):
    """Motivo devuelto por una inserción atómica de documento."""

    INSERTADO = "INSERTADO"
    DUPLICADO_URL = "DUPLICADO_URL"
    DUPLICADO_CONTENIDO = "DUPLICADO_CONTENIDO"

    # Alias de lectura para código que prefiere el verbo ``AGREGADO``.
    AGREGADO = "INSERTADO"


class DocumentoDuplicadoError(ValueError):
    """Indica que una escritura atómica no reemplaza un documento existente."""

    def __init__(self, motivo: str, valor: str) -> None:
        """Guarda el motivo y el valor que duplicaron la escritura."""
        self.motivo = motivo
        self.valor = valor
        super().__init__(f"Documento duplicado por {motivo}: {valor}")


@dataclass(frozen=True, slots=True)
class ResultadoLoteDocumentos:
    """Resumen de un lote de documentos procesado sin inserciones parciales."""

    recibidos: int
    insertados: int
    duplicados_url: int
    duplicados_contenido: int
    omitidos_sin_archivo: int = 0

    @property
    def total_duplicados(self) -> int:
        """Cantidad de documentos omitidos por una clave duplicada."""
        return self.duplicados_url + self.duplicados_contenido

    @property
    def omitidos(self) -> int:
        """Cantidad total de elementos no insertados."""
        return self.total_duplicados + self.omitidos_sin_archivo

    def __int__(self) -> int:
        """Permite usar el resultado como número de documentos insertados."""
        return self.insertados


class _RepositorioBase:
    """Estado común de ciclo de vida para los repositorios."""

    __slots__ = ("_conexion", "_posee_conexion")

    def __init__(
        self,
        conexion: ConexionSQLite | PathLike | FabricaConexion = RUTA_POR_DEFECTO,
    ) -> None:
        """Acepta una conexión, una ruta o una fábrica de conexión.

        Recibir una conexión es la forma recomendada para tuberías.  Aceptar
        una ruta o una fábrica mantiene una API práctica para guiones y
        pruebas; en esos casos el repositorio es propietario y la conexión se
        cierra al terminar.
        """
        if isinstance(conexion, ConexionSQLite):
            self._conexion = conexion
            self._posee_conexion = False
        elif isinstance(conexion, (str, os.PathLike)):
            self._conexion = ConexionSQLite(conexion)
            self._posee_conexion = True
        elif callable(conexion):
            self._conexion = conexion()
            if not isinstance(self._conexion, ConexionSQLite):
                raise TypeError("La fábrica debe devolver ConexionSQLite.")
            self._posee_conexion = True
        else:
            raise TypeError(
                "conexion debe ser ConexionSQLite, una ruta o una fábrica de ConexionSQLite."
            )

    @property
    def conexion(self) -> ConexionSQLite:
        """Conexión compartida que utiliza el repositorio."""
        return self._conexion

    @property
    def connection(self) -> ConexionSQLite:
        """Alias en inglés de :attr:`conexion`."""
        return self._conexion

    @property
    def ruta_base_datos(self) -> Path:
        """Ruta del archivo SQLite utilizado por el repositorio."""
        return self._conexion.ruta

    def cerrar(self) -> None:
        """Cierra sólo la conexión creada internamente por este repositorio."""
        if self._posee_conexion:
            self._conexion.cerrar()

    def __enter__(self) -> _RepositorioBase:
        """Permite usar el repositorio como context manager."""
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Libera la conexión propia al salir del contexto."""
        self.cerrar()

    def __del__(self) -> None:
        """Intenta cerrar una conexión propia si se recoge el repositorio."""
        with suppress(AttributeError, sqlite3.Error):
            self.cerrar()


class RepositorioDocumentos(_RepositorioBase):
    """Operaciones de metadatos, deduplicación y consulta de documentos."""

    __slots__ = ("_ultimo_id",)

    _COLUMNAS = """
        id, url, hash_url, dominio, categoria, ruta_relativa,
        tamano_texto_bytes, hash_contenido, codigo_http, profundidad,
        fecha_descarga, fecha_revisitacion
    """

    def __init__(
        self,
        conexion: ConexionSQLite | PathLike | FabricaConexion = RUTA_POR_DEFECTO,
    ) -> None:
        """Inicializa el repositorio sobre una conexión, ruta o fábrica."""
        super().__init__(conexion)
        self._conexion.assured_initialized()
        self._ultimo_id: int | None = None

    @property
    def ultimo_id(self) -> int | None:
        """Id del último documento insertado por este repositorio."""
        return self._ultimo_id

    @staticmethod
    def _fila_a_documento(fila: sqlite3.Row) -> MetadatoDocumento:
        """Convierte una fila SQLite al modelo de dominio."""
        return MetadatoDocumento(
            url=str(fila["url"]),
            hash_url=str(fila["hash_url"]),
            dominio=str(fila["dominio"]),
            categoria=str(fila["categoria"]),
            ruta_relativa=str(fila["ruta_relativa"]),
            tamano_texto_bytes=int(fila["tamano_texto_bytes"]),
            hash_contenido=str(fila["hash_contenido"]),
            codigo_http=(int(fila["codigo_http"]) if fila["codigo_http"] is not None else None),
            profundidad=int(fila["profundidad"]),
            fecha_descarga=datetime_desde_timestamp(str(fila["fecha_descarga"])),
            fecha_revisitacion=(
                datetime_desde_timestamp(str(fila["fecha_revisitacion"]))
                if fila["fecha_revisitacion"] is not None
                else None
            ),
        )

    def existe_hash_url(self, hash_url: str) -> bool:
        """Indica si ya existe una URL con ese hash."""
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            "SELECT 1 FROM documentos WHERE hash_url = ? LIMIT 1", (hash_url,)
        )
        return fila is not None

    def existe_url(self, url: str) -> bool:
        """Indica si la URL exacta ya fue almacenada."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url debe ser una cadena no vacía.")
        fila = self._conexion.fetchone("SELECT 1 FROM documentos WHERE url = ? LIMIT 1", (url,))
        return fila is not None

    def existe_hash_contenido(self, hash_contenido: str) -> bool:
        """Indica si el contenido limpio ya fue almacenado."""
        if not isinstance(hash_contenido, str) or not hash_contenido.strip():
            raise ValueError("hash_contenido debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            "SELECT 1 FROM documentos WHERE hash_contenido = ? LIMIT 1",
            (hash_contenido,),
        )
        return fila is not None

    def necesita_revisita(
        self,
        hash_url: str,
        *,
        momento: datetime | str | None = None,
    ) -> bool:
        """Indica si una URL nunca fue vista o su próxima revisión ya venció."""

        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            "SELECT fecha_revisitacion FROM documentos WHERE hash_url = ? LIMIT 1",
            (hash_url,),
        )
        if fila is None or fila["fecha_revisitacion"] is None:
            return True
        referencia = normalizar_datetime_utc(momento) if momento is not None else datetime.now(UTC)
        return datetime_desde_timestamp(str(fila["fecha_revisitacion"])) <= referencia

    def registrar_revisita_exitosa(
        self,
        metadato: MetadatoDocumento,
        escribir_archivo: Callable[[str], Any] | None = None,
    ) -> bool:
        """Actualiza frescura cuando la URL conserva exactamente su contenido.

        Si ``escribir_archivo`` se proporciona, verifica o publica el artefacto
        dentro de la misma transacción que renueva las fechas. Un error de disco
        revierte la actualización y no deja metadatos más recientes que un
        archivo ausente.

        Returns:
            ``True`` si la fila existente tenía el mismo ``hash_contenido`` y se
            renovaron sus fechas; ``False`` si no existe, cambió el contenido o
            la URL ya fue eliminada.
        """

        if not isinstance(metadato, MetadatoDocumento):
            raise TypeError("metadato debe ser MetadatoDocumento.")
        if escribir_archivo is not None and not callable(escribir_archivo):
            raise TypeError("escribir_archivo debe ser invocable o None.")
        with self._conexion.transaccion(inmediata=True):
            fila = self._conexion.fetchone(
                "SELECT hash_contenido FROM documentos WHERE hash_url = ? LIMIT 1",
                (metadato.hash_url,),
            )
            if fila is None or str(fila["hash_contenido"]) != metadato.hash_contenido:
                return False
            if escribir_archivo is not None:
                escribir_archivo(metadato.ruta_relativa)
            cursor = self._conexion.execute(
                """
                UPDATE documentos
                   SET fecha_descarga = ?, fecha_revisitacion = ?
                 WHERE hash_url = ? AND hash_contenido = ?
                """,
                (
                    timestamp_utc_iso(metadato.fecha_descarga),
                    (
                        timestamp_utc_iso(metadato.fecha_revisitacion)
                        if metadato.fecha_revisitacion is not None
                        else None
                    ),
                    metadato.hash_url,
                    metadato.hash_contenido,
                ),
            )
        return cursor.rowcount > 0

    def actualizar_documento_por_url(
        self,
        metadato: MetadatoDocumento,
        escribir_archivo: Callable[[str], Any],
    ) -> tuple[str, bool] | None:
        """Actualiza una relectura, incluso si el contenido de la URL cambió.

        Returns ``(ruta_anterior, contenido_cambiado)`` cuando la URL ya
        existía, o ``None`` si se trata de un documento nuevo. La escritura del
        nuevo artefacto y la actualización SQL participaron en la misma
        transacción. La ruta anterior se conserva hasta que el repositorio
        confirma el commit para que el pipeline pueda retirarla después.
        """

        if not isinstance(metadato, MetadatoDocumento):
            raise TypeError("metadato debe ser MetadatoDocumento.")
        if not callable(escribir_archivo):
            raise TypeError("escribir_archivo debe ser invocable.")
        with self._conexion.transaccion(inmediata=True):
            fila = self._conexion.fetchone(
                """
                SELECT id, ruta_relativa, hash_contenido
                  FROM documentos
                 WHERE hash_url = ?
                 LIMIT 1
                """,
                (metadato.hash_url,),
            )
            if fila is None:
                return None

            contenido_cambiado = str(fila["hash_contenido"]) != metadato.hash_contenido
            if contenido_cambiado:
                duplicado = self._conexion.fetchone(
                    """
                    SELECT hash_url
                      FROM documentos
                     WHERE hash_contenido = ? AND hash_url <> ?
                     LIMIT 1
                    """,
                    (metadato.hash_contenido, metadato.hash_url),
                )
                if duplicado is not None:
                    raise DocumentoDuplicadoError("contenido", metadato.hash_contenido)

            escribir_archivo(metadato.ruta_relativa)
            valores = metadato.a_timestamp()
            self._conexion.execute(
                """
                UPDATE documentos
                   SET url = ?, hash_url = ?, dominio = ?, categoria = ?,
                       ruta_relativa = ?, tamano_texto_bytes = ?, hash_contenido = ?,
                       codigo_http = ?, profundidad = ?, fecha_descarga = ?,
                       fecha_revisitacion = ?
                 WHERE id = ?
                """,
                (
                    valores["url"],
                    valores["hash_url"],
                    valores["dominio"],
                    valores["categoria"],
                    valores["ruta_relativa"],
                    valores["tamano_texto_bytes"],
                    valores["hash_contenido"],
                    valores["codigo_http"],
                    valores["profundidad"],
                    valores["fecha_descarga"],
                    valores["fecha_revisitacion"],
                    int(fila["id"]),
                ),
            )
            return str(fila["ruta_relativa"]), contenido_cambiado

    def _detectar_duplicado(self, metadato: MetadatoDocumento) -> ResultadoInsercion | None:
        """Busca una clave duplicada con prioridad a URL y luego contenido."""
        if (
            self._conexion.fetchone(
                "SELECT 1 FROM documentos WHERE url = ? LIMIT 1", (metadato.url,)
            )
            is not None
        ):
            return ResultadoInsercion.DUPLICADO_URL
        if (
            self._conexion.fetchone(
                "SELECT 1 FROM documentos WHERE hash_url = ? LIMIT 1",
                (metadato.hash_url,),
            )
            is not None
        ):
            return ResultadoInsercion.DUPLICADO_URL
        if (
            self._conexion.fetchone(
                "SELECT 1 FROM documentos WHERE hash_contenido = ? LIMIT 1",
                (metadato.hash_contenido,),
            )
            is not None
        ):
            return ResultadoInsercion.DUPLICADO_CONTENIDO
        return None

    @staticmethod
    def _es_conflicto_unico(error: sqlite3.IntegrityError) -> bool:
        """Distingue una colisión esperada de una violación de integridad real."""
        mensaje = str(error).lower()
        return "unique" in mensaje or "constraint failed: documentos.url" in mensaje

    def _insertar_en_transaccion(self, metadato: MetadatoDocumento) -> ResultadoInsercion:
        """Inserta un documento suponiendo una transacción ya abierta."""
        if not isinstance(metadato, MetadatoDocumento):
            raise TypeError("metadato debe ser MetadatoDocumento.")
        duplicado = self._detectar_duplicado(metadato)
        if duplicado is not None:
            return duplicado
        valores = metadato.a_timestamp()
        try:
            cursor = self._conexion.ejecutar(
                """
                INSERT INTO documentos (
                    url, hash_url, dominio, categoria, ruta_relativa,
                    tamano_texto_bytes, hash_contenido, codigo_http, profundidad,
                    fecha_descarga, fecha_revisitacion
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valores["url"],
                    valores["hash_url"],
                    valores["dominio"],
                    valores["categoria"],
                    valores["ruta_relativa"],
                    valores["tamano_texto_bytes"],
                    valores["hash_contenido"],
                    valores["codigo_http"],
                    valores["profundidad"],
                    valores["fecha_descarga"],
                    valores["fecha_revisitacion"],
                ),
            )
        except sqlite3.IntegrityError as error:
            # BEGIN IMMEDIATE serializa escritores, pero se conserva esta
            # defensa para otros procesos y para conexiones abiertas por otro
            # proceso.  Un CHECK o FK inválido nunca se transforma en un
            # duplicado: se propaga y la transacción se revierte.
            if not self._es_conflicto_unico(error):
                raise
            duplicado = self._detectar_duplicado(metadato)
            if duplicado is None:
                raise
            return duplicado
        self._ultimo_id = int(cursor.lastrowid) if cursor.lastrowid is not None else None
        return ResultadoInsercion.INSERTADO

    def intentar_guardar_documento(self, metadato: MetadatoDocumento) -> ResultadoInsercion:
        """Inserta o clasifica un duplicado dentro de una transacción atómica.

        Returns:
            ``INSERTADO`` si se escribió una fila; ``DUPLICADO_URL`` o
            ``DUPLICADO_CONTENIDO`` si ya existía una clave y no se modificó la
            fila original.
        """
        ultimo_id_previo = self._ultimo_id
        try:
            with self._conexion.transaccion(inmediata=True):
                resultado = self._insertar_en_transaccion(metadato)
        except BaseException:
            self._ultimo_id = ultimo_id_previo
            raise
        return resultado

    def guardar_documento(self, metadato: MetadatoDocumento) -> bool:
        """Guarda un documento y devuelve ``True`` sólo si fue insertado.

        Esta forma compacta permite que una tubería decida entre escribir el
        artefacto y registrar una entrada de duplicado sin una segunda
        consulta.  La fila existente nunca se sobrescribe.
        """
        return self.intentar_guardar_documento(metadato) is ResultadoInsercion.INSERTADO

    def guardar_documento_estricto(self, metadato: MetadatoDocumento) -> None:
        """Guarda un documento o lanza :class:`DocumentoDuplicadoError`."""
        resultado = self.intentar_guardar_documento(metadato)
        if resultado is not ResultadoInsercion.INSERTADO:
            if resultado is ResultadoInsercion.DUPLICADO_URL:
                motivo, valor = "url", metadato.url
            else:
                motivo, valor = "contenido", metadato.hash_contenido
            raise DocumentoDuplicadoError(motivo, valor)

    def guardar_documento_atomico(
        self,
        metadato: MetadatoDocumento,
        escribir_archivo: Callable[[str], Any] | None = None,
    ) -> ResultadoInsercion | str:
        """Guarda metadatos y, opcionalmente, coordina la escritura del texto.

        Sin función de escritura delega en :meth:`intentar_guardar_documento`.
        Con una función, la deduplicación, la escritura y el ``INSERT`` comparten una
        transacción ``BEGIN IMMEDIATE``.  La función recibe la ruta relativa y
        debe publicar el archivo de forma atómica; si lanza ``FileNotFoundError``
        u otro error, la transacción SQLite se revierte.  Una colisión
        detectable se comunica mediante :class:`DocumentoDuplicadoError` y el
        función no se invoca.

        Returns:
            ``ResultadoInsercion`` sin función, o la ruta relativa guardada
            cuando ``escribir_archivo`` participa en la operación atómica.
        """
        if escribir_archivo is None:
            return self.intentar_guardar_documento(metadato)
        if not callable(escribir_archivo):
            raise TypeError("escribir_archivo debe ser invocable o None.")

        ultimo_id_previo = self._ultimo_id
        try:
            with self._conexion.transaccion(inmediata=True):
                duplicado = self._detectar_duplicado(metadato)
                if duplicado is not None:
                    if duplicado is ResultadoInsercion.DUPLICADO_URL:
                        motivo, valor = "url", metadato.url
                    else:
                        motivo, valor = "contenido", metadato.hash_contenido
                    raise DocumentoDuplicadoError(motivo, valor)
                escribir_archivo(metadato.ruta_relativa)
                resultado = self._insertar_en_transaccion(metadato)
                if resultado is not ResultadoInsercion.INSERTADO:
                    if resultado is ResultadoInsercion.DUPLICADO_URL:
                        motivo, valor = "url", metadato.url
                    else:
                        motivo, valor = "contenido", metadato.hash_contenido
                    raise DocumentoDuplicadoError(motivo, valor)
        except BaseException:
            self._ultimo_id = ultimo_id_previo
            raise
        return metadato.ruta_relativa

    def obtener_por_id(self, identificador: int) -> MetadatoDocumento | None:
        """Obtiene un documento por su id, o ``None`` si no existe."""
        if isinstance(identificador, bool) or not isinstance(identificador, int):
            raise TypeError("identificador debe ser un entero.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE id = ?", (identificador,)
        )
        return self._fila_a_documento(fila) if fila is not None else None

    def obtener_por_url(self, url: str) -> MetadatoDocumento | None:
        """Obtiene un documento por su URL exacta."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE url = ?", (url,)
        )
        return self._fila_a_documento(fila) if fila is not None else None

    def obtener_por_hash_url(self, hash_url: str) -> MetadatoDocumento | None:
        """Obtiene un documento por el hash de su URL."""
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE hash_url = ?", (hash_url,)
        )
        return self._fila_a_documento(fila) if fila is not None else None

    def obtener_por_hash_contenido(self, hash_contenido: str) -> MetadatoDocumento | None:
        """Obtiene el primer documento con el hash de contenido indicado."""
        if not isinstance(hash_contenido, str) or not hash_contenido.strip():
            raise ValueError("hash_contenido debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE hash_contenido = ? ORDER BY id LIMIT 1",
            (hash_contenido,),
        )
        return self._fila_a_documento(fila) if fila is not None else None

    def obtener_todos_por_hash_contenido(self, hash_contenido: str) -> list[MetadatoDocumento]:
        """Obtiene todos los documentos que comparten un hash de contenido."""
        if not isinstance(hash_contenido, str) or not hash_contenido.strip():
            raise ValueError("hash_contenido debe ser una cadena no vacía.")
        filas = self._conexion.fetchall(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE hash_contenido = ? ORDER BY id",
            (hash_contenido,),
        )
        return [self._fila_a_documento(fila) for fila in filas]

    def obtener_documento_por_hash_url(self, hash_url: str) -> MetadatoDocumento | None:
        """Alias descriptivo de :meth:`obtener_por_hash_url`."""
        return self.obtener_por_hash_url(hash_url)

    def obtener_documento_por_url(self, url: str) -> MetadatoDocumento | None:
        """Alias descriptivo de :meth:`obtener_por_url`."""
        return self.obtener_por_url(url)

    def obtener_por_ruta(self, ruta_relativa: str) -> MetadatoDocumento | None:
        """Obtiene un documento por su ruta relativa de almacenamiento."""
        if not isinstance(ruta_relativa, str) or not ruta_relativa.strip():
            raise ValueError("ruta_relativa debe ser una cadena no vacía.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM documentos WHERE ruta_relativa = ?",
            (ruta_relativa,),
        )
        return self._fila_a_documento(fila) if fila is not None else None

    def listar_documentos(
        self,
        *,
        limite: int | None = 100,
        desplazamiento: int = 0,
        dominio: str | None = None,
        categoria: str | None = None,
    ) -> list[MetadatoDocumento]:
        """Lista documentos con filtros y paginación segura."""
        if limite is not None and (isinstance(limite, bool) or not isinstance(limite, int)):
            raise TypeError("limite debe ser un entero o None.")
        if limite is not None and limite < 0:
            raise ValueError("limite no puede ser negativo.")
        if isinstance(desplazamiento, bool) or not isinstance(desplazamiento, int):
            raise TypeError("desplazamiento debe ser un entero.")
        if desplazamiento < 0:
            raise ValueError("desplazamiento no puede ser negativo.")

        condiciones: list[str] = []
        parametros: list[object] = []
        if dominio is not None:
            if not isinstance(dominio, str) or not dominio.strip():
                raise ValueError("dominio debe ser una cadena no vacía.")
            condiciones.append("dominio = ?")
            parametros.append(dominio)
        if categoria is not None:
            if not isinstance(categoria, str) or not categoria.strip():
                raise ValueError("categoria debe ser una cadena no vacía.")
            condiciones.append("categoria = ?")
            parametros.append(categoria)
        where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        if limite is None:
            consulta = (
                f"SELECT {self._COLUMNAS} FROM documentos{where} "
                "ORDER BY fecha_descarga DESC, id DESC"
            )
        else:
            consulta = (
                f"SELECT {self._COLUMNAS} FROM documentos{where} "
                "ORDER BY fecha_descarga DESC, id DESC LIMIT ? OFFSET ?"
            )
            parametros.extend((limite, desplazamiento))
        filas = self._conexion.fetchall(consulta, parametros)
        return [self._fila_a_documento(fila) for fila in filas]

    listar = listar_documentos

    def obtener_todos(self) -> list[MetadatoDocumento]:
        """Obtiene todos los documentos, sin el límite predeterminado."""
        return self.listar_documentos(limite=None)

    obtener_documentos = obtener_todos

    def filtrar_por_dominio(self, dominio: str) -> list[MetadatoDocumento]:
        """Lista los documentos de un dominio."""
        return self.listar_documentos(limite=None, dominio=dominio)

    def contar_documentos(self) -> int:
        """Cuenta los documentos almacenados."""
        fila = self._conexion.fetchone("SELECT COUNT(*) AS total FROM documentos")
        return int(fila["total"]) if fila is not None else 0

    contar = contar_documentos

    def eliminar_documento(self, hash_url: str) -> bool:
        """Elimina una fila por hash de URL dentro de una transacción."""
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        with self._conexion.transaccion(inmediata=True):
            cursor = self._conexion.ejecutar(
                "DELETE FROM documentos WHERE hash_url = ?", (hash_url,)
            )
        return cursor.rowcount > 0

    def obtener_resumen_estadistico(self) -> dict[str, int | float]:
        """Calcula un resumen numérico de los documentos almacenados."""
        fila = self._conexion.fetchone(
            """
            SELECT
                COUNT(*) AS total_documentos,
                COUNT(DISTINCT dominio) AS dominios,
                COALESCE(SUM(tamano_texto_bytes), 0) AS total_bytes,
                COALESCE(AVG(tamano_texto_bytes), 0.0) AS promedio_bytes,
                COALESCE(AVG(profundidad), 0.0) AS profundidad_promedio,
                COALESCE(SUM(CASE WHEN fecha_revisitacion IS NOT NULL THEN 1 ELSE 0 END), 0)
                    AS documentos_con_revisitacion
            FROM documentos
            """
        )
        if fila is None:
            return {
                "total_documentos": 0,
                "documentos": 0,
                "total_bytes": 0,
                "bytes": 0,
                "dominios": 0,
                "tamano_total_bytes": 0,
                "promedio_bytes": 0.0,
                "tamano_promedio_bytes": 0.0,
                "profundidad_promedio": 0.0,
                "documentos_con_revisitacion": 0,
            }
        total = int(fila["total_documentos"])
        total_bytes = int(fila["total_bytes"])
        promedio = float(fila["promedio_bytes"])
        dominios = int(fila["dominios"])
        return {
            "total_documentos": total,
            "documentos": total,
            "total_bytes": total_bytes,
            "bytes": total_bytes,
            "dominios": dominios,
            "tamano_total_bytes": total_bytes,
            "promedio_bytes": promedio,
            "tamano_promedio_bytes": promedio,
            "profundidad_promedio": float(fila["profundidad_promedio"]),
            "documentos_con_revisitacion": int(fila["documentos_con_revisitacion"]),
        }

    def obtener_resumen(self) -> dict[str, int | float]:
        """Alias de :meth:`obtener_resumen_estadistico`."""
        return self.obtener_resumen_estadistico()

    resumen_estadistico = obtener_resumen_estadistico

    def resumen_por_dominio(self) -> dict[str, int]:
        """Devuelve la cantidad de documentos agrupada por dominio."""
        filas = self._conexion.fetchall(
            """
            SELECT dominio, COUNT(*) AS total
            FROM documentos
            GROUP BY dominio
            ORDER BY total DESC, dominio ASC
            """
        )
        return {str(fila["dominio"]): int(fila["total"]) for fila in filas}

    def resumen_por_categoria(self) -> dict[str, int]:
        """Devuelve la cantidad de documentos agrupada por categoría."""
        filas = self._conexion.fetchall(
            """
            SELECT categoria, COUNT(*) AS total
            FROM documentos
            GROUP BY categoria
            ORDER BY total DESC, categoria ASC
            """
        )
        return {str(fila["categoria"]): int(fila["total"]) for fila in filas}

    def actualizar_fecha_revisitacion(
        self,
        hash_url: str,
        fecha_revisitacion: datetime | str | None,
    ) -> bool:
        """Actualiza la fecha de relectura de un documento por hash de URL."""
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        valor = (
            None
            if fecha_revisitacion is None
            else timestamp_utc_iso(normalizar_datetime_utc(fecha_revisitacion))
        )
        with self._conexion.transaccion(inmediata=True):
            cursor = self._conexion.ejecutar(
                "UPDATE documentos SET fecha_revisitacion = ? WHERE hash_url = ?",
                (valor, hash_url),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _archivo_existe(metadato: MetadatoDocumento, raiz: Path) -> bool:
        """Comprueba que el archivo relativo exista sin salir de la raíz."""
        try:
            raiz_resuelta = raiz.expanduser().resolve()
            ruta = (raiz_resuelta / Path(metadato.ruta_relativa)).resolve()
            ruta.relative_to(raiz_resuelta)
            return ruta.is_file()
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            return False

    def guardar_documento_si_archivo_existe(
        self,
        metadato: MetadatoDocumento,
        raiz_almacenamiento: PathLike,
    ) -> bool:
        """Guarda metadatos sólo si su archivo relativo ya existe.

        Es una operación conveniente para una tubería que escribe primero el
        texto y después registra la fila.  Un ``FileNotFoundError`` o una ruta
        fuera de la raíz produce ``False`` y no deja una fila que apunte a un
        artefacto inexistente.
        """
        if not self._archivo_existe(metadato, Path(raiz_almacenamiento)):
            return False
        return self.guardar_documento(metadato)

    def procesar_lote_documentos(
        self,
        documentos: Iterable[MetadatoDocumento],
        *,
        raiz_almacenamiento: PathLike | None = None,
    ) -> ResultadoLoteDocumentos:
        """Procesa un lote en una única transacción atómica.

        Todos los modelos se validan antes de abrir la transacción.  Si una
        restricción de integridad no deduplicable falla, la excepción sale y el
        lote completo se revierte.  Si se proporciona ``raiz_almacenamiento``,
        los documentos cuyo archivo no existe se omiten; la comprobación se
        hace antes de escribir cada fila.
        """
        elementos = tuple(documentos)
        for elemento in elementos:
            if not isinstance(elemento, MetadatoDocumento):
                raise TypeError("Cada elemento debe ser MetadatoDocumento.")
        raiz = Path(raiz_almacenamiento) if raiz_almacenamiento is not None else None

        insertados = 0
        duplicados_url = 0
        duplicados_contenido = 0
        omitidos_sin_archivo = 0
        ultimo_id_previo = self._ultimo_id
        try:
            with self._conexion.transaccion(inmediata=True):
                for metadato in elementos:
                    if raiz is not None and not self._archivo_existe(metadato, raiz):
                        omitidos_sin_archivo += 1
                        continue
                    resultado = self._insertar_en_transaccion(metadato)
                    if resultado is ResultadoInsercion.INSERTADO:
                        insertados += 1
                    elif resultado is ResultadoInsercion.DUPLICADO_URL:
                        duplicados_url += 1
                    else:
                        duplicados_contenido += 1
        except BaseException:
            # ``_ultimo_id`` se actualiza durante la inserción para que el
            # llamador pueda consultarlo; al revertir un lote nunca debe
            # apuntar a una fila que no llegó a confirmarse.
            self._ultimo_id = ultimo_id_previo
            raise
        return ResultadoLoteDocumentos(
            recibidos=len(elementos),
            insertados=insertados,
            duplicados_url=duplicados_url,
            duplicados_contenido=duplicados_contenido,
            omitidos_sin_archivo=omitidos_sin_archivo,
        )

    def guardar_documentos(
        self,
        documentos: Iterable[MetadatoDocumento],
        *,
        raiz_almacenamiento: PathLike | None = None,
    ) -> int:
        """Guarda un lote deduplicado y devuelve cuántos documentos nuevos hay."""
        return self.procesar_lote_documentos(
            documentos, raiz_almacenamiento=raiz_almacenamiento
        ).insertados

    def guardar_lote(
        self,
        documentos: Iterable[MetadatoDocumento],
        *,
        raiz_almacenamiento: PathLike | None = None,
    ) -> int:
        """Alias de :meth:`guardar_documentos`."""
        return self.guardar_documentos(documentos, raiz_almacenamiento=raiz_almacenamiento)

    def guardar_documentos_si_archivos_existen(
        self,
        documentos: Iterable[MetadatoDocumento],
        raiz_almacenamiento: PathLike,
    ) -> ResultadoLoteDocumentos:
        """Procesa un lote sólo con archivos presentes en la raíz indicada."""
        return self.procesar_lote_documentos(documentos, raiz_almacenamiento=raiz_almacenamiento)

    def guardar_lote_atomico(
        self,
        documentos: Iterable[MetadatoDocumento],
        escribir_archivo: Callable[[str], Any],
    ) -> ResultadoLoteDocumentos:
        """Escribe un lote y sus metadatos dentro de una sola transacción.

        El escritor recibe la ruta relativa de cada documento no duplicado.  Si
        lanza ``FileNotFoundError``, ``sqlite3.IntegrityError`` u otra
        excepción, la transacción se revierte y no se confirman metadatos
        parcial.  La publicación del archivo debe ser atómica y, si el
        llamador necesita deshacerla tras un error, debe conservar su propio
        estado de escritura.
        """
        if not callable(escribir_archivo):
            raise TypeError("escribir_archivo debe ser invocable.")
        elementos = tuple(documentos)
        for elemento in elementos:
            if not isinstance(elemento, MetadatoDocumento):
                raise TypeError("Cada elemento debe ser MetadatoDocumento.")
        insertados = 0
        duplicados_url = 0
        duplicados_contenido = 0
        ultimo_id_previo = self._ultimo_id
        try:
            with self._conexion.transaccion(inmediata=True):
                for metadato in elementos:
                    duplicado = self._detectar_duplicado(metadato)
                    if duplicado is ResultadoInsercion.DUPLICADO_URL:
                        duplicados_url += 1
                        continue
                    if duplicado is ResultadoInsercion.DUPLICADO_CONTENIDO:
                        duplicados_contenido += 1
                        continue
                    escribir_archivo(metadato.ruta_relativa)
                    resultado = self._insertar_en_transaccion(metadato)
                    if resultado is ResultadoInsercion.INSERTADO:
                        insertados += 1
                    elif resultado is ResultadoInsercion.DUPLICADO_URL:
                        duplicados_url += 1
                    else:
                        duplicados_contenido += 1
        except BaseException:
            self._ultimo_id = ultimo_id_previo
            raise
        return ResultadoLoteDocumentos(
            recibidos=len(elementos),
            insertados=insertados,
            duplicados_url=duplicados_url,
            duplicados_contenido=duplicados_contenido,
        )

    guardar_documentos_atomicos = guardar_lote_atomico
    insertar_documentos = guardar_documentos


class RepositorioBitacora(_RepositorioBase):
    """Operaciones de escritura, consulta y resumen para la bitácora."""

    __slots__ = ("_ultimo_id",)

    _COLUMNAS = """
        id, timestamp, dominio, url_origen, url_destino,
        tiempo_respuesta_ms, codigo_http, accion
    """

    def __init__(
        self,
        conexion: ConexionSQLite | PathLike | FabricaConexion = RUTA_POR_DEFECTO,
    ) -> None:
        """Inicializa el repositorio sobre una conexión, ruta o fábrica."""
        super().__init__(conexion)
        self._conexion.assured_initialized()
        self._ultimo_id: int | None = None

    @property
    def ultimo_id(self) -> int | None:
        """Id del último registro de bitácora insertado."""
        return self._ultimo_id

    @staticmethod
    def _fila_a_registro(fila: sqlite3.Row) -> RegistroBitacora:
        """Convierte una fila SQLite al modelo de bitácora."""
        return RegistroBitacora(
            timestamp=datetime_desde_timestamp(str(fila["timestamp"])),
            dominio=str(fila["dominio"]),
            url_origen=(str(fila["url_origen"]) if fila["url_origen"] is not None else None),
            url_destino=str(fila["url_destino"]),
            tiempo_respuesta_ms=float(fila["tiempo_respuesta_ms"]),
            codigo_http=(int(fila["codigo_http"]) if fila["codigo_http"] is not None else None),
            accion=str(fila["accion"]),
        )

    def registrar(self, registro: RegistroBitacora) -> int:
        """Inserta una entrada y devuelve su identificador."""
        if not isinstance(registro, RegistroBitacora):
            raise TypeError("registro debe ser RegistroBitacora.")
        ultimo_id_previo = self._ultimo_id
        try:
            with self._conexion.transaccion(inmediata=True):
                cursor = self._conexion.ejecutar(
                    """
                    INSERT INTO bitacora_recorrido (
                        timestamp, dominio, url_origen, url_destino,
                        tiempo_respuesta_ms, codigo_http, accion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp_utc_iso(registro.timestamp),
                        registro.dominio,
                        registro.url_origen,
                        registro.url_destino,
                        registro.tiempo_respuesta_ms,
                        registro.codigo_http,
                        registro.accion,
                    ),
                )
        except BaseException:
            self._ultimo_id = ultimo_id_previo
            raise
        self._ultimo_id = int(cursor.lastrowid) if cursor.lastrowid is not None else None
        return self._ultimo_id if self._ultimo_id is not None else 0

    def registrar_bitacora(self, registro: RegistroBitacora) -> int:
        """Alias de :meth:`registrar` para tuberías e intermediarios."""
        return self.registrar(registro)

    def guardar_registro(self, registro: RegistroBitacora) -> int:
        """Alias de :meth:`registrar` para consumidores del DAO clásico."""
        return self.registrar(registro)

    def registrar_solicitud(
        self,
        *,
        timestamp: datetime | str,
        dominio: str,
        url_origen: str | None,
        url_destino: str,
        tiempo_respuesta_ms: float,
        codigo_http: int | None,
        accion: str = "VISITA",
    ) -> int:
        """Construye y registra una visita con una API breve para middleware."""
        registro = RegistroBitacora(
            timestamp=normalizar_datetime_utc(timestamp),
            dominio=dominio,
            url_origen=url_origen,
            url_destino=url_destino,
            tiempo_respuesta_ms=tiempo_respuesta_ms,
            codigo_http=codigo_http,
            accion=accion,
        )
        return self.registrar(registro)

    def registrar_error_red(
        self,
        *,
        timestamp: datetime | str,
        dominio: str,
        url_origen: str | None,
        url_destino: str,
        tiempo_respuesta_ms: float = 0.0,
    ) -> int:
        """Registra un fallo de red con ``codigo_http=None``."""
        return self.registrar_solicitud(
            timestamp=timestamp,
            dominio=dominio,
            url_origen=url_origen,
            url_destino=url_destino,
            tiempo_respuesta_ms=tiempo_respuesta_ms,
            codigo_http=None,
            accion="ERROR_RED",
        )

    def registrar_lote(self, registros: Iterable[RegistroBitacora]) -> int:
        """Inserta un lote atómico y devuelve el número de filas insertadas."""
        elementos = tuple(registros)
        for elemento in elementos:
            if not isinstance(elemento, RegistroBitacora):
                raise TypeError("Cada elemento debe ser RegistroBitacora.")
        if not elementos:
            return 0
        filas = [
            (
                timestamp_utc_iso(elemento.timestamp),
                elemento.dominio,
                elemento.url_origen,
                elemento.url_destino,
                elemento.tiempo_respuesta_ms,
                elemento.codigo_http,
                elemento.accion,
            )
            for elemento in elementos
        ]
        with self._conexion.transaccion(inmediata=True):
            cursor = self._conexion.ejecutar_muchos(
                """
                INSERT INTO bitacora_recorrido (
                    timestamp, dominio, url_origen, url_destino,
                    tiempo_respuesta_ms, codigo_http, accion
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                filas,
            )
        self._ultimo_id = int(cursor.lastrowid) if cursor.lastrowid is not None else None
        return len(filas)

    def guardar_lote(self, registros: Iterable[RegistroBitacora]) -> int:
        """Alias de :meth:`registrar_lote`."""
        return self.registrar_lote(registros)

    insertar_lote = registrar_lote

    def registrar_entrada(self, registro: RegistroBitacora) -> int:
        """Alias de :meth:`registrar` para eventos de middleware."""
        return self.registrar(registro)

    def guardar_bitacora(self, registro: RegistroBitacora) -> int:
        """Alias de :meth:`registrar` con nombre explícito de la tabla."""
        return self.registrar(registro)

    def obtener_por_id(self, identificador: int) -> RegistroBitacora | None:
        """Obtiene una entrada por id, o ``None`` si no existe."""
        if isinstance(identificador, bool) or not isinstance(identificador, int):
            raise TypeError("identificador debe ser un entero.")
        fila = self._conexion.fetchone(
            f"SELECT {self._COLUMNAS} FROM bitacora_recorrido WHERE id = ?",
            (identificador,),
        )
        return self._fila_a_registro(fila) if fila is not None else None

    def listar(
        self,
        *,
        limite: int | None = 100,
        desplazamiento: int = 0,
        dominio: str | None = None,
        accion: str | None = None,
        desde: datetime | str | None = None,
        hasta: datetime | str | None = None,
    ) -> list[RegistroBitacora]:
        """Lista entradas de bitácora con filtros temporales y de dominio."""
        if limite is not None and (isinstance(limite, bool) or not isinstance(limite, int)):
            raise TypeError("limite debe ser un entero o None.")
        if limite is not None and limite < 0:
            raise ValueError("limite no puede ser negativo.")
        if isinstance(desplazamiento, bool) or not isinstance(desplazamiento, int):
            raise TypeError("desplazamiento debe ser un entero.")
        if desplazamiento < 0:
            raise ValueError("desplazamiento no puede ser negativo.")

        condiciones: list[str] = []
        parametros: list[object] = []
        if dominio is not None:
            if not isinstance(dominio, str) or not dominio.strip():
                raise ValueError("dominio debe ser una cadena no vacía.")
            condiciones.append("dominio = ?")
            parametros.append(dominio)
        if accion is not None:
            if not isinstance(accion, str) or not accion.strip():
                raise ValueError("accion debe ser una cadena no vacía.")
            condiciones.append("accion = ?")
            parametros.append(accion.upper())
        if desde is not None:
            condiciones.append("timestamp >= ?")
            parametros.append(timestamp_utc_iso(normalizar_datetime_utc(desde)))
        if hasta is not None:
            condiciones.append("timestamp <= ?")
            parametros.append(timestamp_utc_iso(normalizar_datetime_utc(hasta)))
        where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        if limite is None:
            consulta = (
                f"SELECT {self._COLUMNAS} FROM bitacora_recorrido{where} "
                "ORDER BY timestamp DESC, id DESC"
            )
        else:
            consulta = (
                f"SELECT {self._COLUMNAS} FROM bitacora_recorrido{where} "
                "ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?"
            )
            parametros.extend((limite, desplazamiento))
        filas = self._conexion.fetchall(consulta, parametros)
        return [self._fila_a_registro(fila) for fila in filas]

    listar_registros = listar

    def obtener_registros(self, limite: int | None = 100) -> list[RegistroBitacora]:
        """Obtiene los registros más recientes como modelos inmutables."""
        return self.listar(limite=limite)

    def obtener_todos(self) -> list[RegistroBitacora]:
        """Obtiene todos los registros de bitácora."""
        return self.listar(limite=None)

    def contar(self, dominio: str | None = None) -> int:
        """Cuenta entradas, opcionalmente limitadas a un dominio."""
        if dominio is None:
            fila = self._conexion.fetchone("SELECT COUNT(*) AS total FROM bitacora_recorrido")
        else:
            if not isinstance(dominio, str) or not dominio.strip():
                raise ValueError("dominio debe ser una cadena no vacía.")
            fila = self._conexion.fetchone(
                "SELECT COUNT(*) AS total FROM bitacora_recorrido WHERE dominio = ?",
                (dominio,),
            )
        return int(fila["total"]) if fila is not None else 0

    contar_registros = contar

    def obtener_resumen_estadistico(self) -> dict[str, int | float]:
        """Calcula métricas numéricas de recorrido y respuesta."""
        fila = self._conexion.fetchone(
            """
            SELECT
                COUNT(*) AS total_registros,
                COALESCE(SUM(tiempo_respuesta_ms), 0.0) AS tiempo_total_ms,
                COALESCE(AVG(tiempo_respuesta_ms), 0.0) AS tiempo_promedio_ms,
                COALESCE(SUM(CASE WHEN codigo_http IS NULL THEN 1 ELSE 0 END), 0)
                    AS registros_sin_codigo_http,
                COALESCE(SUM(CASE WHEN accion = 'ERROR_RED' THEN 1 ELSE 0 END), 0)
                    AS errores_red,
                COALESCE(SUM(CASE WHEN accion = 'ERROR' THEN 1 ELSE 0 END), 0)
                    AS errores
            FROM bitacora_recorrido
            """
        )
        if fila is None:
            return {
                "total_registros": 0,
                "registros": 0,
                "tiempo_total_ms": 0.0,
                "tiempo_promedio_ms": 0.0,
                "tiempo_promedio": 0.0,
                "registros_sin_codigo_http": 0,
                "errores_red": 0,
                "errores": 0,
            }
        return {
            "total_registros": int(fila["total_registros"]),
            "registros": int(fila["total_registros"]),
            "tiempo_total_ms": float(fila["tiempo_total_ms"]),
            "tiempo_promedio_ms": float(fila["tiempo_promedio_ms"]),
            "tiempo_promedio": float(fila["tiempo_promedio_ms"]),
            "registros_sin_codigo_http": int(fila["registros_sin_codigo_http"]),
            "errores_red": int(fila["errores_red"]),
            "errores": int(fila["errores"]),
        }

    def obtener_resumen(self) -> dict[str, int | float]:
        """Alias de :meth:`obtener_resumen_estadistico`."""
        return self.obtener_resumen_estadistico()

    resumen_estadistico = obtener_resumen_estadistico

    def resumen_por_dominio(self) -> dict[str, int]:
        """Devuelve el número de visitas agrupado por dominio."""
        filas = self._conexion.fetchall(
            """
            SELECT dominio, COUNT(*) AS total
            FROM bitacora_recorrido
            GROUP BY dominio
            ORDER BY total DESC, dominio ASC
            """
        )
        return {str(fila["dominio"]): int(fila["total"]) for fila in filas}

    def resumen_por_accion(self) -> dict[str, int]:
        """Devuelve el número de entradas agrupado por acción."""
        filas = self._conexion.fetchall(
            """
            SELECT accion, COUNT(*) AS total
            FROM bitacora_recorrido
            GROUP BY accion
            ORDER BY total DESC, accion ASC
            """
        )
        return {str(fila["accion"]): int(fila["total"]) for fila in filas}


# Alias de compatibilidad para código que usa el nombre en inglés.
DocumentRepository = RepositorioDocumentos
LogRepository = RepositorioBitacora

__all__ = [
    "DocumentRepository",
    "DocumentoDuplicadoError",
    "FabricaConexion",
    "LogRepository",
    "PathLike",
    "RepositorioBitacora",
    "RepositorioDocumentos",
    "ResultadoInsercion",
    "ResultadoLoteDocumentos",
]
