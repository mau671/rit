// elastic.js
// Funciones pequeñas sobre el cliente oficial de Elasticsearch.

import { Client } from "@elastic/elasticsearch";

export function crearCliente(config) {
  return new Client({
    node: config.elasticsearch.url,
    // forcemerge y las cargas grandes pueden tardar varios minutos
    requestTimeout: 10 * 60 * 1000,
  });
}

/** Verifica la conexión y devuelve la versión del servidor. */
export async function verificarConexion(cliente) {
  try {
    const info = await cliente.info();
    return info.version.number;
  } catch (err) {
    throw new Error(
      `No se pudo conectar con Elasticsearch (${err.message}). ` +
        "¿Está corriendo? Probá con: docker compose up -d",
    );
  }
}

/** Borra el índice si existe y lo crea con la configuración dada. */
export async function recrearIndice(cliente, nombre, { settings, mappings }) {
  if (await cliente.indices.exists({ index: nombre })) {
    await cliente.indices.delete({ index: nombre });
  }
  try {
    await cliente.indices.create({ index: nombre, settings, mappings });
  } catch (err) {
    const razon = err?.meta?.body?.error?.reason ?? err.message;
    if (String(razon).includes("icu_")) {
      throw new Error(
        `Falta el plugin analysis-icu: ${razon}\n` +
          "Reconstruí la imagen con: docker compose up -d --build",
      );
    }
    throw new Error(`No se pudo crear el índice ${nombre}: ${razon}`);
  }
}

/** Tamaño en disco, documentos y segmentos del índice (copias primarias).
 *
 * `store.size_in_bytes` incluye `_recovery_source`: una copia interna del
 * documento original que Elasticsearch 8 guarda mientras el `_source` está
 * filtrado, para poder recuperar el shard sin los datos originales. Esa copia
 * expira sola (no forma parte del índice de búsqueda y a veces ya no está al
 * medir, según cuándo compactó el segmento), así que se descuenta con
 * `_disk_usage` y el tamaño reportado es el del índice. Sin la copia el
 * tamaño es reproducible; con ella variaba por política y por momento.
 */
export async function estadisticasDisco(cliente, nombre) {
  const r = await cliente.indices.stats({ index: nombre, metric: ["store", "docs", "segments"] });
  const p = r.indices[nombre].primaries;
  let recuperacion = 0;
  try {
    const du = await cliente.indices.diskUsage({ index: nombre, run_expensive_tasks: true });
    recuperacion = du[nombre]?.fields?._recovery_source?.stored_fields_in_bytes ?? 0;
  } catch {
    // Sin _disk_usage (versión vieja o sin permisos) se reporta el tamaño completo.
  }
  return {
    tamano_bytes: Math.max(0, p.store.size_in_bytes - recuperacion),
    tamano_store_bytes: p.store.size_in_bytes,
    tamano_recuperacion_bytes: recuperacion,
    documentos: p.docs.count,
    documentos_borrados: p.docs.deleted,
    segmentos: p.segments.count,
  };
}

/** Uso de memoria del heap de la JVM de Elasticsearch, en MB. */
export async function memoriaJvm(cliente) {
  const r = await cliente.nodes.stats({ metric: "jvm" });
  const nodo = Object.values(r.nodes)[0];
  const mb = (b) => Math.round((b / 1024 / 1024) * 100) / 100;
  return {
    heap_usado_mb: mb(nodo.jvm.mem.heap_used_in_bytes),
    heap_maximo_mb: mb(nodo.jvm.mem.heap_max_in_bytes),
  };
}

const dormir = (ms) => new Promise((ok) => setTimeout(ok, ms));

/**
 * Espera a que el índice quede en un solo segmento y sin documentos borrados.
 *
 * `forcemerge` es asíncrono y el sondeo tiene que leer el estado real: sin un
 * `refresh` previo, con `refresh_interval=-1` la vista de `indices.stats`
 * está vacía y el chequeo pasa trivialmente aunque queden cientos de MB de
 * segmentos sin fusionar. Por eso el índice se refresca antes de sondear, y
 * como doble seguridad se pide que el tamaño del store deje de cambiar.
 */
export async function esperarCompactacion(cliente, indice, intentos = 90, pausaMs = 2000) {
  let anterior = -1;
  let estables = 0;
  for (let i = 0; i < intentos; i++) {
    const s = await cliente.indices.stats({ index: indice, metric: ["docs", "segments", "store"] });
    const p = s.indices[indice].primaries;
    const compacto = p.segments.count <= 1 && p.docs.deleted === 0;
    if (compacto && p.store.size_in_bytes === anterior && estables >= 2) return true;
    estables = p.store.size_in_bytes === anterior ? estables + 1 : 0;
    anterior = p.store.size_in_bytes;
    await dormir(pausaMs);
  }
  throw new Error(`La compactación de ${indice} no terminó en ${(intentos * pausaMs) / 1000} s`);
}
