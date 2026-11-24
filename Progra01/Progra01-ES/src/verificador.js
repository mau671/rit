// verificador.js
// Comprueba que Elasticsearch normaliza igual que el indexador propio.
//
// Lee casos/esperados_<politica>.json (generado con herramientas/generar_casos.py
// usando el Normalizador del propio), envía cada texto a _analyze con el
// analyzer del campo correspondiente y compara token por token. También
// compara el idioma detectado por idioma.js con el que detectó el propio.
//
// Usa un índice temporal "<indice>_verificacion" para no tocar el índice real.

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";

import { crearCliente, verificarConexion, recrearIndice } from "./elastic.js";
import { construirIndice, campoDeIdioma } from "./analizadores.js";
import { detectarIdioma } from "./idioma.js";

/** Primera posición donde difieren dos listas, con contexto. */
function primeraDiferencia(esperados, obtenidos) {
  const n = Math.max(esperados.length, obtenidos.length);
  for (let i = 0; i < n; i++) {
    if (esperados[i] !== obtenidos[i]) {
      return {
        posicion: i,
        esperado: esperados.slice(Math.max(0, i - 3), i + 4),
        obtenido: obtenidos.slice(Math.max(0, i - 3), i + 4),
      };
    }
  }
  return null;
}

export async function verificar(config, rutaCasos) {
  const politica = config.general.nombre_politica;
  const ruta = rutaCasos ?? join(config.rutaArchivo, "..", "..", "casos", `esperados_${politica}.json`);
  if (!existsSync(ruta)) {
    throw new Error(
      `No existe ${ruta}.\nGeneralo desde la carpeta del propio con:\n` +
        `  uv run python Progra01-ES/herramientas/generar_casos.py --politica ${politica}`,
    );
  }
  const casos = JSON.parse(readFileSync(ruta, "utf-8"));

  const cliente = crearCliente(config);
  await verificarConexion(cliente);
  const indice = `${config.elasticsearch.indice}_verificacion`;
  await recrearIndice(cliente, indice, construirIndice(config));

  const resultados = [];
  try {
    for (const caso of casos) {
      const idiomaJs = detectarIdioma(caso.texto, config.normalizacion);
      // Se analiza con el idioma del propio, para separar los dos tipos de error
      const r = await cliente.indices.analyze({ index: indice, field: campoDeIdioma(caso.idioma), text: caso.texto });
      const obtenidos = r.tokens.map((t) => t.token);
      const diferencia = primeraDiferencia(caso.tokens, obtenidos);
      resultados.push({
        id: caso.id,
        origen: caso.origen,
        idioma_propio: caso.idioma,
        idioma_elasticsearch: idiomaJs,
        idioma_coincide: idiomaJs === caso.idioma,
        tokens_esperados: caso.tokens.length,
        tokens_obtenidos: obtenidos.length,
        tokens_coinciden: diferencia === null,
        diferencia,
      });
    }
  } finally {
    await cliente.indices.delete({ index: indice }).catch(() => {});
  }

  const okTokens = resultados.filter((r) => r.tokens_coinciden).length;
  const okIdioma = resultados.filter((r) => r.idioma_coincide).length;
  console.log(`==> Verificación de la política "${politica}" (${resultados.length} casos)`);
  console.log(`    Tokens idénticos: ${okTokens}/${resultados.length}`);
  console.log(`    Idioma idéntico:  ${okIdioma}/${resultados.length}`);
  for (const r of resultados.filter((x) => !x.tokens_coinciden || !x.idioma_coincide)) {
    console.log(`\n    ✗ ${r.id} (${r.origen})`);
    if (!r.idioma_coincide) console.log(`      idioma: propio=${r.idioma_propio} elasticsearch=${r.idioma_elasticsearch}`);
    if (r.diferencia) {
      console.log(`      primera diferencia en el token ${r.diferencia.posicion}`);
      console.log(`      propio:        ${JSON.stringify(r.diferencia.esperado)}`);
      console.log(`      elasticsearch: ${JSON.stringify(r.diferencia.obtenido)}`);
    }
  }

  mkdirSync(config.rutas.directorio_salida, { recursive: true });
  const rutaSalida = join(config.rutas.directorio_salida, `verificacion_${politica}.json`);
  writeFileSync(rutaSalida, JSON.stringify(resultados, null, 2), "utf-8");
  console.log(`\n    Detalle guardado en ${rutaSalida}`);
  return resultados;
}
