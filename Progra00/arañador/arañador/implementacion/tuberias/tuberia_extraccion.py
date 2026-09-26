"""Tubería que transforma HTML de respuesta en texto principal limpio."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from scrapy.exceptions import DropItem

from ...bibliotecas.extraccion import EstadoExtraccion, ResultadoExtraccion, extraer_texto
from .modelos import DocumentoItem


class TuberiaExtraccion:
    """Ejecuta Trafilatura antes de validar y almacenar cada documento.

    Las arañas del proyecto entregan un ``DocumentoItemAranador`` que conserva el
    HTML original. Esta tubería usa Trafilatura como motor principal, elimina
    navegación y otros elementos no documentales y reemplaza ``texto`` por el
    resultado limpio. Los ítems creados directamente por otros spiders o pruebas
    pueden omitir ``html``; en ese caso se conserva el ``texto`` ya disponible.

    Args:
        extractor: Función extraíble compatible con la API de
            :mod:`arañador.bibliotecas`.
        umbral_caracteres: Umbral informativo del extractor. La regla de 300
            caracteres sigue aplicando ``TuberiaValidacion``.
        favor_precision: Prioriza precisión frente a una extracción exhaustiva.
        include_comments: Indica si deben conservarse comentarios HTML.
        include_tables: Indica si deben conservarse tablas.
    """

    def __init__(
        self,
        extractor: Callable[..., ResultadoExtraccion] = extraer_texto,
        *,
        umbral_caracteres: int = 1,
        favor_precision: bool = True,
        include_comments: bool = False,
        include_tables: bool = False,
    ) -> None:
        if not callable(extractor):
            raise TypeError("extractor debe ser invocable")
        if isinstance(umbral_caracteres, bool) or not isinstance(umbral_caracteres, int):
            raise TypeError("umbral_caracteres debe ser un entero")
        if umbral_caracteres < 0:
            raise ValueError("umbral_caracteres no puede ser negativo")
        if not isinstance(favor_precision, bool):
            raise TypeError("favor_precision debe ser booleano")
        if not isinstance(include_comments, bool):
            raise TypeError("include_comments debe ser booleano")
        if not isinstance(include_tables, bool):
            raise TypeError("include_tables debe ser booleano")

        self.extractor = extractor
        self.umbral_caracteres = umbral_caracteres
        self.favor_precision = favor_precision
        self.include_comments = include_comments
        self.include_tables = include_tables

    @classmethod
    def from_crawler(cls, crawler: Any) -> TuberiaExtraccion:
        """Construye la tubería usando los settings reales de Scrapy."""

        settings = crawler.settings
        return cls(
            umbral_caracteres=settings.getint("TUBERIA_EXTRACCION_UMBRAL_MINIMO", 1),
            favor_precision=settings.getbool("TUBERIA_EXTRACCION_FAVOR_PRECISION", True),
            include_comments=settings.getbool("TUBERIA_EXTRACCION_INCLUIR_COMENTARIOS", False),
            include_tables=settings.getbool("TUBERIA_EXTRACCION_INCLUIR_TABLAS", False),
        )

    def process_item(self, item: Any, spider: Any = None) -> Any:
        """Extrae el cuerpo principal y actualiza el ítem sin perder metadatos.

        Raises:
            DropItem: Si el ítem no admite actualización, el HTML no es válido o
                Trafilatura no encuentra texto principal.
        """

        del spider
        datos = self._datos(item)
        html = datos.get("html")
        if html is None:
            return item
        if not isinstance(html, (bytes, str)):
            raise DropItem("El campo 'html' debe ser bytes o str")

        url = datos.get("url")
        if not isinstance(url, str) or not url.strip():
            raise DropItem("La extracción requiere una URL absoluta válida")
        titulo = self._texto_opcional(datos.get("titulo"))
        idioma = self._texto_opcional(datos.get("idioma"))
        resultado = self.extractor(
            html,
            url=url.strip(),
            titulo=titulo,
            idioma=idioma,
            umbral_caracteres=self.umbral_caracteres,
            favor_precision=self.favor_precision,
            include_comments=self.include_comments,
            include_tables=self.include_tables,
        )
        if not isinstance(resultado, ResultadoExtraccion):
            raise TypeError("el extractor debe devolver ResultadoExtraccion")
        if resultado.estado is EstadoExtraccion.FALLO or not resultado.texto.strip():
            raise DropItem(
                f"No se pudo extraer texto principal: {resultado.motivo or 'sin motivo'}"
            )

        self._actualizar(item, texto=resultado.texto)
        if not titulo and resultado.titulo:
            self._actualizar(item, titulo=resultado.titulo)
        return item

    @staticmethod
    def _datos(item: Any) -> Mapping[str, Any]:
        if isinstance(item, Mapping):
            return item
        if hasattr(item, "items") and callable(item.items):
            return dict(item.items())
        raise DropItem("El objeto recibido no representa un ítem de documento")

    @staticmethod
    def _texto_opcional(valor: Any) -> str | None:
        if valor is None:
            return None
        if not isinstance(valor, str):
            raise DropItem("Título e idioma deben ser cadenas o None")
        return valor.strip() or None

    @staticmethod
    def _actualizar(item: Any, **campos: object) -> None:
        """Actualiza un Item o mutable mapping sin perder campos auxiliares."""

        try:
            if isinstance(item, (DocumentoItem, Mapping)):
                item.update(campos)
                return
            for campo, valor in campos.items():
                setattr(item, campo, valor)
        except (AttributeError, KeyError, TypeError) as error:
            raise RuntimeError("No se pudo actualizar el documento extraído") from error


__all__ = ["TuberiaExtraccion"]
