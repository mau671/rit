"""Conexión segura y migración de la base de datos SQLite del arañador.

La aplicación escribe únicamente metadatos y una bitácora de recorrido.  Esta
módulo concentra la configuración de SQLite para que todos los repositorios
compartan las mismas garantías:

* la ruta se manipula con :class:`pathlib.Path`;
* se activa el journal WAL en archivos de base de datos;
* se activan las claves foráneas y un tiempo de espera para escritores;
* las filas se devuelven como :class:`sqlite3.Row`;
* el esquema se actualiza mediante migraciones idempotentes y versionadas.

No se dependen de Scrapy ni de otros paquetes de terceros.  Así, la capa de
persistencia puede utilizarse desde una araña, una tubería, un middleware o un
guion de mantenimiento sin arrastrar el marco completo.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ..implementacion.utilidades.rutas import resolver_ruta_datos

RUTA_POR_DEFECTO: Final[Path] = resolver_ruta_datos("almacenamiento", "metadatos_araña.db")
"""Ruta relativa usada cuando no se proporciona una ruta explícita."""

TIEMPO_ESPERA_POR_DEFECTO_MS: Final[int] = 5_000
"""Tiempo de espera predeterminado para contender por el bloqueo de SQLite."""


@dataclass(frozen=True, slots=True)
class Migracion:
    """Una migración de esquema identificada por un número monotónico.

    Attributes:
        numero: Número de versión que se guarda en ``PRAGMA user_version`` y
            en la tabla de control de migraciones.
        nombre: Nombre descriptivo para auditoría y diagnóstico.
        sentencias: Sentencias SQL que se ejecutan en una única transacción.
    """

    numero: int
    nombre: str
    sentencias: tuple[str, ...]

    def __post_init__(self) -> None:
        """Valida los datos básicos de la migración."""
        if isinstance(self.numero, bool) or not isinstance(self.numero, int):
            raise TypeError("numero debe ser un entero.")
        if self.numero < 1:
            raise ValueError("El número de migración debe ser positivo.")
        if not isinstance(self.nombre, str) or not self.nombre.strip():
            raise ValueError("El nombre de la migración no puede estar vacío.")
        if isinstance(self.sentencias, (str, bytes)):
            raise TypeError("sentencias debe ser una secuencia de sentencias SQL.")
        if not self.sentencias:
            raise ValueError("Una migración debe contener al menos una sentencia.")


# La primera migración contiene el esquema público de la aplicación.  Las
# columnas de fecha se guardan como texto ISO-8601 UTC para no perder la zona
# horaria ni depender de la configuración regional de la máquina.
MIGRACION_INICIAL: Final[Migracion] = Migracion(
    numero=1,
    nombre="esquema_documentos_y_bitacora",
    sentencias=(
        """
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            hash_url TEXT NOT NULL UNIQUE,
            dominio TEXT NOT NULL,
            categoria TEXT NOT NULL,
            ruta_relativa TEXT NOT NULL,
            tamano_texto_bytes INTEGER NOT NULL,
            hash_contenido TEXT NOT NULL,
            codigo_http INTEGER,
            profundidad INTEGER NOT NULL,
            fecha_descarga TIMESTAMP NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            fecha_revisitacion TIMESTAMP,
            CHECK (tamano_texto_bytes >= 0),
            CHECK (profundidad >= 0),
            CHECK (codigo_http IS NULL OR codigo_http >= 0)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS bitacora_recorrido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            dominio TEXT NOT NULL,
            url_origen TEXT,
            url_destino TEXT NOT NULL,
            tiempo_respuesta_ms REAL NOT NULL,
            codigo_http INTEGER,
            accion TEXT NOT NULL,
            CHECK (tiempo_respuesta_ms >= 0),
            CHECK (codigo_http IS NULL OR codigo_http >= 0),
            CHECK (length(trim(accion)) > 0)
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_hash_url ON documentos(hash_url)",
        "CREATE INDEX IF NOT EXISTS idx_doc_hash_contenido ON documentos(hash_contenido)",
        "CREATE INDEX IF NOT EXISTS idx_doc_dominio ON documentos(dominio)",
        "CREATE INDEX IF NOT EXISTS idx_doc_fecha ON documentos(fecha_descarga)",
        "CREATE INDEX IF NOT EXISTS idx_bitacora_dominio ON bitacora_recorrido(dominio)",
        "CREATE INDEX IF NOT EXISTS idx_bitacora_fecha ON bitacora_recorrido(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_bitacora_accion ON bitacora_recorrido(accion)",
    ),
)

MIGRACIONES: Final[tuple[Migracion, ...]] = (MIGRACION_INICIAL,)
"""Migraciones conocidas, ordenadas por número."""


class ConexionSQLite:
    """Envoltorio con ciclo de vida explícito para una conexión SQLite.

    La instancia abre la conexión durante su construcción.  ``with
    ConexionSQLite(ruta)`` devuelve la misma instancia y la cierra al salir,
    por lo que las tuberías no necesitan administrar manualmente el recurso::

        with ConexionSQLite("almacenamiento/datos.db") as conexion:
            repositorio = RepositorioDocumentos(conexion)
            repositorio.guardar_documento(metadato)

    Para varios procesos, SQLite serializa las operaciones de escritura mediante
    ``BEGIN IMMEDIATE`` y ``busy_timeout``.  Las transacciones anidadas se
    implementan con ``SAVEPOINT`` para que un repositorio pueda reutilizar otro
    sin que SQLite genere la cadena ``cannot start a transaction within a
    transaction``.
    """

    __slots__ = (
        "_ruta",
        "_conexion_sqlite",
        "_timeout",
        "_busy_timeout_ms",
        "_check_same_thread",
        "_lock",
        "_migraciones",
        "_cerrada",
        "_profundidad_transaccion",
    )

    def __init__(
        self,
        ruta: str | os.PathLike[str] = RUTA_POR_DEFECTO,
        *,
        ruta_base_datos: str | os.PathLike[str] | None = None,
        timeout: float = 5.0,
        busy_timeout_ms: int = TIEMPO_ESPERA_POR_DEFECTO_MS,
        busy_timeout: int | None = None,
        check_same_thread: bool = False,
        migraciones: Sequence[Migracion] | None = None,
        inicializar: bool = True,
    ) -> None:
        """Abre y, salvo que se desactive, inicializa una base de datos.

        Args:
            ruta: Ruta relativa o absoluta del archivo SQLite.  ``:memory:``
                crea una base temporal en memoria.
            ruta_base_datos: Alias de ``ruta`` para factorías que usan el
                nombre del guion de inicialización.
            timeout: Tiempo que SQLite utiliza al esperar un bloqueo.
            busy_timeout_ms: Valor explícito de ``PRAGMA busy_timeout``.
            busy_timeout: Alias de ``busy_timeout_ms``.
            check_same_thread: Se deja en ``False`` para facilitar su uso desde
                componentes de Scrapy; la instancia sigue sin permitir uso
                concurrente sin serialización externa.
            migraciones: Secuencia alternativa de migraciones, útil para
                pruebas o para una aplicación que quiera una copia versionada.
            inicializar: Si es ``False``, solo se abre y configura la conexión.

        Raises:
            ValueError: Si los tiempos de espera no son válidos.
            OSError: Si no se puede crear el directorio o abrir el archivo.
            sqlite3.Error: Si SQLite no puede configurar o migrar la base.
        """
        if ruta_base_datos is not None:
            ruta = ruta_base_datos
        if busy_timeout is not None:
            busy_timeout_ms = busy_timeout
        self._ruta = self._normalizar_ruta(ruta)
        self._timeout = self._validar_timeout(timeout)
        self._busy_timeout_ms = self._validar_busy_timeout(busy_timeout_ms)
        self._check_same_thread = check_same_thread
        self._lock = threading.RLock()
        self._migraciones = self._validar_migraciones(
            MIGRACIONES if migraciones is None else migraciones
        )
        self._cerrada = False
        self._profundidad_transaccion = 0

        if not self._es_memoria():
            # La ruta se conserva relativa para que el proyecto sea portable;
            # sólo se normaliza la tilde del usuario y se crean sus directorios.
            self._ruta.parent.mkdir(parents=True, exist_ok=True)

        destino = ":memory:" if self._es_memoria() else str(self._ruta)
        self._conexion_sqlite = sqlite3.connect(
            destino,
            timeout=self._timeout,
            isolation_level=None,
            check_same_thread=self._check_same_thread,
        )
        self._conexion_sqlite.row_factory = sqlite3.Row
        try:
            self._configurar()
            if inicializar:
                self.inicializar()
        except Exception:
            # No se deja una conexión parcialmente configurada si falla el
            # arranque.  El error original se propaga sin ocultarlo.
            self._conexion_sqlite.close()
            self._cerrada = True
            raise

    @staticmethod
    def _normalizar_ruta(ruta: str | os.PathLike[str]) -> Path:
        """Normaliza una ruta sin convertir relatives en absolutas."""
        if not isinstance(ruta, (str, os.PathLike)):
            raise TypeError("La ruta de SQLite debe ser str o pathlib.Path.")
        if isinstance(ruta, str) and ruta == ":memory:":
            return Path(":memory:")
        return Path(ruta).expanduser()

    @staticmethod
    def _validar_timeout(timeout: float) -> float:
        """Valida el timeout de conexión."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout debe ser un número.")
        valor = float(timeout)
        if not math.isfinite(valor) or valor <= 0:
            raise ValueError("timeout debe ser un número positivo y finito.")
        return valor

    @staticmethod
    def _validar_busy_timeout(busy_timeout_ms: int) -> int:
        """Valida ``busy_timeout`` en milisegundos."""
        if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
            raise TypeError("busy_timeout_ms debe ser un entero.")
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms no puede ser negativo.")
        return busy_timeout_ms

    @staticmethod
    def _validar_migraciones(migraciones: Sequence[Migracion]) -> tuple[Migracion, ...]:
        """Comprueba que las migraciones estén ordenadas y sin duplicados."""
        if isinstance(migraciones, (str, bytes)):
            raise TypeError("migraciones debe ser una secuencia de Migracion.")
        resultado = tuple(migraciones)
        if not resultado:
            raise ValueError("Debe existir al menos una migración.")
        for migracion in resultado:
            if not isinstance(migracion, Migracion):
                raise TypeError("Cada elemento debe ser una Migracion.")
        numeros = [migracion.numero for migracion in resultado]
        if numeros != sorted(numeros) or len(set(numeros)) != len(numeros):
            raise ValueError("Las migraciones deben estar ordenadas y ser únicas.")
        return resultado

    def _es_memoria(self) -> bool:
        """Indica si la ruta representa una base en memoria."""
        return str(self._ruta) == ":memory:"

    def _configurar(self) -> None:
        """Aplica los PRAGMA de seguridad, concurrencia y portabilidad."""
        # PRAGMA no admite parámetros vinculados.  busy_timeout se valida como
        # entero en __init__, por lo que su interpolación es segura.
        self._conexion_sqlite.execute("PRAGMA foreign_keys = ON")
        self._conexion_sqlite.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms:d}")
        # WAL no está disponible para una conexión :memory:, donde SQLite
        # informa legítimamente el modo memory; el resto de bases de archivo
        # deben quedar en WAL.
        modo = self._conexion_sqlite.execute("PRAGMA journal_mode = WAL").fetchone()
        if not self._es_memoria() and (modo is None or str(modo[0]).lower() != "wal"):
            modo_recibido = "desconocido" if modo is None else str(modo[0])
            raise sqlite3.OperationalError(
                f"No fue posible habilitar WAL; SQLite informó {modo_recibido!r}"
            )
        self._conexion_sqlite.execute("PRAGMA synchronous = NORMAL")

    @property
    def ruta(self) -> Path:
        """Ruta configurada; las rutas relativas se mantienen relativas."""
        with self._lock:
            self._asegurar_abierta()
            return self._ruta

    @property
    def ruta_db(self) -> Path:
        """Alias de :attr:`ruta` para consumidores de la factoría."""
        return self.ruta

    @property
    def path(self) -> Path:
        """Alias en inglés de :attr:`ruta`."""
        return self.ruta

    @property
    def conexion(self) -> sqlite3.Connection:
        """Conexión SQLite subyacente para operaciones avanzadas."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite

    @property
    def row_factory(self) -> Any:
        """Devuelve la fábrica de filas configurada (``sqlite3.Row``)."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite.row_factory

    @property
    def connection(self) -> sqlite3.Connection:
        """Alias en inglés para consumidores que snake_case usan ``connection``."""
        return self.conexion

    @property
    def raw_connection(self) -> sqlite3.Connection:
        """Alias explícito de la conexión SQLite subyacente."""
        return self.conexion

    @property
    def con(self) -> sqlite3.Connection:
        """Alias breve de la conexión SQLite subyacente."""
        return self.conexion

    @property
    def in_transaction(self) -> bool:
        """Indica si la conexión SQLite tiene una transacción activa."""
        with self._lock:
            self._asegurar_abierta()
            return bool(self._conexion_sqlite.in_transaction)

    @property
    def cerrada(self) -> bool:
        """Indica si el recurso ya fue cerrado."""
        return self._cerrada

    @property
    def modo_journal(self) -> str:
        """Devuelve el modo de journal actualmente configurado."""
        with self._lock:
            self._asegurar_abierta()
            fila = self._conexion_sqlite.execute("PRAGMA journal_mode").fetchone()
            if fila is None:
                return ""
            return str(fila[0]).lower()

    @property
    def version_esquema(self) -> int:
        """Devuelve la versión del esquema aplicada."""
        with self._lock:
            self._asegurar_abierta()
            fila = self._conexion_sqlite.execute("PRAGMA user_version").fetchone()
            return int(fila[0]) if fila is not None else 0

    @property
    def foreign_keys_activas(self) -> bool:
        """Indica si SQLite tiene activadas las claves foráneas."""
        with self._lock:
            self._asegurar_abierta()
            fila = self._conexion_sqlite.execute("PRAGMA foreign_keys").fetchone()
            return bool(fila[0]) if fila is not None else False

    @property
    def journal_mode(self) -> str:
        """Alias en inglés de :attr:`modo_journal`."""
        return self.modo_journal

    @property
    def foreign_keys(self) -> bool:
        """Alias booleano de :attr:`foreign_keys_activas`."""
        return self.foreign_keys_activas

    @property
    def version(self) -> int:
        """Alias corto de :attr:`version_esquema`."""
        return self.version_esquema

    @property
    def user_version(self) -> int:
        """Alias del PRAGMA ``user_version``."""
        return self.version_esquema

    @property
    def busy_timeout_ms(self) -> int:
        """Valor efectivo de ``PRAGMA busy_timeout`` para esta conexión."""
        fila = self.fetchone("PRAGMA busy_timeout")
        if fila is None:
            return self._busy_timeout_ms
        return int(fila[0])

    @property
    def timeout(self) -> float:
        """Timeout configurado para el bloqueo inicial de SQLite."""
        return self._timeout

    @property
    def busy_timeout(self) -> int:
        """Alias en milisegundos de :attr:`busy_timeout_ms`."""
        return self.busy_timeout_ms

    def _asegurar_abierta(self) -> None:
        """Lanza una excepción clara si se usa una conexión cerrada."""
        if self._cerrada:
            raise sqlite3.ProgrammingError("La conexión SQLite está cerrada.")

    @contextmanager
    def conectar(self) -> Iterator[sqlite3.Connection]:
        """Entrega la conexión SQLite nativa durante un contexto.

        ``ConexionSQLite`` ya mantiene una conexión abierta, por lo que este
        método no crea una segunda conexión ni cambia el modo WAL.  Es un
        adaptador de compatibilidad para componentes que necesitan la API
        ``sqlite3.Connection`` (por ejemplo, ``with conexion.conectar() as
        db: db.execute(...)``).  El context manager principal ``with
        ConexionSQLite(...)`` sigue devolviendo el envoltorio POO.
        """
        with self._lock:
            self._asegurar_abierta()
            try:
                yield self._conexion_sqlite
            except BaseException:
                if self._conexion_sqlite.in_transaction:
                    self._conexion_sqlite.rollback()
                raise

    def execute(self, consulta: str, parametros: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Alias de :meth:`ejecutar` para interoperar con sqlite3."""
        return self.ejecutar(consulta, parametros)

    def executemany(
        self,
        consulta: str,
        parametros: Sequence[Sequence[Any]],
    ) -> sqlite3.Cursor:
        """Alias de :meth:`ejecutar_muchos` para interoperar con sqlite3."""
        return self.ejecutar_muchos(consulta, parametros)

    def commit(self) -> None:
        """Alias de :meth:`confirmar` para interoperar con sqlite3."""
        self.confirmar()

    def rollback(self) -> None:
        """Alias de :meth:`revertir` para interoperar con sqlite3."""
        self.revertir()

    def inicializar(self) -> int:
        """Aplica migraciones pendientes de forma segura y serializada."""
        with self._lock:
            return self._inicializar_bloqueada()

    def _inicializar_bloqueada(self) -> int:
        """Implementa :meth:`inicializar` con el lock de la conexión."""
        self._asegurar_abierta()
        if self._profundidad_transaccion:
            raise sqlite3.OperationalError(
                "No se puede migrar el esquema dentro de una transacción abierta."
            )

        # Esta tabla se crea fuera de la transacción de migración para que el
        # registro de versiones exista incluso para una base recién vacía.
        self._conexion_sqlite.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                nombre TEXT NOT NULL,
                aplicada_en TEXT NOT NULL
            )
            """
        )

        with self.transaccion(inmediata=True):
            aplicadas = {
                int(fila["version"])
                for fila in self._conexion_sqlite.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            versiones_conocidas = {migracion.numero for migracion in self._migraciones}
            versiones_desconocidas = aplicadas - versiones_conocidas
            if versiones_desconocidas:
                raise sqlite3.OperationalError(
                    "La base contiene migraciones desconocidas: "
                    + ", ".join(str(version) for version in sorted(versiones_desconocidas))
                )
            if self.version_esquema > max(versiones_conocidas):
                raise sqlite3.OperationalError(
                    "La versión del esquema es más reciente que esta versión del programa: "
                    f"{self.version_esquema} > {max(versiones_conocidas)}"
                )

            for migracion in self._migraciones:
                if migracion.numero in aplicadas:
                    continue
                # Si una base fue preparada con user_version pero no tiene el
                # registro de control, la migración aún se aplica: el DDL usa
                # IF NOT EXISTS y es seguro para el esquema ya existente.
                for sentencia in migracion.sentencias:
                    self._conexion_sqlite.execute(sentencia)
                self._conexion_sqlite.execute(
                    """
                    INSERT INTO schema_migrations (version, nombre, aplicada_en)
                    VALUES (?, ?, ?)
                    """,
                    (
                        migracion.numero,
                        migracion.nombre,
                        self._ahora_utc().isoformat(),
                    ),
                )
                # PRAGMA no admite marcadores.  El número proviene de una
                # Migracion validada y se interpola como entero.
                self._conexion_sqlite.execute(f"PRAGMA user_version = {migracion.numero:d}")
                aplicadas.add(migracion.numero)

            # Reconstruye el PRAGMA si una copia de la base perdió esa marca
            # pero conserva el registro de control; nunca se reduce una
            # versión mayor desconocida.
            version_aplicada = max(aplicadas, default=0)
            if self.version_esquema < version_aplicada:
                self._conexion_sqlite.execute(f"PRAGMA user_version = {version_aplicada:d}")

        return self.version_esquema

    def assured_initialized(self) -> None:
        """Inicializa el esquema de forma idempotente para una factoría."""
        self.inicializar()

    def asegurar_inicializada(self) -> None:
        """Alias en español de :meth:`assured_initialized`."""
        self.inicializar()

    def ensure_initialized(self) -> None:
        """Alias en inglés de :meth:`assured_initialized`."""
        self.inicializar()

    def close(self) -> None:
        """Alias en inglés de :meth:`cerrar`."""
        self.cerrar()

    def esta_inicializada(self) -> bool:
        """Indica si las tablas públicas y la tabla de control existen."""
        with self._lock:
            self._asegurar_abierta()
            fila = self._conexion_sqlite.execute(
                """
                SELECT COUNT(*) AS total
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('documentos', 'bitacora_recorrido', 'schema_migrations')
                """
            ).fetchone()
            return fila is not None and int(fila["total"]) == 3

    @staticmethod
    def _ahora_utc() -> datetime:
        """Devuelve la hora actual con zona horaria UTC."""
        return datetime.now(UTC)

    def ejecutar(
        self,
        consulta: str,
        parametros: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        """Ejecuta SQL parametrizado y devuelve el cursor nativo."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite.execute(consulta, tuple(parametros))

    def ejecutar_muchos(
        self,
        consulta: str,
        parametros: Sequence[Sequence[Any]],
    ) -> sqlite3.Cursor:
        """Ejecuta una sentencia parametrizada para varios conjuntos de datos."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite.executemany(consulta, parametros)

    def fetchone(
        self,
        consulta: str,
        parametros: Sequence[Any] = (),
    ) -> sqlite3.Row | None:
        """Ejecuta una consulta y devuelve su primera fila."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite.execute(consulta, tuple(parametros)).fetchone()

    def fetchall(
        self,
        consulta: str,
        parametros: Sequence[Any] = (),
    ) -> list[sqlite3.Row]:
        """Ejecuta una consulta y devuelve todas sus filas."""
        with self._lock:
            self._asegurar_abierta()
            return self._conexion_sqlite.execute(consulta, tuple(parametros)).fetchall()

    @contextmanager
    def transaccion(self, *, inmediata: bool = False) -> Iterator[ConexionSQLite]:
        """Context manager para una transacción atómica.

        La transacción exterior usa ``BEGIN IMMEDIATA`` cuando se solicita (es
        el modo que necesitan las operaciones de deduplicación).  Las
        transacciones anidadas usan savepoints y liberan o revierten el
        savepoint correspondiente.
        """
        with self._lock:
            self._asegurar_abierta()
            es_externa = self._profundidad_transaccion == 0
            nombre_savepoint = f"sp_{self._profundidad_transaccion + 1}"
            if es_externa:
                self._conexion_sqlite.execute("BEGIN IMMEDIATE" if inmediata else "BEGIN")
            else:
                self._conexion_sqlite.execute(f"SAVEPOINT {nombre_savepoint}")
            self._profundidad_transaccion += 1
            try:
                yield self
            except BaseException:
                self._profundidad_transaccion -= 1
                if es_externa:
                    with suppress(sqlite3.Error):
                        self._conexion_sqlite.execute("ROLLBACK")
                else:
                    with suppress(sqlite3.Error):
                        self._conexion_sqlite.execute(f"ROLLBACK TO SAVEPOINT {nombre_savepoint}")
                    with suppress(sqlite3.Error):
                        self._conexion_sqlite.execute(f"RELEASE SAVEPOINT {nombre_savepoint}")
                raise
            else:
                self._profundidad_transaccion -= 1
                try:
                    if es_externa:
                        self._conexion_sqlite.execute("COMMIT")
                    else:
                        self._conexion_sqlite.execute(f"RELEASE SAVEPOINT {nombre_savepoint}")
                except BaseException:
                    if self._conexion_sqlite.in_transaction:
                        with suppress(sqlite3.Error):
                            self._conexion_sqlite.execute("ROLLBACK")
                    raise

    def confirmar(self) -> None:
        """Confirma la transacción actual, si existe."""
        with self._lock:
            self._asegurar_abierta()
            if self._profundidad_transaccion:
                raise sqlite3.OperationalError("Hay una transacción context manager abierta.")
            if self._conexion_sqlite.in_transaction:
                self._conexion_sqlite.execute("COMMIT")

    def revertir(self) -> None:
        """Revierte la transacción actual, si existe."""
        with self._lock:
            self._asegurar_abierta()
            if self._profundidad_transaccion:
                raise sqlite3.OperationalError("Hay una transacción context manager abierta.")
            if self._conexion_sqlite.in_transaction:
                self._conexion_sqlite.execute("ROLLBACK")

    def cerrar(self) -> None:
        """Cierra la conexión de forma idempotente."""
        with self._lock:
            if self._cerrada:
                return
            if self._profundidad_transaccion:
                with suppress(sqlite3.Error):
                    self._conexion_sqlite.execute("ROLLBACK")
                self._profundidad_transaccion = 0
            self._conexion_sqlite.close()
            self._cerrada = True

    def __enter__(self) -> ConexionSQLite:
        """Activa el protocolo de context manager."""
        with self._lock:
            self._asegurar_abierta()
            return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Cierra la conexión al salir del context manager."""
        self.cerrar()

    def __del__(self) -> None:
        """Libera el recurso si el objeto se recoge durante la intérprete."""
        with suppress(AttributeError, sqlite3.Error):
            self.cerrar()

    def __repr__(self) -> str:
        """Representación breve para diagnóstico."""
        estado = "cerrada" if self._cerrada else "abierta"
        return f"ConexionSQLite(ruta={str(self._ruta)!r}, {estado})"


# Alias corto para código que ya usaba el nombre ``Conexion``.
Conexion = ConexionSQLite

__all__ = [
    "Conexion",
    "ConexionSQLite",
    "MIGRACION_INICIAL",
    "MIGRACIONES",
    "Migracion",
    "RUTA_POR_DEFECTO",
    "TIEMPO_ESPERA_POR_DEFECTO_MS",
]
