"""Contrato tipado de los documentos producidos por las arañas.

El módulo centraliza el Item que atraviesa las tuberías de Scrapy. ``Item``
ofrece una interfaz de mapa dinámica basada en ``Field`` y no admite ``dataclass``
ni ``slots`` sin perder ese comportamiento; por ello los tipos se expresan mediante
anotaciones y la completitud se documenta como contrato previo a persistencia.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from scrapy import Field, Item

CAMPOS_OPCIONALES: Final[frozenset[str]] = frozenset(
    {"fecha_revisitacion", "idioma", "revisitado", "titulo"}
)


def _campo() -> Any:
    """Crea un ``Field`` cuya asignación dinámica encapsula la API de Scrapy.

    Las anotaciones de clase expresan el tipo del valor almacenado en el Item.
    """

    return Field()


class ElementoDocumento(Item):
    """Representa un documento audiovisual y sus metadatos de recolección.

    Los campos obligatorios deben estar completos antes de que el documento
    llegue a la tubería de almacenamiento. Las arañas pueden entregar un Item
    parcial y completar los hashes, la ruta, el tamaño o las fechas en las
    tuberías, siempre que el valor persistido cumpla las reglas indicadas.

    Attributes:
        url: URL canónica y absoluta desde la que se extrajo el contenido.
        hash_url: SHA-256 hexadecimal de la URL normalizada, en minúsculas.
        dominio: Host de la fuente en ASCII, sin esquema y sin ``www.`` inicial.
        categoria: Categoría audiovisual estable, por ejemplo ``guiones`` o
            ``analisis``.
        texto: Texto principal limpio que se almacenará, sin etiquetas HTML.
        ruta_relativa: Ruta POSIX del archivo respecto a la raíz del repositorio;
            no debe contener rutas absolutas ni segmentos ``..``.
        tamano_texto_bytes: Número exacto de bytes UTF-8 escritos para ``texto``.
        hash_contenido: SHA-256 hexadecimal de los bytes UTF-8 que se escribirán.
        codigo_http: Código HTTP final de la respuesta que produjo el documento.
        profundidad: Distancia desde una URL semilla; una semilla tiene valor 0.
        fecha_descarga: Instante de descarga con zona horaria y normalizado a UTC.
        fecha_revisitacion: Próxima revisión sugerida; ``None`` si no aplica.
        titulo: Título informado por la página o extraído del contenido; ``None``
            cuando la fuente no lo proporciona.
        idioma: Código de idioma BCP 47, por ejemplo ``es`` o ``en``; ``None``
            cuando no puede determinarse con confianza.
        revisitado: ``True`` cuando el contenido y la URL ya existían y sólo se
            renovó la fecha de descarga; ``False`` para un documento nuevo.

    Note:
        Los campos textuales y de fecha opcionales adoptan ``None``; ``revisitado`` usa ``False``.
        No se aplican valores inventados a URLs, hashes, métricas o fechas: esos
        datos deben calcularse o validarse durante el procesamiento.
    """

    url: str = _campo()
    hash_url: str = _campo()
    dominio: str = _campo()
    categoria: str = _campo()
    texto: str = _campo()
    ruta_relativa: str = _campo()
    tamano_texto_bytes: int = _campo()
    hash_contenido: str = _campo()
    codigo_http: int = _campo()
    profundidad: int = _campo()
    fecha_descarga: datetime = _campo()
    fecha_revisitacion: datetime | None = _campo()
    titulo: str | None = _campo()
    idioma: str | None = _campo()
    revisitado: bool = _campo()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Crea el Item e inicializa únicamente los campos realmente opcionales."""

        super().__init__(*args, **kwargs)
        for campo in CAMPOS_OPCIONALES - {"revisitado"}:
            self.setdefault(campo, None)
        self.setdefault("revisitado", False)


# Alias de compatibilidad para módulos que denominan el esquema ``DocumentoItem``.
# Ambas rutas apuntan a la misma clase.
DocumentoItem = ElementoDocumento

CAMPOS_OBLIGATORIOS: Final[frozenset[str]] = frozenset(ElementoDocumento.fields).difference(
    CAMPOS_OPCIONALES
)

__all__ = [
    "CAMPOS_OBLIGATORIOS",
    "CAMPOS_OPCIONALES",
    "DocumentoItem",
    "ElementoDocumento",
]
