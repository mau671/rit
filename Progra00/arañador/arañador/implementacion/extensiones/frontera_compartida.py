"""Frontera compartida como extensión y middleware de Scrapy.

La versión Scrapy deja de usar ``JOBDIR`` como cola propia y pasa a persistir
su frontera en la tabla ``frontera`` de la base SQLite compartida
(``almacenamiento/metadatos_araña.db``), con el mismo esquema que la versión
propia.  Así ambas implementaciones continúan el recorrido donde la otra lo
dejó.

Arquitectura:

* :class:`ExtensionFronteraCompartida` escucha las señales del motor y mantiene
  una única conexión durante la vida del crawler:
  ``request_scheduled`` registra cada URL agendada y ``response_received`` la
  marca como visitada.
* :class:`MiddlewareFronteraInicial` engancha el arranque de la araña
  (``process_start`` en Scrapy >= 2.13, con un ``process_start_requests``
  sincrónico de compatibilidad) para reconciliar la cola con la tabla de
  documentos y anteponer las ``Request`` pendientes a las solicitudes iniciales.
  Abre y cierra su propia conexión.

Señales verificadas en ``scrapy.signals`` (Scrapy 2.19.0):

* ``request_scheduled``: se emite con ``request`` y ``spider``.
* ``response_received``: se emite con ``response``, ``request`` y ``spider``.
* ``spider_closed``: se emite con ``spider`` y ``reason``.

El enganche de arranque de araña verificó que Scrapy 2.13 retiró
``process_start_requests`` en favor de ``process_start`` (generador asíncrono
que recibe el iterador de ``spider.start()``).  Se ofrecen ambas variantes: la
moderna es la que Scrapy usa y la sincrónica conserva compatibilidad y
facilita las pruebas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

from scrapy import Request, Spider, signals
from scrapy.crawler import Crawler
from scrapy.http import Response

from ...bibliotecas.sqlite import ConexionSQLite
from ..bd.frontera import EntradaFrontera, RepositorioFrontera
from ..bd.repositorio import RepositorioDocumentos
from ..configuracion import obtener_ruta_base_datos
from ..utilidades.generador_hash import generar_hash_url

__all__ = ["ExtensionFronteraCompartida", "MiddlewareFronteraInicial"]

#: Espera predeterminada por el bloqueo de SQLite, en segundos.
TIMEOUT_SQLITE_POR_DEFECTO: float = 0.5

#: Tope de intentos antes de dejar de reintentar una URL con error.
MAX_INTENTOS_POR_DEFECTO: int = 3


def _tiempo_espera(timeout_sqlite: float) -> int:
    """Convierte un timeout en segundos al ``busy_timeout`` en milisegundos."""
    return max(1, round(timeout_sqlite * 1_000))


def _profundidad_de(request: Request) -> int:
    """Extrae una profundidad válida de ``request.meta['depth']``.

    Scrapy guarda la profundidad como entero en ``meta``; una petición creada a
    mano puede no tenerla.  Un valor ausente, booleano o no numérico se trata
    como profundidad cero.
    """
    valor: object = request.meta.get("depth", 0) if request.meta else 0
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0
    numero = int(valor)
    return numero if numero >= 0 else 0


class ExtensionFronteraCompartida:
    """Extensión de Scrapy que mantiene la frontera SQLite compartida.

    Escucha ``request_scheduled`` para registrar cada URL y
    ``response_received`` para marcarla como visitada.  La conexión se cierra
    con ``spider_closed``.
    """

    def __init__(
        self,
        conexion: ConexionSQLite,
        repositorio: RepositorioFrontera,
    ) -> None:
        """Guarda la conexión y el repositorio ya abiertos."""
        self._conexion = conexion
        self._repositorio = repositorio
        self._cerrada = False

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> ExtensionFronteraCompartida:
        """Construye la extensión con la configuración efectiva del crawler."""
        ruta = obtener_ruta_base_datos(crawler.settings)
        timeout = crawler.settings.getfloat("FRONTERA_SQLITE_TIMEOUT", TIMEOUT_SQLITE_POR_DEFECTO)
        conexion = ConexionSQLite(
            ruta,
            timeout=timeout,
            busy_timeout_ms=_tiempo_espera(timeout),
        )
        try:
            repositorio = RepositorioFrontera(conexion)
        except BaseException:
            conexion.cerrar()
            raise
        instancia = cls(conexion, repositorio)
        instancia._conectar_senales(crawler)
        return instancia

    def _conectar_senales(self, crawler: Crawler) -> None:
        """Conecta los manejadores a las señales verificadas del motor."""
        crawler.signals.connect(self.agendar, signal=signals.request_scheduled)
        crawler.signals.connect(self.recibida, signal=signals.response_received)
        crawler.signals.connect(self.cerrar, signal=signals.spider_closed)

    def agendar(self, request: Request, spider: Spider | None = None, **_: Any) -> None:
        """Registra una solicitud agendada en la frontera compartida."""
        del spider
        self._repositorio.registrar(request.url, _profundidad_de(request))

    def recibida(
        self,
        response: Response,
        request: Request | None = None,
        spider: Spider | None = None,
        **_: Any,
    ) -> None:
        """Marca como visitada la URL de la respuesta recibida."""
        del spider
        solicitud = getattr(response, "request", None) or request
        if solicitud is None:
            return
        self._repositorio.marcar_visitada(generar_hash_url(solicitud.url))

    def cerrar(
        self,
        spider: Spider | None = None,
        reason: str | None = None,
        **_: Any,
    ) -> None:
        """Cierra la conexión de la extensión de forma idempotente."""
        del spider, reason
        if self._cerrada:
            return
        self._cerrada = True
        self._conexion.cerrar()


class MiddlewareFronteraInicial:
    """Antepone a las solicitudes iniciales las pendientes de la frontera.

    Antes de arrancar, reconcilia la cola con la tabla ``documentos``: descarta
    (marca ``visitada``) lo que ya está fresco y conserva lo revisitable.
    Después antepone las ``Request`` pendientes, con la profundidad original en
    ``meta`` y la prioridad ``-profundidad`` para mantener el recorrido en
    amplitud.  Abre y cierra su propia conexión en cada arranque.
    """

    def __init__(
        self,
        ruta_base_datos: str,
        *,
        timeout_sqlite: float = TIMEOUT_SQLITE_POR_DEFECTO,
        max_intentos: int = MAX_INTENTOS_POR_DEFECTO,
        crawler: Crawler | None = None,
    ) -> None:
        """Valida la configuración y conserva el crawler para ``process_start``."""
        if timeout_sqlite <= 0:
            raise ValueError("timeout_sqlite debe ser mayor que cero")
        if isinstance(max_intentos, bool) or not isinstance(max_intentos, int):
            raise TypeError("max_intentos debe ser un entero")
        if max_intentos < 1:
            raise ValueError("max_intentos debe ser al menos 1")
        self._ruta_base_datos = ruta_base_datos
        self._timeout_sqlite = timeout_sqlite
        self._max_intentos = max_intentos
        self._crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> MiddlewareFronteraInicial:
        """Construye el middleware con la configuración efectiva del crawler."""
        return cls(
            obtener_ruta_base_datos(crawler.settings),
            timeout_sqlite=crawler.settings.getfloat(
                "FRONTERA_SQLITE_TIMEOUT", TIMEOUT_SQLITE_POR_DEFECTO
            ),
            max_intentos=crawler.settings.getint("FRONTERA_MAX_INTENTOS", MAX_INTENTOS_POR_DEFECTO),
            crawler=crawler,
        )

    async def process_start(self, start: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Enganche moderno de Scrapy (>= 2.13) para el arranque de la araña.

        Scrapy pasa únicamente el iterador asíncrono de ``spider.start()``; el
        spider se obtiene del crawler para usar ``spider.parse`` como callback.
        """
        spider = getattr(self._crawler, "spider", None)
        for solicitud in self._solicitudes_pendientes(spider):
            yield solicitud
        async for elemento in start:
            yield elemento

    def process_start_requests(
        self,
        start_requests: Iterable[Request],
        spider: Spider | None = None,
    ) -> Iterator[Request]:
        """Variante sincrónica de compatibilidad: antepone las pendientes.

        Scrapy 2.19 ya no llama a este método, pero se conserva porque expresa
        la misma semántica de forma directa y facilita las pruebas sin reactor.
        """
        yield from self._solicitudes_pendientes(spider)
        yield from start_requests

    def _solicitudes_pendientes(self, spider: Spider | None) -> Iterator[Request]:
        """Construye las ``Request`` de la frontera en orden de extracción."""
        callback = getattr(spider, "parse", None) if spider is not None else None
        for entrada in self._leer_pendientes():
            yield Request(
                entrada.url,
                callback=callback,
                meta={"depth": entrada.profundidad},
                priority=-entrada.profundidad,
            )

    def _leer_pendientes(self) -> list[EntradaFrontera]:
        """Abre una conexión, reconcilia la cola y devuelve las pendientes.

        La conexión se cierra siempre antes de devolver, para no retener un
        escritor de SQLite durante el arranque del rastreo.
        """
        conexion = ConexionSQLite(
            self._ruta_base_datos,
            timeout=self._timeout_sqlite,
            busy_timeout_ms=_tiempo_espera(self._timeout_sqlite),
        )
        try:
            frontera = RepositorioFrontera(conexion)
            documentos = RepositorioDocumentos(conexion)
            frontera.reconciliar(
                lambda hash_url: not documentos.necesita_revisita(hash_url),
                self._max_intentos,
            )
            return frontera.pendientes(self._max_intentos)
        finally:
            conexion.cerrar()
