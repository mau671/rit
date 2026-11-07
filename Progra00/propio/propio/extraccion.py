"""Paso 3: extracción de texto limpio, enlaces y directivas de robots.

Reemplaza a Trafilatura y al LinkExtractor de Scrapy con una subclase de
``html.parser.HTMLParser`` (biblioteca estándar). En una sola pasada por el
HTML obtiene:

* el texto visible, sin scripts, estilos, menús, encabezados ni pies;
* los enlaces ``<a href>`` convertidos a URL absolutas;
* las directivas ``<meta name="robots">`` (noindex, nofollow);
* el ``<title>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from .configuracion import (
    DENSIDAD_MINIMA,
    DENSIDAD_MINIMA_LISTA,
    MINIMO_CARACTERES,
    MINIMO_CARACTERES_LISTA,
    PALABRAS_MINIMAS,
    PALABRAS_MINIMAS_LISTA,
)

# Todo lo que está dentro de estas etiquetas se descarta: código o maquetación.
ETIQUETAS_IGNORADAS = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "canvas", "iframe",
        "nav", "header", "footer", "aside", "form", "button", "select", "textarea",
        "head",
    }
)  # fmt: skip

# Etiquetas de bloque: al abrir o cerrar se inserta un salto de línea.
ETIQUETAS_BLOQUE = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table",
        "section", "article", "main", "blockquote", "pre", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }
)  # fmt: skip

_ESPACIOS = re.compile(r"[ \t\f\v\r\u00a0]+")
_PATRON_ARTEFACTOS = re.compile(r"^(?:Apariencia\s*\n+)+", re.IGNORECASE)
_PATRON_PALABRA = re.compile(r"[^\W_]{2,}", re.UNICODE)
_TERMINOS_DESAMBIGUACION = (
    "(desambiguación)",
    "(desambiguacion)",
    "(disambiguation)",
    "_desambiguación",
    "_desambiguacion",
    "_disambiguation",
)
_PATRONES_DESAMBIGUACION_TEXTO = (
    "puede referirse a:",
    "pueden referirse a:",
    "puede hacer referencia a:",
    "pueden hacer referencia a:",
    "may refer to:",
    "página de desambiguación",
    "pagina de desambiguacion",
    "disambiguation page",
    "esta es una página de desambiguación",
    "esta es una pagina de desambiguacion",
)
_INDICADORES_LISTA = frozenset(
    {
        "anime", "archive", "archives", "catalog", "category", "categories",
        "coleccion", "directorio", "episodes", "films", "index", "lista",
        "list", "lists", "main", "movies", "plots", "portal", "series", "shows",
    }
)
_ENTRADA_LISTA = re.compile(
    r"^(?:(?:\d+|[a-zivx]+)[.)]|[•▪◦*-])\s+|.{2,160}?\s+[—–|]\s+.{2,}$",
    re.IGNORECASE | re.UNICODE,
)


@dataclass(slots=True)
class ResultadoHTML:
    texto: str
    titulo: str
    enlaces: list[str] = field(default_factory=list)
    directivas_robots: set[str] = field(default_factory=set)


class _Analizador(HTMLParser):
    def __init__(self, url_base: str) -> None:
        super().__init__(convert_charrefs=True)  # convierte &amp; &eacute; etc.
        self.url_base = url_base
        self.partes: list[str] = []
        self.enlaces: list[str] = []
        self.directivas: set[str] = set()
        self.titulo: list[str] = []
        self._ignorando = 0  # profundidad dentro de etiquetas ignoradas
        self._en_pre = 0  # dentro de <pre> se conservan los saltos (guiones de IMSDb)
        self._en_titulo = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = {k.lower(): (v or "") for k, v in attrs}

        # Estas tres cosas se leen aunque estén dentro de <head> o <nav>.
        if tag == "base" and atributos.get("href"):
            self.url_base = urljoin(self.url_base, atributos["href"])
        elif tag == "meta" and atributos.get("name", "").lower() == "robots":
            for directiva in atributos.get("content", "").replace(";", ",").split(","):
                if directiva.strip():
                    self.directivas.add(directiva.strip().lower())
        elif tag == "a" and atributos.get("href"):
            self._agregar_enlace(atributos["href"])
        elif tag == "title":
            self._en_titulo = True

        if tag in ETIQUETAS_IGNORADAS:
            self._ignorando += 1
        elif tag in ETIQUETAS_BLOQUE:
            self.partes.append("\n")
        if tag == "pre":
            self._en_pre += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/>, <meta ... />, <a ... /> — se tratan como etiquetas de inicio.
        self.handle_starttag(tag, attrs)
        if tag in ETIQUETAS_IGNORADAS:
            self._ignorando = max(0, self._ignorando - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._en_titulo = False
        if tag in ETIQUETAS_IGNORADAS:
            self._ignorando = max(0, self._ignorando - 1)
        elif tag in ETIQUETAS_BLOQUE:
            self.partes.append("\n")
        if tag == "pre":
            self._en_pre = max(0, self._en_pre - 1)
        if tag in ("td", "th"):
            self.partes.append(" ")

    def handle_data(self, data: str) -> None:
        if self._en_titulo:
            self.titulo.append(data)
            return
        if self._ignorando:
            return
        if self._en_pre:
            self.partes.append(data)
        else:
            self.partes.append(data.replace("\n", " "))

    def _agregar_enlace(self, href: str) -> None:
        href = href.strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            return
        absoluta, _fragmento = urldefrag(urljoin(self.url_base, href))
        if absoluta.startswith(("http://", "https://")):
            self.enlaces.append(absoluta)


def limpiar_texto(texto: str) -> str:
    """Colapsa espacios, recorta líneas y deja como máximo una línea vacía seguida."""

    lineas = [_ESPACIOS.sub(" ", linea).strip() for linea in texto.split("\n")]
    salida: list[str] = []
    for linea in lineas:
        if linea or (salida and salida[-1]):
            salida.append(linea)
    resultado = "\n".join(salida).strip()
    return _PATRON_ARTEFACTOS.sub("", resultado).strip()


def analizar_html(html: str, url_base: str) -> ResultadoHTML:
    """Procesa el HTML una vez y devuelve texto, título, enlaces y directivas."""

    analizador = _Analizador(url_base)
    try:
        analizador.feed(html)
        analizador.close()
    except Exception:  # HTML muy roto: nos quedamos con lo que alcanzó a leer
        pass
    enlaces = list(dict.fromkeys(analizador.enlaces))  # únicos, en orden
    return ResultadoHTML(
        texto=limpiar_texto("".join(analizador.partes)),
        titulo=" ".join("".join(analizador.titulo).split()),
        enlaces=enlaces,
        directivas_robots=analizador.directivas,
    )


def directivas_de_cabecera(valor: str) -> set[str]:
    """Directivas del encabezado HTTP ``X-Robots-Tag``."""

    return {d.strip().lower() for d in valor.replace(";", ",").split(",") if d.strip()}


def es_desambiguacion(texto: str, *, titulo: str = "", url: str = "") -> bool:
    """Identifica páginas de desambiguación por URL, título o texto."""
    if url:
        url_decodificada = unquote(url).lower()
        if any(termino in url_decodificada for termino in _TERMINOS_DESAMBIGUACION):
            return True
    if titulo:
        titulo_lower = titulo.lower()
        if any(termino in titulo_lower for termino in _TERMINOS_DESAMBIGUACION):
            return True
    lineas_inicio = [linea.strip().lower() for linea in texto.splitlines()[:10] if linea.strip()]
    texto_inicio = " ".join(lineas_inicio)
    return any(patron in texto_inicio for patron in _PATRONES_DESAMBIGUACION_TEXTO)


def es_lista_probable(texto: str, *, titulo: str = "", url: str = "") -> bool:
    """Identifica páginas con estructura preponderante de catálogo o lista."""
    ruta = unquote(urlsplit(url).path).lower() if url else ""
    nombre_ruta = ruta.rsplit("/", maxsplit=1)[-1]
    palabras_ruta = set(re.findall(r"[a-záéíóúüñ]+", nombre_ruta.replace("_", " ")))
    pistas_texto = titulo.strip().lower()
    palabras_contexto = set(re.findall(r"[a-záéíóúüñ]+", pistas_texto))
    pista_semantica = bool(palabras_ruta & _INDICADORES_LISTA) or bool(
        palabras_contexto & _INDICADORES_LISTA
    )

    lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    if len(lineas) < 3:
        return False

    candidatas = [
        linea
        for linea in lineas
        if len(linea) <= 300
        and sum(caracter.isalnum() for caracter in linea) >= 3
        and _ENTRADA_LISTA.search(linea) is not None
    ]
    lineas_con_contenido = [
        linea
        for linea in lineas
        if len(linea) <= 300 and sum(caracter.isalnum() for caracter in linea) >= 3
    ]
    proporcion = len(candidatas) / len(lineas)
    proporcion_contenido = len(lineas_con_contenido) / len(lineas)
    return (
        (pista_semantica and len(candidatas) >= 2)
        or (pista_semantica and proporcion_contenido >= 0.60)
        or (len(candidatas) >= 3 and proporcion >= 0.45)
    )


def motivo_rechazo(
    texto: str,
    *,
    es_lista: bool = False,
    titulo: str = "",
    url: str = "",
) -> str | None:
    """Política de calidad textual, igual que la tubería de validación de Scrapy.

    Devuelve ``None`` si el texto sirve, o el motivo del rechazo.
    """
    if es_desambiguacion(texto, titulo=titulo, url=url):
        return "desambiguacion"

    caracteres_min = MINIMO_CARACTERES_LISTA if es_lista else MINIMO_CARACTERES
    palabras_min = PALABRAS_MINIMAS_LISTA if es_lista else PALABRAS_MINIMAS
    densidad_min = DENSIDAD_MINIMA_LISTA if es_lista else DENSIDAD_MINIMA

    if len(texto) < caracteres_min:
        return f"longitud {len(texto)} < {caracteres_min}"

    palabras = len(_PATRON_PALABRA.findall(texto))
    if palabras < palabras_min:
        return f"palabras {palabras} < {palabras_min}"

    visibles = [c for c in texto if not c.isspace()]
    densidad = sum(c.isalnum() for c in visibles) / len(visibles) if visibles else 0.0
    if densidad < densidad_min:
        return f"densidad {densidad:.0%} < {densidad_min:.0%}"
    return None
