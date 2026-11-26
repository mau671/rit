// benchmark.js
// Construye los índices de las tres políticas y los compara con el benchmark
// del indexador propio (propio/salida/benchmark_indices.json).
//
// Para que la comparación sea justa, usá el mismo --limite con el que se
// generó el benchmark del propio (el del repo usa 10 000 documentos).

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { cargarConfiguracion } from "./configuracion.js";
import { indexar } from "./indexador.js";

const fmt = (x, d = 2) => (typeof x === "number" ? x.toLocaleString("es-CR", { maximumFractionDigits: d }) : "—");
const razon = (a, b) => (typeof a === "number" && typeof b === "number" && b !== 0 ? `${fmt(a / b)}×` : "—");

function tablaComparacion(es, propio) {
  const filas = [
    ["Documentos", "total_documentos"],
    ["Vocabulario (términos)", "tamano_vocabulario"],
    ["Postings", "total_postings"],
    ["Tamaño del índice (MB)", "tamano_indice_mb", "tamano_archivo_mb"],
    ["Tiempo total (s)", "tiempo_segundos"],
    ["Bytes por documento", "bytes_por_documento_promedio"],
    ["Postings por término", "postings_por_termino_promedio"],
  ];
  const lineas = [
    `### Política ${es.nombre_politica}`,
    "",
    "| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |",
    "|---|---:|---:|---:|",
  ];
  for (const [nombre, claveEs, clavePropio = claveEs] of filas) {
    const a = propio?.[clavePropio];
    const b = es[claveEs];
    lineas.push(`| ${nombre} | ${fmt(a)} | ${fmt(b)} | ${razon(b, a)} |`);
  }
  const f = es.tiempos_fases_segundos;
  lineas.push(
    "",
    `Fases de Elasticsearch (s): carga ${fmt(f.carga)}, estadísticas ${fmt(f.estadisticas)}, ` +
      `normas ${fmt(f.normas)}, compactación ${fmt(f.compactacion)}. ` +
      `Documentos en español: ${es.documentos_es}, en inglés: ${es.documentos_en}.`,
    "",
  );
  return lineas.join("\n");
}

export async function benchmark(rutasConf, { limite = 0, benchmarkPropio } = {}) {
  const resultados = [];
  for (const ruta of rutasConf) {
    resultados.push(await indexar(cargarConfiguracion(ruta), { limite }));
    console.log("");
  }

  const dirSalida = resolve(join(rutasConf[0], "..", "..", "salida"));
  mkdirSync(dirSalida, { recursive: true });
  writeFileSync(join(dirSalida, "benchmark_elasticsearch.json"), JSON.stringify(resultados, null, 2), "utf-8");

  const rutaPropio = resolve(
    benchmarkPropio ?? join(dirname(rutasConf[0]), "..", "..", "propio", "salida", "benchmark_indices.json"),
  );
  let propio = [];
  if (existsSync(rutaPropio)) {
    propio = JSON.parse(readFileSync(rutaPropio, "utf-8"));
  } else {
    console.warn(`No se encontró el benchmark del propio en ${rutaPropio}; se reporta solo Elasticsearch.`);
  }

  const secciones = resultados.map((r) => tablaComparacion(r, propio.find((p) => p.nombre_politica === r.nombre_politica)));
  const docsPropio = propio[0]?.total_documentos;
  const aviso =
    docsPropio && docsPropio !== resultados[0].total_documentos
      ? `> **Atención:** el propio indexó ${docsPropio} documentos y Elasticsearch ${resultados[0].total_documentos}. ` +
        `Repetí con \`--limite ${docsPropio}\` para comparar en igualdad de condiciones.\n\n`
      : "";
  const md = `# Comparación Propio vs. Elasticsearch\n\n${aviso}${secciones.join("\n")}`;
  const rutaMd = join(dirSalida, "comparacion.md");
  writeFileSync(rutaMd, md, "utf-8");

  console.log(md);
  console.log(`Comparación guardada en ${rutaMd}`);
  return resultados;
}
