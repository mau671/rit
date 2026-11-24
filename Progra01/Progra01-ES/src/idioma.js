// idioma.js
// Detección de idioma por documento, réplica de Normalizador.detectar_idioma
// del indexador propio.
//
// Un analyzer de Elasticsearch no puede decidir qué stemmer usar según el
// documento, así que esa decisión se toma aquí, antes de enviar el documento:
// el texto se manda al campo contenido_es o contenido_en, y cada campo tiene
// su analyzer con el stemmer de su idioma.
//
// Igual que el propio (normalizar_texto llama a detectar_idioma con la lista de
// tokens), se cuentan stopwords de cada idioma entre los primeros 300 tokens
// "brutos" (después de limpiar, marcar fechas/números, tokenizar y pasar a
// minúsculas). Gana inglés solo si tiene estrictamente más votos.

import { STOPWORDS_ES, STOPWORDS_EN } from "./recursos.js";

const SET_ES = new Set(STOPWORDS_ES);
const SET_EN = new Set(STOPWORDS_EN);

// Equivalentes en JavaScript de las regex de Python. En JS, \w, \d y \b solo
// reconocen ASCII aunque se use el flag u, así que se escriben con clases
// Unicode: una "letra o número" de Python es [\p{L}\p{N}].
const W = String.raw`[\p{L}\p{N}_]`;
const INI = String.raw`(?<!${W})`; // equivale a \b al inicio
const FIN = String.raw`(?!${W})`; // equivale a \b al final
const D = String.raw`\p{Nd}`;

const RE_FECHA = new RegExp(
  `${INI}(?:${D}{1,2}[/-]${D}{1,2}[/-](?:18|19|20)${D}{2}|(?:18|19|20)${D}{2}[/-]${D}{1,2}[/-]${D}{1,2})${FIN}`,
  "gu",
);
const RE_ANIO = new RegExp(`${INI}(188[89]|189${D}|19${D}{2}|20${D}{2})${FIN}`, "gu");
const RE_NUMERO = new RegExp(`${INI}${D}+(?:[.,]${D}+)?${FIN}`, "gu");
const RE_TOKEN = /<[A-Z0-9_]+>|[\p{L}\p{N}]+(?:[-'_][\p{L}\p{N}]+)*/gu;

const MAX_TOKENS = 300;
const PREFIJO_CARACTERES = 20000;

/** limpiar_texto_crudo del propio: NFKC, comillas y rayas tipográficas. */
export function limpiarTextoCrudo(texto) {
  return texto
    .normalize("NFKC")
    .replace(/[’‘`]/g, "'")
    .replace(/[“”«»]/g, '"')
    .replace(/[—–]/g, " - ");
}

/** normalizar_fechas_y_numeros_en_texto del propio. */
function marcarFechasYNumeros(texto, n) {
  let t = texto;
  if (n.normalizar_fechas) {
    t = t.replace(RE_FECHA, " <FECHA> ");
    t = t.replace(RE_ANIO, " <ANIO_$1> ");
  }
  if (n.normalizar_numeros) {
    if (n.modo_numeros === "token") t = t.replace(RE_NUMERO, " <NUM> ");
    else if (n.modo_numeros === "descartar") t = t.replace(RE_NUMERO, " ");
  }
  return t;
}

/** Tokens "brutos" tal como los ve detectar_idioma en el propio. */
export function tokensBrutos(texto, n) {
  let t = limpiarTextoCrudo(texto);
  if (n.normalizar_fechas || n.normalizar_numeros) t = marcarFechasYNumeros(t, n);

  let tokens = t.match(RE_TOKEN) ?? [];
  const esMarcador = (x) => x.startsWith("<") && x.endsWith(">");
  if (n.minusculas) tokens = tokens.map((x) => (esMarcador(x) ? x : x.toLowerCase()));
  if (n.remover_acentos) {
    tokens = tokens.map((x) => (esMarcador(x) ? x : x.normalize("NFKD").replace(/\p{M}/gu, "")));
  }
  return tokens;
}

/**
 * Devuelve "es" o "en". Si la configuración fija el idioma, lo respeta.
 * Por rendimiento analiza solo el inicio del texto: si ese prefijo ya trae
 * suficientes tokens, el resultado es idéntico a analizar el texto completo.
 */
export function detectarIdioma(texto, n) {
  if (n.idioma === "es" || n.idioma === "en") return n.idioma;

  let tokens = tokensBrutos(texto.slice(0, PREFIJO_CARACTERES), n);
  if (tokens.length < MAX_TOKENS + 10 && texto.length > PREFIJO_CARACTERES) {
    tokens = tokensBrutos(texto, n);
  }
  tokens = tokens.slice(0, MAX_TOKENS);
  if (tokens.length === 0) return "es";

  let votosEs = 0;
  let votosEn = 0;
  for (const tok of tokens) {
    if (SET_ES.has(tok)) votosEs++;
    if (SET_EN.has(tok)) votosEn++;
  }
  return votosEn > votosEs ? "en" : "es";
}
