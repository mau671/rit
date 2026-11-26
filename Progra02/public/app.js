// app.js — interfaz del buscador.
// El estado vive en la URL (?q=...&p=...&indice=...), así que los resultados
// se pueden compartir, recargar y recorrer con los botones del navegador.
// Los filtros de la barra lateral se escriben en la consulta con la misma
// sintaxis que puede usar la persona (dominio:x, idioma:x).

const $ = (id) => document.getElementById(id);
const formulario = $("formulario");
const campoConsulta = $("q");
const selectorIndice = $("indice");

const NOMBRES_INDICE = { rit_avanzado: "Avanzado", rit_estandar: "Estándar", rit_minimo: "Mínimo", rit_evaluacion: "Muestra" };
const NOMBRES_IDIOMA = { es: "Español", en: "Inglés" };

let indicePorDefecto = "";
let indiceInicial = "";

/** Crea un elemento con atributos e hijos (texto o nodos). Nunca usa innerHTML. */
function el(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor === undefined || valor === null || valor === false) continue;
    if (clave === "class") nodo.className = valor;
    else if (clave.startsWith("on")) nodo.addEventListener(clave.slice(2), valor);
    else nodo.setAttribute(clave, valor === true ? "" : valor);
  }
  for (const hijo of hijos.flat()) {
    if (hijo === null || hijo === undefined || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return nodo;
}

const formatoNumero = new Intl.NumberFormat("es-CR");

function leerEstado() {
  const p = new URLSearchParams(location.search);
  return {
    q: p.get("q") ?? "",
    pagina: Math.max(1, Number.parseInt(p.get("p") ?? "1", 10) || 1),
    indice: p.get("indice") ?? "",
  };
}

function urlDe({ q, pagina = 1, indice }) {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  if (pagina > 1) p.set("p", String(pagina));
  if (indice && indice !== indicePorDefecto) p.set("indice", indice);
  const s = p.toString();
  return s ? `/?${s}` : "/";
}

function navegar(estado) {
  history.pushState(null, "", urlDe(estado));
  mostrar();
}

// ------------------------------------------------------------- Índices

async function cargarIndices() {
  try {
    const r = await fetch("/api/indices");
    const datos = await r.json();
    indicePorDefecto = datos.por_defecto;
    // Si el índice por defecto no está construido, se ofrece uno que sí tenga
    // documentos (por ejemplo, mientras Progra01 reconstruye los estándar).
    const disponibles = datos.indices.filter((i) => i.disponible);
    indiceInicial = (disponibles.find((i) => i.documentos > 0) ?? disponibles[0])?.nombre ?? indicePorDefecto;
    selectorIndice.replaceChildren(
      ...datos.indices.map((i) =>
        el("option", { value: i.nombre, disabled: !i.disponible },
          `${NOMBRES_INDICE[i.nombre] ?? i.nombre}${i.disponible ? "" : " (no construido)"}`),
      ),
    );
  } catch {
    selectorIndice.replaceChildren(el("option", { value: "" }, "Sin conexión"));
  }
}

// ------------------------------------------------------------- Pintado

function pintarFragmento(fragmento) {
  return el("p", { class: "fragmento" },
    fragmento.antes ? "… " : "",
    fragmento.piezas.map((p) => (p.resaltado ? el("mark", {}, p.texto) : p.texto)),
    fragmento.despues ? " …" : "",
  );
}

function pintarResultado(r, estado, indice) {
  const coincidencias = Object.entries(r.coincidencias ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t} ×${n}`)
    .join(", ");

  const parametrosEvaluacion = new URLSearchParams({ q: estado.q });
  if (indice) parametrosEvaluacion.set("indice", indice);

  return el("li", { class: "resultado" },
    el("h2", { class: "resultado__titulo" },
      el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.titulo)),
    el("a", {
      class: "resultado__evaluar",
      href: `/evaluar?${parametrosEvaluacion}`,
      title: "Reabrir esta búsqueda en modo evaluación",
    }, "Evaluar este resultado"),
    el("p", { class: "resultado__url" }, r.url),
    r.fragmentos.length
      ? r.fragmentos.map(pintarFragmento)
      : el("p", { class: "fragmento" }, r.texto_disponible ? "Sin vista previa." : "El texto de este documento no está en el repositorio local."),
    el("p", { class: "resultado__datos" },
      el("span", {}, NOMBRES_IDIOMA[r.idioma] ?? r.idioma),
      el("span", { title: "Similitud coseno con la consulta (0 a 1)" }, `Similitud ${r.similitud.toFixed(4)}`),
      coincidencias ? el("span", { title: "Veces que aparece cada término en el documento" }, coincidencias) : null,
    ),
  );
}

/** Agrega o quita "campo:valor" del texto de la consulta. */
function alternarFiltro(q, campo, valor) {
  const ficha = `${campo}:${valor}`;
  const partes = q.split(/\s+/).filter(Boolean);
  const existe = partes.some((x) => x.toLowerCase() === ficha.toLowerCase());
  const nuevas = existe ? partes.filter((x) => x.toLowerCase() !== ficha.toLowerCase()) : [...partes, ficha];
  return nuevas.join(" ");
}

function pintarFiltros(datos, estado) {
  const grupos = [
    { campo: "idioma", titulo: "Idioma", activos: datos.interpretacion.idiomas, nombre: (v) => NOMBRES_IDIOMA[v] ?? v },
    { campo: "dominio", titulo: "Sitio", activos: datos.interpretacion.dominios, nombre: (v) => v.replace(/^www\./, "") },
  ];
  return grupos
    .filter((g) => datos.facetas[g.campo]?.length)
    .map((g) =>
      el("section", {},
        el("h2", {}, g.titulo),
        el("ul", {},
          datos.facetas[g.campo].map((f) => {
            const activo = g.activos.includes(String(f.valor).toLowerCase());
            return el("li", {},
              el("button", {
                type: "button",
                "aria-pressed": activo ? "true" : "false",
                onclick: () => navegar({ ...estado, q: alternarFiltro(estado.q, g.campo, f.valor), pagina: 1 }),
              },
              el("span", {}, g.nombre(f.valor)),
              el("span", {}, formatoNumero.format(f.cantidad))));
          }),
        ),
      ),
    );
}

function pintarPaginacion(datos, estado) {
  if (datos.paginas <= 1) return [];
  const actual = datos.pagina;
  const enlace = (n, texto, etiqueta) =>
    el("a", {
      href: urlDe({ ...estado, pagina: n }),
      "aria-label": etiqueta,
      onclick: (e) => { e.preventDefault(); navegar({ ...estado, pagina: n }); window.scrollTo(0, 0); },
    }, texto);

  const numeros = new Set([1, datos.paginas, actual - 2, actual - 1, actual, actual + 1, actual + 2]);
  const lista = [...numeros].filter((n) => n >= 1 && n <= datos.paginas).sort((a, b) => a - b);
  const nodos = [];
  if (actual > 1) nodos.push(enlace(actual - 1, "Anterior", "Página anterior"));
  lista.forEach((n, i) => {
    if (i > 0 && n - lista[i - 1] > 1) nodos.push(el("span", { class: "hueco", "aria-hidden": "true" }, "…"));
    nodos.push(n === actual ? el("span", { "aria-current": "page" }, String(n)) : enlace(n, String(n), `Página ${n}`));
  });
  if (actual < datos.paginas) nodos.push(enlace(actual + 1, "Siguiente", "Página siguiente"));
  return nodos;
}

function pintarResumen(datos) {
  const terminos = [...datos.terminos.es, ...datos.terminos.en];
  const unicos = new Map();
  for (const t of terminos) unicos.set(t.termino, Math.max(unicos.get(t.termino) ?? 0, t.df));

  const desde = (datos.pagina - 1) * datos.por_pagina + 1;
  const hasta = desde + datos.resultados.length - 1;
  const nodos = [];
  if (datos.total > 0) {
    nodos.push(el("p", {},
      el("strong", {}, `${formatoNumero.format(datos.total)} ${datos.total === 1 ? "documento" : "documentos"}`),
      `. Del ${desde} al ${hasta}, en ${datos.tiempo_ms} ms.`));
  }
  if (unicos.size) {
    nodos.push(el("p", {}, "Términos buscados después de normalizar la consulta:"));
    nodos.push(el("ul", { class: "terminos", "aria-label": "Términos normalizados" },
      [...unicos].map(([t, df]) => el("li", {}, t, " ", el("span", {}, `en ${formatoNumero.format(df)} ${df === 1 ? "doc." : "docs."}`)))));
  }
  return nodos;
}

function pintarVacio(datos) {
  return el("li", { class: "vacio" },
    el("h2", {}, "No hay documentos que coincidan"),
    el("ul", {},
      el("li", {}, "Probá con menos palabras o quitá las que llevan + o comillas."),
      el("li", {}, "Quitá los filtros de sitio o idioma."),
      el("li", {}, "Cambiá a otro índice: el mínimo no agrupa variantes de las palabras."),
    ),
  );
}

// ------------------------------------------------------------- Flujo

async function mostrar() {
  const estado = leerEstado();
  campoConsulta.value = estado.q;
  const indice = estado.indice || indiceInicial || indicePorDefecto;
  if (indice) selectorIndice.value = indice;

  const enInicio = !estado.q.trim();
  document.body.classList.toggle("en-inicio", enInicio);
  $("inicio").hidden = !enInicio;
  $("pagina-resultados").hidden = enInicio;
  document.title = enInicio ? "Encuadre" : `${estado.q} — Encuadre`;
  if (enInicio) {
    campoConsulta.focus();
    return;
  }

  const lista = $("resultados");
  lista.setAttribute("aria-busy", "true");
  $("resumen").replaceChildren(el("p", {}, "Buscando…"));

  const params = new URLSearchParams({ q: estado.q, p: String(estado.pagina) });
  if (indice) params.set("indice", indice);

  let datos;
  try {
    const r = await fetch(`/api/buscar?${params}`);
    datos = await r.json();
    if (!r.ok) throw new Error(datos.error ?? `Error ${r.status}`);
  } catch (err) {
    lista.removeAttribute("aria-busy");
    $("resumen").replaceChildren(el("p", { class: "aviso", role: "alert" }, `No se pudo buscar: ${err.message}`));
    lista.replaceChildren();
    $("filtros").replaceChildren();
    $("paginacion").replaceChildren();
    return;
  }

  lista.removeAttribute("aria-busy");
  $("resumen").replaceChildren(
    ...datos.avisos.map((a) => el("p", { class: "aviso" }, a)),
    ...pintarResumen(datos),
  );
  lista.setAttribute("start", String((datos.pagina - 1) * datos.por_pagina + 1));
  lista.replaceChildren(...(datos.resultados.length ? datos.resultados.map((r) => pintarResultado(r, estado, indice)) : [pintarVacio(datos)]));
  $("filtros").replaceChildren(...pintarFiltros(datos, estado));
  $("paginacion").replaceChildren(...pintarPaginacion(datos, estado));
}

formulario.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = campoConsulta.value.trim();
  if (!q) return;
  navegar({ q, pagina: 1, indice: selectorIndice.value });
});

selectorIndice.addEventListener("change", () => {
  const estado = leerEstado();
  if (estado.q) navegar({ ...estado, indice: selectorIndice.value, pagina: 1 });
});

window.addEventListener("popstate", mostrar);

await cargarIndices();
mostrar();
