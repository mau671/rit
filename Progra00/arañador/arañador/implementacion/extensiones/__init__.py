"""Extensiones y middlewares propios de la versión Scrapy del arañador."""

from arañador.implementacion.extensiones.frontera_compartida import (
    ExtensionFronteraCompartida,
    MiddlewareFronteraInicial,
)

__all__ = [
    "ExtensionFronteraCompartida",
    "MiddlewareFronteraInicial",
]
