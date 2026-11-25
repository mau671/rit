// configuracion.js
// Lee buscador.conf (mismo formato Linux que los indexadores del Proyecto 2).

import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve, isAbsolute } from "node:path";

function limpiarLinea(linea) {
  let resultado = "";
  let enComillas = null;
  for (const ch of linea) {
    if (ch === '"' || ch === "'") {
      if (enComillas === null) enComillas = ch;
      else if (enComillas === ch) enComillas = null;
      resultado += ch;
    } else if ((ch === "#" || ch === ";") && enComillas === null) {
      break;
    } else {
      resultado += ch;
    }
  }
  return resultado.trim();
}

export function parsearValor(crudo) {
  const v = crudo.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) return v.slice(1, -1);
  const minus = v.toLowerCase();
  if (["true", "yes", "on", "si", "sí"].includes(minus)) return true;
  if (["false", "no", "off"].includes(minus)) return false;
  if (/^-?\d+$/.test(v)) return Number.parseInt(v, 10);
  if (/^-?\d+\.\d+$/.test(v)) return Number.parseFloat(v);
  if (v.includes(",")) return v.split(",").map((x) => x.trim()).filter(Boolean);
  return v;
}

export function parsearTextoConf(texto) {
  let seccion = "global";
  const config = { [seccion]: {} };
  texto.split(/\r?\n/).forEach((lineaBruta, i) => {
    const linea = limpiarLinea(lineaBruta);
    if (!linea) return;
    if (linea.startsWith("[") && linea.endsWith("]")) {
      seccion = linea.slice(1, -1).trim().toLowerCase();
      config[seccion] ??= {};
      return;
    }
    const sep = linea.includes("=") ? "=" : linea.includes(":") ? ":" : null;
    if (!sep) throw new Error(`Error de sintaxis en línea ${i + 1}: ${lineaBruta}`);
    const pos = linea.indexOf(sep);
    config[seccion][linea.slice(0, pos).trim().toLowerCase()] = parsearValor(linea.slice(pos + 1));
  });
  return config;
}

const DEFECTOS = {
  servidor: { puerto: 3000, registrar_consultas: true, archivo_registro: "salida/consultas.jsonl" },
  elasticsearch: {
    url: "http://localhost:9200",
    indice_por_defecto: "rit_avanzado",
    indices_disponibles: ["rit_avanzado"],
  },
  coleccion: { repositorio: "../Progra00/almacenamiento/repositorio" },
  modelo: { esquema_tf: "logaritmico", esquema_idf: "estandar" },
  resultados: {
    por_pagina: 10,
    maximo_resultados: 1000,
    caracteres_fragmento: 300,
    fragmentos_por_resultado: 2,
    caracteres_analisis: 400000,
  },
};

export function cargarConfiguracion(ruta) {
  const rutaAbs = resolve(ruta);
  if (!existsSync(rutaAbs)) throw new Error(`No existe el archivo de configuración ${rutaAbs}`);
  const crudo = parsearTextoConf(readFileSync(rutaAbs, "utf-8"));
  const config = {};
  for (const [seccion, valores] of Object.entries(DEFECTOS)) config[seccion] = { ...valores, ...(crudo[seccion] ?? {}) };

  const es = config.elasticsearch;
  if (!Array.isArray(es.indices_disponibles)) es.indices_disponibles = [String(es.indices_disponibles)];
  if (!es.indices_disponibles.includes(es.indice_por_defecto)) es.indices_disponibles.unshift(es.indice_por_defecto);

  // Variable de entorno para pruebas o para apuntar a otro servidor
  if (process.env.ELASTICSEARCH_URL) es.url = process.env.ELASTICSEARCH_URL;
  if (process.env.PUERTO) config.servidor.puerto = Number(process.env.PUERTO);

  const raiz = dirname(rutaAbs);
  const resolver = (r) => (isAbsolute(r) ? r : resolve(raiz, r));
  config.coleccion.repositorio = resolver(config.coleccion.repositorio);
  config.servidor.archivo_registro = resolver(config.servidor.archivo_registro);
  config.raiz = raiz;
  return config;
}
