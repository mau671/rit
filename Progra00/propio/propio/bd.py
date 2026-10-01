"""Persistencia en SQLite con el MISMO esquema que la versión con Scrapy.

Las tablas ``documentos``, ``bitacora_recorrido``, ``frontera`` y
``schema_migrations`` son idénticas a las migraciones de
``arañador/arañador/bibliotecas/sqlite.py``. Por eso una base creada aquí la
puede abrir el código de Scrapy y viceversa, y los guiones de informe funcionan
sobre ambas.

La tabla ``frontera`` es la cola de URL pendientes compartida: las dos
implementaciones escriben y leen la misma tabla (misma clave ``hash_url``), así
que una puede continuar donde la otra quedó.

Concurrencia: una sola conexión compartida por todos los hilos, protegida con
un ``threading.Lock``. SQLite en modo WAL permite leer mientras se escribe, y
``BEGIN IMMEDIATE`` hace que la comprobación de duplicados y la inserción sean
atómicas.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .configuracion import DIAS_REVISITA, RUTA_BASE_DATOS
from .generador_hash import hash_url

VERSION_ESQUEMA = 2
NOMBRE_MIGRACION = "esquema_documentos_bitacora_frontera"

# Copiado sin cambios de MIGRACION_INICIAL en arañador/bibliotecas/sqlite.py.
SENTENCIAS_ESQUEMA: tuple[str, ...] = (
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
)

# Cola de URL pendientes compartida con la versión con Scrapy. El DDL es parte
# del contrato congelado entre ambos paquetes: no cambiarlo sin cambiar el otro.
SENTENCIAS_FRONTERA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS frontera (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        hash_url       TEXT NOT NULL UNIQUE,
        url            TEXT NOT NULL,
        profundidad    INTEGER NOT NULL,
        prioridad      REAL NOT NULL,
        url_origen     TEXT,
        estado         TEXT NOT NULL DEFAULT 'pendiente',
        intentos       INTEGER NOT NULL DEFAULT 0,
        actualizada_en TIMESTAMP NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        CHECK (profundidad >= 0),
        CHECK (intentos >= 0),
        CHECK (estado IN ('pendiente', 'visitada', 'error'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_frontera_cola ON frontera(estado, prioridad DESC, id ASC)",
)

# Vocabulario de la columna ``accion`` compartido con el intermediario de Scrapy.
ACCION_RESPUESTA = "RESPUESTA"
ACCION_ERROR = "ERROR"
ACCION_ALMACENADO = "ALMACENADO"
ACCION_REVISITADO = "REVISITADO"
ACCION_DUPLICADO_URL = "DUPLICADO_URL"
ACCION_DUPLICADO_CONTENIDO = "DUPLICADO_CONTENIDO"
ACCION_BAJA_DENSIDAD = "BAJA_DENSIDAD"
# Acciones propias: en Scrapy estos casos los resuelve el marco sin registrarlos.
ACCION_BLOQUEADO_ROBOTS = "BLOQUEADO_ROBOTS"
ACCION_NOINDEX = "NOINDEX"
ACCION_NO_HTML = "NO_HTML"


def _leer_fecha(valor: object) -> datetime:
    """Lee una fecha ISO guardada en SQLite (acepta el sufijo ``Z``)."""

    fecha = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def ahora_iso() -> str:
    """Instante actual en ISO-8601 UTC, mismo formato que la versión con Scrapy."""

    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Documento:
    """Metadatos de un documento; mismos campos que ``MetadatoDocumento``."""

    url: str
    hash_url: str
    dominio: str
    categoria: str
    ruta_relativa: str
    tamano_texto_bytes: int
    hash_contenido: str
    codigo_http: int | None
    profundidad: int


@dataclass(frozen=True, slots=True)
class EntradaFrontera:
    """Una URL pendiente leída de la tabla ``frontera``."""

    hash_url: str
    url: str
    profundidad: int
    prioridad: float
    url_origen: str | None


class RepositorioFrontera:
    """Cola persistente de URL pendientes compartida con la versión con Scrapy.

    Recibe la conexión y el lock de :class:`BaseDatos` para reutilizar la misma
    conexión segura entre hilos. La clave natural es ``hash_url``, la huella
    que produce :func:`propio.generador_hash.hash_url`, y el orden de la cola es
    ``prioridad DESC, id ASC`` con ``prioridad = -profundidad``: recorrido en
    amplitud (menor profundidad primero) y FIFO a igual profundidad.
    """

    def __init__(self, conexion: sqlite3.Connection, lock: threading.Lock) -> None:
        self._con = conexion
        self._lock = lock

    def registrar(self, url: str, profundidad: int, url_origen: str | None = None) -> str:
        """Inserta o actualiza una URL en la frontera y la deja ``pendiente``.

        Devuelve su ``hash_url``. Si la fila no existía, se crea con
        ``intentos = 0``. Si ya estaba ``pendiente`` se actualiza su prioridad
        pero se conserva el ``id`` (FIFO); si estaba ``visitada`` o en
        ``error``, se resucita para una revisita y el contador de intentos
        vuelve a cero.
        """

        clave = hash_url(url)
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                self._con.execute(
                    """
                    INSERT INTO frontera (
                        hash_url, url, profundidad, prioridad, url_origen,
                        estado, intentos, actualizada_en
                    ) VALUES (?, ?, ?, ?, ?, 'pendiente', 0, ?)
                    ON CONFLICT(hash_url) DO UPDATE SET
                        url = excluded.url,
                        profundidad = excluded.profundidad,
                        prioridad = excluded.prioridad,
                        url_origen = COALESCE(excluded.url_origen, frontera.url_origen),
                        estado = CASE
                            WHEN frontera.estado IN ('visitada', 'error')
                                THEN 'pendiente'
                            ELSE frontera.estado
                        END,
                        intentos = CASE
                            WHEN frontera.estado IN ('visitada', 'error') THEN 0
                            ELSE frontera.intentos
                        END,
                        actualizada_en = excluded.actualizada_en
                    """,
                    (
                        clave,
                        url,
                        profundidad,
                        -float(profundidad),
                        url_origen,
                        ahora_iso(),
                    ),
                )
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
        return clave

    def marcar_visitada(self, hash_url: str) -> bool:
        """Marca la fila como ``visitada``. Devuelve si existía."""

        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._con.execute(
                    "UPDATE frontera SET estado = 'visitada', actualizada_en = ? "
                    "WHERE hash_url = ?",
                    (ahora_iso(), hash_url),
                )
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
        return cursor.rowcount > 0

    def marcar_error(self, hash_url: str, max_intentos: int = 3) -> bool:
        """Suma un intento fallido y decide el estado final.

        Con ``intentos < max_intentos`` la fila queda en ``error`` (y sigue
        siendo elegible); al alcanzar el tope pasa a ``visitada`` para no
        reintentarla indefinidamente. Devuelve si la fila existía.
        """

        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._con.execute(
                    """
                    UPDATE frontera SET
                        intentos = intentos + 1,
                        estado = CASE
                            WHEN intentos + 1 < ? THEN 'error' ELSE 'visitada'
                        END,
                        actualizada_en = ?
                    WHERE hash_url = ?
                    """,
                    (max_intentos, ahora_iso(), hash_url),
                )
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
        return cursor.rowcount > 0

    def pendientes(self, max_intentos: int = 3) -> list[EntradaFrontera]:
        """URL elegibles en orden BFS: ``prioridad DESC, id ASC``."""

        with self._lock:
            filas = self._con.execute(
                """
                SELECT hash_url, url, profundidad, prioridad, url_origen
                FROM frontera
                WHERE estado = 'pendiente' OR (estado = 'error' AND intentos < ?)
                ORDER BY prioridad DESC, id ASC
                """,
                (max_intentos,),
            ).fetchall()
        return [
            EntradaFrontera(
                hash_url=fila["hash_url"],
                url=fila["url"],
                profundidad=fila["profundidad"],
                prioridad=fila["prioridad"],
                url_origen=fila["url_origen"],
            )
            for fila in filas
        ]

    def reconciliar(self, es_fresco: Callable[[str], bool], max_intentos: int = 3) -> int:
        """Descarta de la cola las URL que ``es_fresco`` marque como vigentes.

        ``es_fresco`` recibe el ``hash_url`` y devuelve ``True`` cuando la URL
        ya no necesita descargarse (por ejemplo,
        ``lambda h: not bd.necesita_revisita(h)``). Cada coincidencia se marca
        ``visitada`` y no se encola. Devuelve cuántas descartó.
        """

        descartadas = 0
        for entrada in self.pendientes(max_intentos):
            if es_fresco(entrada.hash_url):
                self.marcar_visitada(entrada.hash_url)
                descartadas += 1
        return descartadas


class BaseDatos:
    """Acceso seguro entre hilos a la base de metadatos y a la bitácora."""

    def __init__(self, ruta: Path = RUTA_BASE_DATOS) -> None:
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # isolation_level=None: las transacciones se abren a mano con BEGIN.
        self._con = sqlite3.connect(
            self.ruta, timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        self._con.execute("PRAGMA busy_timeout = 5000")
        self._con.execute("PRAGMA journal_mode = WAL")
        self._con.execute("PRAGMA synchronous = NORMAL")
        self.inicializar()
        self._frontera = RepositorioFrontera(self._con, self._lock)

    @property
    def frontera(self) -> RepositorioFrontera:
        """Cola persistente de URL pendientes (compartida con Scrapy)."""

        return self._frontera

    # --- Esquema -----------------------------------------------------------
    def inicializar(self) -> None:
        """Crea el esquema si falta. Es idempotente.

        Garantiza la tabla ``frontera`` incluso sobre una base creada con el
        esquema v1, sin importar si la creó ``propio`` o la versión con Scrapy.
        """

        with self._lock:
            self._con.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    aplicada_en TEXT NOT NULL
                )
                """
            )
            self._con.execute("BEGIN IMMEDIATE")
            try:
                fila = self._con.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION_ESQUEMA,)
                ).fetchone()
                if fila is None:
                    for sentencia in SENTENCIAS_ESQUEMA:
                        self._con.execute(sentencia)
                # La tabla de frontera se asegura siempre: una base v1 puede
                # venir de cualquiera de las dos implementaciones.
                for sentencia in SENTENCIAS_FRONTERA:
                    self._con.execute(sentencia)
                self._con.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, nombre, aplicada_en) "
                    "VALUES (?, ?, ?)",
                    (VERSION_ESQUEMA, NOMBRE_MIGRACION, ahora_iso()),
                )
                self._con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA:d}")
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise

    # --- Consultas de la política de revisitación -------------------------
    def existe_hash_url(self, hash_url: str) -> bool:
        with self._lock:
            return (
                self._con.execute(
                    "SELECT 1 FROM documentos WHERE hash_url = ? LIMIT 1", (hash_url,)
                ).fetchone()
                is not None
            )

    def existe_hash_contenido(self, hash_contenido: str) -> bool:
        with self._lock:
            return (
                self._con.execute(
                    "SELECT 1 FROM documentos WHERE hash_contenido = ? LIMIT 1",
                    (hash_contenido,),
                ).fetchone()
                is not None
            )

    def necesita_revisita(self, hash_url: str) -> bool:
        """``True`` si la URL nunca se guardó o su fecha de revisita ya venció."""

        with self._lock:
            fila = self._con.execute(
                "SELECT fecha_revisitacion FROM documentos WHERE hash_url = ? LIMIT 1",
                (hash_url,),
            ).fetchone()
        if fila is None or fila["fecha_revisitacion"] is None:
            return True
        return _leer_fecha(fila["fecha_revisitacion"]) <= datetime.now(UTC)

    # --- Escrituras --------------------------------------------------------
    def guardar_documento(self, doc: Documento) -> tuple[str, str | None]:
        """Inserta o revisita un documento en una sola transacción.

        Devuelve ``(accion, ruta_anterior)``:

        * ``ALMACENADO``: URL nueva con contenido nuevo.
        * ``REVISITADO``: la URL ya existía y su fecha de revisita venció; la
          fila se actualiza. ``ruta_anterior`` trae el .txt viejo si el
          contenido cambió, para que quien llama lo borre.
        * ``DUPLICADO_URL``: la URL ya existe y todavía está fresca.
        * ``DUPLICADO_CONTENIDO``: el mismo texto ya está guardado con otra URL.
        """

        ahora = datetime.now(UTC)
        revisita = ahora + timedelta(days=DIAS_REVISITA)
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                existente = self._con.execute(
                    "SELECT id, ruta_relativa, hash_contenido, fecha_revisitacion "
                    "FROM documentos WHERE hash_url = ? OR url = ? LIMIT 1",
                    (doc.hash_url, doc.url),
                ).fetchone()

                if existente is not None:
                    vence = existente["fecha_revisitacion"]
                    if vence is not None and _leer_fecha(vence) > ahora:
                        self._con.execute("ROLLBACK")
                        return ACCION_DUPLICADO_URL, None
                    otro = self._con.execute(
                        "SELECT 1 FROM documentos WHERE hash_contenido = ? AND id <> ? LIMIT 1",
                        (doc.hash_contenido, existente["id"]),
                    ).fetchone()
                    if otro is not None:
                        self._con.execute("ROLLBACK")
                        return ACCION_DUPLICADO_CONTENIDO, None
                    self._con.execute(
                        """
                        UPDATE documentos SET
                            ruta_relativa = ?, tamano_texto_bytes = ?, hash_contenido = ?,
                            codigo_http = ?, fecha_descarga = ?, fecha_revisitacion = ?
                        WHERE id = ?
                        """,
                        (
                            doc.ruta_relativa,
                            doc.tamano_texto_bytes,
                            doc.hash_contenido,
                            doc.codigo_http,
                            ahora.isoformat(),
                            revisita.isoformat(),
                            existente["id"],
                        ),
                    )
                    self._con.execute("COMMIT")
                    cambio = existente["ruta_relativa"] != doc.ruta_relativa
                    return ACCION_REVISITADO, (existente["ruta_relativa"] if cambio else None)

                if self._con.execute(
                    "SELECT 1 FROM documentos WHERE hash_contenido = ? LIMIT 1",
                    (doc.hash_contenido,),
                ).fetchone():
                    self._con.execute("ROLLBACK")
                    return ACCION_DUPLICADO_CONTENIDO, None

                self._con.execute(
                    """
                    INSERT INTO documentos (
                        url, hash_url, dominio, categoria, ruta_relativa,
                        tamano_texto_bytes, hash_contenido, codigo_http, profundidad,
                        fecha_descarga, fecha_revisitacion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc.url,
                        doc.hash_url,
                        doc.dominio,
                        doc.categoria,
                        doc.ruta_relativa,
                        doc.tamano_texto_bytes,
                        doc.hash_contenido,
                        doc.codigo_http,
                        doc.profundidad,
                        ahora.isoformat(),
                        revisita.isoformat(),
                    ),
                )
                self._con.execute("COMMIT")
                return ACCION_ALMACENADO, None
            except Exception:
                self._con.execute("ROLLBACK")
                raise

    def registrar_bitacora(
        self,
        *,
        dominio: str,
        url_destino: str,
        accion: str,
        url_origen: str | None = None,
        tiempo_respuesta_ms: float = 0.0,
        codigo_http: int | None = None,
    ) -> None:
        """Agrega una entrada a ``bitacora_recorrido``."""

        with self._lock:
            self._con.execute(
                """
                INSERT INTO bitacora_recorrido (
                    timestamp, dominio, url_origen, url_destino,
                    tiempo_respuesta_ms, codigo_http, accion
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ahora_iso(),
                    dominio or "(desconocido)",
                    url_origen,
                    url_destino,
                    max(0.0, float(tiempo_respuesta_ms)),
                    codigo_http,
                    accion.strip().upper(),
                ),
            )

    # --- Progreso ----------------------------------------------------------
    def resumen(self) -> dict[str, int]:
        """Documentos guardados, bytes de texto y entradas de bitácora."""

        with self._lock:
            docs = self._con.execute(
                "SELECT COUNT(*), COALESCE(SUM(tamano_texto_bytes), 0) FROM documentos"
            ).fetchone()
            visitas = self._con.execute("SELECT COUNT(*) FROM bitacora_recorrido").fetchone()
        return {"documentos": docs[0], "bytes": docs[1], "bitacora": visitas[0]}

    def cerrar(self) -> None:
        with self._lock:
            self._con.close()
