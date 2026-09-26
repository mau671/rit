"""Reexports de los modelos canónicos usados por las tuberías.

Las clases viven en :mod:`arañador.implementacion.elementos` y
:mod:`arañador.implementacion.bd.modelos`.  Este módulo mantiene una API
estable para las tuberías sin duplicar el contrato de dominio.
"""

from arañador.implementacion.bd.modelos import MetadatoDocumento, RegistroBitacora
from arañador.implementacion.bd.modelos import ahora_utc as utc_ahora

from ..elementos import DocumentoItem

__all__ = [
    "DocumentoItem",
    "MetadatoDocumento",
    "RegistroBitacora",
    "utc_ahora",
]
