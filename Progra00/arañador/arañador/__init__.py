"""Paquete principal del arañador web audiovisual."""

from __future__ import annotations

from arañador.implementacion import configuracion as _configuracion
from arañador.implementacion.configuracion import ConfiguracionAranador
from arañador.implementacion.elementos import DocumentoItem, ElementoDocumento

from . import entorno

__version__ = "0.1.0"


def _publicar_ajustes_de_scrapy() -> None:
    """Expone en el módulo los atributos públicos de la clase de configuración.

    Scrapy carga variables globales en mayúsculas desde el módulo indicado por
    ``scrapy.cfg``. La clase sigue siendo la fuente tipada; esta proyección evita
    duplicar sus valores y mantiene un único lugar donde definir políticas.
    """

    for nombre in dir(ConfiguracionAranador):
        if nombre.isupper():
            setattr(_configuracion, nombre, getattr(ConfiguracionAranador, nombre))


_publicar_ajustes_de_scrapy()

__all__ = [
    "ConfiguracionAranador",
    "entorno",
    "DocumentoItem",
    "ElementoDocumento",
    "__version__",
]
