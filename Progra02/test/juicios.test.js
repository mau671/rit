// Pruebas de la persistencia de juicios (archivos temporales, datos sintéticos).

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  borrarJuicio,
  claveJuicio,
  ErrorJuicio,
  guardarJuicio,
  leerJuicios,
  normalizarConsulta,
  validarJuicio,
} from "../src/juicios.js";

const dir = mkdtempSync(join(tmpdir(), "juicios-"));
let n = 0;
const rutaNueva = () => join(dir, `juicios-${++n}.json`);

const base = {
  consulta: "director de terror",
  indice: "rit_avanzado",
  doc_id: "152",
  url: "https://imsdb.com/scripts/Pulp-Fiction.html",
  titulo: "Pulp Fiction",
  evaluador: "e1",
  relevante: 2,
  rango: 1,
};

test("normaliza consultas y arma una clave estable", () => {
  assert.equal(normalizarConsulta("  Director   DE Terror "), "director de terror");
  assert.equal(
    claveJuicio({ consulta: "A  b", indice: "rit_avanzado", doc_id: "1", evaluador: "e1" }),
    claveJuicio({ consulta: "a b", indice: "rit_avanzado", doc_id: 1, evaluador: "e1" }),
  );
});

test("valida los campos del juicio", () => {
  assert.throws(() => validarJuicio({ ...base, relevante: 3 }), ErrorJuicio);
  assert.throws(() => validarJuicio({ ...base, doc_id: " " }), ErrorJuicio);
  assert.throws(() => validarJuicio({ ...base, evaluador: "" }), ErrorJuicio);
  assert.throws(() => validarJuicio({ ...base, url: "file:///etc/passwd" }), ErrorJuicio);
  assert.throws(() => validarJuicio({ ...base, indice: "otro" }, { indices: ["rit_avanzado"] }), ErrorJuicio);
  assert.throws(() => validarJuicio({ ...base, rango: 0 }), ErrorJuicio);
  // acepta doc_id numérico y nota como texto
  assert.equal(validarJuicio({ ...base, doc_id: 12, relevante: "1" }).doc_id, "12");
});

test("guarda, actualiza sin duplicar y borra", async () => {
  const ruta = rutaNueva();
  assert.deepEqual(await leerJuicios(ruta), []);

  const primero = await guardarJuicio(ruta, base);
  assert.equal(primero.creado, true);
  assert.equal(primero.juicio.timestamp.length > 10, true);
  assert.equal(primero.juicio.id, base.doc_id); // alias para documento/scripts/tablas.py
  assert.equal((await leerJuicios(ruta)).length, 1);

  const actualizado = await guardarJuicio(ruta, { ...base, relevante: 0, rango: 3 });
  assert.equal(actualizado.creado, false);
  const guardados = await leerJuicios(ruta);
  assert.equal(guardados.length, 1);
  assert.equal(guardados[0].relevante, 0);
  assert.equal(guardados[0].rango, 3);
  assert.equal(guardados[0].url, base.url); // no se perdió información

  // el mismo documento con otro evaluador es otro juicio
  await guardarJuicio(ruta, { ...base, evaluador: "e2", relevante: 1 });
  assert.equal((await leerJuicios(ruta)).length, 2);

  // mismo doc, otra consulta: otro juicio
  await guardarJuicio(ruta, { ...base, consulta: "otra consulta" });
  assert.equal((await leerJuicios(ruta)).length, 3);

  const borrado = await borrarJuicio(ruta, {
    consulta: base.consulta,
    doc_id: base.doc_id,
    evaluador: base.evaluador,
  });
  assert.equal(borrado.eliminado, true);
  assert.equal((await leerJuicios(ruta)).length, 2);
  assert.equal((await borrarJuicio(ruta, base)).eliminado, false);
});

test("las escrituras simultáneas no pierden juicios", async () => {
  const ruta = rutaNueva();
  const total = 20;
  await Promise.all(
    Array.from({ length: total }, (_, i) => guardarJuicio(ruta, { ...base, doc_id: `d${i}`, relevante: i % 3 })),
  );
  const guardados = await leerJuicios(ruta);
  assert.equal(guardados.length, total);
  assert.equal(new Set(guardados.map((j) => j.doc_id)).size, total);
  // el archivo en disco es JSON válido y coincide con lo leído
  assert.equal(JSON.parse(readFileSync(ruta, "utf-8")).length, total);
});

test("un archivo corrupto se informa y no se sobrescribe en silencio", async () => {
  const ruta = rutaNueva();
  writeFileSync(ruta, "{ esto no es json");
  await assert.rejects(() => leerJuicios(ruta), /no es JSON válido/);
  await assert.rejects(() => guardarJuicio(ruta, base), /no es JSON válido/);
  assert.equal(readFileSync(ruta, "utf-8"), "{ esto no es json");
});

test("un archivo que no es un arreglo se rechaza", async () => {
  const ruta = rutaNueva();
  writeFileSync(ruta, JSON.stringify({ consulta: "x" }));
  await assert.rejects(() => leerJuicios(ruta), /arreglo JSON/);
});

test("ignora entradas incompletas al leer", async () => {
  const ruta = rutaNueva();
  writeFileSync(ruta, JSON.stringify([base, { consulta: "" }, null, 42]));
  const juicios = await leerJuicios(ruta);
  assert.equal(juicios.length, 1);
  assert.equal(juicios[0].doc_id, "152");
});
