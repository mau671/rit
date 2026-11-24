// coleccion.js
// Recorre la colección del arañador, igual que lector_coleccion.py del propio:
// lee la tabla documentos de metadatos_araña.db y carga cada .txt del
// repositorio. Asigna el mismo doc_id secuencial (0..N-1) y omite los archivos
// que no existen en disco, para que ambos indexadores vean los mismos documentos.

import { DatabaseSync } from "node:sqlite";
import { existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

function abrirBase(rutaBd, rutaRepositorio) {
  if (!existsSync(rutaBd)) throw new Error(`Base de datos no encontrada: ${rutaBd}`);
  if (!existsSync(rutaRepositorio) || !statSync(rutaRepositorio).isDirectory()) {
    throw new Error(`Directorio de repositorio no encontrado: ${rutaRepositorio}`);
  }
  return new DatabaseSync(rutaBd, { readOnly: true });
}

/** Cantidad de filas elegibles (con el mismo límite que usa el propio). */
export function contarDocumentos(rutas, limite = 0) {
  const bd = abrirBase(rutas.base_datos, rutas.repositorio);
  try {
    const sql = limite > 0
      ? "SELECT COUNT(*) AS n FROM (SELECT id FROM documentos LIMIT ?)"
      : "SELECT COUNT(*) AS n FROM documentos";
    const fila = limite > 0 ? bd.prepare(sql).get(limite) : bd.prepare(sql).get();
    return Number(fila.n);
  } finally {
    bd.close();
  }
}

/**
 * Generador asíncrono de documentos:
 * { doc_id, db_id, url, hash_url, dominio, ruta_relativa, texto, tamano_bytes }
 */
export async function* iterarDocumentos(rutas, limite = 0) {
  const bd = abrirBase(rutas.base_datos, rutas.repositorio);
  let sql =
    "SELECT id, url, hash_url, dominio, ruta_relativa, tamano_texto_bytes " +
    "FROM documentos ORDER BY id ASC";
  if (limite > 0) sql += ` LIMIT ${Number(limite)}`;

  let filas;
  try {
    // Solo metadatos: caben en memoria sin problema.
    filas = bd.prepare(sql).all();
  } finally {
    bd.close();
  }

  let docId = 0;
  for (const fila of filas) {
    const ruta = join(rutas.repositorio, fila.ruta_relativa);
    let texto;
    try {
      // Igual que read_text(errors="replace"): bytes inválidos -> U+FFFD
      texto = await readFile(ruta, "utf-8");
    } catch {
      continue; // archivo registrado pero ausente en disco
    }
    yield {
      doc_id: docId,
      db_id: Number(fila.id),
      url: fila.url,
      hash_url: fila.hash_url,
      dominio: fila.dominio,
      ruta_relativa: fila.ruta_relativa,
      texto,
      tamano_bytes: Number(fila.tamano_texto_bytes ?? 0),
    };
    docId++;
  }
}
