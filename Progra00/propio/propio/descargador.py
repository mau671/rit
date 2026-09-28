"""Paso 2: descarga HTTP con cortesía, robots.txt y reintentos.

Todo con la biblioteca estándar:

* ``urllib.request`` hace las solicitudes y sigue redirecciones.
* ``urllib.robotparser`` interpreta robots.txt. El archivo lo descargamos
  nosotros con nuestro User-Agent, porque ``RobotFileParser.read()`` no envía
  User-Agent y algunos sitios responden 403 a eso.
* ``ControlCortesia`` reemplaza a DOWNLOAD_DELAY, CONCURRENT_REQUESTS_PER_DOMAIN
  y AutoThrottle de Scrapy.
"""

from __future__ import annotations

import http.client
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from .configuracion import (
    CODIGOS_REINTENTO,
    DEMORA_DESCARGA,
    DEMORA_MAXIMA,
    MAX_REDIRECCIONES,
    MAX_SIMULTANEAS_POR_DOMINIO,
    REINTENTOS,
    TAMANO_MAXIMO_BYTES,
    TIMEOUT_SEGUNDOS,
    USER_AGENT,
)

log = logging.getLogger(__name__)

_CHARSET_META = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


class ErrorDescarga(Exception):
    """La descarga falló después de agotar los reintentos."""

    def __init__(self, mensaje: str, codigo_http: int | None = None, tiempo_ms: float = 0.0):
        super().__init__(mensaje)
        self.codigo_http = codigo_http
        self.tiempo_ms = tiempo_ms


class HostOcupado(Exception):
    """El dominio ya tiene el máximo de descargas o hay que esperar demasiado."""


@dataclass(slots=True)
class Respuesta:
    url_final: str
    codigo_http: int
    cabeceras: dict[str, str]
    cuerpo: bytes
    tiempo_ms: float

    @property
    def tipo_contenido(self) -> str:
        return self.cabeceras.get("content-type", "").split(";")[0].strip().lower()

    @property
    def es_html(self) -> bool:
        return self.tipo_contenido in {"text/html", "application/xhtml+xml", ""}

    def texto(self) -> str:
        """Decodifica el cuerpo con el charset del encabezado o del ``<meta>``."""

        charset = None
        coincidencia = re.search(r"charset=([\w-]+)", self.cabeceras.get("content-type", ""), re.I)
        if coincidencia:
            charset = coincidencia.group(1)
        else:
            en_meta = _CHARSET_META.search(self.cuerpo[:4096])
            if en_meta:
                charset = en_meta.group(1).decode("ascii", "ignore")
        try:
            return self.cuerpo.decode(charset or "utf-8", errors="replace")
        except LookupError:  # charset desconocido
            return self.cuerpo.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Política de cortesía: retardo, concurrencia por dominio y ajuste adaptativo
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _EstadoHost:
    activas: int = 0  # descargas en curso contra este host
    proxima: float = 0.0  # instante (monotonic) en que puede empezar la siguiente
    demora: float = DEMORA_DESCARGA  # retardo actual entre solicitudes


@dataclass
class ControlCortesia:
    """Decide cuándo un hilo puede descargar de un host.

    Reglas:
    1. Nunca más de ``MAX_SIMULTANEAS_POR_DOMINIO`` descargas a la vez por host.
    2. Entre dos inicios de descarga al mismo host pasan al menos ``demora`` s.
    3. La demora se adapta a la latencia del servidor (como AutoThrottle): si
       responde lento o con 429/5xx, esperamos más; nunca menos de
       ``DEMORA_DESCARGA`` ni más de ``DEMORA_MAXIMA``.
    """

    _estados: dict[str, _EstadoHost] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def fijar_demora_minima(self, host: str, segundos: float) -> None:
        """Aplica el ``Crawl-delay`` de robots.txt si es mayor que el nuestro."""

        with self._lock:
            estado = self._estados.setdefault(host, _EstadoHost())
            estado.demora = min(DEMORA_MAXIMA * 2, max(estado.demora, segundos))

    def reservar(self, host: str, espera_maxima: float | None) -> bool:
        """Reserva un turno. Duerme lo necesario y devuelve ``True``.

        Con ``espera_maxima`` devuelve ``False`` sin esperar si el host está
        saturado o la espera sería mayor; así el hilo toma otra URL.
        Con ``None`` espera lo que haga falta.
        """

        while True:
            with self._lock:
                estado = self._estados.setdefault(host, _EstadoHost())
                ahora = time.monotonic()
                espera = max(0.0, estado.proxima - ahora)
                libre = estado.activas < MAX_SIMULTANEAS_POR_DOMINIO
                if libre and (espera_maxima is None or espera <= espera_maxima):
                    estado.activas += 1
                    estado.proxima = max(ahora, estado.proxima) + estado.demora
                    break
                if espera_maxima is not None:
                    return False
            time.sleep(0.1)
        if espera > 0:
            time.sleep(espera)
        return True

    def liberar(self, host: str, latencia_s: float, codigo_http: int | None) -> None:
        """Devuelve el turno y ajusta la demora según cómo respondió el host."""

        with self._lock:
            estado = self._estados.setdefault(host, _EstadoHost())
            estado.activas = max(0, estado.activas - 1)
            # 429/5xx: el servidor pide bajar el ritmo, se duplica la demora.
            # Si no, igual que AutoThrottle con concurrencia objetivo 2.
            en_apuros = codigo_http in CODIGOS_REINTENTO
            objetivo = estado.demora * 2 if en_apuros else latencia_s / 2
            nueva = (estado.demora + objetivo) / 2
            estado.demora = min(DEMORA_MAXIMA, max(DEMORA_DESCARGA, nueva))

    def demora_actual(self, host: str) -> float:
        with self._lock:
            return self._estados.get(host, _EstadoHost()).demora


# ---------------------------------------------------------------------------
# Descargador
# ---------------------------------------------------------------------------
class _Redirecciones(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECCIONES


class Descargador:
    """Descarga páginas respetando robots.txt y la cortesía por host."""

    def __init__(self, cortesia: ControlCortesia | None = None) -> None:
        self.cortesia = cortesia or ControlCortesia()
        self._opener = urllib.request.build_opener(_Redirecciones())
        self._robots: dict[str, RobotFileParser] = {}
        self._locks_robots: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    # --- robots.txt ---------------------------------------------------------
    def permitido_por_robots(self, url: str) -> bool:
        """``True`` si robots.txt del host permite la URL a nuestro User-Agent."""

        partes = urlsplit(url)
        base = f"{partes.scheme}://{partes.netloc}"
        with self._lock:
            lock_host = self._locks_robots.setdefault(base, threading.Lock())
        # Un lock por host: si 16 hilos llegan al mismo sitio nuevo, solo uno
        # descarga robots.txt y el resto espera el resultado.
        with lock_host:
            parser = self._robots.get(base)
            if parser is None:
                parser = self._cargar_robots(base, partes.hostname or "")
                self._robots[base] = parser
        return parser.can_fetch(USER_AGENT, url)

    def _cargar_robots(self, base: str, host: str) -> RobotFileParser:
        parser = RobotFileParser(base + "/robots.txt")
        try:
            respuesta = self._descargar_una_vez(base + "/robots.txt", host)
        except ErrorDescarga as error:
            # Sin robots.txt accesible: 401/403 = prohibido todo; lo demás
            # (404, red caída) = permitido. Es la convención de Scrapy y de RFC 9309.
            if error.codigo_http in (401, 403):
                parser.disallow_all = True
            else:
                parser.allow_all = True
            log.info("robots.txt %s -> %s", base, error)
            return parser
        if respuesta.codigo_http in (401, 403):
            parser.disallow_all = True
        elif respuesta.codigo_http >= 400:
            parser.allow_all = True
        else:
            parser.parse(respuesta.texto().splitlines())
            retardo = parser.crawl_delay(USER_AGENT)
            if retardo:
                self.cortesia.fijar_demora_minima(host, float(retardo))
        log.info("robots.txt %s cargado (código %s)", base, respuesta.codigo_http)
        return parser

    # --- HTTP ----------------------------------------------------------------
    def descargar(self, url: str, espera_maxima: float | None = None) -> Respuesta:
        """Descarga ``url`` con reintentos.

        Raises:
            HostOcupado: el primer turno no está disponible dentro de ``espera_maxima``.
            ErrorDescarga: se agotaron los reintentos o hubo un error definitivo.
        """

        host = urlsplit(url).hostname or ""
        if not self.cortesia.reservar(host, espera_maxima):
            raise HostOcupado(host)
        ultimo_error: ErrorDescarga | None = None
        for intento in range(REINTENTOS + 1):
            if intento > 0:
                time.sleep(2**intento)  # espera exponencial: 2, 4, 8 s
                self.cortesia.reservar(host, None)
            try:
                respuesta = self._descargar_con_turno(url, host)
            except ErrorDescarga as error:
                ultimo_error = error
                if error.codigo_http is not None and error.codigo_http not in CODIGOS_REINTENTO:
                    raise  # 404, 403, etc.: no tiene sentido reintentar
                log.debug("Reintento %d de %s: %s", intento + 1, url, error)
                continue
            if respuesta.codigo_http in CODIGOS_REINTENTO:
                ultimo_error = ErrorDescarga(
                    f"HTTP {respuesta.codigo_http}", respuesta.codigo_http, respuesta.tiempo_ms
                )
                continue
            return respuesta
        assert ultimo_error is not None
        raise ultimo_error

    def _descargar_una_vez(self, url: str, host: str) -> Respuesta:
        self.cortesia.reservar(host, None)
        return self._descargar_con_turno(url, host)

    def _descargar_con_turno(self, url: str, host: str) -> Respuesta:
        """Hace una sola solicitud. El turno ya está reservado y aquí se libera."""

        solicitud = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "Accept-Language": "es,en;q=0.8",
            },
        )
        inicio = time.monotonic()
        codigo: int | None = None
        try:
            with self._opener.open(solicitud, timeout=TIMEOUT_SEGUNDOS) as resp:
                codigo = resp.status
                cabeceras = {k.lower(): v for k, v in resp.headers.items()}
                largo = cabeceras.get("content-length", "")
                if largo.isdigit() and int(largo) > TAMANO_MAXIMO_BYTES:
                    raise ErrorDescarga(f"Demasiado grande ({largo} bytes)", codigo)
                cuerpo = resp.read(TAMANO_MAXIMO_BYTES + 1)
                if len(cuerpo) > TAMANO_MAXIMO_BYTES:
                    raise ErrorDescarga("Supera el tamaño máximo", codigo)
                url_final = resp.geturl()
        except urllib.error.HTTPError as error:
            # 4xx/5xx: es una respuesta válida del servidor, no un fallo de red.
            codigo = error.code
            tiempo = (time.monotonic() - inicio) * 1000
            raise ErrorDescarga(f"HTTP {codigo}", codigo, tiempo) from None
        except ErrorDescarga as error:
            error.tiempo_ms = (time.monotonic() - inicio) * 1000
            raise
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as error:
            raise ErrorDescarga(
                f"Error de red: {error}", None, (time.monotonic() - inicio) * 1000
            ) from None
        finally:
            self.cortesia.liberar(host, time.monotonic() - inicio, codigo)

        return Respuesta(
            url_final, codigo or 0, cabeceras, cuerpo, (time.monotonic() - inicio) * 1000
        )
