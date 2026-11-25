// busqueda.js
// Motor de búsqueda sobre los índices de Progra01-ES.
//
// Ranking: modelo vectorial con similitud coseno, la misma fórmula del índice
//   similitud(q,d) = Σ w(t,q)·w(t,d) / (|q|·|d|)
//   w = (1 + ln tf) · ln(N/df + 1)
// - w(t,q)·w(t,d) lo calcula la similitud "tfidf_propio" del índice; el boost
//   de cada término lleva el peso TF de la consulta.
// - |d| está guardada en el campo "norma" de cada documento.
// - |q| se calcula aquí, una por campo de idioma.
//
// Búsqueda avanzada (ver consulta.js): frases, obligatorios, exclusiones y
// filtros por dominio e idioma. Los filtros de dominio e idioma van en
// post_filter para que los contadores de la barra lateral muestren todas las
// opciones disponibles para la consulta.

import { interpretarConsulta, textoParaPuntaje } from "./consulta.js";
import { armarFragmentos, rutaTexto, tituloDesdeUrl } from "./fragmentos.js";

export const CAMPOS = { es: "contenido_es", en: "contenido_en" };

function pesoTf(tf, esquema) {
  if (tf <= 0) return 0;
  if (esquema === "logaritmico" || esquema === "sublineal") return 1 + Math.log(tf);
  if (esquema === "binario") return 1;
  return tf;
}

function idf(n, df, esquema) {
  if (df <= 0 || n <= 0) return 0;
  if (esquema === "estandar") return Math.log(n / df + 1);
  if (esquema === "probabilistico") return Math.max(0, Math.log((n - df) / df + 1));
  return 1;
}

/**
 * Pasa la consulta por el analyzer de cada campo y obtiene tf (en la
 * consulta), df y la norma |q|. Usa _mtermvectors con un documento
 * artificial: normaliza igual que al indexar y trae las estadísticas.
 */
async function analizarConsulta(cliente, indice, texto, modelo) {
  const salida = {};
  for (const campo of Object.values(CAMPOS)) salida[campo] = { terminos: [], norma: 0 };
  if (!texto.trim()) return salida;

  const campos = Object.values(CAMPOS);
  const r = await cliente.mtermvectors({
    index: indice,
    docs: campos.map((campo) => ({
      doc: { [campo]: texto },
      fields: [campo],
      term_statistics: true,
      field_statistics: true,
      positions: false,
      offsets: false,
    })),
  });

  r.docs.forEach((respuesta, i) => {
    const campo = campos[i];
    const tv = respuesta.term_vectors?.[campo];
    if (!tv) return;
    const n = tv.field_statistics?.doc_count ?? 0;
    let suma = 0;
    for (const [termino, info] of Object.entries(tv.terms)) {
      const df = info.doc_freq ?? 0;
      if (df === 0) continue; // no aparece en ningún documento de este idioma
      const tfw = pesoTf(info.term_freq, modelo.esquema_tf);
      const w = tfw * idf(n, df, modelo.esquema_idf);
      suma += w * w;
      salida[campo].terminos.push({ termino, tf: info.term_freq, df, peso: Math.round(w * 1000) / 1000, boost: tfw });
    }
    salida[campo].norma = Math.sqrt(suma);
  });
  return salida;
}

/** Una cláusula que se cumple si el texto aparece en cualquiera de los dos campos. */
const enAlgunCampo = (tipo, texto) => ({
  bool: {
    should: Object.values(CAMPOS).map((campo) => ({ [tipo]: { [campo]: texto } })),
    minimum_should_match: 1,
  },
});

function construirQuery(c, analisis, { usarFrases }) {
  const should = Object.entries(analisis).flatMap(([campo, { terminos }]) =>
    terminos.map((t) => ({ term: { [campo]: { value: t.termino, boost: t.boost } } })),
  );

  const filter = c.obligatorios.map((p) => enAlgunCampo("match", p));
  for (const frase of c.frases) filter.push(enAlgunCampo(usarFrases ? "match_phrase" : "match", frase));
  const mustNot = c.excluidos.map((p) => enAlgunCampo(usarFrases ? "match_phrase" : "match", p));

  return {
    script_score: {
      query: { bool: { should, minimum_should_match: 1, filter, must_not: mustNot } },
      script: {
        source: `
          double nd = (doc['norma'].size() == 0 || doc['norma'].value == 0) ? 1.0 : doc['norma'].value;
          double nq = doc['idioma'].value == 'en' ? params.nq_en : params.nq_es;
          if (nq == 0) { nq = 1.0; }
          return _score / (nq * nd);
        `,
        params: { nq_es: analisis[CAMPOS.es].norma, nq_en: analisis[CAMPOS.en].norma },
      },
    },
  };
}

function construirPostFilter(c) {
  const filtros = [];
  if (c.dominios.length) filtros.push({ terms: { dominio: c.dominios } });
  if (c.idiomas.length) filtros.push({ terms: { idioma: c.idiomas } });
  return filtros.length ? { bool: { filter: filtros } } : undefined;
}

const esErrorDePosiciones = (err) => /position/i.test(JSON.stringify(err?.meta?.body ?? err?.message ?? ""));

/**
 * Ejecuta una búsqueda. Devuelve el objeto que entrega la API /api/buscar
 * (el formato está documentado en docs/contrato-api.md).
 */
export async function buscar(cliente, config, { q, pagina = 1, indice }) {
  const inicio = performance.now();
  const opciones = config.resultados;
  const c = interpretarConsulta(q);
  const avisos = [];

  const base = {
    consulta: c.texto,
    indice,
    pagina,
    por_pagina: opciones.por_pagina,
    interpretacion: {
      libres: c.libres,
      obligatorios: c.obligatorios,
      frases: c.frases,
      excluidos: c.excluidos,
      dominios: c.dominios,
      idiomas: c.idiomas,
    },
  };

  const analisis = await analizarConsulta(cliente, indice, textoParaPuntaje(c), config.modelo);
  const terminosEs = analisis[CAMPOS.es].terminos;
  const terminosEn = analisis[CAMPOS.en].terminos;
  base.terminos = { es: terminosEs, en: terminosEn };

  if (terminosEs.length === 0 && terminosEn.length === 0) {
    const sinPalabras = textoParaPuntaje(c).trim() === "";
    return {
      ...base,
      total: 0,
      paginas: 0,
      resultados: [],
      facetas: { dominio: [], idioma: [] },
      avisos: [
        sinPalabras
          ? "Escribí al menos una palabra para buscar; los filtros y exclusiones solos no ordenan resultados."
          : "Ninguna palabra de la consulta aparece en la colección. Puede que todas sean palabras vacías o que no estén en ningún documento.",
      ],
      tiempo_ms: Math.round(performance.now() - inicio),
    };
  }

  const desde = (pagina - 1) * opciones.por_pagina;
  if (desde >= opciones.maximo_resultados) throw new Error(`Solo se muestran los primeros ${opciones.maximo_resultados} resultados.`);

  const peticion = (usarFrases) => ({
    index: indice,
    from: desde,
    size: opciones.por_pagina,
    track_total_hits: true,
    _source: ["doc_id", "db_id", "url", "dominio", "idioma", "ruta_relativa", "tamano_bytes"],
    query: construirQuery(c, analisis, { usarFrases }),
    post_filter: construirPostFilter(c),
    aggs: {
      dominio: { terms: { field: "dominio", size: 15 } },
      idioma: { terms: { field: "idioma", size: 5 } },
    },
  });

  let r;
  try {
    r = await cliente.search(peticion(true));
  } catch (err) {
    const hayFrases = c.frases.length > 0 || c.excluidos.some((e) => /\s/.test(e));
    if (hayFrases && esErrorDePosiciones(err)) {
      // El índice se construyó sin posiciones (guardar_posiciones = false)
      avisos.push("Este índice no guarda la posición de las palabras, así que las frases se buscaron como palabras sueltas.");
      r = await cliente.search(peticion(false));
    } else {
      throw err;
    }
  }

  const terminosPorCampo = {
    [CAMPOS.es]: new Set(terminosEs.map((t) => t.termino)),
    [CAMPOS.en]: new Set(terminosEn.map((t) => t.termino)),
  };

  const resultados = await Promise.all(
    r.hits.hits.map(async (h, i) => {
      const fuente = h._source;
      const campo = fuente.idioma === "en" ? CAMPOS.en : CAMPOS.es;
      const extra = await armarFragmentos(cliente, {
        indice,
        campo,
        rutaArchivo: rutaTexto(config.coleccion.repositorio, fuente.ruta_relativa),
        terminos: terminosPorCampo[campo],
        opciones,
      }).catch(() => ({ fragmentos: [], coincidencias: {}, disponible: false }));

      return {
        rango: desde + i + 1,
        id: h._id,
        doc_id: fuente.doc_id,
        db_id: fuente.db_id,
        titulo: tituloDesdeUrl(fuente.url) ?? extra.primeraLinea ?? fuente.dominio,
        url: fuente.url,
        dominio: fuente.dominio,
        idioma: fuente.idioma,
        tamano_bytes: fuente.tamano_bytes,
        similitud: Math.round(h._score * 10000) / 10000,
        fragmentos: extra.fragmentos,
        coincidencias: extra.coincidencias,
        texto_disponible: extra.disponible,
      };
    }),
  );

  const total = r.hits.total.value;
  const visibles = Math.min(total, opciones.maximo_resultados);
  const facetas = {
    dominio: r.aggregations.dominio.buckets.map((b) => ({ valor: b.key, cantidad: b.doc_count })),
    idioma: r.aggregations.idioma.buckets.map((b) => ({ valor: b.key, cantidad: b.doc_count })),
  };

  return {
    ...base,
    total,
    paginas: Math.ceil(visibles / opciones.por_pagina),
    resultados,
    facetas,
    avisos,
    tiempo_ms: Math.round(performance.now() - inicio),
  };
}
