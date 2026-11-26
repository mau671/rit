#!/usr/bin/env node
// servidor.js
// Servidor web del buscador y de las interfaces de evaluación y métricas.
// Es un programa independiente de la araña y de los indexadores: solo lee los
// índices de Elasticsearch, los textos del repositorio y los archivos de salida.
//
//   GET  /                       interfaz de búsqueda (public/index.html)
//   GET  /evaluar                interfaz de evaluación (public/evaluar.html)
//   GET  /metricas               página de métricas (renderizada al vuelo)
//   GET  /api/buscar             búsqueda (contrato en documento/Progra02.tex)
//   GET  /api/indices            índices disponibles y estado
//   GET  /api/evaluacion/juicios juicios guardados (con filtros)
//   POST /api/evaluacion/juicios guarda o actualiza un juicio
//   DELETE /api/evaluacion/juicios borra un juicio
//   GET  /api/evaluacion/resumen conteos de juicios
//   GET  /api/evaluacion/consultas consultas registradas y su avance
//   GET  /api/metricas           informe de métricas en JSON
//
// Uso: node src/servidor.js [ruta a buscador.conf]

import http from "node:http";
import { readFile, appendFile, mkdir } from "node:fs/promises";
import { extname, join, normalize, dirname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@elastic/elasticsearch";

import { cargarConfiguracion } from "./configuracion.js";
import { buscar } from "./busqueda.js";
import { borrarJuicio, guardarJuicio, leerJuicios, normalizarConsulta, ErrorJuicio } from "./juicios.js";
import { generarMetricas, guardarMetricas, leerRegistros, K_MAXIMO, claveConsulta } from "./metricas.js";
import { renderMetricas, renderErrorMetricas } from "./pagina-metricas.js";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const PUBLICO = join(RAIZ, "public");

const TIPOS = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".png": "image/png",
  ".json": "application/json; charset=utf-8",
};

class ErrorHttp extends Error {
  constructor(estado, mensaje) {
    super(mensaje);
    this.estado = estado;
  }
}

function enviarJson(res, estado, cuerpo, cabeceras = {}) {
  res.writeHead(estado, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    ...cabeceras,
  });
  res.end(JSON.stringify(cuerpo));
}

function enviarHtml(res, estado, html) {
  res.writeHead(estado, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
  res.end(html);
}

function mensajeDeError(err) {
  if (err?.meta?.statusCode === 404) return "El índice no existe. Construilo primero con Progra01/elasticsearch.";
  if (err?.name === "ConnectionError") return "No hay conexión con Elasticsearch. Revisá que esté corriendo (docker compose up -d).";
  return err?.meta?.body?.error?.reason ?? err.message;
}

/** Lee y valida el cuerpo JSON de una petición (máximo 64 KB). */
async function leerCuerpo(req) {
  if (!/application\/json/i.test(req.headers["content-type"] ?? "")) {
    throw new ErrorHttp(415, "El cuerpo debe enviarse con content-type: application/json.");
  }
  const limite = 64 * 1024;
  const partes = [];
  let total = 0;
  for await (const parte of req) {
    total += parte.length;
    if (total > limite) throw new ErrorHttp(413, "El cuerpo supera los 64 KB.");
    partes.push(parte);
  }
  if (!partes.length) throw new ErrorHttp(400, "Falta el cuerpo JSON.");
  try {
    return JSON.parse(Buffer.concat(partes).toString("utf-8"));
  } catch {
    throw new ErrorHttp(400, "El cuerpo no es JSON válido.");
  }
}

// ------------------------------------------------------------- Búsqueda

async function apiBuscar(url, res, { config, cliente }) {
  const q = url.searchParams.get("q") ?? "";
  const pagina = Math.max(1, Number.parseInt(url.searchParams.get("p") ?? "1", 10) || 1);
  const indice = url.searchParams.get("indice") || config.elasticsearch.indice_por_defecto;
  if (!config.elasticsearch.indices_disponibles.includes(indice)) {
    return enviarJson(res, 400, { error: `El índice ${indice} no está habilitado en buscador.conf.` });
  }
  if (!q.trim()) return enviarJson(res, 400, { error: "Falta la consulta (parámetro q)." });

  try {
    const resultado = await buscar(cliente, config, { q, pagina, indice });
    enviarJson(res, 200, resultado);
    registrarConsulta(config, {
      fecha: new Date().toISOString(),
      consulta: q,
      indice,
      pagina,
      total: resultado.total,
      tiempo_ms: resultado.tiempo_ms,
      resultados: resultado.resultados.map((r) => r.id),
    });
  } catch (err) {
    console.error(err);
    enviarJson(res, 502, { error: mensajeDeError(err) });
  }
}

async function registrarConsulta(config, entrada) {
  if (!config.servidor.registrar_consultas) return;
  try {
    await mkdir(dirname(config.servidor.archivo_registro), { recursive: true });
    await appendFile(config.servidor.archivo_registro, JSON.stringify(entrada) + "\n", "utf-8");
  } catch (err) {
    console.warn(`No se pudo registrar la consulta: ${err.message}`);
  }
}

async function apiIndices(res, { config, cliente }) {
  const lista = await Promise.all(
    config.elasticsearch.indices_disponibles.map(async (nombre) => {
      try {
        const r = await cliente.count({ index: nombre });
        return { nombre, documentos: r.count, disponible: true };
      } catch {
        return { nombre, documentos: 0, disponible: false };
      }
    }),
  );
  enviarJson(res, 200, { por_defecto: config.elasticsearch.indice_por_defecto, indices: lista });
}

// ----------------------------------------------------------- Evaluación

function filtrarJuicios(juicios, url) {
  const consulta = url.searchParams.get("consulta");
  const indice = url.searchParams.get("indice");
  const evaluador = url.searchParams.get("evaluador");
  const docId = url.searchParams.get("doc_id");
  const buscada = consulta ? normalizarConsulta(consulta) : null;

  return juicios.filter(
    (j) =>
      (!buscada || normalizarConsulta(j.consulta) === buscada) &&
      (!indice || String(j.indice) === indice) &&
      (!evaluador || String(j.evaluador) === evaluador) &&
      (!docId || String(j.doc_id) === docId),
  );
}

async function apiJuicios(url, res, { config }) {
  const juicios = filtrarJuicios(await leerJuicios(config.evaluacion.archivo_juicios), url);
  enviarJson(res, 200, { juicios, total: juicios.length });
}

async function apiGuardarJuicio(req, res, { config }) {
  const cuerpo = await leerCuerpo(req);
  const { juicio, creado } = await guardarJuicio(config.evaluacion.archivo_juicios, cuerpo, {
    indices: config.elasticsearch.indices_disponibles,
  });
  enviarJson(res, creado ? 201 : 200, { juicio, creado });
}

async function apiBorrarJuicio(url, res, { config }) {
  const { eliminado, juicio } = await borrarJuicio(config.evaluacion.archivo_juicios, {
    consulta: url.searchParams.get("consulta"),
    indice: url.searchParams.get("indice"),
    doc_id: url.searchParams.get("doc_id"),
    evaluador: url.searchParams.get("evaluador"),
  });
  enviarJson(res, 200, { eliminado, juicio });
}

async function apiResumenEvaluacion(url, res, { config }) {
  const juicios = filtrarJuicios(await leerJuicios(config.evaluacion.archivo_juicios), url);
  const porRelevante = { 0: 0, 1: 0, 2: 0 };
  const porEvaluador = {};
  const consultas = new Set();
  let ultimo = null;
  for (const j of juicios) {
    porRelevante[Math.max(0, Math.min(2, Number(j.relevante) || 0))] += 1;
    porEvaluador[j.evaluador] = (porEvaluador[j.evaluador] ?? 0) + 1;
    consultas.add(claveConsulta(j.consulta, j.indice));
    if (j.timestamp && (!ultimo || j.timestamp > ultimo)) ultimo = j.timestamp;
  }
  enviarJson(res, 200, {
    total: juicios.length,
    consultas: consultas.size,
    por_relevante: porRelevante,
    por_evaluador: porEvaluador,
    ultimo,
  });
}

function resumirConsultasRegistradas(registros) {
  const porClave = new Map();
  for (const r of registros) {
    const clave = claveConsulta(r.consulta, r.indice);
    const fecha = r.fecha ?? null;
    const previa = porClave.get(clave);
    if (!previa) {
      porClave.set(clave, {
        clave,
        consulta: r.consulta,
        indice: r.indice,
        veces: 1,
        ultima: fecha,
        total: r.total ?? null,
        ultima_pagina: r.pagina ?? 1,
      });
      continue;
    }
    previa.veces += 1;
    if (fecha && (!previa.ultima || Date.parse(fecha) >= Date.parse(previa.ultima))) {
      previa.consulta = r.consulta;
      previa.ultima = fecha;
      previa.total = r.total ?? previa.total;
      previa.ultima_pagina = r.pagina ?? previa.ultima_pagina;
    }
  }
  return [...porClave.values()].sort(
    (a, b) => b.veces - a.veces || (Date.parse(b.ultima ?? 0) || 0) - (Date.parse(a.ultima ?? 0) || 0),
  );
}

async function apiConsultasEvaluacion(url, res, { config }) {
  const { registros, avisos } = await leerRegistros(config.servidor.archivo_registro);
  const todos = await leerJuicios(config.evaluacion.archivo_juicios);
  const evaluador = url.searchParams.get("evaluador");
  const objetivo = config.resultados.por_pagina;

  const juzgadosPorClave = new Map();
  const miosPorClave = new Map();
  for (const j of todos) {
    const clave = claveConsulta(j.consulta, j.indice);
    const nota = Number(j.relevante) || 0;
    const actual = juzgadosPorClave.get(clave) ?? { juicios: 0, relevantes: 0, docs: new Set() };
    actual.docs.add(String(j.doc_id));
    actual.juicios += 1;
    if (nota >= 1) actual.relevantes += 1;
    juzgadosPorClave.set(clave, actual);
    if (evaluador && String(j.evaluador) === evaluador) {
      const mios = miosPorClave.get(clave) ?? new Set();
      mios.add(String(j.doc_id));
      miosPorClave.set(clave, mios);
    }
  }

  const consultas = resumirConsultasRegistradas(registros).map((c) => {
    const agregado = juzgadosPorClave.get(c.clave);
    return {
      consulta: c.consulta,
      indice: c.indice,
      veces: c.veces,
      ultima: c.ultima,
      total: c.total,
      objetivos: Math.min(objetivo, c.total ?? objetivo),
      documentos_juzgados: agregado?.docs.size ?? 0,
      juicios: agregado?.juicios ?? 0,
      relevantes: agregado?.relevantes ?? 0,
      juzgados_por_evaluador: miosPorClave.get(c.clave)?.size ?? 0,
    };
  });

  enviarJson(res, 200, { objetivo, consultas, avisos });
}

// -------------------------------------------------------------- Métricas

function profundidadDe(url, config) {
  const crudo = Number.parseInt(url.searchParams.get("k") ?? "", 10);
  const pedida = Number.isFinite(crudo) ? crudo : config.evaluacion.profundidad_metricas;
  return Math.max(1, Math.min(K_MAXIMO, pedida));
}

async function cargarMetricas(url, config) {
  const juicios = await leerJuicios(config.evaluacion.archivo_juicios);
  const { registros, avisos } = await leerRegistros(config.servidor.archivo_registro);
  const profundidad = profundidadDe(url, config);
  const filtros = {
    evaluador: url.searchParams.get("evaluador") || null,
    indice: url.searchParams.get("indice") || null,
    consulta: url.searchParams.get("consulta") || null,
  };
  const informe = generarMetricas({ juicios, registros, k: profundidad, ...filtros });
  informe.avisos.unshift(...avisos);

  // `salida/metricas.json` es la foto global que lee documento/scripts/tablas.py.
  // Si la página se está mirando con filtros, no se guarda la vista filtrada:
  // se recalcula el informe completo para no contaminar las cifras del informe.
  let paraArchivo = informe;
  if (filtros.evaluador || filtros.indice || filtros.consulta) {
    paraArchivo = generarMetricas({ juicios, registros, k: profundidad });
    paraArchivo.avisos.unshift(...avisos);
  }
  try {
    await guardarMetricas(config.evaluacion.archivo_metricas, paraArchivo);
  } catch (err) {
    console.warn(`No se pudo guardar ${config.evaluacion.archivo_metricas}: ${err.message}`);
    informe.avisos.push(`No se pudo guardar el informe de métricas en disco: ${err.message}`);
  }

  const evaluadores = [...new Set(juicios.map((j) => String(j.evaluador)))].sort((a, b) => a.localeCompare(b, "es"));
  return { informe, juiciosTotales: juicios.length, evaluadores };
}

async function apiMetricas(url, res, { config }) {
  const { informe } = await cargarMetricas(url, config);
  enviarJson(res, 200, informe);
}

async function paginaMetricas(url, res, { config }) {
  try {
    const { informe, juiciosTotales, evaluadores } = await cargarMetricas(url, config);
    enviarHtml(
      res,
      200,
      renderMetricas(informe, {
        indices: config.elasticsearch.indices_disponibles,
        evaluadores,
        juiciosTotales,
      }),
    );
  } catch (err) {
    console.error(err);
    enviarHtml(res, 500, renderErrorMetricas(err.message));
  }
}

// ---------------------------------------------------------------- Estático

const PAGINAS = { "/": "/index.html", "/evaluar": "/evaluar.html" };

async function servirEstatico(url, res) {
  let ruta;
  try {
    ruta = decodeURIComponent(url.pathname);
  } catch {
    ruta = url.pathname;
  }
  ruta = PAGINAS[ruta] ?? ruta;
  const archivo = normalize(join(PUBLICO, ruta));
  if (archivo !== PUBLICO && !archivo.startsWith(PUBLICO + sep)) {
    res.writeHead(403).end();
    return;
  }
  try {
    const contenido = await readFile(archivo);
    res.writeHead(200, {
      "content-type": TIPOS[extname(archivo)] ?? "application/octet-stream",
      "cache-control": "no-cache",
    });
    res.end(contenido);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" }).end("No encontrado");
  }
}

// ----------------------------------------------------------------- Router

async function manejar(req, res, contexto) {
  const url = new URL(req.url, "http://localhost");
  const ruta = url.pathname;

  const soloGet = () => {
    if (req.method !== "GET") {
      enviarJson(res, 405, { error: `Método ${req.method} no permitido.` }, { allow: "GET" });
      return false;
    }
    return true;
  };

  if (ruta === "/api/buscar") return soloGet() && apiBuscar(url, res, contexto);
  if (ruta === "/api/indices") return soloGet() && apiIndices(res, contexto);
  if (ruta === "/api/metricas") return soloGet() && apiMetricas(url, res, contexto);
  if (ruta === "/metricas") return soloGet() && paginaMetricas(url, res, contexto);
  if (ruta === "/api/evaluacion/resumen") return soloGet() && apiResumenEvaluacion(url, res, contexto);
  if (ruta === "/api/evaluacion/consultas") return soloGet() && apiConsultasEvaluacion(url, res, contexto);

  if (ruta === "/api/evaluacion/juicios") {
    if (req.method === "GET") return apiJuicios(url, res, contexto);
    if (req.method === "POST") return apiGuardarJuicio(req, res, contexto);
    if (req.method === "DELETE") return apiBorrarJuicio(url, res, contexto);
    return enviarJson(res, 405, { error: `Método ${req.method} no permitido.` }, { allow: "GET, POST, DELETE" });
  }

  if (!soloGet()) return;
  return servirEstatico(url, res);
}

/** Crea el servidor HTTP con la configuración y el cliente ya resueltos (útil en pruebas). */
export function crearServidor({ config, cliente }) {
  const contexto = { config, cliente };
  return http.createServer((req, res) => {
    manejar(req, res, contexto).catch((err) => {
      if (res.headersSent) {
        res.end();
        return;
      }
      const estado = err instanceof ErrorJuicio || err instanceof ErrorHttp ? err.estado : 500;
      if (estado >= 500) console.error(err);
      enviarJson(res, estado, { error: err.message });
    });
  });
}

/** Arranca el servidor leyendo la configuración (uso normal: npm start). */
export async function iniciar(rutaConfiguracion = join(RAIZ, "buscador.conf")) {
  const config = cargarConfiguracion(rutaConfiguracion);
  const cliente = new Client({ node: config.elasticsearch.url, requestTimeout: 60000 });
  const servidor = crearServidor({ config, cliente });
  await new Promise((listo, falla) => {
    servidor.once("error", falla);
    servidor.listen(config.servidor.puerto, () => {
      servidor.off("error", falla);
      listo();
    });
  });
  console.log(`Buscador Encuadre en http://localhost:${config.servidor.puerto}`);
  console.log(`  Evaluación: http://localhost:${config.servidor.puerto}/evaluar`);
  console.log(`  Métricas:   http://localhost:${config.servidor.puerto}/metricas`);
  console.log(`Elasticsearch: ${config.elasticsearch.url} (índice por defecto: ${config.elasticsearch.indice_por_defecto})`);
  return servidor;
}

const ejecutadoDirectamente =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (ejecutadoDirectamente) {
  iniciar(process.argv[2] ?? join(RAIZ, "buscador.conf")).catch((err) => {
    console.error(err);
    process.exitCode = 1;
  });
}
