// fragmentos.js
// Arma los fragmentos (snippets) de cada resultado con las coincidencias
// resaltadas.
//
// El índice no guarda el texto (para mantenerlo pequeño), así que el texto se
// lee del repositorio de la araña. Para saber qué palabras resaltar no basta
// con buscar la consulta literal: el índice guarda raíces ("corrían" -> "corr")
// y términos del tesauro ("movie" -> "film"). Por eso el texto se pasa por el
// MISMO analyzer del índice con _analyze, que devuelve cada término con su
// posición en el texto original (start_offset / end_offset). Se resaltan los
// tokens cuyo término normalizado está en la consulta. Así "director" en la
// consulta resalta "directores", "cineasta" y "filmmaker" en el texto.

import { readFile } from "node:fs/promises";
import { join } from "node:path";

const VENTANA_TOKENS = 40;

/** Une espacios y saltos de línea en un solo espacio. */
const compactar = (s) => s.replace(/\s+/g, " ");

/** Mejor ventana de tokens (la que tiene más coincidencias) sin pisar las ya usadas. */
function mejorVentana(coincide, usadas) {
  let mejor = { inicio: -1, cuenta: 0 };
  let cuenta = 0;
  for (let i = 0; i < coincide.length; i++) {
    cuenta += coincide[i] ? 1 : 0;
    if (i >= VENTANA_TOKENS) cuenta -= coincide[i - VENTANA_TOKENS] ? 1 : 0;
    const inicio = Math.max(0, i - VENTANA_TOKENS + 1);
    const pisa = usadas.some(([a, b]) => inicio <= b && i >= a);
    if (!pisa && cuenta > mejor.cuenta) mejor = { inicio, cuenta };
  }
  return mejor;
}

/** Convierte un rango del texto en piezas {texto, resaltado}. */
function piezas(texto, desde, hasta, marcas) {
  const salida = [];
  let cursor = desde;
  for (const [a, b] of marcas) {
    if (b <= desde || a >= hasta) continue;
    if (a > cursor) salida.push({ texto: compactar(texto.slice(cursor, a)), resaltado: false });
    salida.push({ texto: compactar(texto.slice(Math.max(a, cursor), b)), resaltado: true });
    cursor = b;
  }
  if (cursor < hasta) salida.push({ texto: compactar(texto.slice(cursor, hasta)), resaltado: false });
  return salida.filter((p) => p.texto);
}

/** Recorta el inicio y el final del fragmento para no cortar palabras. */
function ajustarBordes(texto, desde, hasta, largo) {
  let a = desde;
  let b = Math.min(hasta, desde + largo);
  // Un poco de contexto antes de la primera coincidencia
  const contexto = Math.min(60, a);
  a -= contexto;
  while (a > 0 && /\S/.test(texto[a - 1])) a--;
  while (b < texto.length && /\S/.test(texto[b])) b++;
  return [a, b];
}

/**
 * Devuelve { fragmentos, coincidencias, disponible } para un documento.
 * Cada fragmento es { piezas: [{texto, resaltado}], antes, despues }; antes/despues
 * indican si el fragmento está recortado (para mostrar puntos suspensivos).
 * terminos: Set de términos normalizados de la consulta para el campo del documento.
 */
export async function armarFragmentos(cliente, { indice, campo, rutaArchivo, terminos, opciones }) {
  let texto;
  try {
    texto = await readFile(rutaArchivo, "utf-8");
  } catch {
    return { fragmentos: [], coincidencias: {}, disponible: false };
  }
  texto = texto.slice(0, opciones.caracteres_analisis);
  const largo = opciones.caracteres_fragmento;
  const primeraLinea = primeraLineaDe(texto);

  const inicioDocumento = () => {
    const [a, b] = ajustarBordes(texto, 0, largo, largo);
    return [{ piezas: piezas(texto, 0, b, []), antes: false, despues: b < texto.length }];
  };

  if (terminos.size === 0 || !texto.trim()) {
    return { fragmentos: texto.trim() ? inicioDocumento() : [], coincidencias: {}, disponible: true, primeraLinea };
  }

  const r = await cliente.indices.analyze({ index: indice, field: campo, text: texto });
  const tokens = r.tokens ?? [];
  const coincide = tokens.map((t) => terminos.has(t.token));
  const marcas = [];
  const coincidencias = {};
  tokens.forEach((t, i) => {
    if (!coincide[i]) return;
    marcas.push([t.start_offset, t.end_offset]);
    coincidencias[t.token] = (coincidencias[t.token] ?? 0) + 1;
  });

  if (marcas.length === 0) return { fragmentos: inicioDocumento(), coincidencias, disponible: true, primeraLinea };

  const fragmentos = [];
  const usadas = [];
  for (let n = 0; n < opciones.fragmentos_por_resultado; n++) {
    const { inicio, cuenta } = mejorVentana(coincide, usadas);
    if (inicio < 0 || cuenta === 0) break;
    const fin = Math.min(tokens.length - 1, inicio + VENTANA_TOKENS - 1);
    usadas.push([inicio, fin]);
    // Empezar en la primera coincidencia de la ventana
    let primera = inicio;
    while (primera < fin && !coincide[primera]) primera++;
    const [a, b] = ajustarBordes(texto, tokens[primera].start_offset, tokens[fin].end_offset, largo);
    fragmentos.push({ desde: a, piezas: piezas(texto, a, b, marcas), antes: a > 0, despues: b < texto.length });
  }
  // Mostrar los fragmentos en el orden en que aparecen en el documento
  fragmentos.sort((x, y) => x.desde - y.desde);

  return {
    fragmentos: fragmentos.map(({ piezas: p, antes, despues }) => ({ piezas: p, antes, despues })),
    coincidencias,
    disponible: true,
    primeraLinea,
  };
}

/** Primera línea con texto, recortada a unas 90 letras sin partir palabras. */
function primeraLineaDe(texto) {
  const linea = texto.split(/\r?\n/).map((l) => compactar(l).trim()).find((l) => l.length >= 3);
  if (!linea) return null;
  if (linea.length <= 90) return linea;
  const corte = linea.lastIndexOf(" ", 90);
  return `${linea.slice(0, corte > 40 ? corte : 90)}…`;
}

/** Ruta del .txt de un resultado. */
export function rutaTexto(repositorio, rutaRelativa) {
  return join(repositorio, rutaRelativa);
}

/**
 * Título legible a partir de la URL (por ejemplo /scripts/Pulp-Fiction.html -> Pulp Fiction).
 * Devuelve null si la URL no trae un nombre útil (identificadores como film809297),
 * para usar en su lugar la primera línea del texto.
 */
export function tituloDesdeUrl(url) {
  try {
    const u = new URL(url);
    const partes = u.pathname.split("/").filter(Boolean);
    let ultimo = partes.at(-1) ?? "";
    ultimo = decodeURIComponent(ultimo).replace(/\.(s?html?|php|aspx?|txt|pdf)$/i, "");
    if (/^(index|default|home)$/i.test(ultimo)) ultimo = partes.at(-2) ?? "";
    const titulo = ultimo.replace(/[-_+]+/g, " ").replace(/\s+/g, " ").trim();
    const pareceIdentificador = /\d{3,}/.test(titulo) && !/\s/.test(titulo);
    if (titulo && !pareceIdentificador) return titulo.charAt(0).toUpperCase() + titulo.slice(1);
    return null;
  } catch {
    return null;
  }
}
