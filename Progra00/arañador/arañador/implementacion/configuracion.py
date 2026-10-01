"""Configuración de políticas, descarga y procesamiento de Scrapy.

El módulo se integra con ``scrapy.cfg`` mediante el mecanismo estándar de
módulos de configuración de Scrapy. No depende de Django ni necesita que las
clases se registren en un framework externo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..entorno import leer_texto
from .utilidades.rutas import obtener_raiz_datos, resolver_ruta_datos


def _normalizar_ruta_configurada(valor: object) -> str:
    """Convierte rutas relativas de settings respecto a la raíz de datos."""

    texto = str(valor)
    if texto == ":memory:":
        return texto
    ruta = Path(texto).expanduser()
    if not ruta.is_absolute():
        ruta = obtener_raiz_datos() / ruta
    return str(ruta.resolve(strict=False))


def obtener_ruta_base_datos(settings: Any) -> str:
    """Resuelve una única base compartida por tuberías y middleware.

    ``RUTA_BASE_DATOS`` es la clave canónica. Las dos claves específicas se
    conservan como overrides; se aplica la precedencia numérica de Scrapy para
    que una configuración de CLI no separe accidentalmente los datos.
    """

    claves = (
        "RUTA_BASE_DATOS",
        "TUBERIA_RUTA_BASE_DATOS",
        "INTERMEDIARIO_RUTA_BASE_DATOS",
    )
    candidatas: list[tuple[int, int, str]] = []
    for indice, clave in enumerate(claves):
        valor = settings.get(clave)
        if valor is None or valor == "":
            continue
        prioridad = int(settings.getpriority(clave))
        candidatas.append((prioridad, indice, _normalizar_ruta_configurada(valor)))
    if candidatas:
        # Scrapy aplica el valor de menor prioridad numérica; el índice sólo
        # hace estable el desempate y favorece la clave canónica.
        return min(candidatas)[2]
    return str(resolver_ruta_datos("almacenamiento", "metadatos_araña.db"))


def obtener_ruta_repositorio(settings: Any) -> str:
    """Resuelve la raíz de texto con el mismo criterio de precedencia."""

    candidatas: list[tuple[int, int, str]] = []
    for indice, clave in enumerate(("TUBERIA_RUTA_REPOSITORIO", "RUTA_REPOSITORIO_TEXTO")):
        valor = settings.get(clave)
        if valor is None or valor == "":
            continue
        candidatas.append(
            (int(settings.getpriority(clave)), indice, _normalizar_ruta_configurada(valor))
        )
    if candidatas:
        return min(candidatas)[2]
    return str(resolver_ruta_datos("almacenamiento", "repositorio"))


def _obtener_nivel_log() -> str:
    """Normaliza el nivel de log y usa ``WARNING`` para valores desconocidos.

    El detalle por intento vive en la tabla ``bitacora_recorrido``; el log de
    texto se mantiene en ``WARNING`` para no crecer con una línea por petición.
    """

    nivel = leer_texto("LOG_LEVEL", "WARNING").upper()
    permitidos = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "FATAL"}
    return nivel if nivel in permitidos else "WARNING"


def _obtener_user_agent() -> str:
    """Devuelve un User-Agent configurable sin incluir credenciales reales.

    ``USER_AGENT`` permite definir la identificación de la ejecución. Por
    defecto se identifica como un robot académico respetuoso del curso RIT/IC-8060
    de conformidad con las recomendaciones de diseño y cortesía.
    """

    configurado = leer_texto("USER_AGENT")
    if configurado:
        return " ".join(configurado.split())
    return "AranadorAudiovisualBot/1.0 (+https://tec.ac.cr/ic8060)"


class ConfiguracionAranador:
    """Políticas globales del arañador audiovisual.

    Los atributos en mayúsculas son settings de Scrapy. Los nombres de clases
    declarados aquí usan rutas absolutas para que Scrapy pueda importarlos sin
    registrar módulos de terceros en tiempo de ejecución.
    """

    # Identidad del proyecto y descubrimiento de arañas.
    BOT_NAME = "arañador"
    SPIDER_MODULES = ["arañador.implementacion.aranas"]
    NEWSPIDER_MODULE = "arañador.implementacion.aranas"

    # Política de cortesía e identificación. ``ROBOTSTXT_OBEY`` es obligatorio:
    # Scrapy consulta robots.txt con ``ROBOTSTXT_USER_AGENT`` y no permite
    # rutas que el sitio declare prohibidas.
    ROBOTSTXT_OBEY = True
    USER_AGENT = _obtener_user_agent()
    ROBOTSTXT_USER_AGENT = USER_AGENT
    CONCURRENT_REQUESTS = 4
    CONCURRENT_REQUESTS_PER_DOMAIN = 4
    DOWNLOAD_DELAY = 0.5
    COOKIES_ENABLED = False
    AJAXCRAWL_ENABLED = False

    # Política de selección: profundidad y límite por respuesta.
    DEPTH_LIMIT = 4
    DOWNLOAD_MAXSIZE = 5 * 1024 * 1024
    DOWNLOAD_WARNSIZE = 4 * 1024 * 1024
    URLLENGTH_LIMIT = 2_048
    EXTENSIONES_EXCLUIDAS = (
        ".7z",
        ".aac",
        ".apk",
        ".avi",
        ".bmp",
        ".bz2",
        ".css",
        ".deb",
        ".doc",
        ".docx",
        ".dmg",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".iso",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".m4a",
        ".m4v",
        ".map",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".odp",
        ".ods",
        ".odt",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rar",
        ".svg",
        ".tar",
        ".tgz",
        ".tif",
        ".tiff",
        ".wav",
        ".webm",
        ".webp",
        ".wmv",
        ".xls",
        ".xlsx",
        ".zip",
    )

    # Tiempos, reintentos y redirecciones.
    DOWNLOAD_TIMEOUT = 45
    DEFAULT_REQUEST_TIMEOUT = 45
    RETRY_ENABLED = True
    RETRY_TIMES = 3
    RETRY_HTTP_CODES = (429, 500, 502, 503, 504, 522, 524)
    RETRY_EXCEPTIONS = (
        OSError,
        TimeoutError,
        "scrapy.core.downloader.handlers.http11.TunnelError",
        "twisted.internet.defer.TimeoutError",
        "twisted.web.client.ResponseFailed",
    )
    REDIRECT_ENABLED = True
    REDIRECT_MAX_TIMES = 15

    # AutoThrottle mantiene la concurrencia por dominio dentro de la política de
    # cortesía y aumenta la latencia cuando el servidor responde lentamente.
    AUTOTHROTTLE_ENABLED = True
    AUTOTHROTTLE_DEBUG = False
    AUTOTHROTTLE_START_DELAY = 0.5
    AUTOTHROTTLE_MAX_DELAY = 5.0
    AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
    AUTOTHROTTLE_DEBUG_DELAY = 1.0

    # La frontera (cola de URL pendientes) vive en la base SQLite compartida y
    # no en la cola propia de Scrapy: se desactivan JOBDIR y la persistencia del
    # planificador para no tener dos fuentes de verdad. La extensión y el
    # middleware de frontera conservan el avance entre ejecuciones.
    SCHEDULER_PERSIST = False
    EXTENSIONS = {
        "arañador.implementacion.extensiones.frontera_compartida.ExtensionFronteraCompartida": 500,
    }
    SPIDER_MIDDLEWARES = {
        "arañador.implementacion.extensiones.frontera_compartida.MiddlewareFronteraInicial": 500,
    }
    FRONTERA_SQLITE_TIMEOUT = 0.5
    FRONTERA_MAX_INTENTOS = 3

    # Deduplicación en memoria más filtro persistente de frescura. La segunda
    # capa consulta SQLite antes de descargar una URL ya procesada.
    DUPEFILTER_CLASS = "arañador.implementacion.filtro_duplicados.FiltroDuplicadosPersistentes"
    DUPEFILTER_SQLITE_TIMEOUT = 0.5
    REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

    # Tuberías del proyecto, en orden de extracción, validación, persistencia
    # y métricas. La implementación actual usa el procesador sincrónico de
    # Scrapy y serializa cada escritura con SQLite ``BEGIN IMMEDIATE``.
    ITEM_PIPELINES = {
        "arañador.implementacion.tuberias.tuberia_extraccion.TuberiaExtraccion": 100,
        "arañador.implementacion.tuberias.tuberia_validacion.TuberiaValidacion": 200,
        "arañador.implementacion.tuberias.tuberia_almacenamiento.TuberiaAlmacenamiento": 300,
        "arañador.implementacion.tuberias.tuberia_metricas.TuberiaMetricas": 900,
    }
    # Las rutas de datos se resuelven mediante funciones para que una
    # configuración específica de pipeline o middleware también sea compartida.
    TUBERIA_EXTRACCION_FAVOR_PRECISION = True
    TUBERIA_EXTRACCION_INCLUIR_COMENTARIOS = False
    TUBERIA_EXTRACCION_INCLUIR_TABLAS = False
    TUBERIA_LONGITUD_MINIMA = 300
    TUBERIA_DENSIDAD_MINIMA = 0.35
    TUBERIA_DIAS_REVISITACION = 30

    # El middleware de bitácora se activa después de reintentos y
    # redirecciones para registrar el resultado final de cada intento.
    DOWNLOADER_MIDDLEWARES = {
        "arañador.implementacion.intermediarios.intermediario_bitacora.IntermediarioBitacora": 875,
    }
    INTERMEDIARIO_SQLITE_TIMEOUT = 0.5
    INTERMEDIARIO_REINTENTO_SEGUNDOS = 30.0
    INTERMEDIARIO_BITACORA_OBLIGATORIA = True

    # Registro estándar de Scrapy. ``LOG_LEVEL`` se valida al importar
    # la configuración y un valor desconocido no interrumpe el rastreo.
    LOG_ENABLED = True
    LOG_LEVEL = _obtener_nivel_log()
    LOG_ENCODING = "utf-8"
    LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    LOG_DATEFORMAT = "%Y-%m-%dT%H:%M:%S%z"
    LOGSTATS_INTERVAL = 60
    TELNETCONSOLE_ENABLED = False
