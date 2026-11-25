// Pruebas que no necesitan Elasticsearch: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { interpretarConsulta, textoParaPuntaje } from "../src/consulta.js";
import { armarFragmentos, tituloDesdeUrl } from "../src/fragmentos.js";

test("interpreta la sintaxis avanzada", () => {
  const c = interpretarConsulta('director "ciencia ficción" +kubrick -remake -"versión extendida" dominio:IMSDB.com idioma:en');
  assert.deepEqual(c.libres, ["director"]);
  assert.deepEqual(c.frases, ["ciencia ficción"]);
  assert.deepEqual(c.obligatorios, ["kubrick"]);
  assert.deepEqual(c.excluidos, ["remake", "versión extendida"]);
  assert.deepEqual(c.dominios, ["imsdb.com"]);
  assert.deepEqual(c.idiomas, ["en"]);
  assert.equal(textoParaPuntaje(c), "director kubrick ciencia ficción");
});

test("una comilla sin cerrar se toma como frase hasta el final", () => {
  assert.deepEqual(interpretarConsulta('"el padrino').frases, ["el padrino"]);
});

test("títulos a partir de la URL", () => {
  assert.equal(tituloDesdeUrl("https://imsdb.com/scripts/Pulp-Fiction.html"), "Pulp Fiction");
  assert.equal(tituloDesdeUrl("https://es.wikipedia.org/wiki/El_laberinto_del_fauno"), "El laberinto del fauno");
  assert.equal(tituloDesdeUrl("https://www.filmaffinity.com/es/film809297.html"), null);
  assert.equal(tituloDesdeUrl("no es url"), null);
});

test("los fragmentos resaltan los términos normalizados en el texto original", async () => {
  const dir = mkdtempSync(join(tmpdir(), "frag-"));
  const ruta = join(dir, "doc.txt");
  const texto = "Introducción larga. ".repeat(40) + "Los directores filmaron la película en 1999.";
  writeFileSync(ruta, texto);

  // Cliente falso: _analyze devuelve tokens normalizados con sus posiciones
  const cliente = {
    indices: {
      analyze: async ({ text }) => {
        const tokens = [];
        for (const m of text.matchAll(/\p{L}+|\d+/gu)) {
          const palabra = m[0].toLowerCase();
          const normal = { directores: "realiz", película: "film" }[palabra] ?? palabra;
          tokens.push({ token: normal, start_offset: m.index, end_offset: m.index + m[0].length });
        }
        return { tokens };
      },
    },
  };

  const r = await armarFragmentos(cliente, {
    indice: "x",
    campo: "contenido_es",
    rutaArchivo: ruta,
    terminos: new Set(["realiz", "film"]),
    opciones: { caracteres_analisis: 100000, caracteres_fragmento: 200, fragmentos_por_resultado: 2 },
  });

  const resaltados = r.fragmentos.flatMap((f) => f.piezas.filter((p) => p.resaltado).map((p) => p.texto));
  assert.deepEqual(resaltados, ["directores", "película"]);
  assert.deepEqual(r.coincidencias, { realiz: 1, film: 1 });
  assert.equal(r.fragmentos[0].antes, true);
  assert.equal(r.primeraLinea.endsWith("…"), true);
});

test("documento ausente en el repositorio", async () => {
  const r = await armarFragmentos({}, {
    indice: "x", campo: "c", rutaArchivo: "/no/existe.txt", terminos: new Set(["a"]),
    opciones: { caracteres_analisis: 10, caracteres_fragmento: 10, fragmentos_por_resultado: 1 },
  });
  assert.equal(r.disponible, false);
});
