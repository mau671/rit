// Pruebas unitarias que no necesitan Elasticsearch: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { parsearTextoConf, cargarConfiguracion } from "../src/configuracion.js";
import { detectarIdioma, tokensBrutos } from "../src/idioma.js";
import { construirIndice } from "../src/analizadores.js";
import { pesoTf, idf } from "../src/similitud.js";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const conf = (p) => cargarConfiguracion(join(RAIZ, "configuraciones", `${p}.conf`));

test("el parser lee secciones, tipos, listas y comentarios", () => {
  const c = parsearTextoConf("[a]\nx = true ; comentario\ny: 42\nz = es, en\nw = 'hola # no es comentario'\n");
  assert.deepEqual(c.a, { x: true, y: 42, z: ["es", "en"], w: "hola # no es comentario" });
});

test("la descripción con comas sigue siendo texto", () => {
  assert.equal(typeof conf("avanzado").general.descripcion, "string");
});

test("las tres políticas activan los filtros correctos", () => {
  const filtros = (p) => construirIndice(conf(p)).settings.analysis.analyzer.politica_es.filter;
  assert.deepEqual(filtros("minimo"), ["lowercase", "longitud"]);
  assert.ok(filtros("estandar").includes("stopwords_propias"));
  assert.equal(filtros("estandar").at(-1), "snowball_es");
  assert.ok(!filtros("estandar").includes("tesauro"));
  assert.ok(filtros("avanzado").includes("tesauro"));
  const charAvanzado = construirIndice(conf("avanzado")).settings.analysis.analyzer.politica_es.char_filter;
  assert.deepEqual(charAvanzado, ["nfkc", "tipografia", "fechas", "anios", "numeros"]);
});

test("tokens brutos iguales a los del propio (antes de stopwords y stemming)", () => {
  const n = conf("avanzado").normalizacion;
  const texto = "José vio “El Padrino” (1972) el 15/03/2024 — un sci-fi de O’Brien con 9.5 puntos.";
  assert.deepEqual(tokensBrutos(texto, n), [
    "josé", "vio", "el", "padrino", "<ANIO_1972>", "el", "<FECHA>", "un", "sci-fi", "de", "o'brien", "con", "<NUM>", "puntos",
  ]);
});

test("detección de idioma", () => {
  const n = conf("estandar").normalizacion;
  assert.equal(detectarIdioma("The director and the writer of the movie", n), "en");
  assert.equal(detectarIdioma("El director y el guionista de la película", n), "es");
  assert.equal(detectarIdioma("", n), "es");
  assert.equal(detectarIdioma("the the the", { ...n, idioma: "es" }), "es");
});

test("fórmulas TF e IDF iguales a spimi.py", () => {
  assert.equal(pesoTf(1, "logaritmico"), 1);
  assert.ok(Math.abs(pesoTf(10, "logaritmico") - (1 + Math.log(10))) < 1e-12);
  assert.ok(Math.abs(idf(500, 5, "estandar") - Math.log(500 / 5 + 1)) < 1e-12);
  assert.equal(idf(500, 0, "estandar"), 0);
});
