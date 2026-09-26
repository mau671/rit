"""Helpers para slugs y rutas de almacenamiento seguras.

Las rutas se construyen a partir de componentes sanitizados y nunca aceptan
``..`` o separadores como parte de un nombre de archivo.  La partición por
hash se basa exclusivamente en caracteres hexadecimales del SHA-256.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Final

from ...entorno import RAIZ_PROYECTO, leer_texto

__all__ = [
    "construir_ruta_particionada",
    "crear_ruta_particionada",
    "crear_slug",
    "obtener_raiz_datos",
    "resolver_ruta_datos",
    "generar_ruta_particionada",
    "generar_slug",
    "particiones_hash",
    "particion_hash",
    "particionar_hash",
    "ruta_particionada",
    "ruta_segura",
    "crear_ruta_segura",
    "slug",
    "slugify",
]

_RE_HASH: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{64}$")
_RE_SEPARADORES: Final[re.Pattern[str]] = re.compile(r"[^0-9A-Za-z]+")
VARIABLE_RAIZ_DATOS: Final[str] = "RAIZ_DATOS"


def obtener_raiz_datos() -> Path:
    """Obtiene la raíz de datos declarada en ``.env``.

    Las rutas relativas se resuelven contra la raíz del proyecto, no contra el
    directorio desde el que se invoca el comando.
    """

    configurada = leer_texto(VARIABLE_RAIZ_DATOS)
    base = Path(configurada).expanduser() if configurada else RAIZ_PROYECTO
    if not base.is_absolute():
        base = RAIZ_PROYECTO / base
    return base.resolve(strict=False)


def resolver_ruta_datos(*componentes: str) -> Path:
    """Resuelve una ruta de datos bajo la raíz configurable del proyecto."""

    if not componentes:
        raise ValueError("se requiere al menos un componente de datos")
    partes: list[Path] = []
    for componente in componentes:
        if not isinstance(componente, str) or not componente.strip():
            raise ValueError("los componentes de datos deben ser cadenas no vacías")
        valor = Path(componente)
        if valor.is_absolute() or ".." in valor.parts:
            raise ValueError("los componentes de datos deben ser relativos y seguros")
        partes.append(valor)
    return obtener_raiz_datos().joinpath(*partes)


def _validar_slug_argument(texto: str, max_length: int, separador: str) -> None:
    if not isinstance(texto, str):
        raise TypeError("texto debe ser str")
    if isinstance(max_length, bool) or not isinstance(max_length, int):
        raise TypeError("max_length debe ser int")
    if max_length < 1:
        raise ValueError("max_length debe ser al menos 1")
    if not isinstance(separador, str):
        raise TypeError("separador debe ser str")
    if not separador:
        raise ValueError("separador no puede estar vacío")
    if "/" in separador or "\\" in separador or separador in {".", ".."}:
        raise ValueError("separador no puede contener separadores de ruta")


def _slug_unicode(texto: str) -> str:
    """ProduceASCII cuando es posible y conserva letras Unicode si no lo es."""

    descompuesto = unicodedata.normalize("NFKD", texto)
    ascii_texto = descompuesto.encode("ascii", errors="ignore").decode("ascii")
    if ascii_texto:
        return _RE_SEPARADORES.sub("-", ascii_texto).strip("-").casefold()
    # Un idioma sin escritura ASCII (por ejemplo, chino) no debe desaparecer.
    caracteres = [
        caracter
        for caracter in unicodedata.normalize("NFKC", texto)
        if unicodedata.category(caracter).startswith(("L", "N"))
    ]
    return "-".join("".join(caracteres).split()).strip("-").casefold()


def crear_slug(texto: str, max_length: int = 80, separador: str = "-") -> str:
    """Crea un slug estable y apta para nombres de directorio.

    Primero aplica NFKD y elimina diacríticos; los separadores y signos se
    convierten al parámetro ``separador``.  Si el texto no tiene caracteres
    ASCII, conserva letras y números Unicode para no perder el nombre.  El
    resultado nunca contiene rutas ni espacios.

    Args:
        texto: Texto libre de la que derivar el slug.
        max_length: Longitud máxima del resultado, sin separadores finales.
        separador: Separador entre palabras.

    Returns:
        Slug normalizado, posiblemente vacío si el texto sólo contiene ruido.
    """

    _validar_slug_argument(texto, max_length, separador)
    base = _slug_unicode(texto)
    if separador != "-":
        base = base.replace("-", separador)
    # Colapsa separadores compuestos y recorta al límite de una palabra.
    partes = [parte for parte in base.split(separador) if parte]
    slug = separador.join(partes)
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip(separador)
        corte = slug.rfind(separador)
        if corte > 0:
            slug = slug[:corte].rstrip(separador)
    return slug


slug = crear_slug
slugify = crear_slug
generar_slug = crear_slug


def _validar_hash(hash_documento: str, *, exigir_sha256: bool = False) -> str:
    if not isinstance(hash_documento, str):
        raise TypeError("hash_documento debe ser str")
    valor = hash_documento.strip()
    if exigir_sha256:
        valido = _RE_HASH.fullmatch(valor) is not None
        mensaje = "hash_documento debe ser un SHA-256 hexadecimal de 64 caracteres"
    else:
        valido = bool(re.fullmatch(r"[0-9a-fA-F]{1,64}", valor))
        mensaje = "hash_documento debe contener entre 1 y 64 caracteres hexadecimales"
    if not valido:
        raise ValueError(mensaje)
    return valor.casefold()


def particiones_hash(hash_documento: str, niveles: int = 2) -> tuple[str, ...]:
    """Devuelve prefijos de 2, 4, 6... caracteres para particionar un hash.

    Por ejemplo, un hash que comienza por ``abcd...`` produce
    ``("ab", "abcd")`` con ``niveles=2``.  El nombre completo del hash se
    añade posteriormente por :func:`crear_ruta_particionada`.
    """

    valor = _validar_hash(hash_documento)
    if isinstance(niveles, bool) or not isinstance(niveles, int):
        raise TypeError("niveles debe ser int")
    if niveles < 1 or niveles > 8:
        raise ValueError("niveles debe estar entre 1 y 8")
    return tuple(valor[: indice * 2] for indice in range(1, niveles + 1))


def particion_hash(hash_documento: str, longitud: int = 2) -> str:
    """Devuelve un prefijo hexadecimal de longitud fija."""

    valor = _validar_hash(hash_documento)
    if isinstance(longitud, bool) or not isinstance(longitud, int):
        raise TypeError("longitud debe ser int")
    if longitud < 1 or longitud > len(valor):
        raise ValueError("longitud fuera de rango")
    return valor[:longitud]


def _componente_seguro(valor: str, *, valor_por_defecto: str) -> str:
    if not isinstance(valor, str):
        raise TypeError("los componentes de ruta deben ser str")

    # Se conservan puntos internos para que ``example.com`` sea un segmento
    # natural, pero se neutralizan separadores y traversal (``..``).
    descompuesto = unicodedata.normalize("NFKD", valor)
    ascii_texto = descompuesto.encode("ascii", errors="ignore").decode("ascii")
    if ascii_texto:
        caracteres = [
            caracter if caracter.isascii() and (caracter.isalnum() or caracter in "._-") else "-"
            for caracter in ascii_texto
        ]
    else:
        caracteres = [
            caracter if unicodedata.category(caracter).startswith(("L", "N")) else "-"
            for caracter in unicodedata.normalize("NFKC", valor)
        ]
    limpio = re.sub(r"-+", "-", "".join(caracteres))
    while ".." in limpio:
        limpio = limpio.replace("..", ".")
    limpio = limpio.strip(".-_")
    if not limpio:
        return valor_por_defecto
    return limpio[:120].rstrip(".-_") or valor_por_defecto


def _es_subruta(candidata: Path, base: Path) -> bool:
    try:
        candidata.relative_to(base)
    except ValueError:
        return False
    return True


def ruta_segura(
    base: str | Path,
    *componentes: str,
    valor_por_defecto: str = "documento",
) -> Path:
    """Construye una ruta confinada a ``base`` con componentes sanitizados.

    ``componentes`` se reciben como cadenas, no como rutas, para que ``/`` y
    ``..`` nunca puedan escapar del directorio base.  La comprobación final
    también cubre symlinks ya existentes en la base.
    """

    if not isinstance(base, (str, Path)):
        raise TypeError("base debe ser str o Path")
    if not componentes:
        raise ValueError("se requiere al menos un componente")
    if not isinstance(valor_por_defecto, str) or not valor_por_defecto:
        raise ValueError("valor_por_defecto debe ser str no vacío")
    valor_por_defecto = _componente_seguro(valor_por_defecto, valor_por_defecto="documento")

    base_path = Path(base).expanduser()
    try:
        base_resuelta = base_path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError("no se pudo resolver la ruta base") from exc

    partes: list[str] = []
    for componente in componentes:
        partes.append(_componente_seguro(componente, valor_por_defecto=valor_por_defecto))
    partes = [parte for parte in partes if parte not in {".", ".."}]
    if not partes:
        raise ValueError("no quedan componentes seguros")

    candidata = base_resuelta.joinpath(*partes)
    try:
        candidata_resuelta = candidata.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError("no se pudo resolver la ruta resultante") from exc
    if not _es_subruta(candidata_resuelta, base_resuelta):
        raise ValueError("la ruta resultante escapa de la base")
    return candidata_resuelta


def crear_ruta_particionada(
    base: str | Path,
    dominio: str,
    categoria: str,
    hash_documento: str,
    *,
    sufijo: str = ".txt",
    niveles: int = 1,
) -> Path:
    """Crea ``base/dominio/categoria/prefijos/hash[.txt]``.

    ``dominio`` y ``categoria`` se sanean como componentes de ruta (se
    conservan puntos internos, por ejemplo ``example.com``).  El hash debe ser
    un SHA-256 hexadecimal completo, por lo que el nombre final nunca puede
    contener separadores de ruta.
    """

    if not isinstance(sufijo, str):
        raise TypeError("sufijo debe ser str")
    sufijo_limpio = sufijo.strip()
    if sufijo_limpio and (sufijo_limpio[0] != "." or "/" in sufijo_limpio or "\\" in sufijo_limpio):
        raise ValueError("sufijo debe ser una extensión local segura")
    digest = _validar_hash(hash_documento, exigir_sha256=True)
    prefijos = particiones_hash(digest, niveles=niveles)
    directorio = ruta_segura(base, dominio, categoria, *prefijos, digest)
    return directorio.with_name(digest + sufijo_limpio)


# Alias de nombres usados por las tuberías y por consumidores externos.
ruta_particionada = crear_ruta_particionada
generar_ruta_particionada = crear_ruta_particionada
construir_ruta_particionada = crear_ruta_particionada
particionar_hash = particion_hash
crear_ruta_segura = ruta_segura
