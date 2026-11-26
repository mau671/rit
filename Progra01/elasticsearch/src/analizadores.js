// analizadores.js
// Construye la configuración del índice (settings + mappings) a partir de la
// sección [normalizacion] (tomada del .conf del propio), replicando paso a paso
// el método Normalizador.normalizar_texto del indexador propio.
//
// Correspondencia (en el mismo orden que el propio):
//
//   Propio (normalizador.py)                 Elasticsearch
//   ---------------------------------------  -----------------------------------
//   1. NFKC                                  char_filter icu_normalizer (nfkc)
//   1. comillas y rayas tipográficas         char_filter mapping
//   2. RE_FECHA_COMPLETA -> <FECHA>          char_filter pattern_replace
//   2. RE_FECHA_TEXTUAL -> <FECHA>           char_filter pattern_replace
//   2. RE_ANIO_CINE -> <ANIO_1994>           char_filter pattern_replace
//   2. RE_NUMERO_GENERAL -> <NUM>            char_filter pattern_replace
//   3. RE_TOKEN.findall                      tokenizer pattern con group 0
//   3. minúsculas                            filtro lowercase (+ i turca y restaurar marcadores)
//   4. remover_diacriticos (conserva la ñ)   proteger ñ + icu_transform + restaurar ñ
//   4b. clíticos del inglés ('s, 're...)     filtro pattern_replace
//   6. longitud                              filtro length
//   6. stopwords (recursos/)                 filtro stop con las mismas listas
//   6. tesauro y sinónimos (recursos/)       filtro synonym con reglas "a, b => c"
//   6. raíz fija de términos canónicos       filtro stemmer_override
//   6. marcadores <...> sin stemming         filtro keyword_marker
//   6. Snowball según idioma del documento   filtro snowball (un analyzer por idioma)
//
// Requiere el plugin analysis-icu (lo instala el Dockerfile).

import {
  cargarRecursos,
  FLAGS_REGEX,
  FLAGS_REGEX_SIN_MAYUSCULAS,
  REGEX_FECHA,
  REGEX_FECHA_TEXTUAL,
  REGEX_ANIO,
  REGEX_NUMERO,
  REGEX_TOKEN,
  REGEX_CLITICO,
} from "./recursos.js";
import { removerDiacriticos } from "./idioma.js";
import { construirSimilitud } from "./similitud.js";

export const CAMPO_ES = "contenido_es";
export const CAMPO_EN = "contenido_en";
export const ANALYZER_ES = "politica_es";
export const ANALYZER_EN = "politica_en";

// Caracteres de uso privado para que la ñ sobreviva a la eliminación de tildes
const PROTEGE_ENE = "";
const PROTEGE_ENE_MAYUSCULA = "";

/** Stopwords según idiomas_stopwords (mismos alias que acepta el propio). */
function listaStopwords(idiomas, recursos) {
  const lista = new Set();
  for (const idioma of idiomas) {
    if (["es", "espanol", "español", "spanish"].includes(idioma)) recursos.stopwordsEs.forEach((w) => lista.add(w));
    if (["en", "ingles", "inglés", "english"].includes(idioma)) recursos.stopwordsEn.forEach((w) => lista.add(w));
  }
  return [...lista].sort();
}

const RE_TOKEN_COMPLETO = /^[\p{L}\p{N}]+(?:[-'_][\p{L}\p{N}]+)*$/u;
const VOCALES_CON_TILDE = { a: ["á"], e: ["é"], i: ["í"], o: ["ó"], u: ["ú", "ü"] };

/** Variantes con una tilde (o diéresis) de una palabra sin tildes. */
function variantesConTilde(palabra) {
  const variantes = [];
  [...palabra].forEach((c, i, letras) => {
    for (const conTilde of VOCALES_CON_TILDE[c] ?? []) {
      variantes.push([...letras.slice(0, i), conTilde, ...letras.slice(i + 1)].join(""));
    }
  });
  return variantes;
}

/**
 * Reglas para el filtro synonym que reproducen la búsqueda en el tesauro del
 * propio: primero tesauro.get(token) y, si no está, tesauro.get(sin_tildes(token)).
 *
 * - Una regla por canónico: "variante1, variante2 => canónico" (reemplazo).
 * - Se omiten las variantes que el tokenizador del propio nunca produce como
 *   un solo token (por ejemplo "motion picture").
 * - Si remover_acentos está activo, los tokens ya llegan sin tildes, así que
 *   las variantes con tilde nunca coinciden en el propio y también se omiten.
 * - Si no, se agregan las formas con tilde de cada variante sin tilde (la
 *   segunda búsqueda del propio). Se cubre una tilde por palabra, que es lo
 *   que permite la ortografía del español.
 */
export function reglasTesauro(tesauro, n) {
  const efectivo = new Map();
  for (const [variante, canon] of tesauro) {
    if (!RE_TOKEN_COMPLETO.test(variante)) continue;
    if (n.remover_acentos && removerDiacriticos(variante) !== variante) continue;
    efectivo.set(variante, canon);
  }
  if (!n.remover_acentos) {
    for (const [variante, canon] of [...efectivo]) {
      if (removerDiacriticos(variante) !== variante) continue;
      for (const conTilde of variantesConTilde(variante)) {
        if (!tesauro.has(conTilde) && !efectivo.has(conTilde)) efectivo.set(conTilde, canon);
      }
    }
  }

  const porCanon = new Map();
  for (const [variante, canon] of efectivo) {
    if (variante === canon) continue; // reemplazar por sí mismo no cambia nada
    if (!porCanon.has(canon)) porCanon.set(canon, []);
    porCanon.get(canon).push(variante);
  }
  return [...porCanon].map(([canon, variantes]) => `${variantes.join(", ")} => ${canon}`);
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
    charFilters.fechas_textuales = {
      type: "pattern_replace",
      pattern: REGEX_FECHA_TEXTUAL,
      replacement: " <FECHA> ",
      flags: FLAGS_REGEX_SIN_MAYUSCULAS,
    };
    charFilters.anios = { type: "pattern_replace", pattern: REGEX_ANIO, replacement: " <ANIO_$1> ", flags: FLAGS_REGEX };
    orden.push("fechas", "fechas_textuales", "anios");
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
 * stemmer de cada idioma se agrega al final en construirIndice.
 */
function construirFiltros(n, recursos) {
  const filtros = {};
  const cadena = [];
  const avisos = [];

  if (n.minusculas) {
    // Python pasa "İ" (i turca con punto) a "i" + punto combinado; Lucene la deja
    // como "i" sola. Se convierte antes de lowercase para obtener lo mismo.
    filtros.i_turca = { type: "pattern_replace", pattern: "\u0130", replacement: "i\u0307" };
    cadena.push("i_turca", "lowercase");
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
    // remover_diacriticos del propio quita tildes y diéresis pero conserva la ñ.
    // icu_transform quitaría también la tilde de la ñ, así que antes se cambia
    // la ñ por un carácter de uso privado y después se restaura.
    filtros.proteger_ene = { type: "pattern_replace", pattern: "ñ", replacement: PROTEGE_ENE };
    filtros.proteger_ene_may = { type: "pattern_replace", pattern: "Ñ", replacement: PROTEGE_ENE_MAYUSCULA };
    filtros.sin_acentos = { type: "icu_transform", id: "NFKD; [:Nonspacing Mark:] Remove; NFC" };
    filtros.restaurar_ene = { type: "pattern_replace", pattern: PROTEGE_ENE, replacement: "ñ" };
    filtros.restaurar_ene_may = { type: "pattern_replace", pattern: PROTEGE_ENE_MAYUSCULA, replacement: "Ñ" };
    cadena.push("proteger_ene", "proteger_ene_may", "sin_acentos", "restaurar_ene", "restaurar_ene_may");
  }

  if (n.stopwords) {
    // Paso 4b del propio: "it's" -> "it" antes de filtrar stopwords
    filtros.cliticos = { type: "pattern_replace", pattern: REGEX_CLITICO, replacement: "" };
    cadena.push("cliticos");
  }

  filtros.longitud = { type: "length", min: n.longitud_minima_token, max: n.longitud_maxima_token };
  cadena.push("longitud");

  if (n.stopwords) {
    const lista = listaStopwords(n.idiomas_stopwords, recursos);
    if (lista.length > 0) {
      filtros.stopwords_propias = { type: "stop", stopwords: lista, ignore_case: false };
      cadena.push("stopwords_propias");
    }
  }

  if (n.expandir_sinonimos) {
    // Reglas con flecha: la variante se REEMPLAZA por el canónico, como hace
    // el propio. lenient descarta las reglas cuya variante desaparece con los
    // filtros anteriores (por ejemplo, una stopword), que el propio tampoco aplica.
    const reglas = reglasTesauro(recursos.tesauro, n);
    if (reglas.length > 0) {
      filtros.tesauro = { type: "synonym", synonyms: reglas, lenient: true };
      cadena.push("tesauro");
    }
    // Lucene pasa también el canónico por los filtros anteriores; si eso lo
    // cambia, el resultado ya no sería igual al del propio.
    for (const canon of new Set(recursos.tesauro.values())) {
      const cambiaria =
        (n.remover_acentos && removerDiacriticos(canon) !== canon) ||
        (n.stopwords && (recursos.stopwordsEs.has(canon) || recursos.stopwordsEn.has(canon))) ||
        !RE_TOKEN_COMPLETO.test(canon);
      if (cambiaria) avisos.push(`El canónico "${canon}" del tesauro cambia al analizarlo; revisá esa regla.`);
    }
  }

  if (n.stemming) {
    // Los marcadores no se tocan; los términos canónicos usan siempre la
    // raíz de su idioma (regla especial de aplicar_stemming_palabra).
    filtros.proteger_marcadores = { type: "keyword_marker", keywords_pattern: "<[A-Za-z0-9_]+>" };
    filtros.raices_fijas = { type: "stemmer_override", rules: recursos.raicesFijas };
    filtros.snowball_es = { type: "snowball", language: "Spanish" };
    filtros.snowball_en = { type: "snowball", language: "English" };
    cadena.push("proteger_marcadores", "raices_fijas");
  }

  return { filtros, cadena, avisos };
}

/** Settings y mappings completos del índice para una configuración. */
export function construirIndice(config) {
  const n = config.normalizacion;
  const es = config.elasticsearch;

  const recursos = cargarRecursos(config.rutas.recursos);
  const { charFilters, orden } = construirCharFilters(n);
  const { filtros, cadena, avisos } = construirFiltros(n, recursos);
  for (const aviso of avisos) console.warn(`    Aviso: ${aviso}`);

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
