// recursos.js
// Recursos lingüísticos compartidos con el indexador propio.
//
// Las listas de stopwords, el tesauro y las raíces fijas están en la carpeta
// recursos/ y fueron exportadas directamente desde normalizador.py del propio,
// para que ambos indexadores usen exactamente las mismas palabras.
// Las expresiones regulares son copia literal de las del propio.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const DIR_RECURSOS = join(dirname(fileURLToPath(import.meta.url)), "..", "recursos");

/** Lee un archivo de recursos ignorando líneas vacías y comentarios (#). */
function leerLineas(nombre) {
  return readFileSync(join(DIR_RECURSOS, nombre), "utf-8")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
}

export const STOPWORDS_ES = leerLineas("stopwords_es.txt");
export const STOPWORDS_EN = leerLineas("stopwords_en.txt");

/** Reglas "variante, variante => canónico" para el filtro synonym. */
export const REGLAS_TESAURO = leerLineas("tesauro.txt");

/** Reglas "canónico => raíz" para el filtro stemmer_override. */
export const REGLAS_RAICES_FIJAS = leerLineas("raices_fijas.txt");

// ---------------------------------------------------------------------------
// Expresiones regulares (sintaxis de Java, la que usa Elasticsearch).
// Son las mismas del propio: RE_FECHA_COMPLETA, RE_ANIO_CINE,
// RE_NUMERO_GENERAL y RE_TOKEN. Con el flag UNICODE_CHARACTER_CLASS, \w, \d
// y \b de Java reconocen letras con tilde igual que el módulo re de Python.
// ---------------------------------------------------------------------------

export const FLAGS_REGEX = "UNICODE_CHARACTER_CLASS";

export const REGEX_FECHA = String.raw`\b(?:\d{1,2}[/-]\d{1,2}[/-](?:18|19|20)\d{2}|(?:18|19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2})\b`;
export const REGEX_ANIO = String.raw`\b(188[89]|189\d|19\d{2}|20\d{2})\b`;
export const REGEX_NUMERO = String.raw`\b\d+(?:[.,]\d+)?\b`;
export const REGEX_TOKEN = String.raw`<[A-Z0-9_]+>|[^\W_]+(?:[-'_][^\W_]+)*`;
