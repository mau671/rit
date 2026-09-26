"""Extracción robusta del texto principal con Trafilatura.

La envoltura mantiene una respuesta de error explícita en vez de propagar
excepciones de la biblioteca.  Trafilatura es la primera opción cuando está
disponible; un extractor HTML mínimo, basado sólo en :mod:`html.parser`, sirve
como respaldo para instalaciones de pruebas o para que una descarga vacía no
interrumpa un lote.
"""

from __future__ import annotations

import inspect
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any, Final

try:  # La dependencia es opcional en el momento de importar utilidades.
    import trafilatura  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - depende del entorno de instalación
    trafilatura = None  # type: ignore[assignment]


__all__ = [
    "EstadoExtraccion",
    "ResultadoExtraccion",
    "MINIMO_CARACTERES",
    "UMBRAL_CARACTERES",
    "UMBRAL_MINIMO_CARACTERES",
    "extraer",
    "extraer_texto",
    "extract_text",
    "extraer_texto_limpio",
    "normalizar_texto_extraido",
]


UMBRAL_MINIMO_CARACTERES: Final[int] = 300
MINIMO_CARACTERES: Final[int] = UMBRAL_MINIMO_CARACTERES
UMBRAL_CARACTERES: Final[int] = UMBRAL_MINIMO_CARACTERES
_ESPACIOS_HORIZONTALES = re.compile(r"[^\S\n]+")
_ESPACIOS_EN_LINEA = re.compile(r"[ \t\f\v]+")
_SALTOS_REPETIDOS = re.compile(r"\n{3,}")
_CARACTERES_CONTROL = frozenset({chr(i) for i in range(32)}) - {"\n", "\r", "\t"}


class EstadoExtraccion(StrEnum):
    """Estados públicos de una extracción."""

    FALLO = "fallo"
    TEXTO_CORTO = "texto_corto"
    EXITO = "exito"

    # Alias de lectura para consumidores que prefieren nombres breves.
    ERROR = "fallo"
    CORTO = "texto_corto"
    OK = "exito"
    VACIO = "fallo"
    SIN_TEXTO = "fallo"


def normalizar_texto_extraido(texto: str) -> str:
    """Normaliza Unicode, NUL, saltos de línea y espacios del resultado.

    No modifica palabras ni puntuación.  Conserva como máximo una línea vacía
    entre párrafos y elimina caracteres de control que no sean tabulador,
    salto de línea o retorno.
    """

    if not isinstance(texto, str):
        raise TypeError("texto debe ser str")
    texto = unicodedata.normalize("NFC", texto).replace("\x00", "")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = "".join(
        caracter for caracter in texto if caracter not in _CARACTERES_CONTROL or caracter in "\n\t"
    )
    texto = _ESPACIOS_HORIZONTALES.sub(" ", texto)
    texto = "\n".join(_ESPACIOS_EN_LINEA.sub(" ", linea).strip() for linea in texto.split("\n"))
    texto = _SALTOS_REPETIDOS.sub("\n\n", texto)
    return texto.strip()


def _decodificar_entrada(contenido: bytes | str) -> str:
    """Decodifica bytes con UTF-8 y reemplaza secuencias inválidas."""

    if isinstance(contenido, str):
        return contenido.replace("\x00", "")
    if not isinstance(contenido, bytes):
        raise TypeError("contenido debe ser bytes o str")
    if contenido.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return contenido.decode("utf-32", errors="replace").replace("\x00", "")
    if contenido.startswith((b"\xff\xfe", b"\xfe\xff")):
        return contenido.decode("utf-16", errors="replace").replace("\x00", "")
    if contenido.startswith(b"\xef\xbb\xbf"):
        return contenido[3:].decode("utf-8", errors="replace").replace("\x00", "")
    try:
        return contenido.decode("utf-8").replace("\x00", "")
    except UnicodeDecodeError:
        # Latin-1/Windows-1252 aparece con frecuencia en páginas antiguas.
        return contenido.decode("cp1252", errors="replace").replace("\x00", "")


class _ExtractorHTMLRespaldo(HTMLParser):
    """Extractor deliberadamente pequeño para el respaldo sin dependencias."""

    _BLOQUES = frozenset(
        {
            "address",
            "article",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "figure",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "td",
            "tfoot",
            "th",
            "thead",
            "tr",
            "ul",
        }
    )
    _IGNORAR = frozenset({"head", "noscript", "script", "style", "template"})

    def __init__(self, *, incluir_tablas: bool, incluir_comentarios: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._incluir_tablas = incluir_tablas
        self._incluir_comentarios = incluir_comentarios
        self._omitir: list[str] = []
        self._partes: list[str] = []

    def _omitido(self) -> bool:
        return bool(self._omitir)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_normalizada = tag.casefold()
        if tag_normalizada in self._IGNORAR or (
            tag_normalizada == "table" and not self._incluir_tablas
        ):
            self._omitir.append(tag_normalizada)
            return
        if not self._omitido() and tag_normalizada in self._BLOQUES:
            self._partes.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_normalizada = tag.casefold()
        if not self._omitido() and tag_normalizada in self._BLOQUES:
            self._partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_normalizada = tag.casefold()
        if self._omitir and self._omitir[-1] == tag_normalizada:
            self._omitir.pop()
        if not self._omitido() and tag_normalizada in self._BLOQUES:
            self._partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._omitido():
            self._partes.append(data)

    def handle_comment(self, data: str) -> None:
        if self._incluir_comentarios and not self._omitido():
            self._partes.extend(("\n", data, "\n"))

    def texto(self) -> str:
        return normalizar_texto_extraido("".join(self._partes))


def _respaldo_html(entrada: str, *, incluir_tablas: bool, incluir_comentarios: bool) -> str:
    parser = _ExtractorHTMLRespaldo(
        incluir_tablas=incluir_tablas,
        incluir_comentarios=incluir_comentarios,
    )
    try:
        parser.feed(entrada)
        parser.close()
    except (ValueError, AssertionError):
        # HTML malformado no debe convertirse en un error fatal del lote.
        return normalizar_texto_extraido(re.sub(r"<[^>]*>", " ", entrada))
    return parser.texto()


def _texto_de_resultado(resultado: Any) -> str | None:
    if resultado is None:
        return None
    if isinstance(resultado, str):
        return resultado
    if isinstance(resultado, bytes):
        return _decodificar_entrada(resultado)
    if isinstance(resultado, dict):
        for clave in ("text", "raw_text", "contenido", "texto"):
            valor = resultado.get(clave)
            if isinstance(valor, str):
                return valor
        return None
    texto = getattr(resultado, "text", None)
    if isinstance(texto, str):
        return texto
    texto = getattr(resultado, "raw_text", None)
    if isinstance(texto, str):
        return texto
    return None


def _argumentos_compatibles(funcion: Any, argumentos: dict[str, Any]) -> dict[str, Any]:
    """Filtra opciones desconocidas de versiones antiguas o de dobles de prueba."""

    try:
        firma = inspect.signature(funcion)
    except (TypeError, ValueError):
        return argumentos
    parametros = firma.parameters.values()
    if any(parametro.kind is inspect.Parameter.VAR_KEYWORD for parametro in parametros):
        return argumentos
    nombres = {parametro.name for parametro in parametros}
    return {nombre: valor for nombre, valor in argumentos.items() if nombre in nombres}


def _invocar_trafilatura(
    html: str,
    *,
    url: str | None,
    favor_precision: bool,
    incluir_comentarios: bool,
    incluir_tablas: bool,
) -> Any:
    funcion = getattr(trafilatura, "extract", None)
    if funcion is None:
        return None
    argumentos: dict[str, Any] = {
        "url": url,
        "favor_precision": favor_precision,
        "include_comments": incluir_comentarios,
        "include_tables": incluir_tablas,
        "output_format": "txt",
    }
    return funcion(html, **_argumentos_compatibles(funcion, argumentos))


@dataclass(frozen=True, slots=True)
class ResultadoExtraccion:
    """Resultado inmutable de :func:`extraer_texto`.

    ``densidad`` es la proporción de caracteres de texto limpio respecto de la
    entrada HTML decodificada, limitada a 1.0.  ``caracteres``, ``palabras`` y
    ``signos`` son métricas de ``texto``; ``signos`` cuenta categorías Unicode
    de puntuación y símbolos.
    """

    texto: str
    estado: EstadoExtraccion
    motivo: str | None = None
    densidad: float = 0.0
    caracteres: int = 0
    palabras: int = 0
    signos: int = 0
    titulo: str | None = None
    url: str | None = None
    idioma: str | None = None
    longitud_html: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.texto, str):
            raise TypeError("texto debe ser str")
        if not isinstance(self.estado, EstadoExtraccion):
            raise TypeError("estado debe ser EstadoExtraccion")
        if self.motivo is not None and not isinstance(self.motivo, str):
            raise TypeError("motivo debe ser str o None")
        if self.titulo is not None and not isinstance(self.titulo, str):
            raise TypeError("titulo debe ser str o None")
        if self.url is not None and not isinstance(self.url, str):
            raise TypeError("url debe ser str o None")
        if self.idioma is not None and not isinstance(self.idioma, str):
            raise TypeError("idioma debe ser str o None")
        if isinstance(self.densidad, bool) or not isinstance(self.densidad, (int, float)):
            raise TypeError("densidad debe ser numérica")
        if isinstance(self.longitud_html, bool) or not isinstance(self.longitud_html, int):
            raise TypeError("longitud_html debe ser int")
        texto = normalizar_texto_extraido(self.texto)
        object.__setattr__(self, "texto", texto)
        object.__setattr__(self, "caracteres", len(texto))
        object.__setattr__(self, "palabras", len(re.findall(r"\b\w+\b", texto, flags=re.UNICODE)))
        object.__setattr__(
            self,
            "signos",
            sum(unicodedata.category(caracter).startswith(("P", "S")) for caracter in texto),
        )
        if self.longitud_html < 0:
            raise ValueError("longitud_html no puede ser negativa")
        if self.densidad < 0 or self.densidad > 1:
            raise ValueError("densidad debe estar entre 0 y 1")
        if self.longitud_html and not self.densidad:
            object.__setattr__(self, "densidad", min(1.0, len(texto) / self.longitud_html))

    @property
    def es_exitoso(self) -> bool:
        return self.estado is EstadoExtraccion.EXITO

    @property
    def es_fallo(self) -> bool:
        return self.estado is EstadoExtraccion.FALLO

    @property
    def es_texto_corto(self) -> bool:
        return self.estado is EstadoExtraccion.TEXTO_CORTO

    @property
    def exito(self) -> bool:
        return self.es_exitoso

    @property
    def ok(self) -> bool:
        return self.es_exitoso

    @property
    def es_valido(self) -> bool:
        return self.estado is EstadoExtraccion.EXITO

    @property
    def valido(self) -> bool:
        return self.es_valido

    @property
    def fallo(self) -> bool:
        return self.es_fallo

    @property
    def corto(self) -> bool:
        return self.es_texto_corto

    @property
    def es_vacio(self) -> bool:
        return not self.texto

    @property
    def numero_caracteres(self) -> int:
        return self.caracteres

    @property
    def numero_palabras(self) -> int:
        return self.palabras

    @property
    def numero_signos(self) -> int:
        return self.signos

    @property
    def num_caracteres(self) -> int:
        return self.caracteres

    @property
    def num_signos(self) -> int:
        return self.signos

    @property
    def cantidad_caracteres(self) -> int:
        return self.caracteres

    @property
    def cantidad_signos(self) -> int:
        return self.signos

    @property
    def longitud(self) -> int:
        return self.caracteres

    @property
    def densidad_relativa(self) -> float:
        return self.densidad

    @property
    def densidad_caracteres(self) -> float:
        return self.densidad

    @property
    def texto_limpio(self) -> str:
        return self.texto

    @property
    def text(self) -> str:
        """Alias del texto limpio para consumidores de la API de Trafilatura."""

        return self.texto

    @property
    def contenido(self) -> str:
        return self.texto

    @property
    def error(self) -> str | None:
        return self.motivo if self.es_fallo else None

    @property
    def motivo_fallo(self) -> str | None:
        return self.error

    @property
    def motivo_error(self) -> str | None:
        return self.error

    @property
    def properties(self) -> dict[str, object]:
        """Mapa de métricas para integraciones que esperan ``properties``."""

        return {
            "estado": self.estado.value,
            "motivo": self.motivo,
            "densidad": self.densidad,
            "caracteres": self.caracteres,
            "num_caracteres": self.caracteres,
            "palabras": self.palabras,
            "signos": self.signos,
            "num_signos": self.signos,
            "texto": self.texto,
            "titulo": self.titulo,
            "url": self.url,
            "idioma": self.idioma,
            "es_exitoso": self.es_exitoso,
        }

    @property
    def propiedades(self) -> dict[str, object]:
        """Alias en español de :attr:`properties`."""

        return self.properties


def _validar_opciones(
    contenido: bytes | str,
    url: str | None,
    titulo: str | None,
    idioma: str | None,
    umbral_caracteres: int,
    favor_precision: bool,
    incluir_comentarios: bool,
    incluir_tablas: bool,
) -> tuple[str, str | None, str | None, str | None]:
    if not isinstance(contenido, (bytes, str)):
        raise TypeError("contenido debe ser bytes o str")
    if url is not None and not isinstance(url, str):
        raise TypeError("url debe ser str o None")
    if titulo is not None and not isinstance(titulo, str):
        raise TypeError("titulo debe ser str o None")
    if idioma is not None and not isinstance(idioma, str):
        raise TypeError("idioma debe ser str o None")
    if isinstance(umbral_caracteres, bool) or not isinstance(umbral_caracteres, int):
        raise TypeError("umbral_caracteres debe ser int")
    if umbral_caracteres < 0:
        raise ValueError("umbral_caracteres no puede ser negativo")
    if not isinstance(favor_precision, bool):
        raise TypeError("favor_precision debe ser bool")
    if not isinstance(incluir_comentarios, bool):
        raise TypeError("include_comments debe ser bool")
    if not isinstance(incluir_tablas, bool):
        raise TypeError("include_tables debe ser bool")
    return (
        _decodificar_entrada(contenido),
        url.strip() if url is not None else None,
        normalizar_texto_extraido(titulo) or None if titulo is not None else None,
        idioma.strip() or None if idioma is not None else None,
    )


def extraer_texto(
    contenido: bytes | str,
    url: str | None = None,
    titulo: str | None = None,
    idioma: str | None = None,
    *,
    umbral_caracteres: int = UMBRAL_MINIMO_CARACTERES,
    favor_precision: bool = True,
    include_comments: bool = False,
    include_tables: bool = False,
) -> ResultadoExtraccion:
    """Extrae y normaliza el texto principal de una página.

    Se pasa ``favor_precision=True``, ``include_comments=False`` y
    ``include_tables=False`` a Trafilatura por defecto.  ``titulo`` es una
    pista de metadatos que se conserva en el resultado; la API actual de
    Trafilatura no recibe un argumento ``title`` para la extracción principal.
    ``idioma`` se conserva como metadato y no se usa para descartar contenido.

    HTML vacío, una entrada sin texto visible o una excepción de la biblioteca
    producen un :class:`ResultadoExtraccion` con ``estado=FALLO``; nunca se
    propaga la excepción. Un texto no vacío por debajo de
    ``umbral_caracteres`` se marca como ``TEXTO_CORTO``, no como fallo.
    ``favor_precision`` se conserva como parámetro explícito para booleanos
    de configuración; su valor predeterminado prioriza la precisión.
    """

    html, url_limpio, titulo_limpio, idioma_limpio = _validar_opciones(
        contenido,
        url,
        titulo,
        idioma,
        umbral_caracteres,
        favor_precision,
        include_comments,
        include_tables,
    )
    if not html.replace("\x00", "").strip():
        return ResultadoExtraccion(
            texto="",
            estado=EstadoExtraccion.FALLO,
            motivo="html_vacio",
            titulo=titulo_limpio,
            url=url_limpio,
            idioma=idioma_limpio,
            longitud_html=len(html),
        )

    texto_crudo: str | None = None
    error_trafilatura: Exception | None = None
    if trafilatura is not None:
        try:
            texto_crudo = _texto_de_resultado(
                _invocar_trafilatura(
                    html,
                    url=url_limpio,
                    favor_precision=favor_precision,
                    incluir_comentarios=include_comments,
                    incluir_tablas=include_tables,
                )
            )
        except Exception as exc:  # La robustez del lote es intencional.
            error_trafilatura = exc
    else:
        error_trafilatura = None

    # Si Trafilatura no devuelve texto, el respaldo conserva el contenido
    # visible para que la tubería de validación aplique sus reglas de densidad.
    # No se filtra por idioma: una página puede declarar ``lang`` de forma
    # incorrecta y aun así contener texto útil.
    if texto_crudo is None:
        texto_respaldo = _respaldo_html(
            html,
            incluir_tablas=include_tables,
            incluir_comentarios=include_comments,
        )
        if texto_respaldo:
            texto_crudo = texto_respaldo

    if texto_crudo is None:
        if error_trafilatura is not None:
            detalle = type(error_trafilatura).__name__
            motivo = f"fallo_de_trafilatura:{detalle}"
        else:
            motivo = "html_sin_texto"
        return ResultadoExtraccion(
            texto="",
            estado=EstadoExtraccion.FALLO,
            motivo=motivo,
            titulo=titulo_limpio,
            url=url_limpio,
            idioma=idioma_limpio,
            longitud_html=len(html),
        )

    texto = normalizar_texto_extraido(texto_crudo)
    if not texto:
        return ResultadoExtraccion(
            texto="",
            estado=EstadoExtraccion.FALLO,
            motivo="html_sin_texto",
            titulo=titulo_limpio,
            url=url_limpio,
            idioma=idioma_limpio,
            longitud_html=len(html),
        )

    estado = (
        EstadoExtraccion.TEXTO_CORTO if len(texto) < umbral_caracteres else EstadoExtraccion.EXITO
    )
    return ResultadoExtraccion(
        texto=texto,
        estado=estado,
        motivo="texto_corto" if estado is EstadoExtraccion.TEXTO_CORTO else None,
        titulo=titulo_limpio,
        url=url_limpio,
        idioma=idioma_limpio,
        longitud_html=len(html),
    )


# Alias públicos para mantener una API breve y una API descriptiva.
extraer = extraer_texto
extraer_texto_limpio = extraer_texto
extract_text = extraer_texto
