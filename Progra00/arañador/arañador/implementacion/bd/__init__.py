"""API pública de la capa de persistencia del arañador audiovisual."""

from ...bibliotecas.sqlite import (
    MIGRACION_FRONTERA,
    MIGRACION_INICIAL,
    MIGRACIONES,
    RUTA_POR_DEFECTO,
    TIEMPO_ESPERA_POR_DEFECTO_MS,
    Conexion,
    ConexionSQLite,
    Migracion,
)
from .frontera import (
    EntradaFrontera,
    RepositorioFrontera,
)
from .modelos import (
    MetadatoDocumento,
    RegistroBitacora,
    ahora_utc,
    datetime_desde_timestamp,
    normalizar_datetime_utc,
    timestamp_utc_iso,
)
from .repositorio import (
    DocumentoDuplicadoError,
    FabricaConexion,
    RepositorioBitacora,
    RepositorioDocumentos,
    ResultadoInsercion,
    ResultadoLoteDocumentos,
)

__all__ = [
    "Conexion",
    "ConexionSQLite",
    "DocumentoDuplicadoError",
    "EntradaFrontera",
    "FabricaConexion",
    "MIGRACIONES",
    "MIGRACION_FRONTERA",
    "MIGRACION_INICIAL",
    "MetadatoDocumento",
    "Migracion",
    "RUTA_POR_DEFECTO",
    "RegistroBitacora",
    "RepositorioBitacora",
    "RepositorioDocumentos",
    "RepositorioFrontera",
    "ResultadoInsercion",
    "ResultadoLoteDocumentos",
    "TIEMPO_ESPERA_POR_DEFECTO_MS",
    "ahora_utc",
    "datetime_desde_timestamp",
    "normalizar_datetime_utc",
    "timestamp_utc_iso",
]
