"""Motor de normalización lingüística y tokenización multilingüe (español e inglés).

Implementa:
- Limpieza inicial de texto (Unicode NFKC, caracteres de control, comillas tipográficas).
- Detección de patrones temporales (fechas dd/mm/aaaa, yyyy-mm-dd, años cinematográficos 1888-2099).
- Tokenización robusta basada en expresiones regulares tolerante a tildes, diacríticos y guiones.
- Normalización y preservación/descarte semántico de números y fechas.
- Stopwords, sinónimos y tesauro leídos de ``recursos/`` (los mismos archivos que usa el indexador Lucene).
- Stemming multilingüe usando `snowballstemmer` ('spanish' y 'english'), con detección
  automática de idioma por proporción de stopwords o selección manual por configuración.
- Contracciones del inglés ('it's', "don't") reducidas antes de filtrar stopwords.
- Clase `Normalizador` modular y de alto rendimiento que encadena los pasos según la configuración.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

import snowballstemmer

from .configuracion import ConfiguracionNormalizacion

# ==============================================================================
# RECURSOS COMPARTIDOS (stopwords, sinónimos, tesauro)
# ==============================================================================
# Viven en ``recursos/`` (raíz del proyecto) y no en el código para que el indexador
# Lucene cargue exactamente las mismas listas. ``INDEXADOR_RECURSOS`` permite
# apuntar a otro directorio.
DIRECTORIO_RECURSOS: Final[Path] = Path(
    os.environ.get("INDEXADOR_RECURSOS") or Path(__file__).resolve().parents[2] / "recursos"
)


def _lineas_utiles(nombre: str) -> Iterator[str]:
    """Líneas de un recurso sin comentarios (#) ni líneas vacías."""
    ruta = DIRECTORIO_RECURSOS / nombre
    if not ruta.is_file():
        raise FileNotFoundError(
            f"Recurso de normalización no encontrado: {ruta} "
            "(defina INDEXADOR_RECURSOS si el proyecto no se ejecuta desde su carpeta)"
        )
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#"):
            yield linea


def cargar_lista_palabras(nombre: str) -> frozenset[str]:
    """Lee una lista de una palabra por línea (formato de ``WordlistLoader`` de Lucene)."""
    return frozenset(_lineas_utiles(nombre))


def cargar_sinonimos(*nombres: str) -> dict[str, str]:
    """Lee reglas ``a, b, c => canon`` (formato Solr) y devuelve ``{variante: canon}``.

    Si una variante aparece en dos reglas gana la última, igual que al fusionar
    los archivos en el mismo orden dentro de un ``SynonymMap`` de Lucene.
    """
    mapa: dict[str, str] = {}
    for nombre in nombres:
        for linea in _lineas_utiles(nombre):
            izquierda, separador, canon = linea.partition("=>")
            if not separador:
                raise ValueError(f"{nombre}: regla sin '=>': {linea!r}")
            for variante in izquierda.split(","):
                if variante.strip():
                    mapa[variante.strip().lower()] = canon.strip().lower()
    return mapa


STOPWORDS_ESPANOL: Final[frozenset[str]] = cargar_lista_palabras("stopwords_es.txt")
STOPWORDS_INGLES: Final[frozenset[str]] = cargar_lista_palabras("stopwords_en.txt")

# Sinónimos de obras/formatos/técnica y tesauro de oficios, fusionados en un solo mapa.
TESAURO_AUDIOVISUAL: Final[dict[str, str]] = cargar_sinonimos("sinonimos.txt", "tesauro.txt")


# ==============================================================================
# EXPRESIONES REGULARES PARA TOKENIZACIÓN Y ENTIDADES
# ==============================================================================

# Detección de fechas numéricas:
# Formatos: dd/mm/aaaa, dd-mm-aaaa, aaaa-mm-dd (también con "/")
RE_FECHA_COMPLETA = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:18|19|20)\d{2}|(?:18|19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2})\b"
)

# Fechas escritas con el mes en letras: "25 de diciembre de 1977", "December 25, 1977",
# "25th of December 1977". Solo fechas completas (día + mes + año): "mayo de 1999" es
# una fecha parcial y se deja como texto.
_MESES_ES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_MESES_EN = "january|february|march|april|may|june|july|august|september|october|november|december"
RE_FECHA_TEXTUAL = re.compile(
    rf"\b\d{{1,2}}\s+de\s+(?:{_MESES_ES})\s+de(?:l)?\s+(?:18|19|20)\d{{2}}\b"
    rf"|\b(?:{_MESES_EN})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:18|19|20)\d{{2}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MESES_EN}),?\s+(?:18|19|20)\d{{2}}\b",
    re.IGNORECASE,
)

# Detección de años cinematográficos (1888 - primer filme de la historia - hasta 2099)
RE_ANIO_CINE = re.compile(r"\b(188[89]|189\d|19\d{2}|20\d{2})\b")

# Detección de números en general (enteros o decimales con coma o punto)
RE_NUMERO_GENERAL = re.compile(r"\b\d+(?:[.,]\d+)?\b")

# Tokenizador primario:
# Reconoce tokens especiales de marcado semántico como <NUM>, <FECHA>, <ANIO_1994>
# o palabras compuestas por caracteres alfanuméricos unicode (letras, dígitos),
# permitiendo guiones internos entre letras (ej. 'sci-fi', 'post-credits', 'spider-man')
# y apóstrofes internos para contracciones en inglés (ej. "o'brien", "it's").
RE_TOKEN = re.compile(r"<[A-Z0-9_]+>|[^\W_]+(?:[-'_][^\W_]+)*", re.UNICODE)


# Clíticos del inglés que el tokenizador deja pegados al token ("it's", "I'm", "O'Brien's").
# Se quitan antes de filtrar stopwords; si no, "it's" no coincide con "it" y se cuela al índice.
RE_CLITICO_EN = re.compile(r"'(?:s|re|ve|ll|d|m)$")


def remover_diacriticos(texto: str) -> str:
    """Elimina tildes y diéresis dejando la letra base (ej. 'película' -> 'pelicula').

    La eñe se conserva: 'año' y 'ano' son palabras distintas en español.
    """
    resultado: list[str] = []
    for c in unicodedata.normalize("NFKD", texto):
        if unicodedata.combining(c):
            # la marca U+0303 solo se queda si cuelga de una n (ñ/Ñ)
            if c == "\u0303" and resultado and resultado[-1] in "nN":
                resultado.append(c)
            continue
        resultado.append(c)
    return unicodedata.normalize("NFC", "".join(resultado))


def limpiar_texto_crudo(texto: str) -> str:
    """Aplica normalización Unicode NFKC y reemplaza caracteres de control y comillas raras."""
    if not texto:
        return ""
    # Unicode NFKC para normalizar ligaduras (fi, fl), caracteres de ancho completo, etc.
    normalizado = unicodedata.normalize("NFKC", texto)
    # Estandarizar comillas y apóstrofes tipográficos (`, ’, ‘, “, ”)
    normalizado = normalizado.replace("’", "'").replace("‘", "'").replace("`", "'")
    normalizado = normalizado.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    # Reemplazar guiones tipográficos (em-dash —, en-dash –) por guión regular
    normalizado = normalizado.replace("—", " - ").replace("–", " - ")
    return normalizado


class Normalizador:
    """Motor de normalización lingüística configurable y multilingüe."""

    def __init__(self, configuracion: ConfiguracionNormalizacion | None = None) -> None:
        self.config = configuracion or ConfiguracionNormalizacion()

        # Cargar stopwords según los idiomas seleccionados
        self.stopwords: set[str] = set()
        if self.config.stopwords:
            for lang in self.config.idiomas_stopwords:
                lang_l = lang.lower().strip()
                if lang_l in {"es", "espanol", "español", "spanish"}:
                    self.stopwords.update(STOPWORDS_ESPANOL)
                elif lang_l in {"en", "ingles", "inglés", "english"}:
                    self.stopwords.update(STOPWORDS_INGLES)

        # Instanciar stemmers de Snowball con caché de términos
        self._stemmer_es = snowballstemmer.stemmer("spanish")
        self._stemmer_en = snowballstemmer.stemmer("english")
        self._cache_stem_es: dict[str, str] = {}
        self._cache_stem_en: dict[str, str] = {}

        # Diccionario de tesauro normalizado
        self.tesauro = dict(TESAURO_AUDIOVISUAL)

    def detectar_idioma(self, tokens_o_texto: Sequence[str] | str) -> str:
        """Detecta de forma heurística si el texto está en español ('es') o inglés ('en').
        
        Calcula el conteo de stopwords características en cada idioma.
        Si la diferencia no es determinante, usa la presencia de caracteres exclusivos del español (á, é, í, ó, ú, ñ, ¿, ¡).
        """
        if self.config.idioma in {"es", "en"}:
            return self.config.idioma

        if isinstance(tokens_o_texto, str):
            texto = tokens_o_texto.lower()
            # Señales ortográficas directas de español
            if any(ch in texto for ch in "áéíóúñ¿¡"):
                return "es"
            tokens = [t.lower() for t in RE_TOKEN.findall(texto[:2000])]
        else:
            tokens = [t.lower() for t in tokens_o_texto[:300]]

        if not tokens:
            return "es"

        votos_es = sum(1 for t in tokens if t in STOPWORDS_ESPANOL)
        votos_en = sum(1 for t in tokens if t in STOPWORDS_INGLES)

        if votos_en > votos_es:
            return "en"
        return "es"

    def aplicar_stemming_palabra(self, token: str, idioma: str = "es") -> str:
        """Aplica la reducción de raíces Snowball según el idioma detectado con caché O(1)."""
        if not self.config.stemming:
            return token

        # No alterar tokens especiales como <NUM>, <FECHA>, <ANIO_XXXX>
        if token.startswith("<") and token.endswith(">"):
            return token

        # Si el token proviene de un término mapeado por el tesauro, aplicar el stemmer
        # adecuado para que converja entre idiomas (ej. realizador -> realiz, film -> film, etc.)
        if token in {
            "realizador", "interpret", "interprete", "pelicula", "largometraje",
            "cortometraje", "documental", "animacion", "suspenso", "banda_sonora",
            "fotografia", "edicion",
        }:
            res = self._cache_stem_es.get(token)
            if res is None:
                res = self._stemmer_es.stemWord(token)
                self._cache_stem_es[token] = res
            return res

        if token in {"film", "screenplay", "screenwriter", "cast", "producer"}:
            res = self._cache_stem_en.get(token)
            if res is None:
                res = self._stemmer_en.stemWord(token)
                self._cache_stem_en[token] = res
            return res

        if idioma == "en":
            res = self._cache_stem_en.get(token)
            if res is None:
                res = self._stemmer_en.stemWord(token)
                self._cache_stem_en[token] = res
            return res

        res = self._cache_stem_es.get(token)
        if res is None:
            res = self._stemmer_es.stemWord(token)
            self._cache_stem_es[token] = res
        return res

    def normalizar_fechas_y_numeros_en_texto(self, texto: str) -> str:
        """Reemplaza patrones de fechas y números por marcadores semánticos según la configuración."""
        t = texto

        # 1. Normalizar fechas completas (ej. 25/12/1977 o 25 de diciembre de 1977 -> <FECHA>)
        if self.config.normalizar_fechas:
            t = RE_FECHA_COMPLETA.sub(" <FECHA> ", t)
            t = RE_FECHA_TEXTUAL.sub(" <FECHA> ", t)
            # Años de cine aislados (ej. 1994, 2024 -> <ANIO_1994>)
            t = RE_ANIO_CINE.sub(r" <ANIO_\1> ", t)

        # 2. Normalizar números generales
        if self.config.normalizar_numeros:
            if self.config.modo_numeros == "token":
                t = RE_NUMERO_GENERAL.sub(" <NUM> ", t)
            elif self.config.modo_numeros == "descartar":
                t = RE_NUMERO_GENERAL.sub(" ", t)
            # Si es 'preservar', se dejan tal cual en el texto

        return t

    def normalizar_texto(self, texto: str) -> list[str]:
        """Ejecuta el pipeline completo de normalización sobre un texto y retorna la lista de tokens."""
        if not texto:
            return []

        # Paso 1: Limpieza básica de caracteres y normalización Unicode
        texto_limpio = limpiar_texto_crudo(texto)

        # Paso 2: Normalización opcional de fechas y números a nivel de texto
        if self.config.normalizar_fechas or self.config.normalizar_numeros:
            texto_limpio = self.normalizar_fechas_y_numeros_en_texto(texto_limpio)

        # Paso 3: Identificar tokens preservando mayúsculas/minúsculas para los marcadores
        # Si está activado minúsculas, pasamos a minúsculas salvo marcadores especiales <...>
        tokens_brutos = RE_TOKEN.findall(texto_limpio)
        if not tokens_brutos:
            return []

        if self.config.minusculas:
            tokens_brutos = [
                t if (t.startswith("<") and t.endswith(">")) else t.lower()
                for t in tokens_brutos
            ]

        # Paso 4: Remover acentos si está configurado (no afecta marcadores <...>)
        if self.config.remover_acentos:
            tokens_brutos = [
                t if (t.startswith("<") and t.endswith(">")) else remover_diacriticos(t)
                for t in tokens_brutos
            ]

        # Paso 4b: Clíticos del inglés ('it's' -> 'it'), solo si se filtran stopwords
        if self.config.stopwords:
            tokens_brutos = [
                t if t.startswith("<") else RE_CLITICO_EN.sub("", t) for t in tokens_brutos
            ]

        # Paso 5: Detección heurística de idioma para stemming
        idioma = self.detectar_idioma(tokens_brutos)

        # Paso 6: Filtrado y transformación de tokens
        tokens_finales: list[str] = []
        min_len = self.config.longitud_minima_token
        max_len = self.config.longitud_maxima_token
        filtrar_stopwords = self.config.stopwords and bool(self.stopwords)
        expandir_sinonimos = self.config.expandir_sinonimos
        aplicar_stemming = self.config.stemming

        for tok in tokens_brutos:
            # Respetar tokens especiales
            es_especial = tok.startswith("<") and tok.endswith(">")

            # Longitud
            if not es_especial and not (min_len <= len(tok) <= max_len):
                continue

            # Stopwords
            if filtrar_stopwords and not es_especial and tok in self.stopwords:
                continue

            # Tesauro / Sinónimos audiovisuales
            if expandir_sinonimos and not es_especial:
                # Buscar equivalencia directa o sin tildes
                sinonimo = self.tesauro.get(tok)
                if not sinonimo:
                    tok_sin_tilde = remover_diacriticos(tok)
                    sinonimo = self.tesauro.get(tok_sin_tilde)
                if sinonimo:
                    tok = sinonimo

            # Stemming Snowball
            if aplicar_stemming and not es_especial:
                tok = self.aplicar_stemming_palabra(tok, idioma=idioma)

            tokens_finales.append(tok)

        return tokens_finales

    def iterar_tokens(self, texto: str) -> Iterator[str]:
        """Generador perezoso que produce los tokens normalizados uno a uno."""
        yield from self.normalizar_texto(texto)

    def variantes_de_busqueda(self, token: str) -> list[str]:
        """Devuelve el token y sus posibles reducciones en español e inglés.

        La detección de idioma de una consulta corta es poco fiable: una sola
        palabra en inglés no aporta votos de palabras vacías y termina reducida
        con el stemmer español, lo que produce variantes que no existen en el
        vocabulario (por ejemplo ``actors`` se queda igual en vez de ``actor``).
        El buscador usa esta lista para probar, en orden, las variantes que sí
        estén en el índice.
        """
        if not self.config.stemming:
            return [token]
        if token.startswith("<") and token.endswith(">"):
            return [token]

        variantes = [token]
        for stemmer, cache in (
            (self._stemmer_es, self._cache_stem_es),
            (self._stemmer_en, self._cache_stem_en),
        ):
            reducido = cache.get(token)
            if reducido is None:
                reducido = stemmer.stemWord(token)
                cache[token] = reducido
            if reducido not in variantes:
                variantes.append(reducido)
        return variantes
