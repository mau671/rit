"""Punto de descubrimiento de la araña única del arañador."""

from arañador.implementacion.aranas.arana_base import (
    CATEGORIA_DOCUMENTOS,
    DocumentoItemAranador,
)
from arañador.implementacion.aranas.arana_semillas import (
    NOMBRE_SEMILLAS,
    AranaSemillas,
    cargar_semillas,
    derivar_hosts,
    interpretar_semillas,
)

__all__ = [
    "CATEGORIA_DOCUMENTOS",
    "NOMBRE_SEMILLAS",
    "AranaSemillas",
    "DocumentoItemAranador",
    "cargar_semillas",
    "derivar_hosts",
    "interpretar_semillas",
]
