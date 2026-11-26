// buscador.js
// Consulta de prueba con similitud coseno, equivalente al comando "buscar"
// del propio (cli.py). Sirve para validar el índice y como punto de partida
// para el Proyecto 3.
//
//   similitud(q,d) = Σ w(t,q) * w(t,d) / (|q| * |d|)
//
// - w(t,q) * w(t,d): lo calcula la similitud scripted de cada campo. El boost
//   de cada término lleva tf_w de la consulta.
// - |d|: está guardada en el campo "norma" (fase 3 del indexador).
// - |q|: se calcula aquí con el df de cada término de la consulta. Como cada
//   documento tiene un solo campo de texto, se calcula una |q| por campo.

import { crearCliente, verificarConexion } from "./elastic.js";
import { CAMPO_ES, CAMPO_EN } from "./analizadores.js";
import { pesoTf, idf } from "./similitud.js";

/** Términos de la consulta analizados por cada campo, con tf y df. */
async function analizarConsulta(cliente, indice, consulta, modelo) {
  const campos = [CAMPO_ES, CAMPO_EN];
  const r = await cliente.mtermvectors({
    index: indice,
    docs: campos.map((campo) => ({
      doc: { [campo]: consulta },
      fields: [campo],
      term_statistics: true,
      field_statistics: true,
      positions: false,
      offsets: false,
    })),
  });

  const resultado = {};
  r.docs.forEach((respuesta, i) => {
    const campo = campos[i];
    const tv = respuesta.term_vectors?.[campo];
    const terminos = [];
    let sumaCuadrados = 0;
    if (tv) {
      const nDocs = tv.field_statistics?.doc_count ?? 0;
      for (const [termino, info] of Object.entries(tv.terms)) {
        const tfw = pesoTf(info.term_freq, modelo.esquema_tf);
        const df = info.doc_freq ?? 0;
        if (df === 0) continue; // el término no existe en este campo
        const w = tfw * idf(nDocs, df, modelo.esquema_idf);
        sumaCuadrados += w * w;
        terminos.push({ termino, tf: info.term_freq, df, boost: tfw });
      }
    }
    resultado[campo] = { terminos, norma: Math.sqrt(sumaCuadrados) };
  });
  return resultado;
}

export async function buscar(config, consulta, { top = 10 } = {}) {
  const indice = config.elasticsearch.indice;
  const cliente = crearCliente(config);
  await verificarConexion(cliente);

  const analisis = await analizarConsulta(cliente, indice, consulta, config.modelo);
  for (const [campo, { terminos }] of Object.entries(analisis)) {
    console.log(`==> ${campo}: ${terminos.map((t) => `${t.termino}(tf=${t.tf}, df=${t.df})`).join(", ") || "(sin términos)"}`);
  }

  const clausulas = Object.entries(analisis).flatMap(([campo, { terminos }]) =>
    terminos.map((t) => ({ term: { [campo]: { value: t.termino, boost: t.boost } } })),
  );
  if (clausulas.length === 0) {
    console.log("Ninguno de los términos de la consulta se encuentra en el índice.");
    return [];
  }

  const r = await cliente.search({
    index: indice,
    size: top,
    track_total_hits: true,
    _source: ["doc_id", "url", "dominio", "idioma"],
    query: {
      script_score: {
        query: { bool: { should: clausulas } },
        script: {
          source: `
            double nd = (doc['norma'].size() == 0 || doc['norma'].value == 0) ? 1.0 : doc['norma'].value;
            double nq = doc['idioma'].value == 'en' ? params.nq_en : params.nq_es;
            if (nq == 0) { nq = 1.0; }
            return _score / (nq * nd);
          `,
          params: { nq_es: analisis[CAMPO_ES].norma, nq_en: analisis[CAMPO_EN].norma },
        },
      },
    },
  });

  const resultados = r.hits.hits.map((h, i) => ({
    rango: i + 1,
    similitud: Math.round(h._score * 10000) / 10000,
    ...h._source,
  }));

  console.log(`\nResultados (${r.hits.total.value} coincidencias, top ${resultados.length}):`);
  console.table(resultados);
  return resultados;
}
