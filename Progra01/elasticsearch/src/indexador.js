// indexador.js
// Construye un índice completo para una política, en cuatro fases:
//
//   1. Carga: lee la colección, detecta el idioma de cada documento y lo
//      envía con bulk. Elasticsearch aplica el analyzer y escribe el índice.
//   2. Estadísticas: vuelve a pedir a Elasticsearch los términos de cada
//      documento (_mtermvectors con documentos artificiales, que analiza el
//      texto con el analyzer del campo y devuelve tf, df y cf de cada término).
//      Con eso se calculan el vocabulario, los postings y la norma coseno |d|.
//   3. Normas: reenvía los documentos con su norma en el campo "norma".
//      (Elasticsearch no puede actualizar parcialmente un documento cuyo texto
//      no está en _source, por eso se reenvía completo.)
//   4. Compactación: forcemerge a un solo segmento, que además elimina los
//      documentos reemplazados en la fase 3, y medición del tamaño final.
//
// El propio también necesita dos pasadas (bloques SPIMI y luego la fusión con
// el cálculo de normas), así que el tiempo total es comparable.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

import { iterarDocumentos, contarDocumentos } from "./coleccion.js";
import { detectarIdioma } from "./idioma.js";
import { cargarRecursos } from "./recursos.js";
import { construirIndice, campoDeIdioma, CAMPO_ES, CAMPO_EN } from "./analizadores.js";
import { pesoTf, idf } from "./similitud.js";
import { crearCliente, verificarConexion, recrearIndice, estadisticasDisco, memoriaJvm, esperarCompactacion } from "./elastic.js";

const segundos = (ms) => Math.round(ms) / 1000;
const redondear = (x, d = 2) => Math.round(x * 10 ** d) / 10 ** d;

function mostrarProgreso(etiqueta, actual, total) {
  if (!process.stdout.isTTY) return;
  const pct = total ? Math.floor((actual / total) * 100) : 0;
  process.stdout.write(`\r    ${etiqueta}: ${actual}/${total} (${pct}%)   `);
}

/** Documento tal como se guarda en Elasticsearch. */
function documentoEs(doc, idioma, norma) {
  const salida = {
    doc_id: doc.doc_id,
    db_id: doc.db_id,
    url: doc.url,
    hash_url: doc.hash_url,
    dominio: doc.dominio,
    ruta_relativa: doc.ruta_relativa,
    idioma,
    tamano_bytes: doc.tamano_bytes,
    [campoDeIdioma(idioma)]: doc.texto,
  };
  if (norma !== undefined) salida.norma = norma;
  return salida;
}

/** Envía la colección con bulk. Si se pasan normas, las incluye. */
async function cargarColeccion(cliente, config, limite, total, idiomas, normas) {
  const indice = config.elasticsearch.indice;
  const fallidos = [];
  let enviados = 0;

  async function* fuente() {
    for await (const doc of iterarDocumentos(config.rutas, limite)) {
      let idioma = idiomas.get(doc.db_id);
      if (idioma === undefined) {
        idioma = detectarIdioma(doc.texto, config.normalizacion, cargarRecursos(config.rutas.recursos));
        idiomas.set(doc.db_id, idioma);
      }
      enviados++;
      mostrarProgreso(normas ? "reenviando con normas" : "indexando", enviados, total);
      yield documentoEs(doc, idioma, normas?.get(doc.db_id));
    }
  }

  const resultado = await cliente.helpers.bulk({
    datasource: fuente(),
    onDocument: (doc) => ({ index: { _index: indice, _id: String(doc.db_id) } }),
    onDrop: (fallo) => fallidos.push({ db_id: fallo.document.db_id, error: fallo.error?.reason ?? fallo.error }),
    flushBytes: config.elasticsearch.tamano_lote_bytes,
    concurrency: config.elasticsearch.concurrencia,
    refreshOnCompletion: false,
  });
  if (process.stdout.isTTY) process.stdout.write("\n");
  return { resultado, fallidos };
}

/**
 * Fase 2: recorre los vectores de términos de cada documento.
 * Devuelve normas por db_id y las estadísticas del vocabulario.
 */
async function calcularEstadisticas(cliente, config, limite, total, idiomas) {
  const indice = config.elasticsearch.indice;
  const { esquema_tf: esquemaTf, esquema_idf: esquemaIdf } = config.modelo;
  const tamanoLote = config.elasticsearch.tamano_lote_vectores;

  const normas = new Map();
  const terminosPorCampo = { [CAMPO_ES]: new Map(), [CAMPO_EN]: new Map() }; // término -> {df, cf}
  const docsPorCampo = { [CAMPO_ES]: 0, [CAMPO_EN]: 0 };
  let totalPostings = 0;
  let procesados = 0;

  async function procesarLote(lote) {
    const r = await cliente.mtermvectors({
      index: indice,
      docs: lote.map(({ campo, texto }) => ({
        doc: { [campo]: texto },
        fields: [campo],
        term_statistics: true,
        field_statistics: true,
        positions: false,
        offsets: false,
        payloads: false,
      })),
    });

    r.docs.forEach((respuesta, i) => {
      const { db_id: dbId, campo } = lote[i];
      if (respuesta.error) throw new Error(`termvectors falló para ${dbId}: ${JSON.stringify(respuesta.error)}`);
      const tv = respuesta.term_vectors?.[campo];
      if (!tv) {
        normas.set(dbId, 0); // documento sin términos (por ejemplo, vacío)
        return;
      }
      const nDocs = tv.field_statistics.doc_count;
      docsPorCampo[campo] = nDocs;
      const tabla = terminosPorCampo[campo];

      let sumaCuadrados = 0;
      for (const [termino, info] of Object.entries(tv.terms)) {
        if (!info.doc_freq) continue; // no debería pasar: el documento ya está indexado
        const w = pesoTf(info.term_freq, esquemaTf) * idf(nDocs, info.doc_freq, esquemaIdf);
        sumaCuadrados += w * w;
        totalPostings++;
        if (!tabla.has(termino)) tabla.set(termino, { df: info.doc_freq, cf: info.ttf ?? 0 });
      }
      normas.set(dbId, Math.sqrt(sumaCuadrados));
    });
  }

  let lote = [];
  for await (const doc of iterarDocumentos(config.rutas, limite)) {
    const idioma = idiomas.get(doc.db_id) ?? detectarIdioma(doc.texto, config.normalizacion, cargarRecursos(config.rutas.recursos));
    lote.push({ db_id: doc.db_id, campo: campoDeIdioma(idioma), texto: doc.texto });
    if (lote.length >= tamanoLote) {
      await procesarLote(lote);
      procesados += lote.length;
      mostrarProgreso("estadísticas", procesados, total);
      lote = [];
    }
  }
  if (lote.length > 0) {
    await procesarLote(lote);
    procesados += lote.length;
    mostrarProgreso("estadísticas", procesados, total);
  }
  if (process.stdout.isTTY) process.stdout.write("\n");

  return { normas, terminosPorCampo, docsPorCampo, totalPostings };
}

/** Une el vocabulario de ambos campos (el propio tiene un solo diccionario). */
function vocabularioUnido(terminosPorCampo) {
  const unido = new Map();
  for (const tabla of Object.values(terminosPorCampo)) {
    for (const [termino, { df, cf }] of tabla) {
      const previo = unido.get(termino) ?? { df: 0, cf: 0 };
      unido.set(termino, { df: previo.df + df, cf: previo.cf + cf });
    }
  }
  return unido;
}

/** CSV rango-frecuencia para graficar la ley de Zipf en el documento. */
function escribirZipf(ruta, vocabulario) {
  const filas = [...vocabulario.entries()].sort((a, b) => b[1].cf - a[1].cf);
  const lineas = ["rango,termino,frecuencia_coleccion,frecuencia_documental"];
  filas.forEach(([termino, { df, cf }], i) => {
    lineas.push(`${i + 1},"${termino.replaceAll('"', '""')}",${cf},${df}`);
  });
  writeFileSync(ruta, lineas.join("\n") + "\n", "utf-8");
  return filas.slice(0, 20).map(([termino, { df, cf }]) => ({ termino, cf, df }));
}

/** Solo construye y guarda la configuración del índice, sin conectarse. */
export function generarConfiguracionIndice(config) {
  const definicion = construirIndice(config);
  mkdirSync(config.rutas.directorio_salida, { recursive: true });
  const ruta = join(config.rutas.directorio_salida, `configuracion_${config.general.nombre_politica}.json`);
  writeFileSync(ruta, JSON.stringify(definicion, null, 2), "utf-8");
  return { definicion, ruta };
}

/** Ejecuta las cuatro fases y devuelve las estadísticas del índice. */
export async function indexar(config, { limite = 0 } = {}) {
  const politica = config.general.nombre_politica;
  const indice = config.elasticsearch.indice;
  const cliente = crearCliente(config);
  const version = await verificarConexion(cliente);
  const { definicion } = generarConfiguracionIndice(config);

  const total = contarDocumentos(config.rutas, limite);
  console.log(`==> Política "${politica}" -> índice "${indice}" (Elasticsearch ${version})`);
  console.log(`    Documentos en la base: ${total}${limite ? ` (límite ${limite})` : ""}`);

  const inicio = performance.now();
  await recrearIndice(cliente, indice, definicion);

  // Fase 1: carga
  console.log("[1/4] Carga de documentos");
  const t1 = performance.now();
  const idiomas = new Map();
  const carga = await cargarColeccion(cliente, config, limite, total, idiomas);
  await cliente.indices.refresh({ index: indice });
  const tiempoCarga = performance.now() - t1;
  if (carga.fallidos.length) console.warn(`    ${carga.fallidos.length} documentos fallaron (ver estadísticas)`);

  // Fase 2: estadísticas y normas
  console.log("[2/4] Estadísticas del vocabulario y normas coseno");
  const t2 = performance.now();
  const est = await calcularEstadisticas(cliente, config, limite, total, idiomas);
  const tiempoEstadisticas = performance.now() - t2;

  // Fase 3: reenvío con normas
  let tiempoNormas = 0;
  if (config.modelo.normalizacion_coseno) {
    console.log("[3/4] Guardado de normas");
    const t3 = performance.now();
    const reenvio = await cargarColeccion(cliente, config, limite, total, idiomas, est.normas);
    carga.fallidos.push(...reenvio.fallidos);
    tiempoNormas = performance.now() - t3;
  } else {
    console.log("[3/4] Normas desactivadas (normalizacion_coseno = false)");
  }

  // Fase 4: compactación
  console.log("[4/4] Compactación (forcemerge) y medición");
  const t4 = performance.now();
  await cliente.indices.forcemerge({ index: indice, max_num_segments: 1 });
  // Sin este refresh la vista de indices.stats sigue vacía (refresh_interval=-1)
  // y esperarCompactacion daría por buena una fusión que aún no ocurrió.
  await cliente.indices.refresh({ index: indice });
  await esperarCompactacion(cliente, indice);
  await cliente.indices.flush({ index: indice });
  await cliente.indices.putSettings({ index: indice, settings: { refresh_interval: "1s" } });
  const tiempoCompactacion = performance.now() - t4;
  const tiempoTotal = performance.now() - inicio;

  const disco = await estadisticasDisco(cliente, indice);
  const jvm = await memoriaJvm(cliente);
  const vocabulario = vocabularioUnido(est.terminosPorCampo);

  mkdirSync(config.rutas.directorio_salida, { recursive: true });
  const top = escribirZipf(join(config.rutas.directorio_salida, `zipf_${politica}.csv`), vocabulario);

  const idiomasContados = [...idiomas.values()].reduce((acc, i) => ({ ...acc, [i]: (acc[i] ?? 0) + 1 }), {});
  const totalTokens = [...vocabulario.values()].reduce((s, v) => s + v.cf, 0);

  const resultado = {
    nombre_politica: politica,
    descripcion: config.general.descripcion,
    indexador: "elasticsearch",
    version_elasticsearch: version,
    indice,
    total_documentos: disco.documentos,
    documentos_es: idiomasContados.es ?? 0,
    documentos_en: idiomasContados.en ?? 0,
    tamano_vocabulario: vocabulario.size,
    vocabulario_es: est.terminosPorCampo[CAMPO_ES].size,
    vocabulario_en: est.terminosPorCampo[CAMPO_EN].size,
    total_postings: est.totalPostings,
    total_tokens: totalTokens,
    tamano_indice_bytes: disco.tamano_bytes,
    tamano_store_bytes: disco.tamano_store_bytes,
    tamano_recuperacion_bytes: disco.tamano_recuperacion_bytes,
    tamano_indice_mb: redondear(disco.tamano_bytes / 1024 / 1024),
    segmentos: disco.segmentos,
    documentos_borrados: disco.documentos_borrados,
    tiempo_segundos: segundos(tiempoTotal),
    tiempos_fases_segundos: {
      carga: segundos(tiempoCarga),
      estadisticas: segundos(tiempoEstadisticas),
      normas: segundos(tiempoNormas),
      compactacion: segundos(tiempoCompactacion),
    },
    memoria_jvm: jvm,
    postings_por_termino_promedio: vocabulario.size ? redondear(est.totalPostings / vocabulario.size) : 0,
    bytes_por_documento_promedio: disco.documentos ? redondear(disco.tamano_bytes / disco.documentos) : 0,
    terminos_mas_frecuentes: top,
    configuracion: {
      codec: config.elasticsearch.codec,
      guardar_texto: config.elasticsearch.guardar_texto,
      guardar_posiciones: config.elasticsearch.guardar_posiciones,
      esquema_tf: config.modelo.esquema_tf,
      esquema_idf: config.modelo.esquema_idf,
    },
    documentos_fallidos: carga.fallidos,
  };

  const rutaEst = join(config.rutas.directorio_salida, `estadisticas_${politica}.json`);
  writeFileSync(rutaEst, JSON.stringify(resultado, null, 2), "utf-8");

  console.log(
    `    Listo: ${resultado.total_documentos} docs, vocabulario ${resultado.tamano_vocabulario}, ` +
      `${resultado.tamano_indice_mb} MB, ${resultado.tiempo_segundos} s`,
  );
  console.log(`    Estadísticas guardadas en ${rutaEst}`);
  return resultado;
}
