"""Araña única y agnóstica del arañador audiovisual.

La araña no conoce ningún sitio: toma sus URL de inicio del archivo de semillas
compartido ``Progra00/semillas.txt``, deriva de ellas los hosts permitidos y recorre los
enlaces internos con un ``LinkExtractor`` genérico. No hay patrones,
selectores ni expresiones regulares por dominio: la clasificación temática y la
limpieza del texto ocurren más adelante, en las tuberías de Scrapy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator, Iterable
from pathlib import Path
from typing import Any, Final
from urllib.parse import unquote, urldefrag, urlsplit

from scrapy import Request, Spider
from scrapy.http import Response, TextResponse
from scrapy.linkextractors import LinkExtractor
from scrapy.settings import BaseSettings

from arañador.entorno import RAIZ_PROYECTO, leer_texto
from arañador.implementacion.aranas.arana_base import (
    CATEGORIA_DOCUMENTOS,
    DocumentoItemAranador,
)
from arañador.implementacion.configuracion import ConfiguracionAranador
from arañador.implementacion.elementos import DocumentoItem
from arañador.implementacion.utilidades.normalizador_url import (
    es_url_http,
    normalizar_url,
    obtener_host_origen,
)

#: Nombre canónico del archivo de semillas compartido por ambas implementaciones.
NOMBRE_SEMILLAS: Final[str] = "semillas.txt"

#: Ruta por defecto de las semillas, relativa a la raíz del proyecto (``Progra00/``).
SEMILLAS_POR_DEFECTO: Final[str] = "../semillas.txt"

#: Esquemas admitidos tanto en las semillas como en los enlaces recorridos.
ESQUEMAS_PERMITIDOS: Final[frozenset[str]] = frozenset({"http", "https"})


def _resolver_ruta_semillas() -> Path:
    """Resuelve el archivo de semillas configurado en ``SEMILLAS``.

    Las rutas relativas se resuelven contra la raíz del proyecto, no contra el
    directorio de trabajo, para que el resultado no dependa de desde dónde se
    invoque el arañador.
    """

    configurada = leer_texto("SEMILLAS", SEMILLAS_POR_DEFECTO)
    ruta = Path(configurada).expanduser()
    if not ruta.is_absolute():
        ruta = RAIZ_PROYECTO / ruta
    return ruta.resolve(strict=False)


#: Ruta efectiva del archivo de semillas compartido.
RUTA_SEMILLAS: Final[Path] = _resolver_ruta_semillas()


def interpretar_semillas(contenido: str) -> tuple[str, ...]:
    """Convierte el texto de ``semillas.txt`` en una tupla de URL únicas.

    Acepta líneas vacías, comentarios que empiezan con ``#`` y comentarios al
    final de una URL. Cada entrada debe ser una URL HTTP(S) absoluta; los
    duplicados se descartan y el orden de aparición se conserva.

    Args:
        contenido: Texto completo del archivo de semillas.

    Returns:
        Las URL semilla, sin duplicados y en el orden del archivo.

    Raises:
        ValueError: Si una línea con contenido no es una URL HTTP(S) válida.
    """

    semillas: list[str] = []
    claves: set[str] = set()
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        candidata = linea.split("#", 1)[0].strip()
        if not candidata:
            continue
        clave = _clave_canonica(candidata, numero)
        if clave in claves:
            continue
        claves.add(clave)
        semillas.append(candidata)
    return tuple(semillas)


def cargar_semillas(ruta: str | Path | None = None) -> tuple[str, ...]:
    """Lee las URL semilla del archivo compartido ``Progra00/semillas.txt``.

    La ruta se toma del parámetro ``ruta``, o de la variable ``SEMILLAS`` del
    ``.env``, o del valor por defecto ``../semillas.txt`` relativo a la raíz del
    proyecto. Al vivir fuera de cada paquete, las dos implementaciones usan el
    mismo archivo.

    Args:
        ruta: Ruta explícita al archivo de semillas; si se omite, se usa la
            configurada en ``SEMILLAS``.

    Returns:
        Las URL semilla declaradas en el archivo, sin duplicados y en orden.

    Raises:
        ValueError: Si el archivo no existe o no declara ninguna URL semilla válida.
    """

    archivo = Path(ruta).expanduser() if ruta is not None else RUTA_SEMILLAS
    if not archivo.is_file():
        raise ValueError(f"no se encontró el archivo de semillas '{archivo}'")
    semillas = interpretar_semillas(archivo.read_text(encoding="utf-8"))
    if not semillas:
        raise ValueError(f"{archivo.name} no declara ninguna URL semilla")
    return semillas


def derivar_hosts(semillas: Iterable[str]) -> tuple[str, ...]:
    """Deriva los hosts permitidos de las semillas, sin el prefijo ``www.``.

    El resultado conserva el orden de aparición, no repite hosts y no contiene
    ninguna lista escrita a mano: todo dominio permitido procede de una semilla.
    Las entradas que no puedan interpretarse se omiten.

    Args:
        semillas: URL semilla de las que se obtiene el host.

    Returns:
        Los hosts normalizados con :func:`obtener_host_origen`.
    """

    hosts: list[str] = []
    vistos: set[str] = set()
    for semilla in semillas:
        try:
            host = obtener_host_origen(semilla)
        except (TypeError, ValueError):
            continue
        if host and host not in vistos:
            vistos.add(host)
            hosts.append(host)
    return tuple(hosts)


def _clave_canonica(texto: str, numero: int) -> str:
    """Valida una semilla y devuelve su forma canónica para deduplicar."""

    if not es_url_http(texto):
        raise ValueError(
            f"La línea {numero} de {NOMBRE_SEMILLAS} no es una URL HTTP(S) absoluta: {texto!r}"
        )
    try:
        return normalizar_url(texto)
    except ValueError as error:
        raise ValueError(f"La línea {numero} de {NOMBRE_SEMILLAS} no es válida: {error}") from error


class AranaSemillas(Spider):
    """Recorre los hosts derivados de ``semillas.txt`` con reglas generales.

    ``start_urls`` y ``allowed_domains`` se calculan al importar el módulo a
    partir de las semillas y no hay ninguna lista de dominios escrita en el
    código. Cada respuesta se convierte en un documento genérico y el texto
    definitivo lo produce Trafilatura en la tubería de extracción.
    """

    name = "arañador"

    # Semillas de entrada, tal como se declaran en ``arañador/semillas.txt``.
    SEMILLAS: tuple[str, ...] = cargar_semillas()
    # Una solicitud inicial por semilla; ningún dominio se declara a mano.
    start_urls: list[str] = list(SEMILLAS)
    # Hosts permitidos derivados de las semillas, normalizados sin ``www.``.
    allowed_domains: list[str] = list(derivar_hosts(SEMILLAS))
    # Límites y cortesía generales, independientes de la fuente concreta.
    PROFUNDIDAD_MAXIMA: int = 4
    DEMORA_DESCARGA: float = 0.75
    # Exclusiones generales de extensiones, compartidas con los settings.
    EXTENSIONES_EXCLUIDAS: frozenset[str] = frozenset(ConfiguracionAranador.EXTENSIONES_EXCLUIDAS)

    def __init__(self, name: str | None = None, **kwargs: Any) -> None:
        """Inicializa el extractor genérico de enlaces internos."""

        super().__init__(name, **kwargs)
        self._extractor_enlaces = LinkExtractor(
            allow_domains=list(self.allowed_domains),
            deny_extensions=sorted(self.EXTENSIONES_EXCLUIDAS),
            unique=True,
        )
        # Evita repetir la advertencia por cada respuesta de un host ajeno.
        self._hosts_advertidos: set[str] = set()

    @classmethod
    def update_settings(cls, settings: BaseSettings) -> None:
        """Aplica por clase las políticas de profundidad y cortesía.

        Se usa la API de Scrapy y la prioridad ``spider``; así ``-s`` puede
        ajustar la ejecución sin modificar el código fuente.
        """

        super().update_settings(settings)
        settings.set("DEPTH_LIMIT", cls.PROFUNDIDAD_MAXIMA, priority="spider")
        settings.set("DOWNLOAD_DELAY", cls.DEMORA_DESCARGA, priority="spider")
        settings.set("ROBOTSTXT_OBEY", True, priority="spider")

    async def start(self) -> AsyncIterator[Request]:
        """Crea una solicitud inicial por semilla con la API asíncrona actual."""

        # Las semillas son puntos de entrada: se solicitan aunque ya existan
        # para poder redescubrir enlaces. La persistencia sigue evitando
        # duplicados de contenido y la cola persistente conserva el avance.
        for url in self.start_urls:
            yield Request(url, callback=self.parse, meta={"depth": 0}, dont_filter=True)

    def parse(self, response: Response) -> Generator[Request | DocumentoItem, None, None]:
        """Extrae un documento y continúa por los enlaces internos permitidos."""

        if not isinstance(response, TextResponse):
            return
        if not self.es_url_permitida(response.url):
            self._advertir_respuesta_omitida(response.url)
            return

        directivas_robots = self._extraer_directivas_robots(response)

        # Política de cortesía inline: noindex prohíbe almacenar el documento.
        if (
            200 <= response.status < 300
            and "noindex" not in directivas_robots
            and "none" not in directivas_robots
        ):
            yield self._crear_documento(response)

        # Política de cortesía inline: nofollow prohíbe seguir enlaces de la página.
        if "nofollow" not in directivas_robots and "none" not in directivas_robots:
            yield from self._seguir_enlaces(response)

    def _extraer_directivas_robots(self, response: TextResponse) -> set[str]:
        """Extrae directivas de robots inline (<meta name="robots"> y X-Robots-Tag)."""

        directivas: set[str] = set()
        meta_robots = self._meta_contenido(response, "robots")
        if meta_robots:
            for directiva in meta_robots.replace(";", ",").split(","):
                limpia = directiva.strip().casefold()
                if limpia:
                    directivas.add(limpia)

        if hasattr(response, "headers") and response.headers:
            x_robots = response.headers.get("X-Robots-Tag")
            if x_robots:
                texto_x_robots = (
                    x_robots.decode("utf-8", errors="replace")
                    if isinstance(x_robots, bytes)
                    else str(x_robots)
                )
                for directiva in texto_x_robots.replace(";", ",").split(","):
                    limpia = directiva.strip().casefold()
                    if limpia:
                        directivas.add(limpia)

        return directivas

    def es_url_permitida(self, url: str) -> bool:
        """Indica si una URL pertenece a los hosts derivados y no es binaria.

        La comprobación es genérica: esquema HTTP(S), host permitido (o uno de
        sus subdominios), sin credenciales y con una extensión utilizable.
        """

        try:
            partes = urlsplit(url)
        except ValueError:
            return False
        if partes.scheme.casefold() not in ESQUEMAS_PERMITIDOS or not partes.hostname:
            return False
        if partes.username is not None or partes.password is not None:
            return False
        if self._tiene_extension_excluida(partes.path):
            return False
        return any(
            self._host_permitido(partes.hostname, dominio) for dominio in self.allowed_domains
        )

    def _seguir_enlaces(self, response: TextResponse) -> Generator[Request, None, None]:
        """Filtra y agenda los enlaces internos que respetan los límites."""

        profundidad = int(response.meta.get("depth", 0))
        if profundidad >= self.PROFUNDIDAD_MAXIMA:
            return

        for enlace in self._extractor_enlaces.extract_links(response):
            url, _fragmento = urldefrag(response.urljoin(enlace.url))
            if not self.es_url_permitida(url):
                continue
            yield response.follow(
                url,
                callback=self.parse,
                meta={"depth": profundidad + 1},
            )

    def _crear_documento(self, response: TextResponse) -> DocumentoItem:
        """Construye un ``DocumentoItemAranador`` a partir de una respuesta válida."""

        profundidad = int(response.meta.get("depth", 0))
        datos: dict[str, object] = {
            "url": response.url,
            "dominio": obtener_host_origen(response.url),
            "categoria": CATEGORIA_DOCUMENTOS,
            "titulo": self._extraer_titulo(response) or "",
            "idioma": self._extraer_idioma(response) or "",
            "html": response.text,
            "texto": self._extraer_texto(response),
            "metadatos": self._extraer_metadatos(response),
            "codigo_http": response.status,
            "profundidad": profundidad,
        }

        # Esta asignación por campos permite evolucionar el item compartido sin
        # romper la araña, a la vez que conserva HTML, texto y metadatos.
        item = DocumentoItemAranador()
        for clave, valor in datos.items():
            if clave in item.fields:
                item[clave] = valor
        return item

    def _extraer_texto(self, response: TextResponse) -> str:
        """Devuelve el texto completo del HTML; la tubería lo depurará después.

        La araña no intenta aislar el cuerpo principal ni usa selectores por
        dominio: entrega el texto de ``body`` (o del documento completo cuando
        no existe) y Trafilatura aplica la limpieza en la tubería de extracción.
        """

        cuerpo = response.xpath("string(//body)").get(default="")
        texto = cuerpo if cuerpo.strip() else response.text
        return "\n".join(linea.strip() for linea in texto.splitlines() if linea.strip())

    def _extraer_titulo(self, response: TextResponse) -> str | None:
        """Obtiene el título con selectores estándar, sin reglas por dominio."""

        return self._primer_valor(
            response,
            ("string(//h1)", "//meta[@property='og:title']/@content", "//title/text()"),
        )

    def _extraer_idioma(self, response: TextResponse) -> str | None:
        """Obtiene el idioma declarado por la página, si existe."""

        return self._primer_valor(
            response,
            ("//html/@lang", "//meta[@http-equiv='content-language']/@content"),
        )

    def _extraer_metadatos(self, response: TextResponse) -> dict[str, str]:
        """Recopila metadatos estándar sin depender del diseño de cada sitio."""

        canonica = self._primer_valor(response, ("//link[@rel='canonical']/@href",))
        return {
            "descripcion": self._meta_contenido(response, "description") or "",
            "autor": self._meta_contenido(response, "author") or "",
            "fecha_publicacion": self._meta_contenido(response, "article:published_time") or "",
            "url_canonica": response.urljoin(canonica) if canonica else response.url,
        }

    @staticmethod
    def _meta_contenido(response: TextResponse, nombre: str) -> str | None:
        """Devuelve el atributo ``content`` de un ``meta`` por nombre."""

        valor = response.xpath(
            "//meta[translate(@name, $mayus, $minus)=$nombre or "
            "translate(@property, $mayus, $minus)=$nombre]/@content",
            nombre=nombre.casefold(),
            mayus="ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            minus="abcdefghijklmnopqrstuvwxyz",
        ).get()
        return valor.strip() if valor else None

    @staticmethod
    def _primer_valor(response: TextResponse, selectores: tuple[str, ...]) -> str | None:
        """Selecciona el primer texto no vacío de varios selectores XPath."""

        for selector in selectores:
            valor = response.xpath(selector).get()
            if valor and valor.strip():
                return valor.strip()
        return None

    @classmethod
    def _tiene_extension_excluida(cls, ruta: str) -> bool:
        """Comprueba la extensión final de una ruta, sin distinguir mayúsculas."""

        nombre = unquote(urlsplit(ruta).path).rstrip("/").rsplit("/", 1)[-1].casefold()
        return any(nombre.endswith(extension) for extension in cls.EXTENSIONES_EXCLUIDAS)

    @staticmethod
    def _host_permitido(host: str, dominio_permitido: str) -> bool:
        """Compara un host con un dominio permitido o uno de sus subdominios."""

        host = host.casefold().rstrip(".")
        dominio_permitido = dominio_permitido.casefold().rstrip(".")
        return host == dominio_permitido or host.endswith(f".{dominio_permitido}")

    def _advertir_respuesta_omitida(self, url: str) -> None:
        """Registra una sola advertencia por host ajeno a las semillas."""

        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        if host in self._hosts_advertidos:
            return
        self._hosts_advertidos.add(host)
        self.logger.warning(
            "Se omite una respuesta ajena a los hosts de %s: %s",
            NOMBRE_SEMILLAS,
            url,
        )


__all__ = [
    "ESQUEMAS_PERMITIDOS",
    "NOMBRE_SEMILLAS",
    "AranaSemillas",
    "cargar_semillas",
    "derivar_hosts",
    "interpretar_semillas",
]
