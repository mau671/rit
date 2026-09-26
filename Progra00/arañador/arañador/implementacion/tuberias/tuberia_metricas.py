"""Tubería de métricas acumulativas y seguras entre hilos."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from scrapy import signals

_LOGGER = logging.getLogger(__name__)


class TuberiaMetricas:
    """Acumula bytes, documentos, dominios y errores sin bloquearse entre sí.

    Cuando Scrapy construye la instancia mediante ``from_crawler``, el conteo
    exitoso se hace con la señal ``item_scraped`` (después de toda la cadena de
    tuberías) y no dentro de ``process_item``.  Una instancia creada
    directamente cuenta por defecto en ``process_item``.

    Args:
        reloj: Reloj monónico inyectable para hacer deterministas las pruebas.
        contar_en_proceso: Si es ``True`` (predeterminado), ``process_item``
            incrementa las métricas de éxito inmediatamente.  ``from_crawler``
            lo desactiva para contar mediante señales al final de la cadena.
    """

    def __init__(
        self,
        *,
        reloj: Callable[[], float] = time.monotonic,
        contar_en_proceso: bool = True,
    ) -> None:
        self._reloj = reloj
        self._contar_en_proceso = contar_en_proceso
        self._lock = threading.RLock()
        self._inicio = reloj()
        self._documentos = 0
        self._revisitas = 0
        self._bytes = 0
        self._errores = 0
        self._dominios: Counter[str] = Counter()

    @classmethod
    def from_crawler(cls, crawler: Any) -> TuberiaMetricas:
        """Construye métricas y conecta señales reales de Scrapy.

        Args:
            crawler: Instancia de :class:`scrapy.crawler.Crawler`.

        Returns:
            Tubería conectada a ``item_scraped``, ``item_dropped`` e
            ``item_error`` cuando la versión de Scrapy ofrece esa última señal.
        """

        instancia = cls(contar_en_proceso=False)
        manejadores = crawler.signals
        manejadores.connect(instancia.spider_opened, signal=signals.spider_opened)
        manejadores.connect(instancia.spider_closed, signal=signals.spider_closed)
        manejadores.connect(instancia._al_esquivar_item, signal=signals.item_dropped)
        manejadores.connect(instancia._al_procesar_item, signal=signals.item_scraped)
        if hasattr(signals, "item_error"):
            manejadores.connect(instancia._al_error_item, signal=signals.item_error)
        return instancia

    @property
    def documentos(self) -> int:
        """Cantidad de documentos contabilizados correctamente."""

        with self._lock:
            return self._documentos

    @property
    def revisitas(self) -> int:
        """Cantidad de relecturas idénticas que sólo renovaron frescura."""

        with self._lock:
            return self._revisitas

    @property
    def bytes(self) -> int:
        """Cantidad de bytes UTF-8 de texto contabilizados."""

        with self._lock:
            return self._bytes

    @property
    def dominios(self) -> int:
        """Cantidad de dominios distintos observados."""

        with self._lock:
            return len(self._dominios)

    @property
    def errores(self) -> int:
        """Cantidad de errores o descartes contabilizados."""

        with self._lock:
            return self._errores

    @property
    def documentos_por_segundo(self) -> float:
        """Velocidad aproximada de documentos desde el inicio."""

        with self._lock:
            transcurrido = max(self._reloj() - self._inicio, 0.0)
            return self._documentos / transcurrido if transcurrido > 0 else 0.0

    @property
    def bytes_por_segundo(self) -> float:
        """Velocidad aproximada de bytes desde el inicio."""

        with self._lock:
            transcurrido = max(self._reloj() - self._inicio, 0.0)
            return self._bytes / transcurrido if transcurrido > 0 else 0.0

    def registrar_documento(
        self,
        tamano_texto_bytes: int,
        dominio: str,
        *,
        cantidad: int = 1,
    ) -> None:
        """Registra uno o varios documentos y sus dominios.

        Args:
            tamano_texto_bytes: Bytes por documento.
            dominio: Dominio de origen.
            cantidad: Número de documentos representados por las mismas métricas.
        """

        if isinstance(tamano_texto_bytes, bool) or not isinstance(tamano_texto_bytes, int):
            raise TypeError("tamano_texto_bytes debe ser un entero")
        if tamano_texto_bytes < 0:
            raise ValueError("tamano_texto_bytes no puede ser negativo")
        if not isinstance(dominio, str) or not dominio.strip():
            raise ValueError("dominio debe ser una cadena no vacía")
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 0:
            raise ValueError("cantidad debe ser un entero no negativo")

        with self._lock:
            self._documentos += cantidad
            self._bytes += tamano_texto_bytes * cantidad
            if cantidad:
                self._dominios[dominio.strip().lower()] += cantidad

    def registrar_revisita(self, dominio: str, *, cantidad: int = 1) -> None:
        """Suma relecturas que no agregan documentos ni bytes al corpus."""

        if not isinstance(dominio, str) or not dominio.strip():
            raise ValueError("dominio debe ser una cadena no vacía")
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 0:
            raise ValueError("cantidad debe ser un entero no negativo")
        with self._lock:
            self._revisitas += cantidad

    def registrar_error(self, cantidad: int = 1) -> None:
        """Suma errores o descartes al contador."""

        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 0:
            raise ValueError("cantidad debe ser un entero no negativo")
        with self._lock:
            self._errores += cantidad

    def resumen(self) -> dict[str, int | float | dict[str, int]]:
        """Devuelve una instantánea coherente de todos los contadores."""

        with self._lock:
            transcurrido = max(self._reloj() - self._inicio, 0.0)
            docs_por_segundo = self._documentos / transcurrido if transcurrido else 0.0
            bytes_por_segundo = self._bytes / transcurrido if transcurrido else 0.0
            return {
                "documentos": self._documentos,
                "revisitas": self._revisitas,
                "bytes": self._bytes,
                "dominios": len(self._dominios),
                "errores": self._errores,
                "segundos": transcurrido,
                "documentos_por_segundo": docs_por_segundo,
                "bytes_por_segundo": bytes_por_segundo,
                "documentos_por_dominio": dict(self._dominios),
            }

    def reiniciar(self) -> None:
        """Pone todos los contadores y el instante inicial a cero."""

        with self._lock:
            self._inicio = self._reloj()
            self._documentos = 0
            self._revisitas = 0
            self._bytes = 0
            self._errores = 0
            self._dominios.clear()

    def process_item(self, item: Any, spider: Any = None) -> Any:
        """Devuelve el ítem sin alterarlo y opcionalmente lo contabiliza.

        Scrapy exige que ``process_item`` devuelva el mismo objeto o uno nuevo.
        Los descartes de otros componentes se cuentan mediante señales cuando la
        instancia fue creada por ``from_crawler``.
        """

        del spider
        if self._contar_en_proceso:
            tamano, dominio = self._extraer_metricas(item)
            if self._es_revisita(item):
                self.registrar_revisita(dominio)
            else:
                self.registrar_documento(tamano, dominio)
        return item

    def spider_opened(self, spider: Any = None) -> None:
        """Reinicia el intervalo al abrir una araña."""

        del spider
        self.reiniciar()

    def spider_closed(self, spider: Any = None) -> None:
        """Registra un resumen de cierre sin modificar los contadores."""

        _LOGGER.info(
            "Métricas de %s: %s",
            getattr(spider, "name", "araña"),
            self.resumen(),
        )

    def _al_procesar_item(self, item: Any, **_: Any) -> None:
        tamano, dominio = self._extraer_metricas(item)
        if self._es_revisita(item):
            self.registrar_revisita(dominio)
        else:
            self.registrar_documento(tamano, dominio)

    def _al_esquivar_item(self, item: Any, **_: Any) -> None:
        del item
        self.registrar_error()

    def _al_error_item(self, item: Any, **_: Any) -> None:
        del item
        self.registrar_error()

    @staticmethod
    def _es_revisita(item: Any) -> bool:
        if isinstance(item, Mapping):
            return item.get("revisitado") is True
        return bool(getattr(item, "get", lambda _clave: False)("revisitado"))

    @staticmethod
    def _extraer_metricas(item: Any) -> tuple[int, str]:
        if isinstance(item, Mapping):
            datos: Mapping[str, Any] = item
        elif hasattr(item, "items") and callable(item.items):
            datos = dict(item.items())
        else:
            raise TypeError("La señal item_scraped no recibió un objeto de tipo Item")

        tamano = datos.get("tamano_texto_bytes")
        if tamano is None:
            texto = datos.get("texto")
            if not isinstance(texto, str):
                raise TypeError("El ítem descartado no contiene texto ni tamaño")
            tamano = len(texto.encode("utf-8"))
        if isinstance(tamano, bool) or not isinstance(tamano, int) or tamano < 0:
            raise TypeError("tamano_texto_bytes debe ser un entero no negativo")

        dominio = datos.get("dominio")
        if not isinstance(dominio, str) or not dominio.strip():
            raise TypeError("El ítem descartado no contiene un dominio válido")
        return tamano, dominio


__all__ = ["TuberiaMetricas"]
