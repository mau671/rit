"""Configuración y políticas de la araña propia.

Los valores de política son los mismos que usa la versión con Scrapy
(``arañador/arañador/implementacion/configuracion.py``) para que la comparación
entre ambas implementaciones sea justa. El archivo ``.env`` se lee con un
analizador mínimo propio, sin ``python-dotenv``.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ_PROYECTO: Path = Path(__file__).resolve().parents[1]
ARCHIVO_ENV: Path = RAIZ_PROYECTO / ".env"


def cargar_env(ruta: Path = ARCHIVO_ENV) -> None:
    """Carga ``CLAVE=valor`` desde ``.env`` hacia ``os.environ``.

    Ignora líneas vacías y comentarios, y quita comillas simples o dobles.
    El archivo es la fuente autoritativa, igual que en la versión con Scrapy.
    """

    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ[clave.strip()] = valor


def leer_texto(nombre: str, predeterminado: str = "") -> str:
    return os.getenv(nombre, predeterminado).strip()


def leer_entero(nombre: str, predeterminado: int) -> int:
    try:
        return int(leer_texto(nombre))
    except ValueError:
        return predeterminado


def resolver_ruta(valor: str) -> Path:
    """Resuelve rutas relativas contra la carpeta ``propio/``."""

    ruta = Path(valor).expanduser()
    return (ruta if ruta.is_absolute() else RAIZ_PROYECTO / ruta).resolve(strict=False)


cargar_env()

# --- Rutas de datos (misma estructura que la versión con Scrapy) -------------
RAIZ_DATOS: Path = resolver_ruta(leer_texto("RAIZ_DATOS", "."))
RUTA_BASE_DATOS: Path = RAIZ_DATOS / "almacenamiento" / "metadatos_araña.db"
RUTA_REPOSITORIO: Path = RAIZ_DATOS / "almacenamiento" / "repositorio"
RUTA_LOG: Path = resolver_ruta(leer_texto("LOG_FILE", "bitacoras/recorrido.log"))
NIVEL_LOG: str = leer_texto("LOG_LEVEL", "INFO").upper()

# Semillas compartidas: una sola fuente de verdad para ambas arañas.
RUTA_SEMILLAS: Path = resolver_ruta(leer_texto("SEMILLAS", "../arañador/arañador/semillas.txt"))

# --- Política de identificación (cortesía) ----------------------------------
USER_AGENT: str = " ".join(
    leer_texto("USER_AGENT", "AranadorAudiovisualPropioBot/1.0 (+https://tec.ac.cr/ic8060)").split()
)

# --- Política de concurrencia y retardo (cortesía) --------------------------
HILOS: int = max(1, leer_entero("HILOS", 16))
MAX_SIMULTANEAS_POR_DOMINIO: int = 4
DEMORA_DESCARGA: float = 0.75  # segundos entre solicitudes al mismo dominio
DEMORA_MAXIMA: float = 5.0  # techo del retardo adaptativo

# --- Tiempos y reintentos ----------------------------------------------------
TIMEOUT_SEGUNDOS: int = 45
REINTENTOS: int = 3
CODIGOS_REINTENTO: frozenset[int] = frozenset({429, 500, 502, 503, 504, 522, 524})
MAX_REDIRECCIONES: int = 15

# --- Políticas de selección --------------------------------------------------
PROFUNDIDAD_MAXIMA: int = 4
TAMANO_MAXIMO_BYTES: int = 5 * 1024 * 1024
LONGITUD_MAXIMA_URL: int = 2_048
MINIMO_CARACTERES: int = 300
DENSIDAD_MINIMA: float = 0.35
CATEGORIA_DOCUMENTOS: str = "documentos"
EXTENSIONES_EXCLUIDAS: frozenset[str] = frozenset(
    {
        ".7z", ".aac", ".apk", ".avi", ".bmp", ".bz2", ".css", ".deb", ".doc",
        ".docx", ".dmg", ".exe", ".gif", ".gz", ".ico", ".iso", ".jpeg", ".jpg",
        ".js", ".json", ".m4a", ".m4v", ".map", ".mkv", ".mov", ".mp3", ".mp4",
        ".mpeg", ".mpg", ".odp", ".ods", ".odt", ".pdf", ".png", ".ppt", ".pptx",
        ".rar", ".svg", ".tar", ".tgz", ".tif", ".tiff", ".wav", ".webm", ".webp",
        ".wmv", ".xls", ".xlsx", ".zip",
    }
)  # fmt: skip

# --- Política de frescura (revisitación) -------------------------------------
DIAS_REVISITA: int = 30

# --- Meta de la cosecha --------------------------------------------------------
OBJETIVO_BYTES: int = max(1, leer_entero("OBJETIVO_GIB", 10)) * 1024**3
