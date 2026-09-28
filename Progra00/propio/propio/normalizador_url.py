# Copia literal de arañador/arañador/implementacion/utilidades/normalizador_url.py.
# Se duplica (en vez de importarla) para que ambas arañas calculen exactamente
# las mismas URL canónicas y huellas SHA-256 sin que propio dependa de Scrapy.
"""Normalización de URL y cálculo local del dominio registral.

El módulo no realiza solicitudes de red.  La lista de sufijos públicos es
deliberadamente local y conservadora; para sufijos desconocidos se aplica
la aproximación habitual ``eTLD + 1`` (las dos últimas etiquetas), en vez de
intentar descargar una lista de sufijos desde Internet.
"""

from __future__ import annotations

import ipaddress
import unicodedata
from typing import Final
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

__all__ = [
    "PARAMETROS_TRACKING",
    "SUFIJOS_PUBLICOS_LOCALES",
    "SUFFIXES_LOCALES",
    "canonicalizar_url",
    "dominio_registrable",
    "es_url_http",
    "extraer_dominio",
    "extraer_dominio_registrable",
    "normalizar_url",
    "obtener_dominio",
    "obtener_dominio_registrable",
    "obtener_host_origen",
]


# Sufijos incluidos explícitamente para los dominios habituales del proyecto.
# No es una lista exhaustiva: el algoritmo sigue siendo determinista y local
# cuando una etiqueta no aparece aquí.
SUFIJOS_PUBLICOS_LOCALES: Final[frozenset[str]] = frozenset(
    {
        # Sufijos genéricos y nuevos TLD frecuentes.
        "com",
        "org",
        "net",
        "edu",
        "gov",
        "mil",
        "int",
        "info",
        "biz",
        "name",
        "pro",
        "app",
        "dev",
        "ai",
        "io",
        "co",
        "xyz",
        "site",
        "online",
        "blog",
        "news",
        "tech",
        "cloud",
        "space",
        "store",
        "wiki",
        "academy",
        "agency",
        "digital",
        "media",
        "network",
        "systems",
        # América del Norte.
        "us",
        "ca",
        "mx",
        "pr",
        # Europa.
        "uk",
        "de",
        "fr",
        "es",
        "pt",
        "it",
        "nl",
        "be",
        "ch",
        "at",
        "se",
        "no",
        "dk",
        "fi",
        "pl",
        "cz",
        "sk",
        "ie",
        "gr",
        "ro",
        "hu",
        "ru",
        "ua",
        # América Latina.
        "ar",
        "br",
        "cl",
        "pe",
        "ve",
        "uy",
        "py",
        "bo",
        "ec",
        "cr",
        "cu",
        "do",
        "gt",
        "sv",
        "hn",
        "ni",
        "pa",
        # Asia y Oceanía.
        "jp",
        "cn",
        "kr",
        "in",
        "id",
        "sg",
        "my",
        "th",
        "vn",
        "ph",
        "tw",
        "hk",
        "au",
        "nz",
        "za",
        "ng",
        "ke",
        "il",
        "ae",
        # Sufijos compuestos frecuentes.
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "net.uk",
        "sch.uk",
        "com.au",
        "net.au",
        "org.au",
        "edu.au",
        "gov.au",
        "asn.au",
        "id.au",
        "co.nz",
        "net.nz",
        "org.nz",
        "govt.nz",
        "ac.nz",
        "geek.nz",
        "school.nz",
        "com.br",
        "net.br",
        "org.br",
        "gov.br",
        "edu.br",
        "com.mx",
        "org.mx",
        "gob.mx",
        "edu.mx",
        "com.ar",
        "net.ar",
        "org.ar",
        "gov.ar",
        "edu.ar",
        "com.co",
        "net.co",
        "org.co",
        "gov.co",
        "edu.co",
        "com.pe",
        "com.ve",
        "com.uy",
        "com.ec",
        "com.bo",
        "com.py",
        "co.jp",
        "ne.jp",
        "or.jp",
        "ac.jp",
        "go.jp",
        "ad.jp",
        "ed.jp",
        "gr.jp",
        "lg.jp",
        "co.kr",
        "ne.kr",
        "or.kr",
        "re.kr",
        "go.kr",
        "ac.kr",
        "com.cn",
        "net.cn",
        "org.cn",
        "gov.cn",
        "edu.cn",
        "ac.cn",
        "com.hk",
        "org.hk",
        "net.hk",
        "edu.hk",
        "gov.hk",
        "com.sg",
        "net.sg",
        "org.sg",
        "edu.sg",
        "gov.sg",
        "com.tw",
        "net.tw",
        "org.tw",
        "edu.tw",
        "gov.tw",
        "co.in",
        "net.in",
        "org.in",
        "gen.in",
        "firm.in",
        "ind.in",
        "ac.in",
        "edu.in",
        "gov.in",
        "com.my",
        "net.my",
        "org.my",
        "edu.my",
        "gov.my",
        "co.th",
        "in.th",
        "ac.th",
        "go.th",
        "or.th",
        "net.th",
        "co.id",
        "web.id",
        "or.id",
        "ac.id",
        "go.id",
        "sch.id",
        "com.ph",
        "net.ph",
        "org.ph",
        "edu.ph",
        "gov.ph",
        "com.vn",
        "net.vn",
        "org.vn",
        "edu.vn",
        "gov.vn",
        "co.il",
        "org.il",
        "net.il",
        "ac.il",
        "gov.il",
        "com.sa",
        "com.eg",
        "com.ng",
        "com.gh",
        "co.za",
        "org.za",
        "net.za",
        "gov.za",
        "ac.za",
        "co.ke",
        "or.ke",
        "ac.ke",
        "go.ke",
    }
)

# Alias explícito para código que utiliza nombres en inglés.
SUFFIXES_LOCALES: Final[frozenset[str]] = SUFIJOS_PUBLICOS_LOCALES


# Nombres que no aportan una identidad estable del recurso.  Se comparan sin
# distinguir mayúsculas y también se acepta el prefijo ``utm_``.
PARAMETROS_TRACKING: Final[frozenset[str]] = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "ttclid",
        "igshid",
        "igsh",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "ref",
        "referrer",
        "source",
        "campaign_id",
        "campaign",
        "trk",
        "trkcampaign",
        "scid",
        "vero_id",
        "vero_conv",
        "oly_enc_id",
        "oly_anon_id",
        "mkt_tok",
        "s_kwcid",
        "hsctatracking",
        "__hstc",
        "__hssc",
        "_hsenc",
        "_hsmi",
    }
)

_PREFIJOS_TRACKING: Final[tuple[str, ...]] = (
    "utm_",
    "hsa_",
    "pk_",
    "piwik_",
    "matomo_",
    "mtm_",
)

_UNRESERVED: Final[frozenset[int]] = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_ESQUEMAS_SOPORTADOS: Final[frozenset[str]] = frozenset({"http", "https"})


def _validar_texto(valor: str, nombre: str) -> None:
    if not isinstance(valor, str):
        raise TypeError(f"{nombre} debe ser str")
    if any(ord(c) < 32 or ord(c) == 127 for c in valor):
        raise ValueError(f"{nombre} contiene caracteres de control")


def _host_a_idna(host: str) -> str:
    """Convierte un host Unicode a su forma ASCII sin resolverlo por DNS."""

    _validar_texto(host, "host")
    host = unicodedata.normalize("NFC", unquote(host)).strip().rstrip(".")
    if not host:
        raise ValueError("la URL no contiene un host")

    # ``urlsplit`` quita los corchetes de IPv6, pero se admiten entradas directas.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    host = host.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    etiquetas: list[str] = []
    for etiqueta in host.split("."):
        if not etiqueta:
            raise ValueError("el host contiene una etiqueta vacía")
        try:
            ascii_etiqueta = etiqueta.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("el host no puede convertirse a IDNA") from exc
        if len(ascii_etiqueta) > 63:
            raise ValueError("una etiqueta del host supera 63 caracteres")
        if any(
            not (caracter.isascii() and (caracter.isalnum() or caracter in "-_"))
            for caracter in ascii_etiqueta
        ):
            raise ValueError("el host contiene caracteres no permitidos")
        if ascii_etiqueta.startswith("-") or (
            ascii_etiqueta.endswith("-") and not ascii_etiqueta.startswith("xn--")
        ):
            raise ValueError("una etiqueta del host empieza o termina con guion")
        etiquetas.append(ascii_etiqueta.lower())
    resultado = ".".join(etiquetas)
    if len(resultado) > 253:
        raise ValueError("el host supera 253 caracteres")
    return resultado


def _normalizar_percentajes(valor: str, *, seguros: str) -> str:
    """Normaliza escapes existentes y codifica Unicode como UTF-8.

    Los escapes de bytes reservados se conservan, pero se normalizan a
    mayúsculas.  Los escapes de caracteres no reservados se decodifican, lo
    que hace equivalentes, por ejemplo, ``%7E`` y ``~``.
    """

    salida: list[str] = []
    indice = 0
    while indice < len(valor):
        caracter = valor[indice]
        if caracter == "%" and indice + 2 < len(valor):
            hexadecimal = valor[indice + 1 : indice + 3]
            if len(hexadecimal) == 2 and all(c in "0123456789abcdefABCDEF" for c in hexadecimal):
                byte = int(hexadecimal, 16)
                if byte in _UNRESERVED:
                    salida.append(chr(byte))
                else:
                    salida.append(f"%{byte:02X}")
                indice += 3
                continue
        salida.append(quote(caracter, safe=seguros, encoding="utf-8", errors="strict"))
        indice += 1
    return "".join(salida)


def _escapar_utf8(valor: str, *, seguros: str) -> str:
    return quote(
        unicodedata.normalize("NFC", valor), safe=seguros, encoding="utf-8", errors="strict"
    )


def _quitar_dot_segmentos(ruta: str) -> str:
    """Elimina ``.`` y ``..`` sin convertir una ruta relativa en absoluta."""

    if not ruta:
        return ruta
    inicial = ruta.startswith("/")
    final = ruta.endswith("/") and len(ruta) > 1
    pila: list[str] = []
    for segmento in ruta.split("/"):
        if segmento in {"", "."}:
            continue
        if segmento == "..":
            if pila and pila[-1] != "..":
                pila.pop()
            elif not inicial:
                pila.append("..")
        else:
            pila.append(segmento)
    resultado = ("/" if inicial else "") + "/".join(pila)
    if final and resultado and not resultado.endswith("/"):
        resultado += "/"
    return resultado or ("/" if inicial else "")


def _normalizar_ruta(ruta: str, *, canonicalizar: bool) -> str:
    # Los signos RFC 3986 que no tienen significado estructural se mantienen.
    ruta = _normalizar_percentajes(ruta, seguros="/:@-._~!$&'()*+,;=")
    if canonicalizar:
        ruta = _quitar_dot_segmentos(ruta)
    return ruta


def _es_parametro_tracking(nombre: str) -> bool:
    nombre = unquote(nombre).casefold().strip()
    return nombre in PARAMETROS_TRACKING or any(
        nombre.startswith(prefijo) for prefijo in _PREFIJOS_TRACKING
    )


def _normalizar_query(
    query: str,
    *,
    eliminar_tracking: bool,
    canonicalizar: bool,
) -> str:
    if not query:
        return ""
    if not eliminar_tracking and not canonicalizar:
        return query

    pares = parse_qsl(
        query,
        keep_blank_values=True,
        strict_parsing=False,
        encoding="utf-8",
        errors="replace",
    )
    if eliminar_tracking:
        pares = [(clave, valor) for clave, valor in pares if not _es_parametro_tracking(clave)]
    if canonicalizar:
        # Ordenar por nombre conserva el orden relativo de valores repetidos.
        pares.sort(key=lambda par: par[0])
    return "&".join(
        f"{_escapar_utf8(clave, seguros='-._~')}={_escapar_utf8(valor, seguros='-._~')}"
        for clave, valor in pares
    )


def _normalizar_userinfo(usuario: str | None, contraseña: str | None) -> str:
    if usuario is None and contraseña is None:
        return ""
    usuario_normalizado = _escapar_utf8(unquote(usuario or ""), seguros="-._~!$&'()*+,;=:")
    if contraseña is None:
        return usuario_normalizado
    contraseña_normalizada = _escapar_utf8(unquote(contraseña), seguros="-._~!$&'()*+,;=:")
    return f"{usuario_normalizado}:{contraseña_normalizada}"


def normalizar_url(
    url: str,
    *,
    quitar_fragmento: bool = True,
    eliminar_tracking: bool = True,
    canonicalizar: bool = True,
) -> str:
    """Normaliza una URL HTTP(S) de forma determinista y sin red.

    Por defecto elimina el fragmento, quita parámetros de rastreo, ordena los
    parámetros restantes, convierte el host a IDNA, codifica rutas y consultas
    en UTF-8/RFC 3986, resuelve segmentos ``.``/``..`` y omite puertos
    predeterminados.  La barra final y el orden lógico de pares repetidos se
    conservan cuando no tienen una canonicalización inequívoca.

    Args:
        url: URL absoluta o relativa que se normalizará.
        quitar_fragmento: Si es ``True``, no incluye ``#fragmento``.
        eliminar_tracking: Si es ``True``, filtra parámetros conocidos de
            rastreo.
        canonicalizar: Aplica las reglas de host, ruta, puerto y orden de
            consulta.  Las tres opciones pueden desactivarse de forma
            independiente.

    Returns:
        La URL normalizada.

    Raises:
        TypeError: Si ``url`` no es una cadena.
        ValueError: Si la URL tiene un esquema no admitido, un host inválido o
            un puerto inválido.
    """

    if not isinstance(url, str):
        raise TypeError("url debe ser str")
    if not isinstance(quitar_fragmento, bool):
        raise TypeError("quitar_fragmento debe ser bool")
    if not isinstance(eliminar_tracking, bool):
        raise TypeError("eliminar_tracking debe ser bool")
    if not isinstance(canonicalizar, bool):
        raise TypeError("canonicalizar debe ser bool")
    _validar_texto(url, "url")
    url = unicodedata.normalize("NFC", url.strip())
    if not url:
        raise ValueError("la URL no puede estar vacía")

    try:
        partes = urlsplit(url)
    except ValueError as exc:
        raise ValueError("URL mal formada") from exc

    esquema = partes.scheme.casefold()
    if esquema and esquema not in _ESQUEMAS_SOPORTADOS:
        raise ValueError(f"esquema de URL no admitido: {esquema}")
    if esquema and not partes.netloc:
        raise ValueError("una URL HTTP(S) debe contener host")

    host: str | None = None
    puerto: int | None = None
    userinfo = ""
    if partes.netloc:
        try:
            host = partes.hostname
            puerto = partes.port
            userinfo = _normalizar_userinfo(partes.username, partes.password)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("netloc o puerto inválido") from exc
        if host is None:
            raise ValueError("la URL no contiene un host")
        host = _host_a_idna(host)
    elif esquema:
        raise ValueError("una URL HTTP(S) debe contener host")

    ruta = partes.path
    if canonicalizar and not ruta and partes.netloc:
        ruta = "/"
    ruta = _normalizar_ruta(ruta, canonicalizar=canonicalizar)

    if (
        canonicalizar
        and host is not None
        and puerto is not None
        and ((esquema == "http" and puerto == 80) or (esquema == "https" and puerto == 443))
    ):
        puerto = None

    consulta = _normalizar_query(
        partes.query,
        eliminar_tracking=eliminar_tracking,
        canonicalizar=canonicalizar,
    )
    fragmento = (
        ""
        if quitar_fragmento
        else _normalizar_percentajes(partes.fragment, seguros="-._~!$&'()*+,;=:@/?")
    )

    if host is None:
        netloc = ""
    else:
        host_para_url = f"[{host}]" if ":" in host else host
        netloc = f"{userinfo}@{host_para_url}" if userinfo else host_para_url
        if puerto is not None:
            netloc += f":{puerto}"

    return urlunsplit((esquema, netloc, ruta, consulta, fragmento))


def canonicalizar_url(url: str) -> str:
    """Alias explícito de :func:`normalizar_url` con canonicalización completa."""

    return normalizar_url(url)


def _host_de_entrada(url: str) -> str:
    if not isinstance(url, str):
        raise TypeError("url debe ser str")
    _validar_texto(url, "url")
    texto = unicodedata.normalize("NFC", url.strip())
    if not texto:
        raise ValueError("el dominio no puede estar vacío")

    # Acepta tanto ``example.com`` como ``example.com/ruta``.
    try:
        ip_directa = ipaddress.ip_address(texto.split("%", 1)[0])
    except ValueError:
        partes = (
            urlsplit(texto) if "://" in texto or texto.startswith("//") else urlsplit("//" + texto)
        )
    else:
        return str(ip_directa)
    if partes.hostname is None:
        raise ValueError("no se pudo determinar el host")
    return _host_a_idna(partes.hostname)


def extraer_dominio(url: str) -> str:
    """Devuelve el host completo, sin información de puerto."""

    return _host_de_entrada(url)


def _es_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _sufijo_publico(host: str) -> str:
    etiquetas = host.split(".")
    if len(etiquetas) <= 1:
        return etiquetas[-1] if etiquetas else ""
    # Se buscan primero sufijos compuestos (por ejemplo, ``co.uk``).
    for cantidad in range(min(4, len(etiquetas) - 1), 0, -1):
        sufijo = ".".join(etiquetas[-cantidad:])
        if sufijo in SUFIJOS_PUBLICOS_LOCALES:
            return sufijo
    return etiquetas[-1]


def obtener_dominio_registrable(url: str) -> str:
    """Obtiene el dominio registral (``eTLD + 1``) sin DNS ni red.

    Se usa una caché local explícita de sufijos públicos.  Para un TLD no
    incluido, se devuelve la última etiqueta junto con la inmediatamente
    anterior; es una aproximación segura para el rastreo, no una consulta a
    una PSL remota.
    """

    host = _host_de_entrada(url)
    if _es_ip(host) or "." not in host:
        return host
    sufijo = _sufijo_publico(host)
    if not sufijo:
        return host
    numero_etiquetas_sufijo = len(sufijo.split("."))
    etiquetas = host.split(".")
    if len(etiquetas) <= numero_etiquetas_sufijo:
        return host
    return ".".join(etiquetas[-(numero_etiquetas_sufijo + 1) :])


# Alias en español y nombres alternativos para consumidores de la capa de arañas.
extraer_dominio_registrable = obtener_dominio_registrable
dominio_registrable = obtener_dominio_registrable
obtener_dominio = obtener_dominio_registrable


def obtener_host_origen(url: str) -> str:
    """Obtiene el host de la fuente y elimina únicamente el prefijo ``www.``.

    A diferencia del dominio registral, conserva subdominios. Eliminar ``www``
    sólo evita separar dos carpetas del mismo sitio público.
    """

    host = _host_de_entrada(url)
    return host[4:] if host.startswith("www.") else host


def es_url_http(url: str) -> bool:
    """Indica si ``url`` tiene un esquema HTTP o HTTPS y un host válido."""

    if not isinstance(url, str) or not url or any(caracter.isspace() for caracter in url):
        return False
    try:
        partes = urlsplit(url)
    except (AttributeError, TypeError, ValueError):
        return False
    return partes.scheme.casefold() in _ESQUEMAS_SOPORTADOS and bool(partes.hostname)
