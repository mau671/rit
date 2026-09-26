"""Filtro de solicitudes con deduplicación persistente por fecha de relectura."""

from __future__ import annotations

import os
from typing import Any

from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.dupefilters import RFPDupeFilter

from ..bibliotecas.sqlite import ConexionSQLite
from .bd.repositorio import RepositorioDocumentos
from .configuracion import obtener_ruta_base_datos
from .utilidades.generador_hash import generar_hash_url


class FiltroDuplicadosPersistentes(RFPDupeFilter):
    """Combina ``RFPDupeFilter`` con la fecha de re-visitación de SQLite.

    La deduplicación en memoria de Scrapy continúa siendo la primera capa para
    una misma ejecución. La segunda capa consulta el hash de la
    URL normalizada: omite el documento si su próxima relectura es futura y lo
    permite cuando no existe o ya venció. Las URLs más superficiales reciben una
    prioridad numérica menor, por lo que la cola las procesa antes.
    """

    def __init__(
        self,
        ruta_base_datos: str | os.PathLike[str],
        *,
        timeout_sqlite: float = 0.5,
        fingerprinter: Any = None,
    ) -> None:
        if timeout_sqlite <= 0:
            raise ValueError("timeout_sqlite debe ser mayor que cero")
        super().__init__(debug=False, fingerprinter=fingerprinter)
        self.ruta_base_datos = ruta_base_datos
        self.timeout_sqlite = timeout_sqlite
        self._conexion: ConexionSQLite | None = None
        self._repositorio: RepositorioDocumentos | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> FiltroDuplicadosPersistentes:
        """Construye el filtro con la configuración efectiva del crawler."""

        return cls(
            obtener_ruta_base_datos(crawler.settings),
            timeout_sqlite=crawler.settings.getfloat("DUPEFILTER_SQLITE_TIMEOUT", 0.5),
            fingerprinter=crawler.request_fingerprinter,
        )

    def open(self) -> None:
        """Inicializa SQLite de forma obligatoria antes de aceptar solicitudes."""

        super().open()
        if self._repositorio is not None:
            return
        conexion = ConexionSQLite(
            self.ruta_base_datos,
            timeout=self.timeout_sqlite,
            busy_timeout_ms=max(1, round(self.timeout_sqlite * 1_000)),
        )
        try:
            self._repositorio = RepositorioDocumentos(conexion)
        except BaseException:
            conexion.cerrar()
            raise
        self._conexion = conexion

    def request_seen(self, request: Request) -> bool:
        """Indica si la solicitud ya se vio o su documento aún no está vencido."""

        if super().request_seen(request):
            return True
        if self._repositorio is None:
            self.open()
        repositorio = self._repositorio
        if repositorio is None:  # pragma: no cover - la apertura obligatoria no deja None
            raise RuntimeError("No se pudo inicializar el filtro persistente")

        profundidad = request.meta.get("depth", 0)
        if isinstance(profundidad, int) and not isinstance(profundidad, bool) and profundidad >= 0:
            request.priority += profundidad * 10

        hash_url = generar_hash_url(request.url)
        if not repositorio.necesita_revisita(hash_url):
            request.meta["filtro_duplicados"] = "persistido"
            return True
        request.meta["filtro_duplicados"] = "permitido"
        return False

    def close(self, reason: str) -> None:
        """Cierra la conexión persistente antes de finalizar el crawler."""

        try:
            if self._repositorio is not None:
                self._repositorio.cerrar()
        finally:
            self._repositorio = None
            self._conexion = None
            super().close(reason)


__all__ = ["FiltroDuplicadosPersistentes"]
