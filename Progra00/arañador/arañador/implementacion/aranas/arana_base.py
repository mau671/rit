"""Contrato del documento que producen las arañas y elementos compartidos.

El paquete declara una única araña, ``arañador``, en
:mod:`arañador.implementacion.aranas.arana_semillas`. Este módulo solo define el
item que esa araña entrega a las tuberías, de modo que el contrato de
extracción no dependa del sitio recorrido.
"""

from __future__ import annotations

from typing import Final

from scrapy import Field

from arañador.implementacion.elementos import DocumentoItem

#: Categoría genérica y estable mientras la semilla no aporte una clasificación
#: propia. La araña no infiere categorías con reglas por dominio.
CATEGORIA_DOCUMENTOS: Final[str] = "documentos"


class DocumentoItemAranador(DocumentoItem):
    """Extensión compatible del item que conserva el HTML y sus metadatos.

    ``DocumentoItem`` sigue siendo la clase base y, por tanto, toda instancia
    producida es un ``DocumentoItem``. El HTML completo se conserva para que la
    tubería de extracción lo depure con Trafilatura y ``metadatos`` mantiene los
    datos estándar de la página (descripción, autoría, fecha y URL canónica) sin
    ninguna regla por dominio.
    """

    # Scrapy no parametriza ``Field``; las anotaciones declaran el contrato.
    html: str = Field()  # ty: ignore[invalid-assignment]
    metadatos: dict[str, str] = Field()


__all__ = [
    "CATEGORIA_DOCUMENTOS",
    "DocumentoItemAranador",
]
