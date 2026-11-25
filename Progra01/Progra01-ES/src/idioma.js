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
// "brutos": después de limpiar, marcar fechas y números, tokenizar, pasar a
// minúsculas, quitar tildes (si está activo) y quitar clíticos del inglés (si
// se filtran stopwords). Gana inglés solo si tiene estrictamente más votos.

// Equivalentes en JavaScript de las regex de Python. En JS, \w, \d y \b solo
// reconocen ASCII aunque se use el flag u, así que se escriben con clases
// Unicode: una "letra o número" de Python es [\p{L}\p{N}].
const W = String.raw`[\p{L}\p{N}_]`;
const INI = String.raw`(?<!${W})`; // equivale a \b al inicio
const FIN = String.raw`(?!${W})`; // equivale a \b al final
const D = String.raw`\p{Nd}`;
const MESES_ES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre";
const MESES_EN = "january|february|march|april|may|june|july|august|september|october|november|december";

const RE_FECHA = new RegExp(
  `${INI}(?:${D}{1,2}[/-]${D}{1,2}[/-](?:18|19|20)${D}{2}|(?:18|19|20)${D}{2}[/-]${D}{1,2}[/-]${D}{1,2})${FIN}`,
  "gu",
);
const RE_FECHA_TEXTUAL = new RegExp(
  `${INI}${D}{1,2}\\s+de\\s+(?:${MESES_ES})\\s+de(?:l)?\\s+(?:18|19|20)${D}{2}${FIN}` +
    `|${INI}(?:${MESES_EN})\\s+${D}{1,2}(?:st|nd|rd|th)?,?\\s+(?:18|19|20)${D}{2}${FIN}` +
    `|${INI}${D}{1,2}(?:st|nd|rd|th)?\\s+(?:of\\s+)?(?:${MESES_EN}),?\\s+(?:18|19|20)${D}{2}${FIN}`,
  "giu",
);
const RE_ANIO = new RegExp(`${INI}(188[89]|189${D}|19${D}{2}|20${D}{2})${FIN}`, "gu");
const RE_NUMERO = new RegExp(`${INI}${D}+(?:[.,]${D}+)?${FIN}`, "gu");
const RE_TOKEN = /<[A-Z0-9_]+>|[\p{L}\p{N}]+(?:[-'_][\p{L}\p{N}]+)*/gu;
const RE_CLITICO = /'(?:s|re|ve|ll|d|m)$/u;

const MAX_TOKENS = 300;
const PREFIJO_CARACTERES = 20000;

const esMarcador = (x) => x.startsWith("<") && x.endsWith(">");

/** limpiar_texto_crudo del propio: NFKC, comillas y rayas tipográficas. */
export function limpiarTextoCrudo(texto) {
  return texto
    .normalize("NFKC")
    .replace(/[’‘`]/g, "'")
    .replace(/[“”«»]/g, '"')
    .replace(/[—–]/g, " - ");
}

/** remover_diacriticos del propio: quita tildes y diéresis, pero conserva la ñ. */
export function removerDiacriticos(texto) {
  let resultado = "";
  for (const c of texto.normalize("NFKD")) {
    if (/\p{Mn}/u.test(c)) {
      if (c === "̃" && /[nN]$/.test(resultado)) resultado += c;
      continue;
    }
    resultado += c;
  }
  return resultado.normalize("NFC");
}

/** normalizar_fechas_y_numeros_en_texto del propio. */
function marcarFechasYNumeros(texto, n) {
  let t = texto;
  if (n.normalizar_fechas) {
    t = t.replace(RE_FECHA, " <FECHA> ");
    t = t.replace(RE_FECHA_TEXTUAL, " <FECHA> ");
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
  if (n.minusculas) tokens = tokens.map((x) => (esMarcador(x) ? x : x.toLowerCase()));
  if (n.remover_acentos) tokens = tokens.map((x) => (esMarcador(x) ? x : removerDiacriticos(x)));
  if (n.stopwords) tokens = tokens.map((x) => (x.startsWith("<") ? x : x.replace(RE_CLITICO, "")));
  return tokens;
}

/**
 * Devuelve "es" o "en". Si la configuración fija el idioma, lo respeta.
 * recursos: { stopwordsEs, stopwordsEn } (ver recursos.js).
 * Por rendimiento analiza solo el inicio del texto: si ese prefijo ya trae
 * suficientes tokens, el resultado es idéntico a analizar el texto completo.
 */
export function detectarIdioma(texto, n, recursos) {
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
    if (recursos.stopwordsEs.has(tok)) votosEs++;
    if (recursos.stopwordsEn.has(tok)) votosEn++;
  }
  return votosEn > votosEs ? "en" : "es";
}
