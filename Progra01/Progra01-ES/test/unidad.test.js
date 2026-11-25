// Pruebas unitarias que no necesitan Elasticsearch ni la colección: npm test
// Usan una copia mínima del propio en test/fixtures/Progra01.

import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { parsearTextoConf, cargarConfiguracion } from "../src/configuracion.js";
import { detectarIdioma, tokensBrutos, removerDiacriticos } from "../src/idioma.js";
import { construirIndice, reglasTesauro } from "../src/analizadores.js";
import { cargarRecursos } from "../src/recursos.js";
import { pesoTf, idf } from "../src/similitud.js";

const FIXTURE = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "Progra01", "Progra01-ES");
const conf = (p) => cargarConfiguracion(join(FIXTURE, "configuraciones", `${p}.conf`));
const filtrosDe = (p) => construirIndice(conf(p)).settings.analysis.analyzer.politica_es.filter;

test("el parser lee secciones, tipos, listas y comentarios", () => {
  const c = parsearTextoConf("[a]\nx = true ; comentario\ny: 42\nz = es, en\nw = 'hola # no es comentario'\n");
  assert.deepEqual(c.a, { x: true, y: 42, z: ["es", "en"], w: "hola # no es comentario" });
});

test("la normalización se toma del .conf del propio", () => {
  const c = conf("avanzado");
  assert.equal(c.normalizacion.remover_acentos, true); // en el fixture del propio está en true
  assert.equal(c.normalizacion.expandir_sinonimos, true);
  assert.equal(typeof c.general.descripcion, "string");
  assert.ok(c.rutaPropio.endsWith(join("configuraciones", "avanzado.conf")));
});

test("las tres políticas activan los filtros correctos", () => {
  assert.deepEqual(filtrosDe("minimo"), ["i_turca", "lowercase", "longitud"]);
  assert.ok(filtrosDe("estandar").includes("cliticos"));
  assert.ok(filtrosDe("estandar").includes("stopwords_propias"));
  assert.equal(filtrosDe("estandar").at(-1), "snowball_es");
  assert.ok(!filtrosDe("estandar").includes("tesauro"));
  assert.ok(filtrosDe("avanzado").includes("tesauro"));
  assert.ok(filtrosDe("avanzado").includes("sin_acentos"));
  const charAvanzado = construirIndice(conf("avanzado")).settings.analysis.analyzer.politica_es.char_filter;
  assert.deepEqual(charAvanzado, ["nfkc", "tipografia", "fechas", "fechas_textuales", "anios", "numeros"]);
});

test("remover_diacriticos conserva la ñ", () => {
  assert.equal(removerDiacriticos("pingüino año película ÑANDÚ"), "pinguino año pelicula ÑANDU");
});

test("tokens brutos: fechas textuales, clíticos y tildes como en el propio", () => {
  const n = conf("avanzado").normalizacion;
  const texto = "José vio “El Padrino” (1972) el 25 de diciembre de 1977 — It’s November 3, 1997 con 9.5 puntos.";
  assert.deepEqual(tokensBrutos(texto, n), [
    "jose", "vio", "el", "padrino", "<ANIO_1972>", "el", "<FECHA>", "it", "<FECHA>", "con", "<NUM>", "puntos",
  ]);
});

test("reglas del tesauro: gana la última, se omiten frases y respeta las tildes", () => {
  const { tesauro } = cargarRecursos(join(FIXTURE, "..", "recursos"));
  // movie aparece dos veces: gana la última regla (=> pelicula)
  assert.equal(tesauro.get("movie"), "pelicula");

  const conTildes = reglasTesauro(tesauro, { remover_acentos: true });
  assert.ok(conTildes.some((r) => r.startsWith("movie") && r.endsWith("=> pelicula")));
  assert.ok(!conTildes.join().includes("película")); // con tildes quitadas, la variante con tilde nunca coincide
  assert.ok(!conTildes.join().includes("motion picture")); // el propio nunca la ve como un solo token

  const sinQuitar = reglasTesauro(tesauro, { remover_acentos: false });
  // director -> también directór, dírector... (segunda búsqueda del propio, sin tildes)
  assert.ok(sinQuitar.some((r) => r.includes("diréctor") && r.endsWith("=> realizador")));
});

test("detección de idioma", () => {
  const n = conf("estandar").normalizacion;
  const recursos = cargarRecursos(join(FIXTURE, "..", "recursos"));
  assert.equal(detectarIdioma("The director and the writer of the movie", n, recursos), "en");
  assert.equal(detectarIdioma("El director y el guionista de la película", n, recursos), "es");
  assert.equal(detectarIdioma("It's it's it's", n, recursos), "en"); // clíticos quitados
  assert.equal(detectarIdioma("", n, recursos), "es");
});

test("fórmulas TF e IDF iguales a spimi.py", () => {
  assert.equal(pesoTf(1, "logaritmico"), 1);
  assert.ok(Math.abs(pesoTf(10, "logaritmico") - (1 + Math.log(10))) < 1e-12);
  assert.ok(Math.abs(idf(500, 5, "estandar") - Math.log(500 / 5 + 1)) < 1e-12);
  assert.equal(idf(500, 0, "estandar"), 0);
});
