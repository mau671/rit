// similitud.js
// Similitud TF/IDF del modelo vectorial con la misma fórmula del propio.
//
// Elasticsearch eliminó la similitud clásica (TF/IDF) en la versión 7 y usa
// BM25 por defecto, así que se define una similitud "scripted" en Painless.
//
// El propio calcula (spimi.py y cli.py):
//   peso documento  w(t,d) = tf_w(tf_td) * idf(t)
//   peso consulta   w(t,q) = tf_w(tf_tq) * idf(t)
//   similitud(q,d)  = Σ w(t,q) * w(t,d) / (|q| * |d|)
//
// Lucene suma los puntajes de cada término. Con weight_script se calcula la
// parte de la consulta (el boost lleva tf_w de la consulta) y con script la
// del documento, de modo que cada término aporta w(t,q) * w(t,d).
// La división entre |q| y |d| se hace al buscar (ver buscador.js), porque
// |d| depende de todos los términos del documento y la similitud de Lucene
// solo ve un término a la vez. |d| se precalcula y se guarda en "norma".

/** Expresión Painless del peso TF según esquema_tf. */
export function expresionTf(esquema, variable = "doc.freq") {
  switch (esquema) {
    case "logaritmico":
    case "sublineal":
      return `(${variable} > 0 ? 1.0 + Math.log(${variable}) : 0.0)`;
    case "binario":
      return `(${variable} > 0 ? 1.0 : 0.0)`;
    case "natural":
      return `((double) ${variable})`;
    default:
      throw new Error(`esquema_tf desconocido: ${esquema}`);
  }
}

/** Expresión Painless del IDF según esquema_idf. */
export function expresionIdf(esquema) {
  const n = "((double) field.docCount)";
  const df = "((double) term.docFreq)";
  switch (esquema) {
    case "estandar":
      return `(${df} > 0 ? Math.log(${n} / ${df} + 1.0) : 0.0)`;
    case "probabilistico":
      return `(${df} > 0 ? Math.max(0.0, Math.log((${n} - ${df}) / ${df} + 1.0)) : 0.0)`;
    case "ninguno":
      return "1.0";
    default:
      throw new Error(`esquema_idf desconocido: ${esquema}`);
  }
}

/** Definición de la similitud para settings.similarity. */
export function construirSimilitud(modelo) {
  const idf = expresionIdf(modelo.esquema_idf);
  const tf = expresionTf(modelo.esquema_tf);
  return {
    type: "scripted",
    weight_script: { source: `double idf = ${idf}; return query.boost * idf;` },
    script: { source: `double idf = ${idf}; return weight * ${tf} * idf;` },
  };
}

// Mismas fórmulas en JavaScript, para calcular normas y pesos de consulta.

export function pesoTf(tf, esquema) {
  if (tf <= 0) return 0;
  if (esquema === "logaritmico" || esquema === "sublineal") return 1 + Math.log(tf);
  if (esquema === "binario") return 1;
  return tf;
}

export function idf(nDocs, df, esquema) {
  if (df <= 0 || nDocs <= 0) return 0;
  if (esquema === "estandar") return Math.log(nDocs / df + 1);
  if (esquema === "probabilistico") return Math.max(0, Math.log((nDocs - df) / df + 1));
  return 1;
}
