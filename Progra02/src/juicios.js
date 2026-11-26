// juicios.js
// Persistencia de los juicios de relevancia de la interfaz de evaluación.
//
// Un juicio ocupa una línea de un arreglo JSON (`salida/juicios.json`) con esta
// forma (contrato en documento/Progra02.tex):
//
//   { consulta, indice, doc_id, url, titulo, evaluador, relevante,
//     rango, timestamp }
//
// `relevante` es graduado: 0 = no relevante, 1 = relevante, 2 = muy relevante.
// La clave de un juicio es (consulta normalizada, índice, doc_id, evaluador):
// volver a marcar el mismo documento reemplaza la nota anterior en vez de
// duplicarla, así la persona puede reanudar y corregir sin ensuciar las
// métricas. Escribir siempre el arreglo completo de forma atómica evita dejar
// el archivo a medias si el proceso se cae.

import { mkdir, readFile, rename, writeFile, unlink } from "node:fs/promises";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";

export const CALIFICACIONES = [
  { valor: 0, etiqueta: "No relevante", corta: "No" },
  { valor: 1, etiqueta: "Relevante", corta: "Sí" },
  { valor: 2, etiqueta: "Muy relevante", corta: "Mucho" },
];

export const RELEVANTE_MINIMO = 1; // desde esta nota el documento cuenta como relevante

/** Error con código HTTP para que el servidor responda 400 y no 500. */
export class ErrorJuicio extends Error {
  constructor(mensaje, estado = 400) {
    super(mensaje);
    this.name = "ErrorJuicio";
    this.estado = estado;
  }
}

/** Normaliza la consulta para emparejar búsquedas y juicios sin distinguir mayúsculas ni espacios. */
export function normalizarConsulta(texto) {
  return String(texto ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

/** Clave estable de un juicio: consulta normalizada + índice + documento + evaluador. */
export function claveJuicio({ consulta, indice, doc_id: docId, evaluador }) {
  return [normalizarConsulta(consulta), String(indice ?? "").trim(), String(docId ?? ""), String(evaluador ?? "").trim()].join("\u0000");
}

const esTexto = (v) => typeof v === "string" && v.trim() !== "";
const recortar = (v, max) => String(v).trim().slice(0, max);

function notaValida(valor) {
  const n = Number(valor);
  return Number.isInteger(n) && n >= 0 && n <= 2 ? n : null;
}

/**
 * Valida y normaliza un juicio recibido por la API. Lanza ErrorJuicio (400) con
 * un mensaje entendible para la persona que está evaluando.
 */
export function validarJuicio(entrada, { indices = [] } = {}) {
  if (!entrada || typeof entrada !== "object" || Array.isArray(entrada)) {
    throw new ErrorJuicio("El cuerpo debe ser un objeto con el juicio.");
  }

  if (!esTexto(entrada.consulta)) throw new ErrorJuicio("Falta la consulta del juicio.");
  const consulta = recortar(entrada.consulta, 500);
  if (normalizarConsulta(consulta) === "") throw new ErrorJuicio("La consulta no puede quedar vacía.");

  if (!esTexto(entrada.indice)) throw new ErrorJuicio("Falta el índice del juicio.");
  const indice = recortar(entrada.indice, 80);
  if (indices.length && !indices.includes(indice)) {
    throw new ErrorJuicio(`El índice ${indice} no está habilitado en buscador.conf.`);
  }

  if (!esTexto(entrada.evaluador)) throw new ErrorJuicio("Falta el nombre o código de quien evalúa.");
  const evaluador = recortar(entrada.evaluador, 80);
  if (/[\u0000-\u001f\u007f]/.test(evaluador)) throw new ErrorJuicio("El nombre del evaluador tiene caracteres de control.");

  if (entrada.doc_id === undefined || entrada.doc_id === null || String(entrada.doc_id).trim() === "") {
    throw new ErrorJuicio("Falta el identificador del documento (doc_id).");
  }
  const doc_id = recortar(entrada.doc_id, 200);

  const relevante = notaValida(entrada.relevante);
  if (relevante === null) throw new ErrorJuicio("La relevancia debe ser 0 (no), 1 (sí) o 2 (muy relevante).");

  let url = "";
  if (entrada.url !== undefined && entrada.url !== null && String(entrada.url).trim() !== "") {
    url = recortar(entrada.url, 2000);
    let parseada;
    try {
      parseada = new URL(url);
    } catch {
      throw new ErrorJuicio("La URL del documento no es válida.");
    }
    if (!["http:", "https:"].includes(parseada.protocol)) throw new ErrorJuicio("La URL debe empezar con http:// o https://.");
  }

  const titulo = entrada.titulo ? recortar(entrada.titulo, 300) : "";
  let rango = null;
  if (entrada.rango !== undefined && entrada.rango !== null && entrada.rango !== "") {
    const r = Number(entrada.rango);
    if (!Number.isInteger(r) || r < 1 || r > 10000) throw new ErrorJuicio("El rango del resultado debe ser un entero positivo.");
    rango = r;
  }

  return { consulta, indice, doc_id, id: doc_id, url, titulo, evaluador, relevante, rango };
}

/** Lee el archivo de juicios. Si no existe devuelve []; si está corrupto, avisa. */
export async function leerJuicios(ruta) {
  let crudo;
  try {
    crudo = await readFile(ruta, "utf-8");
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
  if (!crudo.trim()) return [];

  let datos;
  try {
    datos = JSON.parse(crudo);
  } catch (err) {
    throw new Error(`El archivo de juicios ${ruta} no es JSON válido: ${err.message}`);
  }
  if (!Array.isArray(datos)) throw new Error(`El archivo de juicios ${ruta} debe contener un arreglo JSON.`);

  return datos.filter(
    (j) => j && typeof j === "object" && esTexto(j.consulta) && esTexto(j.doc_id) && esTexto(j.evaluador),
  );
}

async function escribirAtomico(ruta, juicios) {
  await mkdir(dirname(ruta), { recursive: true });
  const temporal = join(dirname(ruta), `.${Date.now()}-${randomBytes(4).toString("hex")}.juicios.tmp`);
  try {
    await writeFile(temporal, JSON.stringify(juicios, null, 2) + "\n", "utf-8");
    await rename(temporal, ruta);
  } catch (err) {
    await unlink(temporal).catch(() => {});
    throw err;
  }
}

// Cola por archivo: dos peticiones simultáneas no pueden leer el mismo estado y
// pisarse los juicios. Es una promesa encadenada (el servidor es un solo proceso).
const colas = new Map();

function enCola(ruta, tarea) {
  const anterior = colas.get(ruta) ?? Promise.resolve();
  const siguiente = anterior.then(tarea, tarea);
  colas.set(ruta, siguiente.catch(() => {}));
  return siguiente;
}

/**
 * Guarda (o actualiza) un juicio. Devuelve { juicio, creado }.
 * La nota, el rango, la URL y el título se actualizan; el timestamp es el de la
 * última modificación.
 */
export function guardarJuicio(ruta, entrada, opciones = {}) {
  const juicio = validarJuicio(entrada, opciones);
  return enCola(ruta, async () => {
    const juicios = await leerJuicios(ruta);
    const clave = claveJuicio(juicio);
    const posicion = juicios.findIndex((j) => claveJuicio(j) === clave);
    const guardado = {
      ...(posicion >= 0 ? juicios[posicion] : {}),
      ...juicio,
      // si el cliente no manda estos datos, no se borran los que ya estaban
      url: juicio.url || (posicion >= 0 ? juicios[posicion].url : ""),
      titulo: juicio.titulo || (posicion >= 0 ? juicios[posicion].titulo : ""),
      rango: juicio.rango ?? (posicion >= 0 ? juicios[posicion].rango ?? null : null),
      timestamp: new Date().toISOString(),
    };
    const creado = posicion < 0;
    if (creado) juicios.push(guardado);
    else juicios[posicion] = guardado;
    await escribirAtomico(ruta, juicios);
    return { juicio: guardado, creado };
  });
}

/** Borra el juicio que coincide con la clave. Devuelve { eliminado, juicio }. */
export function borrarJuicio(ruta, referencia) {
  const { consulta, indice, doc_id: docId, evaluador } = referencia ?? {};
  if (!esTexto(consulta) || !esTexto(docId) || !esTexto(evaluador)) {
    throw new ErrorJuicio("Para borrar un juicio hacen falta consulta, doc_id y evaluador.");
  }
  return enCola(ruta, async () => {
    const juicios = await leerJuicios(ruta);
    const buscada = normalizarConsulta(consulta);
    const indiceBuscado = esTexto(indice) ? String(indice).trim() : "";
    const posicion = juicios.findIndex(
      (j) =>
        normalizarConsulta(j.consulta) === buscada &&
        String(j.doc_id) === String(docId).trim() &&
        String(j.evaluador) === String(evaluador).trim() &&
        (!indiceBuscado || String(j.indice) === indiceBuscado),
    );
    if (posicion < 0) return { eliminado: false, juicio: null };
    const [juicio] = juicios.splice(posicion, 1);
    await escribirAtomico(ruta, juicios);
    return { eliminado: true, juicio };
  });
}
