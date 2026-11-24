// analizadores.js
// Construye la configuración del índice (settings + mappings) a partir de la
// sección [normalizacion] del .conf, replicando paso a paso el método
// Normalizador.normalizar_texto del indexador propio.
//
// Correspondencia (en el mismo orden que el propio):
//
//   Propio (normalizador.py)                Elasticsearch
//   --------------------------------------  -----------------------------------
//   1. NFKC                                 char_filter icu_normalizer (nfkc)
//   1. comillas y rayas tipográficas        char_filter mapping
//   2. fechas -> <FECHA>                    char_filter pattern_replace
//   2. años -> <ANIO_1994>                  char_filter pattern_replace
//   2. números -> <NUM> / se descartan      char_filter pattern_replace
//   3. RE_TOKEN.findall                     tokenizer pattern con group 0
//   4. minúsculas                           filtro lowercase (+ restaurar marcadores)
//   4. remover acentos (NFKD sin marcas)    filtro icu_transform
//   6. longitud 2..50                       filtro length
//   6. stopwords ES + EN propias            filtro stop con lista propia
//   6. tesauro (reemplazo)                  filtro synonym con reglas "a, b => c"
//   6. stemmer fijo de términos canónicos   filtro stemmer_override
//   6. marcadores <...> sin stemming        filtro keyword_marker
//   6. Snowball según idioma del documento  filtro snowball (un analyzer por idioma)
//
// Requiere el plugin analysis-icu (lo instala el Dockerfile).

import {
  STOPWORDS_ES,
  STOPWORDS_EN,
  REGLAS_TESAURO,
  REGLAS_RAICES_FIJAS,
  FLAGS_REGEX,
  REGEX_FECHA,
  REGEX_ANIO,
  REGEX_NUMERO,
  REGEX_TOKEN,
} from "./recursos.js";
import { construirSimilitud } from "./similitud.js";

export const CAMPO_ES = "contenido_es";
export const CAMPO_EN = "contenido_en";
export const ANALYZER_ES = "politica_es";
export const ANALYZER_EN = "politica_en";

/** Stopwords según idiomas_stopwords (mismos alias que acepta el propio). */
function listaStopwords(idiomas) {
  const lista = new Set();
  for (const idioma of idiomas) {
    if (["es", "espanol", "español", "spanish"].includes(idioma)) STOPWORDS_ES.forEach((w) => lista.add(w));
    if (["en", "ingles", "inglés", "english"].includes(idioma)) STOPWORDS_EN.forEach((w) => lista.add(w));
  }
  return [...lista].sort();
}

/** Char filters: limpieza del texto crudo y marcadores de fechas y números. */
function construirCharFilters(n) {
  const charFilters = {
    nfkc: { type: "icu_normalizer", name: "nfkc", mode: "compose" },
    tipografia: {
      type: "mapping",
      // ' = ' , " = " ,   = espacio (el filtro mapping recorta
      // los espacios sin escapar)
      mappings: [
        "’ => \\u0027",
        "‘ => \\u0027",
        "` => \\u0027",
        "“ => \\u0022",
        "” => \\u0022",
        "« => \\u0022",
        "» => \\u0022",
        "— => \\u0020-\\u0020",
        "– => \\u0020-\\u0020",
      ],
    },
  };
  const orden = ["nfkc", "tipografia"];

  if (n.normalizar_fechas) {
    charFilters.fechas = { type: "pattern_replace", pattern: REGEX_FECHA, replacement: " <FECHA> ", flags: FLAGS_REGEX };
    charFilters.anios = { type: "pattern_replace", pattern: REGEX_ANIO, replacement: " <ANIO_$1> ", flags: FLAGS_REGEX };
    orden.push("fechas", "anios");
  }
  if (n.normalizar_numeros && n.modo_numeros !== "preservar") {
    const reemplazo = n.modo_numeros === "token" ? " <NUM> " : " ";
    charFilters.numeros = { type: "pattern_replace", pattern: REGEX_NUMERO, replacement: reemplazo, flags: FLAGS_REGEX };
    orden.push("numeros");
  }
  return { charFilters, orden };
}

/**
 * Filtros de token. Devuelve los filtros compartidos y la cadena común; el
 * stemmer de cada idioma se agrega al final en construirSettings.
 */
function construirFiltros(n) {
  const filtros = {};
  const cadena = [];

  if (n.minusculas) {
    cadena.push("lowercase");
    // El propio no pasa a minúsculas los marcadores <FECHA>, <NUM> y <ANIO_x>.
    // lowercase no puede saltarse tokens antes del filtro synonym (el filtro
    // condition no se admite ahí), así que se restauran después.
    if (n.normalizar_fechas || (n.normalizar_numeros && n.modo_numeros === "token")) {
      filtros.marcador_fecha = { type: "pattern_replace", pattern: "^<fecha>$", replacement: "<FECHA>" };
      filtros.marcador_num = { type: "pattern_replace", pattern: "^<num>$", replacement: "<NUM>" };
      filtros.marcador_anio = { type: "pattern_replace", pattern: "^<anio_(\\d{4})>$", replacement: "<ANIO_$1>" };
      cadena.push("marcador_fecha", "marcador_num", "marcador_anio");
    }
  }

  if (n.remover_acentos) {
    // Igual que remover_diacriticos del propio: descomponer y quitar marcas.
    filtros.sin_acentos = { type: "icu_transform", id: "NFD; [:Nonspacing Mark:] Remove; NFC" };
    cadena.push("sin_acentos");
  }

  filtros.longitud = { type: "length", min: n.longitud_minima_token, max: n.longitud_maxima_token };
  cadena.push("longitud");

  if (n.stopwords) {
    const lista = listaStopwords(n.idiomas_stopwords);
    if (lista.length > 0) {
      filtros.stopwords_propias = { type: "stop", stopwords: lista, ignore_case: false };
      cadena.push("stopwords_propias");
    }
  }

  if (n.expandir_sinonimos) {
    // Reglas con flecha: la variante se REEMPLAZA por el canónico, como hace
    // el propio. lenient evita que el índice falle si una regla queda vacía
    // después de pasar por los filtros anteriores.
    filtros.tesauro = { type: "synonym", synonyms: REGLAS_TESAURO, lenient: true };
    cadena.push("tesauro");
  }

  if (n.stemming) {
    // Los marcadores no se tocan; los términos canónicos usan siempre la
    // raíz de su idioma (regla especial de aplicar_stemming_palabra).
    filtros.proteger_marcadores = { type: "keyword_marker", keywords_pattern: "<[A-Za-z0-9_]+>" };
    filtros.raices_fijas = { type: "stemmer_override", rules: REGLAS_RAICES_FIJAS };
    filtros.snowball_es = { type: "snowball", language: "Spanish" };
    filtros.snowball_en = { type: "snowball", language: "English" };
    cadena.push("proteger_marcadores", "raices_fijas");
  }

  return { filtros, cadena };
}

/** Settings y mappings completos del índice para una configuración. */
export function construirIndice(config) {
  const n = config.normalizacion;
  const es = config.elasticsearch;

  const { charFilters, orden } = construirCharFilters(n);
  const { filtros, cadena } = construirFiltros(n);

  const analizador = (stemmer) => ({
    type: "custom",
    char_filter: orden,
    tokenizer: "tokens_propio",
    filter: n.stemming ? [...cadena, stemmer] : [...cadena],
  });

  const settings = {
    number_of_shards: es.shards,
    number_of_replicas: es.replicas,
    codec: es.codec,
    refresh_interval: "-1", // se activa al terminar la carga masiva
    "analyze.max_token_count": 1000000, // para verificar documentos completos
    similarity: { tfidf_propio: construirSimilitud(config.modelo) },
    analysis: {
      char_filter: charFilters,
      tokenizer: {
        tokens_propio: { type: "pattern", pattern: REGEX_TOKEN, group: 0, flags: FLAGS_REGEX },
      },
      filter: filtros,
      analyzer: {
        [ANALYZER_ES]: analizador("snowball_es"),
        [ANALYZER_EN]: analizador("snowball_en"),
      },
    },
  };

  const campoTexto = (analyzer) => ({
    type: "text",
    analyzer,
    similarity: "tfidf_propio",
    // El propio guarda en sus postings solo doc_id y tf (sin posiciones).
    index_options: es.guardar_posiciones ? "positions" : "freqs",
    // La norma de longitud de Lucene no se usa: la norma coseno se calcula
    // aparte y se guarda en el campo "norma".
    norms: false,
  });

  const mappings = {
    dynamic: "strict",
    // El texto no se guarda en _source (el propio tampoco guarda el texto);
    // los metadatos sí, para mostrar la URL original en el Proyecto 3.
    _source: es.guardar_texto ? { enabled: true } : { excludes: [CAMPO_ES, CAMPO_EN] },
    properties: {
      doc_id: { type: "integer" },
      db_id: { type: "integer" },
      url: { type: "keyword" },
      hash_url: { type: "keyword" },
      dominio: { type: "keyword" },
      ruta_relativa: { type: "keyword", index: false },
      idioma: { type: "keyword" },
      tamano_bytes: { type: "integer" },
      norma: { type: "float" },
      [CAMPO_ES]: campoTexto(ANALYZER_ES),
      [CAMPO_EN]: campoTexto(ANALYZER_EN),
    },
  };

  return { settings, mappings };
}

/** Campo y analyzer que corresponden a un idioma ("es" o "en"). */
export function campoDeIdioma(idioma) {
  return idioma === "en" ? CAMPO_EN : CAMPO_ES;
}
