"""Persistencia en SQLite con el MISMO esquema que la versión con Scrapy.

Las tablas ``documentos``, ``bitacora_recorrido`` y ``schema_migrations`` son
idénticas a la migración 1 de ``arañador/arañador/bibliotecas/sqlite.py``. Por
eso una base creada aquí la puede abrir el código de Scrapy y viceversa, y los
guiones de informe funcionan sobre ambas.

Concurrencia: una sola conexión compartida por todos los hilos, protegida con
un ``threading.Lock``. SQLite en modo WAL permite leer mientras se escribe, y
``BEGIN IMMEDIATE`` hace que la comprobación de duplicados y la inserción sean
atómicas.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .configuracion import DIAS_REVISITA, RUTA_BASE_DATOS

VERSION_ESQUEMA = 1
NOMBRE_MIGRACION = "esquema_documentos_y_bitacora"

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

    # --- Esquema -----------------------------------------------------------
    def inicializar(self) -> None:
        """Crea el esquema si falta. Es idempotente."""

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
                    self._con.execute(
                        "INSERT INTO schema_migrations (version, nombre, aplicada_en) "
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
