// recursos.js
// Recursos lingüísticos compartidos con el indexador propio.
//
// Las stopwords, los sinónimos y el tesauro se leen de los MISMOS archivos que
// usa el propio (Progra01/recursos/, ruta "recursos" en el .conf), así que si
// el grupo cambia una lista, los dos indexadores la ven igual sin copiar nada.
//
// Las raíces fijas de los términos canónicos (elasticsearch/recursos/raices_fijas.txt)
// se generan con el stemmer del propio: las escribe herramientas/generar_casos.py.
//
// Las expresiones regulares son las del propio traducidas a Java (ver abajo).

import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const DIR_RECURSOS_ES = join(dirname(fileURLToPath(import.meta.url)), "..", "recursos");

/** Líneas útiles de un archivo (sin vacías ni comentarios), igual que _lineas_utiles del propio. */
function leerLineas(ruta) {
  if (!existsSync(ruta)) {
    throw new Error(
      `Recurso de normalización no encontrado: ${ruta}\n` +
        "Revisá la clave \"recursos\" de [rutas] en el .conf (debe apuntar a Progra01/recursos).",
    );
  }
  return readFileSync(ruta, "utf-8")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
}

/** Igual que cargar_sinonimos del propio: {variante: canónico}; si se repite, gana la última. */
function leerSinonimos(rutas) {
  const mapa = new Map();
  for (const ruta of rutas) {
    for (const linea of leerLineas(ruta)) {
      const pos = linea.indexOf("=>");
      if (pos < 0) throw new Error(`${ruta}: regla sin '=>': ${linea}`);
      const canon = linea.slice(pos + 2).trim().toLowerCase();
      for (const variante of linea.slice(0, pos).split(",")) {
        const v = variante.trim().toLowerCase();
        if (v) {
          mapa.delete(v); // para que el orden de inserción refleje "gana la última"
          mapa.set(v, canon);
        }
      }
    }
  }
  return mapa;
}

const cache = new Map();

/**
 * Carga los recursos de un directorio:
 * { stopwordsEs: Set, stopwordsEn: Set, tesauro: Map, raicesFijas: [reglas] }
 */
export function cargarRecursos(dirRecursos) {
  if (cache.has(dirRecursos)) return cache.get(dirRecursos);
  const recursos = {
    stopwordsEs: new Set(leerLineas(join(dirRecursos, "stopwords_es.txt"))),
    stopwordsEn: new Set(leerLineas(join(dirRecursos, "stopwords_en.txt"))),
    tesauro: leerSinonimos([join(dirRecursos, "sinonimos.txt"), join(dirRecursos, "tesauro.txt")]),
    raicesFijas: leerLineas(join(DIR_RECURSOS_ES, "raices_fijas.txt")),
  };
  cache.set(dirRecursos, recursos);
  return recursos;
}

// ---------------------------------------------------------------------------
// Expresiones regulares en sintaxis de Java (la que usa Elasticsearch).
//
// En Python, \w son las letras y números Unicode (str.isalnum) más "_", y \d
// son los dígitos decimales Unicode. En Java esas clases cambian según los
// flags y además \w incluye signos combinados (por ejemplo las vocales del
// bengalí). Para que sean idénticas se escriben de forma explícita:
//   Python \w   ->  [\p{L}\p{N}_]
//   Python \d   ->  \p{Nd}
//   Python \b   ->  (?<![\p{L}\p{N}_]) al inicio y (?![\p{L}\p{N}_]) al final
//                   (en estas regex \b siempre está pegado a un dígito o letra)
// ---------------------------------------------------------------------------

export const FLAGS_REGEX = "UNICODE_CHARACTER_CLASS";
export const FLAGS_REGEX_SIN_MAYUSCULAS = "CASE_INSENSITIVE|UNICODE_CHARACTER_CLASS";

const W = String.raw`[\p{L}\p{N}_]`;
const B_INI = String.raw`(?<!${W})`;
const B_FIN = String.raw`(?!${W})`;
const D = String.raw`\p{Nd}`;

const MESES_ES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre";
const MESES_EN = "january|february|march|april|may|june|july|august|september|october|november|december";

// RE_FECHA_COMPLETA
export const REGEX_FECHA =
  `${B_INI}(?:${D}{1,2}[/-]${D}{1,2}[/-](?:18|19|20)${D}{2}|(?:18|19|20)${D}{2}[/-]${D}{1,2}[/-]${D}{1,2})${B_FIN}`;

// RE_FECHA_TEXTUAL (sin distinguir mayúsculas)
export const REGEX_FECHA_TEXTUAL =
  `${B_INI}${D}{1,2}\\s+de\\s+(?:${MESES_ES})\\s+de(?:l)?\\s+(?:18|19|20)${D}{2}${B_FIN}` +
  `|${B_INI}(?:${MESES_EN})\\s+${D}{1,2}(?:st|nd|rd|th)?,?\\s+(?:18|19|20)${D}{2}${B_FIN}` +
  `|${B_INI}${D}{1,2}(?:st|nd|rd|th)?\\s+(?:of\\s+)?(?:${MESES_EN}),?\\s+(?:18|19|20)${D}{2}${B_FIN}`;

// RE_ANIO_CINE
export const REGEX_ANIO = `${B_INI}(188[89]|189${D}|19${D}{2}|20${D}{2})${B_FIN}`;

// RE_NUMERO_GENERAL
export const REGEX_NUMERO = `${B_INI}${D}+(?:[.,]${D}+)?${B_FIN}`;

// RE_TOKEN: en Python [^\W_] es "letra o número"
export const REGEX_TOKEN = String.raw`<[A-Z0-9_]+>|[\p{L}\p{N}]+(?:[-'_][\p{L}\p{N}]+)*`;

// RE_CLITICO_EN
export const REGEX_CLITICO = String.raw`'(?:s|re|ve|ll|d|m)$`;
