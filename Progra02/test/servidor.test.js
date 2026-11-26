// Pruebas de los endpoints de evaluación y métricas con un servidor real en un
// puerto efímero. No necesitan Elasticsearch: el cliente se reemplaza por un
// doble y los datos de juicios/consultas son SINTÉTICOS (archivos temporales).

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { cargarConfiguracion } from "../src/configuracion.js";
import { crearServidor } from "../src/servidor.js";

const dir = mkdtempSync(join(tmpdir(), "servidor-rit-"));
const rutaJuicios = join(dir, "juicios.json");
const rutaRegistro = join(dir, "consultas.jsonl");
const rutaConf = join(dir, "buscador.conf");

writeFileSync(
  rutaConf,
  `[servidor]
puerto = 0
registrar_consultas = false
archivo_registro = ${rutaRegistro}

[elasticsearch]
url = http://localhost:9200
indice_por_defecto = rit_avanzado
indices_disponibles = rit_avanzado, rit_estandar

[coleccion]
repositorio = ${dir}

[evaluacion]
archivo_juicios = ${rutaJuicios}
profundidad_metricas = 10
`,
);

const config = cargarConfiguracion(rutaConf);
const servidor = crearServidor({ config, cliente: { count: async () => ({ count: 3 }) } });
await new Promise((listo) => servidor.listen(0, "127.0.0.1", listo));
const base = `http://127.0.0.1:${servidor.address().port}`;

async function pedir(ruta, opciones) {
  const r = await fetch(base + ruta, opciones);
  const tipo = r.headers.get("content-type") ?? "";
  const cuerpo = tipo.includes("application/json") ? await r.json() : await r.text();
  return { r, cuerpo };
}

const postJson = (cuerpo) =>
  pedir("/api/evaluacion/juicios", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(cuerpo),
  });

const juicio = (extra = {}) => ({
  consulta: "gatos",
  indice: "rit_avanzado",
  doc_id: "a",
  url: "https://ejemplo.cr/a",
  titulo: "Documento A",
  evaluador: "e1",
  relevante: 2,
  rango: 1,
  ...extra,
});

test("interfaz de evaluación y métricas", async (t) => {
  await t.test("sirve las páginas y el índice falso", async () => {
    const inicio = await pedir("/");
    assert.equal(inicio.r.status, 200);
    assert.match(inicio.cuerpo, /Encuadre/);

    const evaluar = await pedir("/evaluar");
    assert.equal(evaluar.r.status, 200);
    assert.match(evaluar.cuerpo, /Evaluación de relevancia/);

    const indices = await pedir("/api/indices");
    assert.equal(indices.r.status, 200);
    assert.equal(indices.cuerpo.indices[0].documentos, 3);
    assert.equal(indices.cuerpo.indices[0].disponible, true);
  });

  await t.test("la página de métricas muestra el estado vacío", async () => {
    const { r, cuerpo } = await pedir("/metricas");
    assert.equal(r.status, 200);
    assert.match(cuerpo, /Todavía no hay juicios de relevancia/);
    assert.match(cuerpo, /Ir a|Empezar a evaluar/);

    const api = await pedir("/api/metricas?k=4");
    assert.equal(api.r.status, 200);
    assert.deepEqual(api.cuerpo.porConsulta, []);
    assert.equal(api.cuerpo.global["P@4"], null);
    assert.equal(api.cuerpo.uso.juicios, 0);

    // el endpoint persiste el informe que espera documento/scripts/tablas.py
    const archivo = JSON.parse(readFileSync(config.evaluacion.archivo_metricas, "utf-8"));
    assert.equal(archivo.version, 1);
    assert.equal(archivo.k, 4);
    assert.equal(archivo.metricas_globales["P@4"], null);
    assert.equal(archivo.metricas_globales.kappa, null);
    assert.deepEqual(archivo.agregados.por_consulta, []);
    assert.deepEqual(archivo.agregados.por_indice, []);
  });

  await t.test("guarda un juicio y no lo duplica al volver a marcarlo", async () => {
    const primero = await postJson(juicio());
    assert.equal(primero.r.status, 201);
    assert.equal(primero.cuerpo.creado, true);
    assert.equal(primero.cuerpo.juicio.relevante, 2);
    assert.ok(primero.cuerpo.juicio.timestamp);

    const segundo = await postJson(juicio({ relevante: 0, rango: 2 }));
    assert.equal(segundo.r.status, 200);
    assert.equal(segundo.cuerpo.creado, false);

    const lista = await pedir("/api/evaluacion/juicios?consulta=GATOS&indice=rit_avanzado&evaluador=e1");
    assert.equal(lista.cuerpo.total, 1);
    assert.equal(lista.cuerpo.juicios[0].relevante, 0);
    assert.equal(lista.cuerpo.juicios[0].rango, 2);

    const otro = await pedir("/api/evaluacion/juicios?evaluador=e2");
    assert.equal(otro.cuerpo.total, 0);

    // se deja en 2 para las métricas de más abajo
    await postJson(juicio({ relevante: 2 }));
  });

  await t.test("rechaza juicios inválidos con 400/415", async () => {
    assert.equal((await postJson(juicio({ relevante: 5 }))).r.status, 400);
    assert.equal((await postJson(juicio({ doc_id: "" }))).r.status, 400);
    assert.equal((await postJson(juicio({ indice: "rit_inventado" }))).r.status, 400);
    assert.equal((await postJson(juicio({ url: "javascript:alert(1)" }))).r.status, 400);

    const sinTipo = await pedir("/api/evaluacion/juicios", { method: "POST", body: "{}" });
    assert.equal(sinTipo.r.status, 415);

    const roto = await pedir("/api/evaluacion/juicios", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{no es json",
    });
    assert.equal(roto.r.status, 400);
  });

  await t.test("resumen de juicios", async () => {
    const { r, cuerpo } = await pedir("/api/evaluacion/resumen?consulta=gatos&indice=rit_avanzado&evaluador=e1");
    assert.equal(r.status, 200);
    assert.equal(cuerpo.total, 1);
    assert.deepEqual(cuerpo.por_relevante, { 0: 0, 1: 0, 2: 1 });
    assert.equal(cuerpo.consultas, 1);
  });

  await t.test("cola de consultas registradas y métricas calculadas", async () => {
    writeFileSync(
      rutaRegistro,
      [
        {
          fecha: "2026-11-20T10:00:00.000Z",
          consulta: "gatos",
          indice: "rit_avanzado",
          pagina: 1,
          total: 4,
          tiempo_ms: 12,
          resultados: ["a", "b", "c", "d"],
        },
        {
          fecha: "2026-11-20T10:05:00.000Z",
          consulta: "perros",
          indice: "rit_avanzado",
          pagina: 1,
          total: 2,
          tiempo_ms: 9,
          resultados: ["h", "i"],
        },
      ].map((linea) => JSON.stringify(linea)).join("\n") + "\n",
    );
    await postJson(juicio({ doc_id: "b", relevante: 0, rango: 2 }));
    await postJson(juicio({ doc_id: "c", relevante: 1, rango: 3 }));

    const cola = await pedir("/api/evaluacion/consultas?evaluador=e1");
    assert.equal(cola.r.status, 200);
    assert.equal(cola.cuerpo.consultas.length, 2);
    const gatos = cola.cuerpo.consultas.find((c) => c.consulta === "gatos");
    assert.equal(gatos.documentos_juzgados, 3);
    assert.equal(gatos.juzgados_por_evaluador, 3);
    assert.equal(gatos.objetivos, 4);
    assert.equal(gatos.relevantes, 2);

    const api = await pedir("/api/metricas?k=4");
    assert.equal(api.r.status, 200);
    assert.equal(api.cuerpo.k, 4);
    assert.equal(api.cuerpo.porConsulta.length, 1);
    const fila = api.cuerpo.porConsulta[0];
    assert.equal(fila.consulta, "gatos");
    assert.equal(fila.fuente, "registro");
    assert.equal(fila["P@4"], 0.5);
    assert.ok(Math.abs(fila.AP - 5 / 6) < 1e-12);
    assert.equal(fila.RR, 1);
    assert.ok(Math.abs(api.cuerpo.global.MAP - 5 / 6) < 1e-12);
    assert.equal(api.cuerpo.uso.juicios, 3);
    assert.equal(api.cuerpo.uso.consultasRegistradas, 2);
    assert.equal(api.cuerpo.uso.evaluadores, 1);
    assert.equal(api.cuerpo.sinJuicios.length, 1);
    assert.equal(api.cuerpo.sinJuicios[0].consulta, "perros");

    const html = await pedir("/metricas?k=4");
    assert.equal(html.r.status, 200);
    assert.match(html.cuerpo, /P@4/);
    assert.match(html.cuerpo, /0\.500/);
    assert.match(html.cuerpo, /gatos/);
    assert.match(html.cuerpo, /Consultas reales sin juicios/);

    const archivo = JSON.parse(readFileSync(config.evaluacion.archivo_metricas, "utf-8"));
    assert.equal(archivo.metricas_globales["P@4"], 0.5);
    assert.equal(archivo.metricas_globales.MAP, api.cuerpo.global.MAP);
    assert.equal(archivo.agregados.por_consulta.length, 1);
    assert.equal(archivo.agregados.por_consulta[0].precision, 0.5);
    assert.ok(Math.abs(archivo.agregados.por_consulta[0].precision_promedio - 5 / 6) < 1e-12);
    assert.equal(archivo.agregados.por_indice[0].indice, "rit_avanzado");
    assert.equal(archivo.uso.juicios, 3);
    assert.equal(archivo.uso.distribucion[2].juicios, 1);
    // las cifras globales no deben repetirse dentro de los agregados
    for (const clave of Object.keys(archivo.agregados.por_consulta[0])) {
      assert.doesNotMatch(clave, /^(p@\d+|map|mrr|ndcg@\d+|recall@\d+|kappa)$/i);
    }

    // una vista filtrada no debe contaminar el archivo global del documento
    const filtrada = await pedir("/api/metricas?k=4&evaluador=otra-persona");
    assert.equal(filtrada.cuerpo.porConsulta.length, 0);
    const canonico = JSON.parse(readFileSync(config.evaluacion.archivo_metricas, "utf-8"));
    assert.equal(canonico.metricas_globales["P@4"], 0.5);
    assert.equal(canonico.filtros.evaluador, null);
  });

  await t.test("borra un juicio", async () => {
    const borrar = await pedir(
      "/api/evaluacion/juicios?consulta=gatos&indice=rit_avanzado&doc_id=c&evaluador=e1",
      { method: "DELETE" },
    );
    assert.equal(borrar.r.status, 200);
    assert.equal(borrar.cuerpo.eliminado, true);
    const lista = await pedir("/api/evaluacion/juicios?consulta=gatos&evaluador=e1");
    assert.equal(lista.cuerpo.total, 2);
    const otraVez = await pedir(
      "/api/evaluacion/juicios?consulta=gatos&indice=rit_avanzado&doc_id=c&evaluador=e1",
      { method: "DELETE" },
    );
    assert.equal(otraVez.cuerpo.eliminado, false);
  });

  await t.test("métodos no permitidos y rutas fuera de public", async () => {
    const put = await pedir("/api/evaluacion/juicios", { method: "PUT" });
    assert.equal(put.r.status, 405);
    assert.equal(put.r.headers.get("allow"), "GET, POST, DELETE");

    const postBuscar = await pedir("/api/buscar?q=x", { method: "POST" });
    assert.equal(postBuscar.r.status, 405);

    const traversal = await fetch(`${base}/..%2fpackage.json`);
    assert.equal(traversal.status, 403);

    const inexistente = await fetch(`${base}/no-existe.html`);
    assert.equal(inexistente.status, 404);
  });
});

test.after(() => {
  servidor.close();
});
