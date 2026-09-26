"""Reexports de los repositorios canónicos y excepciones de integración.

La implementación y la migración SQLite viven en
:mod:`arañador.implementacion.bd`.  Las tuberías reutilizan sus tipos para
que ``MetadatoDocumento`` y ``DocumentoDuplicadoError`` sean los mismos objetos
en middleware, scripts y pipelines.
"""

from typing import Literal

from ..bd.repositorio import (
    DocumentoDuplicadoError,
    RepositorioBitacora,
    RepositorioDocumentos,
)

MotivoDuplicado = Literal["url", "contenido", "ruta"]


__all__ = [
    "DocumentoDuplicadoError",
    "MotivoDuplicado",
    "RepositorioBitacora",
    "RepositorioDocumentos",
]
