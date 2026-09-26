"""Middleware de auditoría para solicitudes, respuestas y excepciones."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol, Self, cast
from urllib.parse import urlsplit

from scrapy import Request, Spider, signals
from scrapy.crawler import Crawler
from scrapy.http import Response

from arañador.bibliotecas.sqlite import (
    RUTA_POR_DEFECTO,
    ConexionSQLite,
)
from arañador.implementacion.bd.modelos import RegistroBitacora
from arañador.implementacion.bd.repositorio import RepositorioBitacora
from arañador.implementacion.configuracion import obtener_ruta_base_datos

_LOGGER = logging.getLogger(__name__)


class RepositorioBitacoraProtocol(Protocol):
    """Contrato mínimo que permite inyectar un DAO en pruebas."""

    def registrar(self, registro: RegistroBitacora) -> int:
        """Registra una entrada de bitácora."""
        ...


class IntermediarioBitacora:
    """Mide cada intento de descarga y lo registra sin alterar el flujo.

    El repositorio es reutilizado y protegido por un lock. En modo obligatorio,
    cualquier fallo de SQLite se propaga para no presentar una recolección como
    completa cuando falta auditoría. Un enfriamiento evita reintentar inútilmente
    una base que acaba de fallar.
    """

    CLAVE_INICIO: ClassVar[str] = "aranador_bitacora_inicio"
    CLAVE_RESULTADO_REGISTRADO: ClassVar[str] = "aranador_bitacora_resultado_registrado"
    ACCION_RESPUESTA: ClassVar[str] = "RESPUESTA"
    ACCION_EXCEPCION: ClassVar[str] = "ERROR"

    def __init__(
        self,
        repositorio: RepositorioBitacoraProtocol | None = None,
        *,
        ruta_base_datos: str | os.PathLike[str] = RUTA_POR_DEFECTO,
        timeout_sqlite: float = 0.5,
        intervalo_reintento: float = 30.0,
        obligatoria: bool = True,
        reloj: Callable[[], float] = time.perf_counter,
        reloj_utc: Callable[[], datetime] | None = None,
    ) -> None:
        """Inicializa el middleware con un repositorio o una ruta SQLite.

        Args:
            repositorio: DAO inyectado. Si se omite, se crea de forma perezosa.
            ruta_base_datos: Archivo usado al crear el repositorio propio.
            timeout_sqlite: Espera máxima por un bloqueo, en segundos.
            intervalo_reintento: Espera antes de reabrir tras un error de DB.
            obligatoria: Si es ``True``, una pérdida de bitácora detiene el rastreo.
            reloj: Reloj monónico inyectable para medir tiempos.
            reloj_utc: Reloj de UTC inyectable para determinismo en pruebas.
        """

        if timeout_sqlite <= 0:
            raise ValueError("timeout_sqlite debe ser mayor que cero")
        if intervalo_reintento < 0:
            raise ValueError("intervalo_reintento no puede ser negativo")
        if not isinstance(obligatoria, bool):
            raise TypeError("obligatoria debe ser booleano")

        self._repositorio = repositorio
        self._posee_repositorio = repositorio is None
        self._ruta_base_datos = ruta_base_datos
        self._timeout_sqlite = timeout_sqlite
        self._intervalo_reintento = intervalo_reintento
        self._obligatoria = obligatoria
        self._reloj = reloj
        self._reloj_utc = reloj_utc or (lambda: datetime.now(UTC))
        self._proximo_intento = 0.0
        self._registros_fallidos = 0
        self._registros_perdidos = 0
        self._registros_retrasados = 0
        self._lock = threading.RLock()
        self._cerrado = False

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        """Construye el middleware mediante la API de Scrapy."""

        repositorio = crawler.settings.get("INTERMEDIARIO_REPOSITORIO_BITACORA")
        if repositorio is None:
            repositorio = crawler.settings.get("REPOSITORIO_BITACORA")
        if repositorio is not None and not callable(getattr(repositorio, "registrar", None)):
            raise TypeError("El repositorio de bitácora debe implementar registrar()")

        ruta = obtener_ruta_base_datos(crawler.settings)

        instancia = cls(
            cast(RepositorioBitacoraProtocol | None, repositorio),
            ruta_base_datos=ruta,
            timeout_sqlite=crawler.settings.getfloat(
                "INTERMEDIARIO_SQLITE_TIMEOUT",
                0.5,
            ),
            intervalo_reintento=crawler.settings.getfloat(
                "INTERMEDIARIO_REINTENTO_SEGUNDOS",
                30.0,
            ),
            obligatoria=crawler.settings.getbool("INTERMEDIARIO_BITACORA_OBLIGATORIA", True),
        )
        if repositorio is not None:
            instancia._posee_repositorio = False
        elif instancia._obligatoria and instancia._obtener_repositorio() is None:
            raise RuntimeError(
                "No fue posible inicializar la bitácora SQLite obligatoria; "
                "corrige la ruta, permisos o bloqueos antes de iniciar el rastreo"
            )
        crawler.signals.connect(instancia.cerrar, signal=signals.spider_closed)
        crawler.signals.connect(instancia._al_esquivar_item, signal=signals.item_dropped)
        crawler.signals.connect(instancia._al_procesar_item, signal=signals.item_scraped)
        if hasattr(signals, "item_error"):
            crawler.signals.connect(instancia._al_error_item, signal=signals.item_error)
        return instancia

    def process_request(self, request: Request, spider: Spider | None = None) -> None:
        """Marca el comienzo de un intento de descarga.

        ``spider`` forma parte de la firma de downloader middleware aunque no
        se necesita para medir la solicitud.
        """

        del spider
        request.meta.pop(self.CLAVE_RESULTADO_REGISTRADO, None)
        request.meta[self.CLAVE_INICIO] = self._reloj()

    def process_response(
        self,
        request: Request,
        response: Response,
        spider: Spider | None = None,
    ) -> Response:
        """Registra la respuesta y la devuelve sin modificarla."""

        del spider
        if request.meta.pop(self.CLAVE_RESULTADO_REGISTRADO, False):
            return response
        self._registrar_resultado(
            request=request,
            codigo_http=int(response.status),
            accion=self.ACCION_RESPUESTA,
        )
        request.meta[self.CLAVE_RESULTADO_REGISTRADO] = True
        return response

    def process_exception(
        self,
        request: Request,
        exception: Exception,
        spider: Spider | None = None,
    ) -> None:
        """Registra una excepción y permite que RetryMiddleware la procese."""

        del spider, exception
        if request.meta.pop(self.CLAVE_RESULTADO_REGISTRADO, False):
            return None
        self._registrar_resultado(
            request=request,
            codigo_http=None,
            accion=self.ACCION_EXCEPCION,
        )
        # Si otra capa recupera la solicitud y la convierte en respuesta, se
        # evita registrar dos resultados para el mismo intento. ``process_request``
        # limpia esta marca en el siguiente reintento.
        request.meta[self.CLAVE_RESULTADO_REGISTRADO] = True
        return None

    def _al_procesar_item(
        self,
        item: Any,
        response: Response | None = None,
        **_: Any,
    ) -> None:
        """Registra el resultado final de una tubería de Scrapy."""

        accion = "REVISITADO" if self._item_es_revisita(item) else "ALMACENADO"
        self._registrar_accion_item(item, response, accion)

    def _al_esquivar_item(
        self,
        item: Any,
        response: Response | None = None,
        exception: BaseException | None = None,
        **_: Any,
    ) -> None:
        """Registra un ``DropItem`` con una razón de procesamiento."""

        accion = self._clasificar_drop(str(exception) if exception is not None else "DROP")
        self._registrar_accion_item(item, response, accion)

    def _al_error_item(
        self,
        item: Any,
        response: Response | None = None,
        failure: Any = None,
        **_: Any,
    ) -> None:
        """Registra un error no convertido en ``DropItem``."""

        detalle = getattr(failure, "value", failure)
        accion = self._clasificar_drop(str(detalle)) if detalle is not None else "ERROR"
        self._registrar_accion_item(item, response, accion)

    @staticmethod
    def _item_es_revisita(item: Any) -> bool:
        if isinstance(item, dict):
            return item.get("revisitado") is True
        getter = getattr(item, "get", None)
        return callable(getter) and getter("revisitado") is True

    @staticmethod
    def _clasificar_drop(mensaje: str) -> str:
        texto = mensaje.casefold()
        if "duplicado por url" in texto:
            return "DUPLICADO_URL"
        if "duplicado por contenido" in texto:
            return "DUPLICADO_CONTENIDO"
        if "densidad" in texto:
            return "BAJA_DENSIDAD"
        if "texto" in texto or "longitud" in texto:
            return "BAJA_DENSIDAD"
        return "ERROR"

    def _registrar_accion_item(
        self,
        item: Any,
        response: Response | None,
        accion: str,
    ) -> None:
        """Construye un registro de procesamiento a partir de la respuesta."""

        url = response.url if response is not None else self._dato_item(item, "url")
        if not isinstance(url, str) or not url:
            return
        solicitud = response.request if response is not None else None
        dominio = urlsplit(url).hostname or "localhost"
        registro = RegistroBitacora(
            timestamp=self._reloj_utc(),
            dominio=dominio,
            url_origen=self._referer(solicitud) if solicitud is not None else None,
            url_destino=url,
            tiempo_respuesta_ms=0.0,
            codigo_http=int(response.status) if response is not None else None,
            accion=accion,
        )
        self._persistir(registro)

    @staticmethod
    def _dato_item(item: Any, clave: str) -> object:
        if isinstance(item, dict):
            return item.get(clave)
        getter = getattr(item, "get", None)
        return getter(clave) if callable(getter) else None

    def cerrar(self, spider: object | None = None) -> None:
        """Cierra sólo el repositorio propiedad de este middleware."""

        del spider
        with self._lock:
            if self._cerrado:
                return
            self._cerrado = True
            if not self._posee_repositorio or self._repositorio is None:
                return
            try:
                cerrar = getattr(self._repositorio, "cerrar", None)
                if callable(cerrar):
                    cerrar()
            except Exception:  # pragma: no cover - depende del estado externo
                _LOGGER.warning("No se pudo cerrar la bitácora SQLite", exc_info=True)
            finally:
                self._repositorio = None

    def _registrar_resultado(
        self,
        *,
        request: Request,
        codigo_http: int | None,
        accion: str,
    ) -> bool:
        """Construye y persiste una entrada según la política de errores."""

        if self._cerrado:
            return False
        inicio = request.meta.pop(self.CLAVE_INICIO, None)
        tiempo_ms = 0.0
        if isinstance(inicio, (int, float)) and not isinstance(inicio, bool):
            tiempo_ms = max((self._reloj() - float(inicio)) * 1_000.0, 0.0)

        dominio = urlsplit(request.url).hostname or "localhost"
        registro = RegistroBitacora(
            timestamp=self._reloj_utc(),
            dominio=dominio,
            url_origen=self._referer(request),
            url_destino=request.url,
            tiempo_respuesta_ms=tiempo_ms,
            codigo_http=codigo_http,
            accion=accion,
        )
        return self._persistir(registro)

    @property
    def registros_fallidos(self) -> int:
        """Intentos cuyo registro en SQLite falló."""

        with self._lock:
            return self._registros_fallidos

    @property
    def registros_perdidos(self) -> int:
        """Registros descartados porque la bitácora no estaba disponible."""

        with self._lock:
            return self._registros_perdidos

    @property
    def registros_retrasados(self) -> int:
        """Intentos omitidos durante el enfriamiento posterior a un error."""

        with self._lock:
            return self._registros_retrasados

    def _persistir(self, registro: RegistroBitacora) -> bool:
        """Serializa escrituras y hace cumplir la política de bitácora."""

        with self._lock:
            if self._cerrado:
                if self._obligatoria:
                    raise RuntimeError("La bitácora obligatoria ya fue cerrada")
                return False
            repositorio = self._obtener_repositorio()
            if repositorio is None:
                self._registros_perdidos += 1
                if self._obligatoria:
                    raise RuntimeError(
                        f"No se pudo registrar la bitácora obligatoria de {registro.url_destino}"
                    )
                return False
            try:
                repositorio.registrar(registro)
            except Exception as error:
                self._registros_fallidos += 1
                if self._posee_repositorio:
                    self._repositorio = None
                    self._proximo_intento = self._reloj() + self._intervalo_reintento
                    try:
                        cerrar = getattr(repositorio, "cerrar", None)
                        if callable(cerrar):
                            cerrar()
                    except Exception:
                        _LOGGER.debug(
                            "No se pudo cerrar el repositorio de bitácora",
                            exc_info=True,
                        )
                if self._obligatoria:
                    raise RuntimeError(
                        f"No se pudo registrar la bitácora obligatoria de {registro.url_destino}"
                    ) from error
                self._registros_perdidos += 1
                _LOGGER.warning(
                    "No se pudo registrar la bitácora de %s: %s",
                    registro.url_destino,
                    registro.accion,
                    exc_info=True,
                )
                return False
            return True

    def _obtener_repositorio(self) -> RepositorioBitacoraProtocol | None:
        """Crea el DAO con espera acotada y un enfriamiento entre reintentos."""

        if self._repositorio is not None:
            return self._repositorio
        ahora = self._reloj()
        if ahora < self._proximo_intento:
            self._registros_retrasados += 1
            return None

        conexion: ConexionSQLite | None = None
        try:
            conexion = ConexionSQLite(
                str(self._ruta_base_datos),
                timeout=self._timeout_sqlite,
                busy_timeout_ms=max(1, round(self._timeout_sqlite * 1_000)),
            )
            self._repositorio = RepositorioBitacora(conexion)
        except Exception:
            self._registros_fallidos += 1
            if conexion is not None:
                try:
                    conexion.cerrar()
                except Exception:
                    _LOGGER.debug(
                        "No se pudo cerrar la conexión SQLite tras un error",
                        exc_info=True,
                    )
            self._repositorio = None
            self._proximo_intento = ahora + self._intervalo_reintento
            _LOGGER.error(
                "La bitácora SQLite no está disponible",
                exc_info=True,
            )
        return self._repositorio

    @staticmethod
    def _referer(request: Request) -> str | None:
        """Obtiene y decodifica el encabezado HTTP ``Referer``."""

        valor = request.headers.get("Referer")
        if valor is None:
            return None
        if isinstance(valor, bytes):
            return valor.decode("utf-8", errors="replace")
        return str(valor)


__all__ = ["IntermediarioBitacora", "RepositorioBitacoraProtocol"]
