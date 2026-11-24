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

/** Tamaño en disco, documentos y segmentos del índice (copias primarias). */
export async function estadisticasDisco(cliente, nombre) {
  const r = await cliente.indices.stats({ index: nombre, metric: ["store", "docs", "segments"] });
  const p = r.indices[nombre].primaries;
  return {
    tamano_bytes: p.store.size_in_bytes,
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
