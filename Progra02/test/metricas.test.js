// Pruebas del cálculo de métricas con datos SINTÉTICOS (no son resultados del
// proyecto). Solo verifican las fórmulas y el ensamblado del informe.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  agregadosPorIndice,
  consolidar,
  estadisticasUso,
  exportarMetricas,
  generarMetricas,
  guardarMetricas,
  interpretarKappa,
  kappaCohen,
  leerRegistros,
  ndcgEnK,
  precisionEnK,
  precisionPromedio,
  promedio,
  rangoReciproco,
  rankingDesdeJuicios,
  recallEnK,
  reconstruirRanking,
} from "../src/metricas.js";

const cerca = (real, esperado, epsilon = 1e-9) =>
  assert.ok(Math.abs(real - esperado) < epsilon, `${real} ≈ ${esperado}`);

// --------------------------------------------------------------- Fórmulas

test("P@k: lo que no se devolvió cuenta como fallo", () => {
  assert.equal(precisionEnK([true, false, true, true], 3), 2 / 3);
  cerca(precisionEnK([true, false, true, true], 10), 0.3);
  assert.throws(() => precisionEnK([true], 0), RangeError);
});

test("Recall@k relativo al pool de relevantes conocidos", () => {
  assert.equal(recallEnK([true, false, true], 3, 4), 0.5);
  assert.equal(recallEnK([true], 3, 0), null);
});

test("AP: relevantes en 1 y 3 de 2 conocidos", () => {
  cerca(precisionPromedio([true, false, true, false], 2), 5 / 6);
  // un relevante conocido que nunca se recuperó penaliza
  cerca(precisionPromedio([true, false], 2), 0.5);
  assert.equal(precisionPromedio([false], 0), null);
});

test("MRR: posición del primer relevante", () => {
  cerca(rangoReciproco([false, false, true]), 1 / 3);
  assert.equal(rangoReciproco([false, false]), 0);
});

test("nDCG: ejemplo de Wikipedia y ranking ideal", () => {
  const ganancias = [3, 2, 3, 0, 1, 2];
  cerca(ndcgEnK(ganancias, 6, ganancias), 0.961, 1e-3);
  cerca(ndcgEnK([2, 1, 0], 3, [0, 1, 2]), 1);
  assert.equal(ndcgEnK([0, 0], 2, [0, 0]), null);
});

test("promedio ignora los null", () => {
  assert.equal(promedio([1, null, 0]), 0.5);
  assert.equal(promedio([null, undefined]), null);
});

test("κ de Cohen y lectura de Landis y Koch", () => {
  const a = [true, true, false, false, true, false];
  const b = [true, true, false, false, false, false];
  cerca(kappaCohen(a, b), 2 / 3);
  assert.equal(kappaCohen([true, true], [true, true]), null); // acuerdo esperado 1
  assert.equal(kappaCohen([], []), null);
  assert.equal(interpretarKappa(0.5), "moderado");
  assert.equal(interpretarKappa(0.61), "sustancial");
  assert.equal(interpretarKappa(null), "no definido");
});

// -------------------------------------------------------- Datos y ranking

test("consolidar entre evaluadores: mayoría y ganancia promedio", () => {
  const juicios = [
    { consulta: "q", indice: "rit_avanzado", doc_id: "a", evaluador: "e1", relevante: 2 },
    { consulta: "Q", indice: "rit_avanzado", doc_id: "a", evaluador: "e2", relevante: 0 },
    { consulta: "q", indice: "rit_avanzado", doc_id: "b", evaluador: "e1", relevante: 0 },
  ];
  const grupo = consolidar(juicios).values().next().value;
  assert.equal(grupo.docs.get("a").ganancia, 1);
  assert.equal(grupo.docs.get("a").relevante, true); // empate 1-1 cuenta como relevante
  assert.equal(grupo.docs.get("a").nEvaluadores, 2);
  assert.equal(grupo.docs.get("b").relevante, false);
  // consulta con distinta capitalización cae en el mismo grupo
  assert.equal(consolidar(juicios).size, 1);
});

test("reconstruir el ranking con la última búsqueda de cada página", () => {
  const registros = [
    { fecha: "2026-11-20T10:00:00Z", consulta: "gatos", indice: "rit_avanzado", pagina: 1, resultados: ["viejo1", "viejo2"] },
    { fecha: "2026-11-20T10:05:00Z", consulta: "Gatos", indice: "rit_avanzado", pagina: 1, resultados: ["a", "c"] },
    { fecha: "2026-11-20T10:06:00Z", consulta: "gatos", indice: "rit_avanzado", pagina: 2, resultados: ["d", "e"] },
    { fecha: "2026-11-20T09:00:00Z", consulta: "gatos", indice: "rit_estandar", pagina: 1, resultados: ["otro"] },
  ];
  const r = reconstruirRanking(registros, "gatos", "rit_avanzado");
  assert.deepEqual(r.ids, ["a", "c", "d", "e"]);
  assert.equal(r.fuente, "registro");
  assert.equal(reconstruirRanking(registros, "perros", "rit_avanzado"), null);
});

test("sin registro, el ranking de respaldo usa el rango del juicio", () => {
  const docs = new Map([
    ["x", { doc_id: "x", rango: 2 }],
    ["y", { doc_id: "y", rango: 4 }],
  ]);
  const r = rankingDesdeJuicios(docs, 4);
  assert.deepEqual(r.ids, [null, "x", null, "y"]);
  assert.equal(r.fuente, "juicios");
});

test("leerRegistros tolera líneas rotas y archivo ausente", async () => {
  const dir = mkdtempSync(join(tmpdir(), "reg-"));
  const ruta = join(dir, "consultas.jsonl");
  writeFileSync(ruta, `${JSON.stringify({ consulta: "a", indice: "i", resultados: ["1"] })}\nno es json\n\n`);
  const { registros, avisos } = await leerRegistros(ruta);
  assert.equal(registros.length, 1);
  assert.equal(avisos.length, 1);
  assert.deepEqual((await leerRegistros(join(dir, "no-existe"))).registros, []);
});

// ------------------------------------------------------------ Informe

function datosSinteticos() {
  const registros = [
    {
      fecha: "2026-11-20T10:00:00.000Z",
      consulta: "gatos",
      indice: "rit_avanzado",
      pagina: 1,
      total: 4,
      resultados: ["a", "b", "c", "d"],
    },
    {
      fecha: "2026-11-20T10:10:00.000Z",
      consulta: "perros",
      indice: "rit_avanzado",
      pagina: 1,
      total: 2,
      resultados: ["h", "i"],
    },
  ];
  const juicios = [
    { consulta: "gatos", indice: "rit_avanzado", doc_id: "a", evaluador: "e1", relevante: 1, timestamp: "2026-11-20T10:01:00Z" },
    { consulta: "gatos", indice: "rit_avanzado", doc_id: "b", evaluador: "e1", relevante: 0, timestamp: "2026-11-20T10:02:00Z" },
    { consulta: "gatos", indice: "rit_avanzado", doc_id: "c", evaluador: "e1", relevante: 2, timestamp: "2026-11-20T10:03:00Z" },
  ];
  return { registros, juicios };
}

test("informe de una consulta con valores conocidos", () => {
  const { registros, juicios } = datosSinteticos();
  const informe = generarMetricas({ juicios, registros, k: 4 });
  assert.equal(informe.porConsulta.length, 1);
  const fila = informe.porConsulta[0];
  assert.equal(fila.consulta, "gatos");
  assert.equal(fila.fuente, "registro");
  assert.equal(fila[`P@4`], 0.5); // a y c relevantes de 4
  cerca(fila.AP, 5 / 6); // (1/1 + 2/3) / 2 relevantes
  assert.equal(fila.RR, 1);
  assert.equal(fila[`Recall@4`], 1);
  cerca(fila[`nDCG@4`], 2 / (2 + 1 / Math.log2(3)));
  assert.equal(informe.global["P@4"], 0.5);
  cerca(informe.global.MAP, 5 / 6);
  cerca(informe.global["nDCG@4"], 2 / (2 + 1 / Math.log2(3)));
  assert.equal(informe.global.MRR, 1);
  assert.equal(informe.global["Recall@4"], 1);
  assert.deepEqual(Object.keys(informe.global), ["P@4", "MAP", "nDCG@4", "MRR", "Recall@4"]);
  // "perros" no tiene juicios: se informa como pendiente, no se evalúa
  assert.equal(informe.sinJuicios.length, 1);
  assert.equal(informe.sinJuicios[0].consulta, "perros");
  assert.equal(informe.uso.consultasConJuicios, 1);
  assert.equal(informe.uso.consultasRegistradas, 2);
  assert.equal(informe.uso.juicios, 3);
});

test("sin juicios el informe queda vacío y sin métricas inventadas", () => {
  const { registros } = datosSinteticos();
  const informe = generarMetricas({ juicios: [], registros, k: 4 });
  assert.deepEqual(informe.porConsulta, []);
  assert.deepEqual(informe.porEvaluador, []);
  assert.deepEqual(informe.global, {
    "P@4": null,
    MAP: null,
    "nDCG@4": null,
    MRR: null,
    "Recall@4": null,
  });
  assert.equal(informe.uso.juicios, 0);
  assert.ok(informe.avisos.length >= 1);
});

test("filtros por evaluador, índice y consulta", () => {
  const { registros, juicios } = datosSinteticos();
  const otro = [
    ...juicios,
    { consulta: "gatos", indice: "rit_avanzado", doc_id: "a", evaluador: "e2", relevante: 0 },
  ];
  const soloE2 = generarMetricas({ juicios: otro, registros, k: 4, evaluador: "e2" });
  assert.equal(soloE2.porConsulta.length, 1);
  assert.equal(soloE2.porConsulta[0].evaluadores.join(","), "e2");
  assert.equal(soloE2.porConsulta[0][`P@4`], 0); // e2 no marcó ningún relevante
  assert.equal(soloE2.uso.kappaPromedio, null);

  const otroIndice = generarMetricas({ juicios, registros, k: 4, indice: "rit_estandar" });
  assert.deepEqual(otroIndice.porConsulta, []);

  const otraConsulta = generarMetricas({ juicios, registros, k: 4, consulta: "GATOS" });
  assert.equal(otraConsulta.porConsulta.length, 1);
});

test("κ promedio y cobertura con dos evaluadores", () => {
  const ids = ["a", "b", "c", "d", "e", "f"];
  const registros = [
    { fecha: "2026-11-20T10:00:00Z", consulta: "q", indice: "rit_avanzado", pagina: 1, total: 6, resultados: ids },
  ];
  const notas = {
    e1: [1, 1, 0, 0, 1, 0],
    e2: [1, 1, 0, 0, 0, 0],
  };
  const juicios = Object.entries(notas).flatMap(([evaluador, valores]) =>
    ids.map((doc_id, i) => ({
      consulta: "q",
      indice: "rit_avanzado",
      doc_id,
      evaluador,
      relevante: valores[i],
      timestamp: `2026-11-20T10:0${i}:00Z`,
    })),
  );
  const informe = generarMetricas({ juicios, registros, k: 6 });
  cerca(informe.global[`P@6`], 0.5);
  cerca(informe.global.MAP, (1 + 2 / 2 + 3 / 5) / 3);
  assert.equal(informe.uso.evaluadores, 2);
  assert.equal(informe.uso.paresKappa.length, 1);
  assert.equal(informe.uso.paresKappa[0].documentosComunes, 6);
  cerca(informe.uso.kappaPromedio, 2 / 3);
  assert.equal(informe.uso.coberturaEnK, 1);
  assert.equal(informe.uso.distribucion[2], 0);

  const uso = estadisticasUso(juicios, registros, informe.porConsulta, 6);
  cerca(uso.kappaPromedio, 2 / 3);
});

test("sin registro se avisa y se usa el rango guardado", () => {
  const juicios = [
    { consulta: "sola", indice: "rit_avanzado", doc_id: "x", evaluador: "e1", relevante: 1, rango: 1 },
    { consulta: "sola", indice: "rit_avanzado", doc_id: "y", evaluador: "e1", relevante: 0, rango: 2 },
  ];
  const informe = generarMetricas({ juicios, registros: [], k: 2 });
  assert.equal(informe.porConsulta[0].fuente, "juicios");
  assert.equal(informe.porConsulta[0][`P@2`], 0.5);
  assert.ok(informe.avisos.some((a) => /no hay registro/i.test(a)));
});

test("exportación para el documento: globales únicos y agregados descriptivos", () => {
  const { registros, juicios } = datosSinteticos();
  const informe = generarMetricas({ juicios, registros, k: 4 });
  const exportado = exportarMetricas(informe);

  assert.equal(exportado.version, 1);
  assert.equal(exportado.metricas_globales["P@4"], 0.5);
  assert.equal(exportado.metricas_globales.MAP, informe.global.MAP);
  assert.equal(exportado.metricas_globales.kappa, null);

  const fila = exportado.agregados.por_consulta[0];
  assert.equal(fila.consulta, "gatos");
  assert.equal(fila.precision, 0.5);
  assert.ok(Math.abs(fila.precision_promedio - 5 / 6) < 1e-12);
  // ninguna clave de agregado debe parecerse a una métrica: tablas.py las sumaría
  for (const clave of Object.keys(fila)) {
    assert.doesNotMatch(clave, /^(p@\d+|map|mrr|ndcg@\d+|recall@\d+|kappa)$/i);
  }

  assert.equal(exportado.agregados.por_indice.length, 1);
  const porIndice = exportado.agregados.por_indice[0];
  assert.equal(porIndice.indice, "rit_avanzado");
  assert.equal(porIndice.consultas, 1);
  assert.equal(porIndice.juzgados, 3);
  assert.equal(porIndice.relevantes, 2);
  assert.equal(porIndice.precision, 0.5);
  assert.ok(Math.abs(porIndice.precision_promedio - 5 / 6) < 1e-12);
  assert.equal(porIndice.ndcg, fila.ndcg);
  assert.equal(porIndice.rango_reciproco, 1);
  assert.equal(porIndice.recall, 1);
  assert.equal(exportado.uso.juicios, 3);
  assert.equal(exportado.uso.distribucion[2].juicios, 1);
  assert.equal(exportado.uso.acuerdo.lectura, "no definido");
  assert.deepEqual(agregadosPorIndice([], 10), []);
});

test("guardarMetricas escribe el archivo de forma atómica", async () => {
  const { registros, juicios } = datosSinteticos();
  const informe = generarMetricas({ juicios, registros, k: 4 });
  const dir = mkdtempSync(join(tmpdir(), "metricas-"));
  const ruta = join(dir, "salida", "metricas.json");

  await guardarMetricas(ruta, informe);
  const primero = JSON.parse(readFileSync(ruta, "utf-8"));
  assert.equal(primero.metricas_globales["P@4"], 0.5);
  assert.ok(primero.generado);

  const otro = generarMetricas({ juicios: [], registros: [], k: 4 });
  await guardarMetricas(ruta, otro);
  const segundo = JSON.parse(readFileSync(ruta, "utf-8"));
  assert.equal(segundo.metricas_globales["P@4"], null);
  assert.deepEqual(segundo.agregados.por_consulta, []);
});
