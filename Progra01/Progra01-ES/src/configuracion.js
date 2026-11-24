// configuracion.js
// Lee los archivos de configuración estilo Linux (.conf) del indexador.
//
// Usa el mismo formato que el indexador propio (ParserConfiguracionLinux en
// configuracion.py): secciones [nombre], pares "clave = valor" o "clave: valor",
// comentarios con # o ; y listas separadas por comas. Así ambos indexadores se
// configuran con archivos equivalentes y las mismas claves de normalización.

import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve, isAbsolute } from "node:path";

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

/** Elimina comentarios (# o ;) que no estén dentro de comillas. */
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

/** Convierte un valor de texto a booleano, número, lista o cadena. */
export function parsearValor(crudo) {
  const v = crudo.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1);
  }
  const minus = v.toLowerCase();
  if (["true", "yes", "on", "si", "sí"].includes(minus)) return true;
  if (["false", "no", "off"].includes(minus)) return false;
  if (/^-?\d+$/.test(v)) return Number.parseInt(v, 10);
  if (/^-?\d+\.\d+$/.test(v)) return Number.parseFloat(v);
  if (v.includes(",")) return v.split(",").map((x) => x.trim()).filter(Boolean);
  return v;
}

/** Parsea el texto de un .conf y devuelve { seccion: { clave: valor } }. */
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

    const separador = linea.includes("=") ? "=" : linea.includes(":") ? ":" : null;
    if (!separador) {
      throw new Error(`Error de sintaxis en línea ${i + 1}: se esperaba '=' o ':' -> ${lineaBruta}`);
    }
    const pos = linea.indexOf(separador);
    const clave = linea.slice(0, pos).trim().toLowerCase();
    if (!clave) throw new Error(`Clave vacía en línea ${i + 1}: ${lineaBruta}`);
    config[seccion][clave] = parsearValor(linea.slice(pos + 1));
  });

  return config;
}

// ---------------------------------------------------------------------------
// Valores por defecto (mismos que el propio, más la sección [elasticsearch])
// ---------------------------------------------------------------------------

const DEFECTOS = {
  general: {
    nombre_politica: "estandar",
    descripcion: "",
  },
  rutas: {
    base_datos: "../../Progra00/almacenamiento/metadatos_araña.db",
    repositorio: "../../Progra00/almacenamiento/repositorio",
    directorio_salida: "salida",
  },
  normalizacion: {
    idioma: "auto",
    minusculas: true,
    eliminar_puntuacion: true,
    remover_acentos: false,
    stopwords: true,
    idiomas_stopwords: ["es", "en"],
    stemming: true,
    algoritmo_stemming: "snowball",
    normalizar_numeros: false,
    modo_numeros: "preservar",
    normalizar_fechas: false,
    expandir_sinonimos: false,
    longitud_minima_token: 2,
    longitud_maxima_token: 50,
  },
  modelo: {
    esquema_tf: "logaritmico",
    esquema_idf: "estandar",
    normalizacion_coseno: true,
    suavizado_idf: true,
  },
  elasticsearch: {
    url: "http://localhost:9200",
    indice: "",
    shards: 1,
    replicas: 0,
    codec: "default",
    guardar_texto: false,
    guardar_posiciones: false,
    tamano_lote_bytes: 5000000,
    concurrencia: 2,
    tamano_lote_vectores: 50,
  },
};

/** Garantiza que un valor sea lista (un solo idioma se lee como cadena). */
function comoLista(valor) {
  if (Array.isArray(valor)) return valor;
  if (valor === undefined || valor === null || valor === "") return [];
  return [String(valor)];
}

/**
 * Carga un .conf, lo combina con los valores por defecto y resuelve las rutas.
 * Las rutas relativas se resuelven desde la raíz del proyecto (la carpeta que
 * contiene a configuraciones/), igual que el propio las resuelve desde Progra01.
 */
export function cargarConfiguracion(rutaConf) {
  const rutaAbs = resolve(rutaConf);
  if (!existsSync(rutaAbs)) {
    throw new Error(`Archivo de configuración no encontrado: ${rutaAbs}`);
  }
  const crudo = parsearTextoConf(readFileSync(rutaAbs, "utf-8"));

  const config = {};
  for (const [seccion, valores] of Object.entries(DEFECTOS)) {
    config[seccion] = { ...valores, ...(crudo[seccion] ?? {}) };
  }

  // Textos libres que pueden traer comas (el parser los leería como listas)
  for (const clave of ["nombre_politica", "descripcion"]) {
    const v = config.general[clave];
    if (Array.isArray(v)) config.general[clave] = v.join(", ");
  }

  const n = config.normalizacion;
  n.idiomas_stopwords = comoLista(n.idiomas_stopwords).map((x) => x.toLowerCase());
  n.idioma = String(n.idioma).toLowerCase();
  n.modo_numeros = String(n.modo_numeros).toLowerCase();

  const raiz = resolve(dirname(rutaAbs), "..");
  const resolver = (r) => (isAbsolute(r) ? r : resolve(raiz, r));
  config.rutas.base_datos = resolver(config.rutas.base_datos);
  config.rutas.repositorio = resolver(config.rutas.repositorio);
  config.rutas.directorio_salida = resolver(config.rutas.directorio_salida);

  if (!config.elasticsearch.indice) {
    config.elasticsearch.indice = `rit_${config.general.nombre_politica}`;
  }
  config.elasticsearch.indice = String(config.elasticsearch.indice).toLowerCase();
  config.rutaArchivo = rutaAbs;
  return config;
}
