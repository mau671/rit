"""Frontera compartida (cola de URL pendientes) sobre la base SQLite común.

La tabla ``frontera`` es un contrato congelado entre las dos implementaciones
del proyecto: la versión Scrapy y la versión propia comparten el mismo archivo
``almacenamiento/metadatos_araña.db`` y el mismo esquema, de modo que una
ejecución puede continuar el recorrido donde la otra lo dejó.

Semántica de la cola (recorrido en amplitud):

* la clave de deduplicación es ``hash_url`` (``generar_hash_url``);
* ``prioridad = -float(profundidad)`` y el orden de extracción es
  ``prioridad DESC, id ASC``: primero las URL menos profundas y, a igual
  profundidad, la más antigua por orden de inserción (FIFO);
* volver a registrar una URL ``visitada`` o ``error`` la devuelve a
  ``pendiente`` (revisitas) sin cambiar su ``id``, por lo que conserva su
  posición relativa en la secuencia.

Este módulo no depende de Scrapy: la extensión y el middleware de la versión
Scrapy lo envuelven, pero el repositorio puede usarse desde un guion o desde la
otra implementación.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...bibliotecas.sqlite import RUTA_POR_DEFECTO, ConexionSQLite
from ..utilidades.generador_hash import generar_hash_url
from .repositorio import FabricaConexion, PathLike, _RepositorioBase

__all__ = ["EntradaFrontera", "RepositorioFrontera"]

#: Estados admitidos por el ``CHECK`` del esquema congelado.
PENDIENTE: str = "pendiente"
VISITADA: str = "visitada"
ERROR: str = "error"

_ESTADOS_RESURRECTOS: tuple[str, ...] = (VISITADA, ERROR)

_MARCA_TIEMPO_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


@dataclass(frozen=True, slots=True)
class EntradaFrontera:
    """Una URL pendiente recuperada de la frontera compartida.

    Attributes:
        hash_url: Huella SHA-256 de la URL normalizada.
        url: URL tal como se registró en la frontera.
        profundidad: Profundidad de descubrimiento de la URL.
        prioridad: ``-float(profundidad)``; mayor valor se extrae antes.
        url_origen: URL desde la que se descubrió, si se conoce.
    """

    hash_url: str
    url: str
    profundidad: int
    prioridad: float
    url_origen: str | None


class RepositorioFrontera(_RepositorioBase):
    """Operaciones de la cola compartida ``frontera``.

    Acepta una :class:`ConexionSQLite`, una ruta o una fábrica, igual que los
    repositorios de documentos y bitácora, y reutiliza las transacciones
    ``BEGIN IMMEDIATE`` del proyecto para serializar escritores entre procesos.
    """

    __slots__ = ()

    def __init__(
        self,
        conexion: ConexionSQLite | PathLike | FabricaConexion = RUTA_POR_DEFECTO,
    ) -> None:
        """Inicializa el repositorio sobre una conexión, ruta o fábrica."""
        super().__init__(conexion)
        self._conexion.assured_initialized()

    @staticmethod
    def _validar_url(url: str) -> str:
        """Valida una URL no vacía para el contrato de la frontera."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url debe ser una cadena no vacía.")
        return url.strip()

    @staticmethod
    def _validar_profundidad(profundidad: int) -> int:
        """Valida una profundidad entera no negativa."""
        if isinstance(profundidad, bool) or not isinstance(profundidad, int):
            raise TypeError("profundidad debe ser un entero.")
        if profundidad < 0:
            raise ValueError("profundidad no puede ser negativa.")
        return profundidad

    @staticmethod
    def _validar_max_intentos(max_intentos: int) -> int:
        """Valida el tope de intentos, que debe ser al menos uno."""
        if isinstance(max_intentos, bool) or not isinstance(max_intentos, int):
            raise TypeError("max_intentos debe ser un entero.")
        if max_intentos < 1:
            raise ValueError("max_intentos debe ser al menos 1.")
        return max_intentos

    @staticmethod
    def _validar_origen(url_origen: str | None) -> str | None:
        """Valida la URL de origen, admitiendo ``None``."""
        if url_origen is None:
            return None
        if not isinstance(url_origen, str) or not url_origen.strip():
            raise ValueError("url_origen debe ser una cadena no vacía o None.")
        return url_origen.strip()

    def registrar(
        self,
        url: str,
        profundidad: int,
        url_origen: str | None = None,
    ) -> str:
        """Inserta o actualiza una URL pendiente y devuelve su ``hash_url``.

        El ``ON CONFLICT`` conserva el ``id`` existente: si la URL ya estaba
        ``pendiente`` no pierde su turno; si estaba ``visitada`` o ``error``
        vuelve a ``pendiente`` con los intentos en cero (revisita).
        """
        url = self._validar_url(url)
        profundidad = self._validar_profundidad(profundidad)
        url_origen = self._validar_origen(url_origen)
        hash_url = generar_hash_url(url)
        prioridad = -float(profundidad)

        with self._conexion.transaccion(inmediata=True):
            self._conexion.ejecutar(
                f"""
                INSERT INTO frontera (
                    hash_url, url, profundidad, prioridad, url_origen,
                    estado, intentos, actualizada_en
                ) VALUES (?, ?, ?, ?, ?, '{PENDIENTE}', 0, {_MARCA_TIEMPO_SQL})
                ON CONFLICT(hash_url) DO UPDATE SET
                    url = excluded.url,
                    profundidad = excluded.profundidad,
                    prioridad = excluded.prioridad,
                    url_origen = COALESCE(excluded.url_origen, frontera.url_origen),
                    estado = CASE
                        WHEN frontera.estado IN ('{_ESTADOS_RESURRECTOS[0]}',
                                                 '{_ESTADOS_RESURRECTOS[1]}')
                            THEN '{PENDIENTE}'
                        ELSE frontera.estado
                    END,
                    intentos = CASE
                        WHEN frontera.estado IN ('{_ESTADOS_RESURRECTOS[0]}',
                                                 '{_ESTADOS_RESURRECTOS[1]}')
                            THEN 0
                        ELSE frontera.intentos
                    END,
                    actualizada_en = {_MARCA_TIEMPO_SQL}
                """,
                (hash_url, url, profundidad, prioridad, url_origen),
            )
        return hash_url

    def marcar_visitada(self, hash_url: str) -> bool:
        """Marca una entrada como ``visitada``; devuelve si existía."""
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        with self._conexion.transaccion(inmediata=True):
            cursor = self._conexion.ejecutar(
                f"""
                UPDATE frontera
                   SET estado = '{VISITADA}', actualizada_en = {_MARCA_TIEMPO_SQL}
                 WHERE hash_url = ?
                """,
                (hash_url,),
            )
        return cursor.rowcount > 0

    def marcar_error(self, hash_url: str, max_intentos: int = 3) -> bool:
        """Incrementa los intentos de una entrada y la marca ``error``.

        Al alcanzar ``max_intentos`` la entrada pasa a ``visitada`` para no
        reintentarla indefinidamente. Devuelve si la fila existía.
        """
        if not isinstance(hash_url, str) or not hash_url.strip():
            raise ValueError("hash_url debe ser una cadena no vacía.")
        max_intentos = self._validar_max_intentos(max_intentos)
        with self._conexion.transaccion(inmediata=True):
            cursor = self._conexion.ejecutar(
                f"""
                UPDATE frontera
                   SET intentos = intentos + 1,
                       estado = CASE
                           WHEN intentos + 1 >= ? THEN '{VISITADA}'
                           ELSE '{ERROR}'
                       END,
                       actualizada_en = {_MARCA_TIEMPO_SQL}
                 WHERE hash_url = ?
                """,
                (max_intentos, hash_url),
            )
        return cursor.rowcount > 0

    def pendientes(self, max_intentos: int = 3) -> list[EntradaFrontera]:
        """Devuelve la cola extraíble en orden ``prioridad DESC, id ASC``.

        Incluye las URL ``pendiente`` y las que están en ``error`` pero aún no
        agotaron ``max_intentos``. Las ``visitada`` nunca se devuelven.
        """
        max_intentos = self._validar_max_intentos(max_intentos)
        filas = self._conexion.fetchall(
            """
            SELECT hash_url, url, profundidad, prioridad, url_origen
              FROM frontera
             WHERE estado = ?
                OR (estado = ? AND intentos < ?)
             ORDER BY prioridad DESC, id ASC
            """,
            (PENDIENTE, ERROR, max_intentos),
        )
        return [
            EntradaFrontera(
                hash_url=str(fila["hash_url"]),
                url=str(fila["url"]),
                profundidad=int(fila["profundidad"]),
                prioridad=float(fila["prioridad"]),
                url_origen=(str(fila["url_origen"]) if fila["url_origen"] is not None else None),
            )
            for fila in filas
        ]

    def reconciliar(
        self,
        es_fresco: Callable[[str], bool],
        max_intentos: int = 3,
    ) -> int:
        """Descarta de la cola lo que ya está fresco.

        ``es_fresco`` es un invocable ``hash_url -> bool`` (por ejemplo,
        ``lambda h: not RepositorioDocumentos.necesita_revisita(h)``). Toda
        entrada pendiente para la que devuelva ``True`` se marca ``visitada``.
        Devuelve cuántas entradas se descartaron.
        """
        if not callable(es_fresco):
            raise TypeError("es_fresco debe ser invocable.")
        max_intentos = self._validar_max_intentos(max_intentos)
        descartadas = 0
        with self._conexion.transaccion(inmediata=True):
            candidatas = self.pendientes(max_intentos)
            for entrada in candidatas:
                if es_fresco(entrada.hash_url):
                    self.marcar_visitada(entrada.hash_url)
                    descartadas += 1
        return descartadas
