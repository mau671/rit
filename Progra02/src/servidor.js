#!/usr/bin/env node
// servidor.js
// Servidor web del buscador. Es un programa independiente de la araña y de
// los indexadores: solo lee los índices de Elasticsearch y los textos del
// repositorio.
//
//   GET /                 interfaz de búsqueda (public/index.html)
//   GET /api/buscar       búsqueda (ver docs/contrato-api.md)
//   GET /api/indices      índices disponibles y estado
//
// Uso: node src/servidor.js [ruta a buscador.conf]

import http from "node:http";
import { readFile, appendFile, mkdir } from "node:fs/promises";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@elastic/elasticsearch";

import { cargarConfiguracion } from "./configuracion.js";
import { buscar } from "./busqueda.js";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const PUBLICO = join(RAIZ, "public");
const config = cargarConfiguracion(process.argv[2] ?? join(RAIZ, "buscador.conf"));
const cliente = new Client({ node: config.elasticsearch.url, requestTimeout: 60000 });

const TIPOS = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".png": "image/png",
};

function enviarJson(res, estado, cuerpo) {
  res.writeHead(estado, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(cuerpo));
}

async function registrarConsulta(entrada) {
  if (!config.servidor.registrar_consultas) return;
  try {
    await mkdir(dirname(config.servidor.archivo_registro), { recursive: true });
    await appendFile(config.servidor.archivo_registro, JSON.stringify(entrada) + "\n", "utf-8");
  } catch (err) {
    console.warn(`No se pudo registrar la consulta: ${err.message}`);
  }
}

function mensajeDeError(err) {
  if (err?.meta?.statusCode === 404) return "El índice no existe. Construilo primero con Progra01-ES.";
  if (err?.name === "ConnectionError") return "No hay conexión con Elasticsearch. Revisá que esté corriendo (docker compose up -d).";
  return err?.meta?.body?.error?.reason ?? err.message;
}

async function apiBuscar(url, res) {
  const q = url.searchParams.get("q") ?? "";
  const pagina = Math.max(1, Number.parseInt(url.searchParams.get("p") ?? "1", 10) || 1);
  let indice = url.searchParams.get("indice") || config.elasticsearch.indice_por_defecto;
  if (!config.elasticsearch.indices_disponibles.includes(indice)) {
    return enviarJson(res, 400, { error: `El índice ${indice} no está habilitado en buscador.conf.` });
  }
  if (!q.trim()) return enviarJson(res, 400, { error: "Falta la consulta (parámetro q)." });

  try {
    const resultado = await buscar(cliente, config, { q, pagina, indice });
    enviarJson(res, 200, resultado);
    registrarConsulta({
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

async function apiIndices(res) {
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

async function servirEstatico(url, res) {
  const ruta = url.pathname === "/" ? "/index.html" : url.pathname;
  const archivo = normalize(join(PUBLICO, ruta));
  if (!archivo.startsWith(PUBLICO)) {
    res.writeHead(403).end();
    return;
  }
  try {
    const contenido = await readFile(archivo);
    res.writeHead(200, { "content-type": TIPOS[extname(archivo)] ?? "application/octet-stream" });
    res.end(contenido);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" }).end("No encontrado");
  }
}

const servidor = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (req.method !== "GET") return enviarJson(res, 405, { error: "Método no permitido" });
  if (url.pathname === "/api/buscar") return apiBuscar(url, res);
  if (url.pathname === "/api/indices") return apiIndices(res);
  return servirEstatico(url, res);
});

servidor.listen(config.servidor.puerto, () => {
  console.log(`Buscador Encuadre en http://localhost:${config.servidor.puerto}`);
  console.log(`Elasticsearch: ${config.elasticsearch.url} (índice por defecto: ${config.elasticsearch.indice_por_defecto})`);
});
