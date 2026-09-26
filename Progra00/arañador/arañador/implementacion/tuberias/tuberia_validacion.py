"""Tubería de normalización y validación de documentos extraídos."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

from scrapy.exceptions import DropItem

from ..utilidades.generador_hash import (
    generar_hash_contenido,
    generar_hash_url,
    normalizar_contenido_para_hash,
)
from ..utilidades.normalizador_url import normalizar_url, obtener_host_origen
from .modelos import DocumentoItem, utc_ahora

# La lista sigue las exclusiones del proyecto, pero se amplía para evitar descargar
# tipos que no pueden producir texto útil aunque una araña los enlace por error.
_EXTENSIONS_PROHIBIDAS = frozenset(
    {
        # Imágenes y diseño.
        "7z",
        "apk",
        "avi",
        "bmp",
        "deb",
        "dmg",
        "exe",
        "gif",
        "gz",
        "ico",
        "iso",
        "jpeg",
        "jpg",
        "m4v",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "mpeg",
        "mpg",
        "odp",
        "ods",
        "odt",
        "pdf",
        "png",
        "ppt",
        "pptx",
        "rar",
        "svg",
        "tar",
        "tgz",
        "tif",
        "tiff",
        "webm",
        "webp",
        "wmv",
        "xls",
        "xlsx",
        "zip",
    }
)

# Palabras que identifican índices, taxonomías y listados sin confundir una
# ficha individual con una película o serie cuyo título contiene «film».
_INDICADORES_LISTA = frozenset(
    {
        "anime",
        "archive",
        "archives",
        "catalog",
        "category",
        "categories",
        "coleccion",
        "directorio",
        "episodes",
        "films",
        "index",
        "lista",
        "list",
        "lists",
        "main",
        "movies",
        "plots",
        "portal",
        "series",
        "shows",
    }
)

_ENTRADA_LISTA = re.compile(
    r"^(?:(?:\d+|[a-zivx]+)[.)]|[•▪◦*-])\s+|.{2,160}?\s+[—–|]\s+.{2,}$",
    re.IGNORECASE | re.UNICODE,
)


class TuberiaValidacion:
    """Normaliza y valida un :class:`DocumentoItem` antes de almacenarlo.

    La longitud mínima de 300 caracteres del proyecto se aplica a páginas
    narrativas.  Las páginas de índice y listas legítimas se identifican por su
    estructura; se exceptúan del umbral de longitud y usan una densidad
    mínima relajada, evitando que un catálogo útil sea descartado por ser corto.

    Args:
        caracteres_minimos: Mínimo de caracteres para contenido no listoso.
        densidad_minima: Proporción mínima de caracteres alfanuméricos entre los
            caracteres no blancos para contenido no listoso.
        densidad_minima_lista: Umbral relajado para una lista reconocida.
        entradas_minimas_lista: Número de entradas necesarias para reconocer una
            lista por su estructura.
        permitir_http: Si es ``False``, HTTPS es obligatorio.
        dias_revisitacion: Días sugeridos después de la descarga cuando la araña
            no informa una fecha de relectura explícita.
        extensiones_prohibidas: Extensiones de ruta que no se procesan.
    """

    def __init__(
        self,
        caracteres_minimos: int = 300,
        densidad_minima: float = 0.35,
        densidad_minima_lista: float = 0.15,
        entradas_minimas_lista: int = 3,
        dias_revisitacion: int = 30,
        *,
        permitir_http: bool = True,
        extensiones_prohibidas: frozenset[str] | set[str] = _EXTENSIONS_PROHIBIDAS,
    ) -> None:
        if isinstance(caracteres_minimos, bool) or not isinstance(caracteres_minimos, int):
            raise TypeError("caracteres_minimos debe ser un entero")
        if caracteres_minimos < 0:
            raise ValueError("caracteres_minimos no puede ser negativo")
        if isinstance(densidad_minima, bool) or not isinstance(densidad_minima, (int, float)):
            raise TypeError("densidad_minima debe ser numérica")
        if not 0.0 <= densidad_minima <= 1.0:
            raise ValueError("densidad_minima debe estar entre 0 y 1")
        if isinstance(densidad_minima_lista, bool) or not isinstance(
            densidad_minima_lista, (int, float)
        ):
            raise TypeError("densidad_minima_lista debe ser numérica")
        if not 0.0 <= densidad_minima_lista <= densidad_minima:
            raise ValueError("densidad_minima_lista debe estar entre 0 y densidad_minima")
        if isinstance(entradas_minimas_lista, bool) or not isinstance(entradas_minimas_lista, int):
            raise TypeError("entradas_minimas_lista debe ser un entero")
        if entradas_minimas_lista < 2:
            raise ValueError("entradas_minimas_lista debe ser al menos 2")
        if not isinstance(permitir_http, bool):
            raise TypeError("permitir_http debe ser booleano")
        if isinstance(dias_revisitacion, bool) or not isinstance(dias_revisitacion, int):
            raise TypeError("dias_revisitacion debe ser un entero")
        if dias_revisitacion < 1:
            raise ValueError("dias_revisitacion debe ser al menos uno")

        self.caracteres_minimos = caracteres_minimos
        self.densidad_minima = densidad_minima
        self.densidad_minima_lista = densidad_minima_lista
        self.entradas_minimas_lista = entradas_minimas_lista
        self.dias_revisitacion = dias_revisitacion
        self.permitir_http = permitir_http
        if any(not isinstance(extension, str) for extension in extensiones_prohibidas):
            raise TypeError("extensiones_prohibidas debe contener cadenas")
        self.extensiones_prohibidas = frozenset(
            extension.strip().lower().lstrip(".") for extension in extensiones_prohibidas
        )

    @classmethod
    def from_crawler(cls, crawler: Any) -> TuberiaValidacion:
        """Construye la tubería usando la API real de Scrapy.

        Args:
            crawler: Instancia de :class:`scrapy.crawler.Crawler`.

        Returns:
            Tubería configurada con ``crawler.settings``.
        """

        settings = crawler.settings
        extensiones_configuradas = settings.get("TUBERIA_EXTENSIONS_PROHIBIDAS", None)
        if extensiones_configuradas is None:
            extensiones_configuradas = settings.getlist(
                "EXTENSIONES_EXCLUIDAS",
                sorted(cls._extensiones_predeterminadas()),
            )
        if isinstance(extensiones_configuradas, str):
            extensiones = extensiones_configuradas.split(",")
        else:
            extensiones = list(extensiones_configuradas)
        if not extensiones or all(not str(valor).strip() for valor in extensiones):
            extensiones = sorted(cls._extensiones_predeterminadas())

        densidad_minima = settings.getfloat("TUBERIA_DENSIDAD_MINIMA", 0.35)
        return cls(
            caracteres_minimos=settings.getint("TUBERIA_LONGITUD_MINIMA", 300),
            densidad_minima=densidad_minima,
            densidad_minima_lista=settings.getfloat(
                "TUBERIA_DENSIDAD_MINIMA_LISTA", min(0.15, densidad_minima)
            ),
            entradas_minimas_lista=settings.getint("TUBERIA_ENTRADAS_LISTA_MINIMAS", 3),
            dias_revisitacion=settings.getint("TUBERIA_DIAS_REVISITACION", 30),
            permitir_http=settings.getbool("TUBERIA_PERMITIR_HTTP", True),
            extensiones_prohibidas=frozenset(extensiones),
        )

    @staticmethod
    def _extensiones_predeterminadas() -> frozenset[str]:
        """Devuelve las extensiones de la tubería, no la variable mutable externa."""

        return _EXTENSIONS_PROHIBIDAS

    def process_item(self, item: Any, spider: Any = None) -> DocumentoItem:
        """Valida y completa un ítem de documento.

        Args:
            item: ``scrapy.Item`` o diccionario con los campos del documento.
            spider: Araña actual; se acepta por compatibilidad con Scrapy, pero no
                es necesario para realizar la validación.

        Returns:
            Una nueva instancia de :class:`DocumentoItem` normalizada.

        Raises:
            DropItem: Si faltan campos esenciales, un tipo es inválido, la URL no
                es HTTP(S), la extensión está prohibida o el texto no supera las
                reglas de longitud y densidad.
        """

        del spider
        datos = self._convertir_a_datos(item)
        url = self._normalizar_url(datos.get("url"))
        texto = self._normalizar_texto(datos.get("texto"))
        dominio = self._normalizar_dominio(datos.get("dominio"), url)

        self._validar_extension(url)
        es_lista = self._es_lista_probable(url, datos.get("titulo"), datos.get("categoria"), texto)
        self._validar_texto(texto, es_lista=es_lista)

        if "codigo_http" not in datos or datos["codigo_http"] is None:
            raise DropItem("El campo 'codigo_http' es obligatorio")
        if "profundidad" not in datos or datos["profundidad"] is None:
            raise DropItem("El campo 'profundidad' es obligatorio")
        codigo_http = self._normalizar_codigo_http(datos["codigo_http"])
        profundidad = self._normalizar_profundidad(datos["profundidad"])
        fecha_descarga = self._normalizar_fecha(
            datos.get("fecha_descarga"), utc_ahora(), campo="fecha_descarga"
        )
        fecha_revisitacion_raw = datos.get("fecha_revisitacion")
        fecha_revisitacion = (
            fecha_descarga + timedelta(days=self.dias_revisitacion)
            if fecha_revisitacion_raw is None or fecha_revisitacion_raw == ""
            else self._normalizar_fecha(
                fecha_revisitacion_raw,
                utc_ahora(),
                campo="fecha_revisitacion",
            )
        )

        categoria = self._normalizar_texto_opcional(datos.get("categoria"), "sin_categoria")
        titulo = self._normalizar_texto_nulo(datos.get("titulo"))
        idioma = self._normalizar_texto_nulo(datos.get("idioma"))
        ruta_relativa = self._normalizar_texto_opcional(datos.get("ruta_relativa"), "")

        documento = DocumentoItem(
            url=url,
            hash_url=generar_hash_url(url),
            dominio=dominio,
            categoria=categoria,
            texto=texto,
            ruta_relativa=ruta_relativa,
            tamano_texto_bytes=len(texto.encode("utf-8")),
            hash_contenido=generar_hash_contenido(texto),
            codigo_http=codigo_http,
            profundidad=profundidad,
            fecha_descarga=fecha_descarga,
            fecha_revisitacion=fecha_revisitacion,
            titulo=titulo,
            idioma=idioma,
        )
        return documento

    @staticmethod
    def _convertir_a_datos(item: Any) -> dict[str, Any]:
        if isinstance(item, DocumentoItem):
            return dict(item)
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "items") and callable(item.items):
            return dict(item.items())
        raise DropItem("El objeto recibido no representa un ítem de documento")

    @staticmethod
    def _normalizar_texto_opcional(valor: Any, predeterminado: str) -> str:
        if valor is None:
            return predeterminado
        if not isinstance(valor, str):
            raise DropItem("Los campos textuales deben ser cadenas")
        normalizado = " ".join(valor.strip().split())
        return normalizado or predeterminado

    @staticmethod
    def _normalizar_texto_nulo(valor: Any) -> str | None:
        if valor is None:
            return None
        if not isinstance(valor, str):
            raise DropItem("Los campos textuales deben ser cadenas")
        return " ".join(valor.split()) or None

    @staticmethod
    def _normalizar_texto(valor: Any) -> str:
        if not isinstance(valor, str):
            raise DropItem("El campo 'texto' es obligatorio y debe ser una cadena")
        if "\x00" in valor:
            raise DropItem("El texto contiene caracteres nulos no permitidos")

        texto = normalizar_contenido_para_hash(valor)
        if not texto:
            raise DropItem("El texto está vacío")
        return texto

    def _normalizar_url(self, valor: Any) -> str:
        if not isinstance(valor, str):
            raise DropItem("El campo 'url' es obligatorio y debe ser una cadena")
        if not valor.strip() or any(caracter.isspace() for caracter in valor):
            raise DropItem("La URL está vacía o contiene espacios")
        if "\\" in valor:
            raise DropItem("La URL contiene barras invertidas no permitidas")

        try:
            url = normalizar_url(valor)
            partes = urlsplit(url)
        except (TypeError, ValueError) as error:
            raise DropItem(f"La URL no es válida: {error}") from error

        esquema = partes.scheme.lower()
        if esquema not in {"http", "https"} or (esquema == "http" and not self.permitir_http):
            raise DropItem("Solo se admiten URL HTTPS y, si está habilitado, HTTP")
        if not partes.hostname or not partes.netloc:
            raise DropItem("La URL debe incluir un dominio")
        if partes.username is not None or partes.password is not None:
            raise DropItem("Las credenciales incrustadas en la URL no se permiten")
        return url

    @staticmethod
    def _normalizar_dominio(valor: Any, url: str) -> str:
        dominio_esperado = obtener_host_origen(url)
        if valor is None or valor == "":
            return dominio_esperado
        if not isinstance(valor, str):
            raise DropItem("El campo 'dominio' debe ser una cadena")
        try:
            dominio = valor.strip().encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as error:
            raise DropItem("El campo 'dominio' no es válido") from error
        if dominio.startswith("www."):
            dominio = dominio[4:]
        if dominio != dominio_esperado:
            raise DropItem("El dominio no coincide con el de la URL")
        return dominio

    def _validar_extension(self, url: str) -> None:
        ruta = unquote(urlsplit(url).path).lower()
        nombre = ruta.rsplit("/", maxsplit=1)[-1]
        if "." not in nombre:
            return
        extension = nombre.rsplit(".", maxsplit=1)[-1]
        if extension in self.extensiones_prohibidas:
            raise DropItem(f"La extensión '.{extension}' no contiene texto limpio")

    def _validar_texto(self, texto: str, *, es_lista: bool) -> None:
        caracteres_visibles = [caracter for caracter in texto if not caracter.isspace()]
        caracteres_alfanumericos = sum(caracter.isalnum() for caracter in caracteres_visibles)
        densidad = (
            caracteres_alfanumericos / len(caracteres_visibles) if caracteres_visibles else 0.0
        )

        if not es_lista and len(texto) < self.caracteres_minimos:
            raise DropItem(
                f"El texto tiene {len(texto)} caracteres; se requieren "
                f"{self.caracteres_minimos} o una estructura de lista válida"
            )
        densidad_requerida = self.densidad_minima_lista if es_lista else self.densidad_minima
        if densidad < densidad_requerida:
            tipo_texto = "lista" if es_lista else "documento"
            raise DropItem(
                f"La densidad textual del {tipo_texto} ({densidad:.2%}) es inferior "
                f"al mínimo ({densidad_requerida:.2%})"
            )

    def _es_lista_probable(
        self,
        url: str,
        titulo: Any,
        categoria: Any,
        texto: str,
    ) -> bool:
        ruta = unquote(urlsplit(url).path).lower()
        nombre_ruta = ruta.rsplit("/", maxsplit=1)[-1]
        palabras_ruta = set(re.findall(r"[a-záéíóúüñ]+", nombre_ruta.replace("_", " ")))
        pistas_texto = " ".join(
            valor.strip().lower() for valor in (titulo, categoria) if isinstance(valor, str)
        )
        palabras_contexto = set(re.findall(r"[a-záéíóúüñ]+", pistas_texto))
        pista_semantica = bool(palabras_ruta & _INDICADORES_LISTA) or bool(
            palabras_contexto & _INDICADORES_LISTA
        )

        lineas = [linea.strip() for linea in texto.splitlines() if linea.strip()]
        if len(lineas) < self.entradas_minimas_lista:
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
            or (len(candidatas) >= self.entradas_minimas_lista and proporcion >= 0.45)
        )

    @staticmethod
    def _normalizar_codigo_http(valor: Any) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise DropItem("El campo 'codigo_http' debe ser un entero")
        if not 100 <= valor <= 599:
            raise DropItem("El código HTTP debe estar entre 100 y 599")
        return valor

    @staticmethod
    def _normalizar_profundidad(valor: Any) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise DropItem("El campo 'profundidad' debe ser un entero")
        if valor < 0:
            raise DropItem("La profundidad no puede ser negativa")
        return valor

    @staticmethod
    def _normalizar_fecha(valor: Any, predeterminado: datetime, *, campo: str) -> datetime:
        if valor is None or valor == "":
            return predeterminado
        if isinstance(valor, datetime):
            fecha = valor
        elif isinstance(valor, str):
            try:
                fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
            except ValueError as error:
                raise DropItem(f"El campo '{campo}' no contiene una fecha ISO válida") from error
        else:
            raise DropItem(f"El campo '{campo}' debe ser datetime o texto ISO")
        if fecha.tzinfo is None:
            return fecha.replace(tzinfo=UTC)
        return fecha.astimezone(UTC)


__all__ = ["TuberiaValidacion"]
