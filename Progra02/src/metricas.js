// metricas.js
// Cálculo de las métricas de evaluación de ranking.
//
// Entradas:
//   - juicios:   arreglo de salida/juicios.json (interfaz de evaluación).
//   - registros: líneas de salida/consultas.jsonl (ranking realmente mostrado).
//
// Salidas: P@k, Recall@k, AP (y su media, MAP), nDCG@k, MRR, la curva de
// precisión P@1..P@k, el acuerdo entre evaluadores (κ de Cohen) y estadísticas
// de uso. Las fórmulas siguen a Manning, Raghavan y Schütze (2008, cap. 8) y
// Järvelin y Kekäläinen (2002) para nDCG; son las mismas del módulo Python de
// la rama `metricas` (metricas/calculo.py), portadas a Node para no depender de
// dos ejecutables.
//
// Convenciones:
//   - Un documento recuperado que nadie juzgó cuenta como NO relevante.
//   - Un documento juzgado como relevante que el sistema no devolvió también
//     penaliza AP, porque el denominador es el total de relevantes conocidos.
//   - Si una métrica no está definida (por ejemplo, Recall sin ningún relevante
//     conocido) se devuelve null y los promedios la ignoran.

import { readFile, mkdir, writeFile, rename, unlink } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

import { normalizarConsulta, RELEVANTE_MINIMO } from "./juicios.js";

export const K_POR_DEFECTO = 10;
export const K_MAXIMO = 50;

// --------------------------------------------------------------- Fórmulas

/** P@k: relevantes entre los k primeros; lo que no se devolvió cuenta como fallo. */
export function precisionEnK(rel, k) {
  if (!Number.isInteger(k) || k <= 0) throw new RangeError("k debe ser un entero positivo");
  return rel.slice(0, k).filter(Boolean).length / k;
}

/** Recall@k: relevantes del top k sobre todos los relevantes conocidos (el pool juzgado). */
export function recallEnK(rel, k, totalRelevantes) {
  if (!Number.isInteger(k) || k <= 0) throw new RangeError("k debe ser un entero positivo");
  if (totalRelevantes <= 0) return null;
  return rel.slice(0, k).filter(Boolean).length / totalRelevantes;
}

/** AP: promedio de P@i en cada posición i con relevante, dividido entre los relevantes conocidos. */
export function precisionPromedio(rel, totalRelevantes) {
  if (totalRelevantes <= 0) return null;
  let aciertos = 0;
  let suma = 0;
  rel.forEach((esRelevante, i) => {
    if (!esRelevante) return;
    aciertos += 1;
    suma += aciertos / (i + 1);
  });
  return suma / totalRelevantes;
}

/** RR: 1/posición del primer relevante; 0 si no hay ninguno. */
export function rangoReciproco(rel) {
  const i = rel.findIndex(Boolean);
  return i < 0 ? 0 : 1 / (i + 1);
}

function dcg(ganancias, k) {
  return ganancias
    .slice(0, k)
    .reduce((suma, g, i) => suma + (Number(g) || 0) / Math.log2(i + 2), 0);
}

/**
 * nDCG@k con ganancia lineal y descuento logarítmico (Järvelin y Kekäläinen, 2002).
 * El ideal se arma con todas las ganancias conocidas de la consulta, de mayor a menor.
 */
export function ndcgEnK(ganancias, k, gananciasConocidas) {
  if (!Number.isInteger(k) || k <= 0) throw new RangeError("k debe ser un entero positivo");
  const ideal = dcg([...gananciasConocidas].sort((a, b) => b - a), k);
  if (ideal <= 0) return null;
  return dcg(ganancias, k) / ideal;
}

/** Media ignorando null/undefined; null si no queda ningún valor. */
export function promedio(valores) {
  const utiles = valores.filter((v) => typeof v === "number" && Number.isFinite(v));
  return utiles.length ? utiles.reduce((a, b) => a + b, 0) / utiles.length : null;
}

/**
 * κ de Cohen entre dos evaluadores binarios (Cohen, 1960).
 * Devuelve null si no hay casos o si el acuerdo esperado es 1.
 */
export function kappaCohen(a, b) {
  if (a.length !== b.length) throw new RangeError("Los dos arreglos deben tener el mismo tamaño");
  const n = a.length;
  if (n === 0) return null;
  const po = a.filter((x, i) => x === b[i]).length / n;
  const pa = a.filter(Boolean).length / n;
  const pb = b.filter(Boolean).length / n;
  const pe = pa * pb + (1 - pa) * (1 - pb);
  if (pe === 1) return null;
  return (po - pe) / (1 - pe);
}

/** Escala de Landis y Koch (1977) para leer κ. */
export function interpretarKappa(k) {
  if (k === null || k === undefined || !Number.isFinite(k)) return "no definido";
  if (k <= 0) return "pobre";
  if (k <= 0.2) return "leve";
  if (k <= 0.4) return "aceptable";
  if (k <= 0.6) return "moderado";
  if (k <= 0.8) return "sustancial";
  return "casi perfecto";
}

// -------------------------------------------------------- Datos del buscador

export const claveConsulta = (consulta, indice) =>
  `${normalizarConsulta(consulta)}\u0000${String(indice ?? "").trim()}`;

const fechaMs = (valor) => {
  const t = Date.parse(valor ?? "");
  return Number.isNaN(t) ? -Infinity : t;
};

/** Lee el registro de búsquedas (JSONL). Tolera archivo ausente y líneas rotas. */
export async function leerRegistros(ruta) {
  let crudo;
  try {
    crudo = await readFile(ruta, "utf-8");
  } catch (err) {
    if (err.code === "ENOENT") return { registros: [], avisos: [] };
    throw err;
  }
  const registros = [];
  let rotas = 0;
  for (const linea of crudo.split(/\r?\n/)) {
    if (!linea.trim()) continue;
    try {
      const r = JSON.parse(linea);
      if (r && typeof r === "object" && typeof r.consulta === "string" && Array.isArray(r.resultados)) registros.push(r);
      else rotas += 1;
    } catch {
      rotas += 1;
    }
  }
  return {
    registros,
    avisos: rotas ? [`Se ignoraron ${rotas} líneas del registro de consultas que no eran JSON válido.`] : [],
  };
}

/**
 * Reconstruye el ranking que se mostró para una consulta e índice a partir del
 * registro: toma la última búsqueda de cada página y las concatena en orden.
 * Devuelve null si esa consulta nunca se registró.
 */
export function reconstruirRanking(registros, consulta, indice) {
  const clave = claveConsulta(consulta, indice);
  const delGrupo = registros.filter(
    (r) => claveConsulta(r.consulta, r.indice) === clave && Array.isArray(r.resultados),
  );
  if (!delGrupo.length) return null;

  const porPagina = new Map();
  for (const r of delGrupo) {
    const pagina = Number.isInteger(r.pagina) && r.pagina > 0 ? r.pagina : 1;
    const previa = porPagina.get(pagina);
    if (!previa || fechaMs(r.fecha) >= fechaMs(previa.fecha)) porPagina.set(pagina, r);
  }

  const paginas = [...porPagina.keys()].sort((a, b) => a - b);
  const ids = [];
  const vistos = new Set();
  for (const pagina of paginas) {
    for (const id of porPagina.get(pagina).resultados) {
      const s = String(id);
      if (!vistos.has(s)) {
        vistos.add(s);
        ids.push(s);
      }
    }
  }
  if (!ids.length) return null;
  const ultima = delGrupo.reduce((a, b) => (fechaMs(b.fecha) > fechaMs(a.fecha) ? b : a));
  return { ids, fuente: "registro", paginas, total: ultima.total ?? null, fecha: ultima.fecha ?? null };
}

/** Ranking de respaldo cuando no hay registro: coloca cada juicio en su rango. */
export function rankingDesdeJuicios(docs, k = K_POR_DEFECTO) {
  const ordenados = [...docs.values()].sort(
    (a, b) =>
      (a.rango ?? Number.POSITIVE_INFINITY) - (b.rango ?? Number.POSITIVE_INFINITY) ||
      String(a.doc_id).localeCompare(String(b.doc_id)),
  );
  const porRango = new Map();
  const sueltos = [];
  let maxRango = 0;
  for (const d of ordenados) {
    if (Number.isInteger(d.rango) && d.rango > 0) {
      porRango.set(d.rango, d.doc_id);
      maxRango = Math.max(maxRango, d.rango);
    } else {
      sueltos.push(d.doc_id);
    }
  }
  const largo = Math.max(maxRango, k, 1);
  const ids = Array(largo).fill(null);
  for (const [rango, id] of porRango) ids[rango - 1] = id;
  for (let i = 0; i < largo && sueltos.length; i += 1) {
    if (ids[i] === null) ids[i] = sueltos.shift();
  }
  // Los huecos sin juicio quedan como null: cuentan como no relevantes.
  return { ids, fuente: "juicios", paginas: [], total: null, fecha: null };
}

// ---------------------------------------------------------- Consolidación

/**
 * Agrupa los juicios por consulta + índice y los consolida entre evaluadores:
 * ganancia = promedio de las notas; relevante si al menos la mitad marcó >= 1.
 * Igual criterio que metricas/datos.py de la rama `metricas`.
 */
export function consolidar(juicios) {
  const grupos = new Map();
  for (const j of juicios) {
    const clave = claveConsulta(j.consulta, j.indice);
    if (!grupos.has(clave)) {
      grupos.set(clave, { clave, consulta: j.consulta, indice: j.indice, docs: new Map() });
    }
    const grupo = grupos.get(clave);
    const docId = String(j.doc_id);
    if (!grupo.docs.has(docId)) {
      grupo.docs.set(docId, { doc_id: docId, votos: new Map(), url: "", titulo: "", rango: null });
    }
    const doc = grupo.docs.get(docId);
    const nota = Math.max(0, Math.min(2, Number(j.relevante) || 0));
    doc.votos.set(String(j.evaluador), nota);
    doc.url ||= j.url ?? "";
    doc.titulo ||= j.titulo ?? "";
    if (Number.isInteger(j.rango) && j.rango > 0 && (doc.rango === null || j.rango < doc.rango)) doc.rango = j.rango;
  }

  for (const grupo of grupos.values()) {
    for (const doc of grupo.docs.values()) {
      const notas = [...doc.votos.values()];
      doc.ganancia = promedio(notas) ?? 0;
      doc.relevante = notas.filter((n) => n >= RELEVANTE_MINIMO).length / notas.length >= 0.5;
      doc.nEvaluadores = notas.length;
    }
  }
  return grupos;
}

// ------------------------------------------------------------ Evaluación

function filaDeGrupo(grupo, registros, k) {
  const avisos = [];
  let ranking = reconstruirRanking(registros, grupo.consulta, grupo.indice);
  if (!ranking) {
    ranking = rankingDesdeJuicios(grupo.docs, k);
    avisos.push(
      `No hay registro de la búsqueda «${grupo.consulta}» (${grupo.indice}); el ranking se reconstruyó con el rango guardado en los juicios.`,
    );
  }

  const ids = ranking.ids;
  const ganancias = ids.map((id) => (id && grupo.docs.get(id) ? grupo.docs.get(id).ganancia : 0));
  const rel = ids.map((id) => Boolean(id && grupo.docs.get(id)?.relevante));
  const relevantesConocidos = [...grupo.docs.values()].filter((d) => d.relevante).length;
  const gananciasConocidas = [...grupo.docs.values()].map((d) => d.ganancia);
  const enK = ids.slice(0, k);
  const juzgadosEnK = enK.filter((id) => id && grupo.docs.has(id)).length;

  const fila = {
    clave: grupo.clave,
    consulta: grupo.consulta,
    indice: grupo.indice,
    fuente: ranking.fuente,
    evaluadores: [...new Set([...grupo.docs.values()].flatMap((d) => [...d.votos.keys()]))].sort(),
    juzgados: ids.filter((id) => id && grupo.docs.has(id)).length,
    juzgadosEnK,
    relevantesConocidos,
    cobertura: enK.length ? juzgadosEnK / enK.length : null,
    resultados: ids.length,
    [`P@${k}`]: precisionEnK(rel, k),
    [`Recall@${k}`]: recallEnK(rel, k, relevantesConocidos),
    AP: precisionPromedio(rel, relevantesConocidos),
    [`nDCG@${k}`]: ndcgEnK(ganancias, k, gananciasConocidas),
    RR: rangoReciproco(rel),
    curva: Array.from({ length: k }, (_, i) => precisionEnK(rel, i + 1)),
    detalle: ids.slice(0, Math.max(k, 10)).map((id, i) => {
      const doc = id ? grupo.docs.get(id) : null;
      return {
        posicion: i + 1,
        doc_id: id,
        juzgado: Boolean(doc),
        relevante: Boolean(doc?.relevante),
        ganancia: doc?.ganancia ?? 0,
        url: doc?.url ?? "",
        titulo: doc?.titulo ?? "",
      };
    }),
  };
  return { fila, avisos };
}

function globalesDe(filas, k) {
  const valores = (fn) => promedio(filas.map(fn));
  return {
    [`P@${k}`]: valores((f) => f[`P@${k}`]),
    MAP: valores((f) => f.AP),
    [`nDCG@${k}`]: valores((f) => f[`nDCG@${k}`]),
    MRR: valores((f) => f.RR),
    [`Recall@${k}`]: valores((f) => f[`Recall@${k}`]),
  };
}

/**
 * Calcula el informe completo.
 *
 * @param {object} opciones
 * @param {Array} opciones.juicios   juicios guardados
 * @param {Array} opciones.registros líneas del registro de consultas
 * @param {number} [opciones.k]      profundidad (1..50)
 * @param {string} [opciones.evaluador] filtra a un evaluador
 * @param {string} [opciones.indice]    filtra a un índice
 * @param {string} [opciones.consulta]  filtra a una consulta
 */
export function generarMetricas({ juicios = [], registros = [], k = K_POR_DEFECTO, evaluador = null, indice = null, consulta = null } = {}) {
  const profundidad = Math.max(1, Math.min(K_MAXIMO, Number.isInteger(Number(k)) ? Number(k) : K_POR_DEFECTO));
  const avisos = [];

  let filtrados = juicios;
  if (evaluador) filtrados = filtrados.filter((j) => String(j.evaluador) === String(evaluador));
  if (indice) filtrados = filtrados.filter((j) => String(j.indice) === String(indice));
  if (consulta) {
    const buscada = normalizarConsulta(consulta);
    filtrados = filtrados.filter((j) => normalizarConsulta(j.consulta) === buscada);
  }

  const grupos = consolidar(filtrados);
  const filas = [];
  for (const grupo of grupos.values()) {
    const { fila, avisos: avisosFila } = filaDeGrupo(grupo, registros, profundidad);
    filas.push(fila);
    avisos.push(...avisosFila);
  }
  filas.sort((a, b) => a.consulta.localeCompare(b.consulta, "es") || a.indice.localeCompare(b.indice));

  // El uso se informa sobre el registro que cae dentro de los filtros.
  const consultaBuscada = consulta ? normalizarConsulta(consulta) : null;
  const registrosVisibles = registros.filter(
    (r) =>
      (!indice || String(r.indice) === String(indice)) &&
      (!consultaBuscada || normalizarConsulta(r.consulta) === consultaBuscada),
  );

  const globales = globalesDe(filas, profundidad);
  const curvaPrecision = Array.from({ length: profundidad }, (_, i) =>
    promedio(filas.map((f) => f.curva[i])),
  );

  // Métricas de cada evaluador por separado: permite comparar criterios incluso
  // cuando solo hay una persona (allí κ no está definido).
  const nombres = [...new Set(juicios.map((j) => String(j.evaluador)))].sort();
  const porEvaluador = nombres
    .filter((e) => !evaluador || e === String(evaluador))
    .map((e) => {
      const propios = filtrados.filter((j) => String(j.evaluador) === e);
      const filasE = [];
      for (const grupo of consolidar(propios).values()) filasE.push(filaDeGrupo(grupo, registros, profundidad).fila);
      return {
        evaluador: e,
        consultas: filasE.length,
        juicios: propios.length,
        ...globalesDe(filasE, profundidad),
      };
    });

  const uso = estadisticasUso(filtrados, registrosVisibles, filas, profundidad, { evaluador, indice, consulta });
  const sinJuicios = consultasSinJuicios(registrosVisibles, filtrados, { indice, consulta });

  if (!filtrados.length) {
    avisos.unshift(
      juicios.length
        ? "No hay juicios que coincidan con los filtros elegidos."
        : "Todavía no hay juicios de relevancia. Entrá a /evaluar para marcar resultados.",
    );
  }
  if (juicios.length && filas.some((f) => f.fuente === "juicios")) {
    avisos.push("Algunas consultas no tienen registro de búsqueda: su ranking se armó con el rango guardado en los juicios.");
  }

  return {
    k: profundidad,
    generado: new Date().toISOString(),
    filtros: { evaluador: evaluador ?? null, indice: indice ?? null, consulta: consulta ?? null },
    global: globales,
    porConsulta: filas,
    porEvaluador,
    curvaPrecision,
    uso,
    sinJuicios,
    avisos,
  };
}

// ------------------------------------------------------------- Estadísticas

function consultasSinJuicios(registros, juicios, { indice = null, consulta = null } = {}) {
  const juzgadas = new Set(juicios.map((j) => claveConsulta(j.consulta, j.indice)));
  const porClave = new Map();
  for (const r of registros) {
    const clave = claveConsulta(r.consulta, r.indice);
    if (juzgadas.has(clave)) continue;
    if (indice && String(r.indice) !== String(indice)) continue;
    if (consulta && normalizarConsulta(r.consulta) !== normalizarConsulta(consulta)) continue;
    const previa = porClave.get(clave);
    if (!previa) {
      porClave.set(clave, { consulta: r.consulta, indice: r.indice, veces: 1, total: r.total ?? null, ultima: r.fecha ?? null });
    } else {
      previa.veces += 1;
      if (fechaMs(r.fecha) >= fechaMs(previa.ultima)) {
        previa.consulta = r.consulta;
        previa.total = r.total ?? previa.total;
        previa.ultima = r.fecha ?? previa.ultima;
      }
    }
  }
  return [...porClave.values()]
    .sort((a, b) => b.veces - a.veces || fechaMs(b.ultima) - fechaMs(a.ultima))
    .slice(0, 50);
}

/** Estadísticas de uso y acuerdo entre evaluadores para la página y el documento. */
export function estadisticasUso(juicios, registros, filas, k = K_POR_DEFECTO, filtros = {}) {
  const porEvaluador = {};
  const porIndice = {};
  const distribucion = { 0: 0, 1: 0, 2: 0 };
  let relevantes = 0;
  let primer = null;
  let ultimo = null;

  for (const j of juicios) {
    const e = String(j.evaluador);
    porEvaluador[e] = (porEvaluador[e] ?? 0) + 1;
    const i = String(j.indice);
    porIndice[i] = (porIndice[i] ?? 0) + 1;
    const nota = Math.max(0, Math.min(2, Number(j.relevante) || 0));
    distribucion[nota] += 1;
    if (nota >= RELEVANTE_MINIMO) relevantes += 1;
    const t = j.timestamp ?? null;
    if (t && (!primer || fechaMs(t) < fechaMs(primer))) primer = t;
    if (t && (!ultimo || fechaMs(t) > fechaMs(ultimo))) ultimo = t;
  }

  // Votos binarios por documento para κ.
  const votos = new Map();
  for (const j of juicios) {
    const clave = `${claveConsulta(j.consulta, j.indice)}\u0000${String(j.doc_id)}`;
    if (!votos.has(clave)) votos.set(clave, { consulta: j.consulta, indice: j.indice, doc: j.doc_id, votos: new Map() });
    votos.get(clave).votos.set(String(j.evaluador), Number(j.relevante) >= RELEVANTE_MINIMO);
  }
  const evaluadores = Object.keys(porEvaluador).sort();
  const pares = [];
  for (let a = 0; a < evaluadores.length; a += 1) {
    for (let b = a + 1; b < evaluadores.length; b += 1) {
      const comunes = [...votos.values()].filter((v) => v.votos.has(evaluadores[a]) && v.votos.has(evaluadores[b]));
      const kappa = kappaCohen(
        comunes.map((v) => v.votos.get(evaluadores[a])),
        comunes.map((v) => v.votos.get(evaluadores[b])),
      );
      if (kappa !== null) {
        pares.push({ evaluadores: [evaluadores[a], evaluadores[b]], documentosComunes: comunes.length, kappa });
      }
    }
  }
  const kappaPromedio = promedio(pares.map((p) => p.kappa));

  const clavesRegistradas = new Set(registros.map((r) => claveConsulta(r.consulta, r.indice)));
  const consultasConJuicios = filas.length;
  const cobertura = promedio(filas.map((f) => f.cobertura));

  return {
    consultasRegistradas: clavesRegistradas.size,
    busquedasRegistradas: registros.length,
    consultasConJuicios,
    consultasSinJuicios: Math.max(0, clavesRegistradas.size - new Set(filas.map((f) => f.clave)).size),
    evaluadores: evaluadores.length,
    juicios: juicios.length,
    juiciosPorConsultaMedia: consultasConJuicios ? juicios.length / consultasConJuicios : 0,
    juiciosPorEvaluador: porEvaluador,
    juiciosPorIndice: porIndice,
    distribucion,
    porcentajeRelevantes: juicios.length ? relevantes / juicios.length : null,
    coberturaEnK: cobertura,
    kappaPromedio,
    kappaLectura: interpretarKappa(kappaPromedio),
    paresKappa: pares,
    primerJuicio: primer,
    ultimoJuicio: ultimo,
    filtros,
    k,
  };
}

// ------------------------------------------------- Persistencia para el documento

/**
 * Valores de una fila o de un agregado por evaluador, con nombres estables.
 *
 * No se usan las claves `P@k`, `MAP`, `nDCG@k`, `MRR` ni `Recall@k` en los
 * agregados a propósito: `documento/scripts/tablas.py` recorre el JSON y
 * promedia *todos* los números cuyas claves parezcan métricas. Si se repitieran
 * allí, la tabla del informe mezclaría el promedio global con las métricas de
 * cada consulta. El `global` de este archivo tiene una sola ocurrencia de cada
 * una y es el que alimenta esa tabla.
 */
function metricasDe(fila, k) {
  return {
    precision: fila[`P@${k}`] ?? null,
    precision_promedio: fila.AP ?? fila.MAP ?? null,
    ndcg: fila[`nDCG@${k}`] ?? null,
    rango_reciproco: fila.RR ?? fila.MRR ?? null,
    recall: fila[`Recall@${k}`] ?? null,
  };
}

/** Agrega las filas por consulta en promedios por índice. */
export function agregadosPorIndice(porConsulta, k) {
  const grupos = new Map();
  for (const fila of porConsulta) {
    const indice = String(fila.indice ?? "");
    if (!grupos.has(indice)) grupos.set(indice, []);
    grupos.get(indice).push(fila);
  }
  return [...grupos]
    .map(([indice, filas]) => ({
      indice,
      consultas: filas.length,
      juzgados: filas.reduce((suma, f) => suma + (Number(f.juzgados) || 0), 0),
      relevantes: filas.reduce((suma, f) => suma + (Number(f.relevantesConocidos) || 0), 0),
      precision: promedio(filas.map((f) => f[`P@${k}`])),
      precision_promedio: promedio(filas.map((f) => f.AP)),
      ndcg: promedio(filas.map((f) => f[`nDCG@${k}`])),
      rango_reciproco: promedio(filas.map((f) => f.RR)),
      recall: promedio(filas.map((f) => f[`Recall@${k}`])),
    }))
    .sort((a, b) => a.indice.localeCompare(b.indice, "es"));
}

/**
 * Construye el objeto estable de `salida/metricas.json` a partir del informe
 * de `generarMetricas()`. Conserva el informe completo en la API (`/api/metricas`)
 * y aplana aquí lo que necesita el documento:
 *
 * - `metricas_globales`: una sola vez P@k, MAP, nDCG@k, MRR, Recall@k y κ.
 * - `agregados.por_consulta`, `por_indice`, `por_evaluador` y `curva_precision`.
 * - `uso`: estadísticas de participación y acuerdo.
 *
 * `documento/scripts/tablas.py` promedia los números del archivo cuyas claves
 * se parezcan a una métrica. Su detector convierte `P@10` en `p@k` antes de
 * comparar, así que solo recoge las métricas P@k/nDCG@k/Recall@k cuando están
 * dentro de una clave que contenga "metrica" (por eso el bloque se llama
 * `metricas_globales`). Los agregados usan nombres descriptivos y no repiten
 * `P@k`/`MAP`/etc. para que no se mezclen con los globales.
 */
export function exportarMetricas(informe) {
  const k = informe.k;
  const uso = informe.uso ?? {};
  const pares = (uso.paresKappa ?? []).map((p) => ({
    evaluadores: p.evaluadores,
    documentos_comunes: p.documentosComunes,
    valor: p.kappa,
  }));

  return {
    version: 1,
    generado: informe.generado,
    k,
    filtros: informe.filtros ?? { evaluador: null, indice: null, consulta: null },
    metricas_globales: {
      ...informe.global,
      kappa: uso.kappaPromedio ?? null,
    },
    agregados: {
      por_consulta: (informe.porConsulta ?? []).map((f) => ({
        clave: f.clave,
        consulta: f.consulta,
        indice: f.indice,
        fuente: f.fuente,
        evaluadores: f.evaluadores,
        juzgados: f.juzgados,
        juzgados_en_k: f.juzgadosEnK,
        relevantes: f.relevantesConocidos,
        resultados: f.resultados,
        cobertura: f.cobertura,
        ...metricasDe(f, k),
      })),
      por_indice: agregadosPorIndice(informe.porConsulta ?? [], k),
      por_evaluador: (informe.porEvaluador ?? []).map((e) => ({
        evaluador: e.evaluador,
        consultas: e.consultas,
        juicios: e.juicios,
        ...metricasDe(e, k),
      })),
      curva_precision: informe.curvaPrecision ?? [],
    },
    uso: {
      consultasRegistradas: uso.consultasRegistradas ?? 0,
      busquedasRegistradas: uso.busquedasRegistradas ?? 0,
      consultasConJuicios: uso.consultasConJuicios ?? 0,
      consultasSinJuicios: uso.consultasSinJuicios ?? 0,
      evaluadores: uso.evaluadores ?? 0,
      juicios: uso.juicios ?? 0,
      juiciosPorConsultaMedia: uso.juiciosPorConsultaMedia ?? 0,
      juiciosPorEvaluador: Object.entries(uso.juiciosPorEvaluador ?? {}).map(([evaluador, juicios]) => ({
        evaluador,
        juicios,
      })),
      juiciosPorIndice: Object.entries(uso.juiciosPorIndice ?? {}).map(([indice, juicios]) => ({ indice, juicios })),
      distribucion: [0, 1, 2].map((relevancia) => ({
        relevancia,
        juicios: uso.distribucion?.[relevancia] ?? 0,
      })),
      porcentajeRelevantes: uso.porcentajeRelevantes ?? null,
      coberturaEnK: uso.coberturaEnK ?? null,
      acuerdo: {
        valor: uso.kappaPromedio ?? null,
        lectura: uso.kappaLectura ?? "no definido",
        pares,
      },
      primerJuicio: uso.primerJuicio ?? null,
      ultimoJuicio: uso.ultimoJuicio ?? null,
    },
    sinJuicios: informe.sinJuicios ?? [],
    avisos: informe.avisos ?? [],
  };
}

/** Escribe `salida/metricas.json` de forma atómica (temporal + rename). */
export async function guardarMetricas(ruta, informe) {
  const datos = exportarMetricas(informe);
  await mkdir(dirname(ruta), { recursive: true });
  const temporal = `${ruta}.${process.pid}-${randomUUID()}.tmp`;
  try {
    await writeFile(temporal, JSON.stringify(datos, null, 2) + "\n", "utf-8");
    await rename(temporal, ruta);
  } catch (err) {
    await unlink(temporal).catch(() => {});
    throw err;
  }
  return datos;
}
