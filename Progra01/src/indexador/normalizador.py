"""Motor de normalización lingüística y tokenización multilingüe (español e inglés).

Implementa:
- Limpieza inicial de texto (Unicode NFKC, caracteres de control, comillas tipográficas).
- Detección de patrones temporales (fechas dd/mm/aaaa, yyyy-mm-dd, años cinematográficos 1888-2099).
- Tokenización robusta basada en expresiones regulares tolerante a tildes, diacríticos y guiones.
- Normalización y preservación/descarte semántico de números y fechas.
- Lista completa y rigurosa de stopwords en español e inglés (artículos, preposiciones, pronombres, etc.).
- Stemming multilingüe usando `snowballstemmer` ('spanish' y 'english'), con detección
  automática de idioma por proporción de stopwords o selección manual por configuración.
- Tesauro y diccionario de equivalencias cinematográficas y audiovisuales (ej. 'film' -> 'pelicula',
  'screenplay' -> 'guion', 'director' -> 'realizador', 'movie' -> 'film', etc.).
- Clase `Normalizador` modular y de alto rendimiento que encadena los pasos según la configuración.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from typing import Final

import snowballstemmer

from .configuracion import ConfiguracionNormalizacion

# ==============================================================================
# STOPWORDS EN ESPAÑOL E INGLÉS
# ==============================================================================

STOPWORDS_ESPANOL: Final[frozenset[str]] = frozenset({
    # Artículos
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
    # Preposiciones
    "a", "ante", "bajo", "cabe", "con", "contra", "de", "desde", "durante",
    "en", "entre", "hacia", "hasta", "mediante", "para", "por", "segun", "según",
    "sin", "so", "sobre", "tras", "versus", "via", "vía",
    # Conjunciones
    "y", "e", "ni", "o", "u", "pero", "mas", "sino", "aunque", "porque", "pues",
    "que", "si", "siquiera", "ya", "como", "cuando", "donde", "mientras",
    # Pronombres y posesivos
    "yo", "tu", "tú", "él", "ella", "ellos", "ellas", "nosotros", "nosotras",
    "vosotros", "vosotras", "usted", "ustedes", "me", "te", "se", "nos", "os",
    "le", "les", "mi", "mí", "mis", "tus", "su", "sus", "nuestro", "nuestra",
    "nuestros", "nuestras", "vuestro", "vuestra", "vuestros", "vuestras", "suyo", "suya",
    "suyos", "suyas", "mio", "mío", "mia", "mía", "mios", "míos", "mias", "mías",
    # Demostrativos
    "este", "esta", "estos", "estas", "esto", "ese", "esa", "esos", "esas", "eso",
    "aquel", "aquella", "aquellos", "aquellas", "aquello",
    # Verbos auxiliares y muy frecuentes (ser, estar, haber, tener)
    "es", "son", "era", "eran", "fue", "fueron", "ser", "sido", "siendo", "sea", "sean",
    "está", "estan", "están", "estaba", "estaban", "estuvo", "estuvieron",
    "estar", "estado", "estando", "esté", "esten", "estén",
    "ha", "han", "hay", "habia", "había", "habian", "habían", "hubo", "haber", "habido",
    "habiendo", "haya", "hayan",
    "tiene", "tienen", "tenia", "tenía", "tenian", "tenían", "tuvo", "tuvieron", "tener",
    "tenido", "teniendo", "tenga", "tengan",
    # Adverbios y otros frecuentes
    "no", "sí", "muy", "más", "menos", "tan", "tanto", "tanta", "tantos",
    "tantas", "mucho", "mucha", "muchos", "muchas", "poco", "poca", "pocos", "pocas",
    "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras", "mismo", "misma",
    "mismos", "mismas", "algo", "nada", "alguien", "nadie", "cual", "cuál", "cuales",
    "cuáles", "quien", "quién", "quienes", "quiénes", "cuyo", "cuya", "cuyos", "cuyas",
    "tambien", "también", "tampoco", "solo", "sólo", "solamente", "ahora", "despues",
    "después", "antes", "bien", "asi", "así", "cada",
})

STOPWORDS_INGLES: Final[frozenset[str]] = frozenset({
    # Articles
    "a", "an", "the",
    # Prepositions
    "about", "above", "across", "after", "against", "along", "among", "around", "at",
    "before", "behind", "below", "beneath", "beside", "between", "beyond", "by",
    "down", "during", "except", "for", "from", "in", "inside", "into", "near", "of",
    "off", "on", "onto", "out", "outside", "over", "past", "through", "throughout",
    "to", "toward", "towards", "under", "underneath", "until", "up", "upon", "with",
    "within", "without",
    # Conjunctions
    "and", "but", "or", "nor", "yet", "so", "although", "because", "since",
    "unless", "while", "where", "whereas", "whether", "if", "that", "than",
    # Pronouns & Possessives
    "i", "me", "my", "myself", "we", "us", "our", "ours", "ourselves", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "himself", "she", "her",
    "hers", "herself", "it", "its", "itself", "they", "them", "their", "theirs",
    "themselves", "what", "which", "who", "whom", "this", "these", "those",
    # Auxiliary verbs & forms of to be, to have, to do
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "would", "should", "could", "ought", "may", "might", "must", "can", "shall", "will",
    # Contractions stems/fragments
    "re", "ve", "ll", "d", "m", "t", "s", "don", "didn", "doesn", "hadn", "hasn", "haven",
    "isn", "wasn", "weren", "won", "wouldn", "couldn", "shouldn",
    # Adverbs & common fillers
    "not", "no", "yes", "very", "too", "quite", "more", "most", "less", "least",
    "only", "own", "same", "then", "there", "here", "when", "why", "how",
    "all", "any", "both", "each", "few", "other", "some", "such", "again",
    "further", "once", "now", "just", "also", "even",
})


# ==============================================================================
# TESAURO / SINÓNIMOS AUDIOVISUALES
# ==============================================================================
# Mapea términos del dominio del cine, televisión y guiones hacia un lema o
# concepto canónico, permitiendo que consultas como "movie" o "film" coincidan,
# o "screenplay" y "guion" converjan al mismo concepto semántico.
TESAURO_AUDIOVISUAL: Final[dict[str, str]] = {
    # Cine / Película
    "pelicula": "film",
    "peliculas": "film",
    "película": "film",
    "películas": "film",
    "movie": "film",
    "movies": "film",
    "cinema": "film",
    "cinematografo": "film",
    "cinematógrafo": "film",
    "cinematografia": "film",
    "cinematografía": "film",
    "cinematografico": "film",
    "cinematográfico": "film",
    "motion picture": "film",
    "motion_picture": "film",
    "motion-picture": "film",
    "cine": "film",
    "filme": "film",
    "filmes": "film",
    "feature": "film",

    # Dirección / Realización
    "director": "realizador",
    "directores": "realizador",
    "directora": "realizador",
    "directoras": "realizador",
    "directing": "realizador",
    "filmmaker": "realizador",
    "filmmakers": "realizador",
    "cineasta": "realizador",
    "cineastas": "realizador",

    # Guion / Libreto / Screenplay
    "guion": "screenplay",
    "guiones": "screenplay",
    "guión": "screenplay",
    "guiónes": "screenplay",
    "guionista": "screenwriter",
    "guionistas": "screenwriter",
    "script": "screenplay",
    "scripts": "screenplay",
    "scriptwriter": "screenwriter",
    "screenwriter": "screenwriter",
    "screenwriters": "screenwriter",
    "screenwriting": "screenplay",
    "libreto": "screenplay",
    "libretos": "screenplay",
    "libretista": "screenwriter",

    # Actuación / Reparto / Cast
    "actor": "interprete",
    "actores": "interprete",
    "actriz": "interprete",
    "actrices": "interprete",
    "actuacion": "interprete",
    "actuación": "interprete",
    "reparto": "cast",
    "elenco": "cast",
    "cast": "cast",
    "casting": "cast",
    "interprete": "interprete",
    "intérprete": "interprete",
    "interpretes": "interprete",
    "intérpretes": "interprete",
    "protagonista": "interprete",
    "protagonistas": "interprete",
    "star": "interprete",
    "stars": "interprete",
    "starring": "interprete",

    # Producción
    "productor": "producer",
    "productores": "producer",
    "productora": "producer",
    "productoras": "producer",
    "produccion": "producer",
    "producción": "producer",
    "producer": "producer",
    "producers": "producer",
    "producing": "producer",

    # Formatos y Géneros frecuentes
    "largometraje": "largometraje",
    "largometrajes": "largometraje",
    "cortometraje": "cortometraje",
    "cortometrajes": "cortometraje",
    "short": "cortometraje",
    "shorts": "cortometraje",
    "short film": "cortometraje",
    "documental": "documental",
    "documentales": "documental",
    "documentary": "documental",
    "documentaries": "documental",
    "animacion": "animacion",
    "animación": "animacion",
    "animation": "animacion",
    "animated": "animacion",
    "thriller": "suspenso",
    "suspense": "suspenso",
    "suspenso": "suspenso",

    # Banda Sonora / Música
    "soundtrack": "banda_sonora",
    "soundtracks": "banda_sonora",
    "ost": "banda_sonora",
    "score": "banda_sonora",
    "musica": "banda_sonora",
    "música": "banda_sonora",
    "compositor": "banda_sonora",
    "composer": "banda_sonora",

    # Fotografía y Montaje
    "fotografia": "fotografia",
    "fotografía": "fotografia",
    "cinematography": "fotografia",
    "cameraman": "fotografia",
    "montaje": "edicion",
    "edicion": "edicion",
    "edición": "edicion",
    "editing": "edicion",
    "editor": "edicion",
}


# ==============================================================================
# EXPRESIONES REGULARES PARA TOKENIZACIÓN Y ENTIDADES
# ==============================================================================

# Detección de fechas numéricas:
# Formatos: dd/mm/aaaa, dd-mm-aaaa, aaaa-mm-dd, dd.mm.aaaa
RE_FECHA_COMPLETA = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:18|19|20)\d{2}|(?:18|19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2})\b"
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


def remover_diacriticos(texto: str) -> str:
    """Elimina tildes y diacríticos preservando los caracteres base (ej. 'película' -> 'pelicula').
    
    Nota: La 'ñ' se preserva como 'n' o se puede conservar según se requiera. NFKD + decomposición.
    """
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


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

        # 1. Normalizar fechas completas (ej. 25/12/1977 -> <FECHA>)
        if self.config.normalizar_fechas:
            t = RE_FECHA_COMPLETA.sub(" <FECHA> ", t)
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
