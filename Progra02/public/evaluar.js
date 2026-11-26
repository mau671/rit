// evaluar.js — interfaz de evaluación de relevancia (modo independiente).
//
// El estado vive en la URL (?q=...&p=...&indice=...&evaluador=...), así que una
// sesión de evaluación se puede recargar, compartir o retomar. Las notas se
// guardan en el servidor (salida/juicios.json) y se recargan al volver a la
// misma consulta, de modo que no hay que empezar de cero.
//
// La página es un cliente de /api/buscar y de /api/evaluacion/*; no calcula
// métricas: eso queda en /metricas, que lee los mismos juicios.

const $ = (id) => document.getElementById(id);
const CLAVE_EVALUADOR = "encuadre.evaluador";

const NOMBRES_INDICE = { rit_avanzado: "Avanzado", rit_estandar: "Estándar", rit_minimo: "Mínimo", rit_evaluacion: "Muestra" };
const NOMBRES_IDIOMA = { es: "Español", en: "Inglés" };
const ETIQUETAS_NOTA = {
  0: { boton: "No relevante", estado: "Guardado como no relevante" },
  1: { boton: "Relevante", estado: "Guardado como relevante" },
  2: { boton: "Muy relevante", estado: "Guardado como muy relevante" },
};

const estado = {
  indicePorDefecto: "",
  indiceInicial: "",
  evaluador: "",
  consulta: null,
  indice: "",
  juicios: new Map(),
  resumen: null,
  soloPendientes: false,
  pendientes: new Set(),
  token: 0,
};

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

// --------------------------------------------------------------- Estado URL

function leerEstadoUrl() {
  const p = new URLSearchParams(location.search);
  return {
    q: p.get("q") ?? "",
    pagina: Math.max(1, Number.parseInt(p.get("p") ?? "1", 10) || 1),
    indice: p.get("indice") ?? "",
    evaluador: (p.get("evaluador") ?? "").trim(),
  };
}

function urlDe({ q, pagina = 1, indice, evaluador }) {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  if (pagina > 1) p.set("p", String(pagina));
  if (indice && indice !== estado.indicePorDefecto) p.set("indice", indice);
  if (evaluador) p.set("evaluador", evaluador);
  const s = p.toString();
  return s ? `/evaluar?${s}` : "/evaluar";
}

function navegar(nuevo, { reemplazar = false } = {}) {
  const url = urlDe({ ...leerEstadoUrl(), ...nuevo, evaluador: nuevo.evaluador ?? estado.evaluador });
  if (reemplazar) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
  mostrar();
}

async function pedirJson(url, opciones = {}) {
  const r = await fetch(url, opciones);
  let datos = {};
  try {
    datos = await r.json();
  } catch {
    /* respuesta sin JSON */
  }
  if (!r.ok) throw new Error(datos.error ?? `Error ${r.status}`);
  return datos;
}

// ------------------------------------------------------------- Identidad

function guardarEvaluadorLocal(nombre) {
  try {
    localStorage.setItem(CLAVE_EVALUADOR, nombre);
  } catch {
    /* modo privado: se sigue usando en memoria */
  }
}

function leerEvaluadorLocal() {
  try {
    return localStorage.getItem(CLAVE_EVALUADOR) ?? "";
  } catch {
    return "";
  }
}

function pintarIdentidad() {
  const nombre = estado.evaluador;
  const params = new URLSearchParams();
  if (nombre) params.set("evaluador", nombre);
  const cadena = params.toString();
  $("enlace-exportar").href = `/api/evaluacion/juicios${cadena ? `?${cadena}` : ""}`;
}

function ponerEvaluador(nombre, { refrescar = true } = {}) {
  estado.evaluador = String(nombre ?? "").trim().slice(0, 80);
  $("evaluador").value = estado.evaluador;
  if (estado.evaluador) guardarEvaluadorLocal(estado.evaluador);
  pintarIdentidad();
  if (refrescar) mostrar();
}

// --------------------------------------------------------------- Índices

async function cargarIndices() {
  let datos;
  try {
    datos = await pedirJson("/api/indices");
  } catch {
    datos = { por_defecto: "rit_avanzado", indices: ["rit_avanzado", "rit_estandar", "rit_minimo"].map((nombre) => ({ nombre, documentos: 0, disponible: true })) };
  }
  estado.indicePorDefecto = datos.por_defecto;
  const disponibles = datos.indices.filter((i) => i.disponible);
  estado.indiceInicial = (disponibles.find((i) => i.documentos > 0) ?? disponibles[0])?.nombre ?? estado.indicePorDefecto;
  $("indice").replaceChildren(
    ...datos.indices.map((i) =>
      el("option", { value: i.nombre, disabled: !i.disponible },
        `${NOMBRES_INDICE[i.nombre] ?? i.nombre}${i.disponible ? "" : " (no construido)"}`),
    ),
  );
}

// -------------------------------------------------------------- Resultados

function pintarFragmento(fragmento) {
  return el("p", { class: "fragmento" },
    fragmento.antes ? "… " : "",
    fragmento.piezas.map((p) => (p.resaltado ? el("mark", {}, p.texto) : p.texto)),
    fragmento.despues ? " …" : "",
  );
}

function estadoDelJuicio(docId) {
  const j = estado.juicios.get(String(docId));
  return j ? Number(j.relevante) : null;
}

function pintarBotonera(r) {
  const docId = String(r.id);
  const nota = estadoDelJuicio(docId);
  const etiqueta = ETIQUETAS_NOTA[nota]?.estado ?? "Sin juzgar";

  const botones = [0, 1, 2].map((n) =>
    el("button", {
      type: "button",
      class: "juicio__boton",
      "data-nota": String(n),
      "aria-pressed": nota === n ? "true" : "false",
      title: n === 0 ? "No responde a la consulta" : n === 1 ? "Responde a la consulta" : "Responde muy bien a la consulta",
      onclick: () => marcar(r, n),
    }, ETIQUETAS_NOTA[n].boton),
  );

  return el("div", { class: "juicio", role: "group", "aria-label": `Relevancia de ${r.titulo}` },
    el("span", { class: "juicio__etiqueta" }, "Relevancia"),
    botones,
    el("span", { class: "juicio__estado", "data-estado": nota === null ? "" : "guardado" }, etiqueta),
  );
}

function pintarResultado(r) {
  const coincidencias = Object.entries(r.coincidencias ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `${t} ×${n}`)
    .join(", ");
  const docId = String(r.id);

  return el("li", { class: "resultado", "data-doc": docId, "data-pendiente": estado.juicios.has(docId) ? "false" : "true" },
    el("div", { class: "resultado__cabecera" },
      el("span", { class: "rango", title: "Posición en el ranking" }, `#${r.rango}`),
      el("h2", { class: "resultado__titulo" },
        el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.titulo)),
    ),
    el("p", { class: "resultado__url" }, r.url),
    r.fragmentos.length
      ? r.fragmentos.map(pintarFragmento)
      : el("p", { class: "fragmento" }, r.texto_disponible ? "Sin vista previa." : "El texto de este documento no está en el repositorio local."),
    el("p", { class: "resultado__datos" },
      el("span", {}, NOMBRES_IDIOMA[r.idioma] ?? r.idioma),
      el("span", { title: "Similitud coseno con la consulta (0 a 1)" }, `Similitud ${r.similitud.toFixed(4)}`),
      coincidencias ? el("span", { title: "Veces que aparece cada término en el documento" }, coincidencias) : null,
    ),
    pintarBotonera(r),
  );
}

function pintarLista() {
  const resultados = estado.consulta?.resultados ?? [];
  const visibles = estado.soloPendientes
    ? resultados.filter((r) => !estado.juicios.has(String(r.id)))
    : resultados;
  const lista = $("resultados");

  if (!visibles.length) {
    lista.replaceChildren(el("li", { class: "vacio" },
      el("h2", {}, resultados.length ? "Todos los resultados de esta página están juzgados" : "No hay documentos que coincidan"),
      el("p", {}, resultados.length
        ? "Desmarcá «Ocultar los ya juzgados» para revisar o corregir lo marcado."
        : "Probá con menos palabras, otro índice o quitá los filtros de la consulta."),
    ));
    return;
  }
  lista.replaceChildren(...visibles.map(pintarResultado));
}

function pintarResumen() {
  const datos = estado.consulta;
  if (!datos) return;
  const desde = (datos.pagina - 1) * datos.por_pagina + 1;
  const hasta = desde + datos.resultados.length - 1;
  const nodos = [];
  if (datos.total > 0) {
    nodos.push(el("p", {},
      el("strong", {}, `${formatoNumero.format(datos.total)} ${datos.total === 1 ? "documento" : "documentos"}`),
      `. Del ${desde} al ${hasta}, en ${datos.tiempo_ms} ms.`,
    ));
  } else {
    nodos.push(el("p", {}, "La consulta no devolvió documentos."));
  }
  for (const aviso of datos.avisos ?? []) nodos.push(el("p", { class: "aviso" }, aviso));
  $("resumen-busqueda").replaceChildren(...nodos);
}

function actualizarProgreso() {
  const datos = estado.consulta;
  if (!datos) return;
  const enPagina = datos.resultados.filter((r) => estado.juicios.has(String(r.id))).length;
  const resumen = estado.resumen;
  const juzgados = resumen?.total ?? enPagina;
  const objetivo = Math.max(1, Math.min(datos.por_pagina, datos.total || datos.por_pagina));
  const avance = Math.min(100, Math.round((100 * juzgados) / objetivo));

  $("resumen-eval").replaceChildren(
    el("p", {},
      el("strong", {}, `${enPagina} de ${datos.resultados.length}`),
      " resultados juzgados en esta página",
    ),
  );
  $("barra").setAttribute("aria-valuenow", String(avance));
  $("barra-relleno").style.width = `${avance}%`;
}

// ------------------------------------------------------------------ Guardar

function buscarLi(docId) {
  return [...$("resultados").children].find((n) => n.dataset.doc === String(docId)) ?? null;
}

function actualizarBotoneraLi(li, docId) {
  if (!li) return;
  const nota = estadoDelJuicio(docId);
  for (const boton of li.querySelectorAll(".juicio__boton")) {
    boton.setAttribute("aria-pressed", Number(boton.dataset.nota) === nota ? "true" : "false");
  }
  const marca = li.querySelector(".juicio__estado");
  if (marca) {
    marca.textContent = ETIQUETAS_NOTA[nota]?.estado ?? "Sin juzgar";
    marca.dataset.estado = nota === null ? "" : "guardado";
  }
  li.dataset.pendiente = nota === null ? "true" : "false";
}

function ponerBotonesOcupados(li, ocupado) {
  if (!li) return;
  for (const boton of li.querySelectorAll(".juicio__boton")) boton.disabled = ocupado;
}

function mensaje(texto, tono = "") {
  $("mensaje").textContent = texto;
  if (tono) $("mensaje").dataset.tono = tono;
  else delete $("mensaje").dataset.tono;
}

function paramsJuicio(docId) {
  return new URLSearchParams({
    consulta: estado.consulta?.consulta ?? leerEstadoUrl().q,
    indice: estado.indice,
    doc_id: String(docId),
    evaluador: estado.evaluador,
  });
}

async function marcar(r, nota) {
  if (!estado.evaluador) {
    mensaje("Escribí tu nombre o código arriba antes de marcar resultados.", "error");
    $("evaluador").focus();
    return;
  }
  const docId = String(r.id);
  if (estado.pendientes.has(docId)) return;
  const li = buscarLi(docId);
  const anterior = estado.juicios.get(docId);
  estado.pendientes.add(docId);
  ponerBotonesOcupados(li, true);
  mensaje("Guardando…");

  try {
    if (anterior && Number(anterior.relevante) === nota) {
      await pedirJson(`/api/evaluacion/juicios?${paramsJuicio(docId)}`, { method: "DELETE" });
      estado.juicios.delete(docId);
      mensaje("Juicio eliminado, podés marcarlo de nuevo cuando quieras", "");
    } else {
      const { juicio } = await pedirJson("/api/evaluacion/juicios", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          consulta: estado.consulta?.consulta ?? leerEstadoUrl().q,
          indice: estado.indice,
          doc_id: docId,
          url: r.url,
          titulo: r.titulo,
          evaluador: estado.evaluador,
          relevante: nota,
          rango: r.rango,
        }),
      });
      estado.juicios.set(docId, juicio);
      mensaje(`${r.titulo}: ${ETIQUETAS_NOTA[nota].estado.toLowerCase()}.`, "ok");
    }
    actualizarBotoneraLi(li, docId);
    const params = new URLSearchParams({ consulta: estado.consulta?.consulta ?? "", indice: estado.indice, evaluador: estado.evaluador });
    estado.resumen = await pedirJson(`/api/evaluacion/resumen?${params}`).catch(() => estado.resumen);
    actualizarProgreso();
    if (estado.soloPendientes) pintarLista();
  } catch (err) {
    mensaje(`No se pudo guardar el juicio: ${err.message}`, "error");
  } finally {
    estado.pendientes.delete(docId);
    ponerBotonesOcupados(li, false);
  }
}

// --------------------------------------------------------------- Carga

async function cargarResultados() {
  const e = leerEstadoUrl();
  const token = ++estado.token;
  estado.consulta = null;
  estado.juicios = new Map();
  estado.resumen = null;
  $("resultados").replaceChildren(el("li", { class: "vacio" }, el("p", {}, "Buscando…")));
  $("paginacion").replaceChildren();
  $("resumen-busqueda").replaceChildren();
  $("resumen-eval").replaceChildren(el("p", {}, "Buscando…"));
  $("zona").hidden = false;

  const params = new URLSearchParams({ q: e.q, p: String(e.pagina) });
  const indice = $("indice").value || e.indice || estado.indiceInicial;
  if (indice) params.set("indice", indice);

  try {
    const datos = await pedirJson(`/api/buscar?${params}`);
    if (token !== estado.token) return;
    estado.consulta = datos;
    estado.indice = datos.indice;

    if (estado.evaluador) {
      const base = new URLSearchParams({ consulta: e.q, indice: datos.indice, evaluador: estado.evaluador });
      const [guardados, resumen] = await Promise.all([
        pedirJson(`/api/evaluacion/juicios?${base}`),
        pedirJson(`/api/evaluacion/resumen?${base}`),
      ]);
      if (token !== estado.token) return;
      estado.juicios = new Map(guardados.juicios.map((j) => [String(j.doc_id), j]));
      estado.resumen = resumen;
    }
  } catch (err) {
    if (token !== estado.token) return;
    $("resumen-busqueda").replaceChildren(el("p", { class: "aviso", role: "alert" }, `No se pudo buscar: ${err.message}`));
    $("resumen-eval").replaceChildren();
    $("barra").setAttribute("aria-valuenow", "0");
    $("barra-relleno").style.width = "0%";
    $("resultados").replaceChildren(el("li", { class: "vacio" },
      el("h2", {}, "No se pudo cargar la búsqueda"),
      el("p", {}, "Revisá que Elasticsearch esté corriendo y que el índice exista, y después recargá la página"),
    ));
    return;
  }

  pintarResumen();
  actualizarProgreso();
  pintarLista();
  pintarPaginacion();
  document.title = `${e.q} — Evaluar — Encuadre`;
}

function pintarPaginacion() {
  const datos = estado.consulta;
  const e = leerEstadoUrl();
  if (!datos || datos.paginas <= 1) return;
  const enlace = (n, texto, etiqueta) =>
    el("a", {
      href: urlDe({ ...e, pagina: n, evaluador: estado.evaluador }),
      "aria-label": etiqueta,
      onclick: (ev) => {
        ev.preventDefault();
        navegar({ ...e, pagina: n, evaluador: estado.evaluador });
        window.scrollTo(0, 0);
      },
    }, texto);

  const numeros = new Set([1, datos.paginas, datos.pagina - 2, datos.pagina - 1, datos.pagina, datos.pagina + 1, datos.pagina + 2]);
  const lista = [...numeros].filter((n) => n >= 1 && n <= datos.paginas).sort((a, b) => a - b);
  const nodos = [];
  if (datos.pagina > 1) nodos.push(enlace(datos.pagina - 1, "Anterior", "Página anterior"));
  lista.forEach((n, i) => {
    if (i > 0 && n - lista[i - 1] > 1) nodos.push(el("span", { class: "hueco", "aria-hidden": "true" }, "…"));
    nodos.push(n === datos.pagina ? el("span", { "aria-current": "page" }, String(n)) : enlace(n, String(n), `Página ${n}`));
  });
  if (datos.pagina < datos.paginas) nodos.push(enlace(datos.pagina + 1, "Siguiente", "Página siguiente"));
  $("paginacion").replaceChildren(...nodos);
}

// --------------------------------------------------------------- Cola

async function cargarCola() {
  const params = new URLSearchParams();
  if (estado.evaluador) params.set("evaluador", estado.evaluador);
  try {
    const datos = await pedirJson(`/api/evaluacion/consultas?${params}`);
    if (!datos.consultas.length) {
      $("lista-cola").replaceChildren(
        el("li", { class: "cola-vacia" }, "Todavía no hay consultas registradas"),
      );
      return;
    }
    $("lista-cola").replaceChildren(...datos.consultas.slice(0, 12).map((c) => {
      const objetivo = c.objetivos || 0;
      const propios = c.juzgados_por_evaluador;
      const completa = objetivo > 0 && propios >= objetivo;
      const avance = estado.evaluador
        ? `${propios} de ${objetivo} juzgadas`
        : `${c.documentos_juzgados} juzgadas`;
      return el("li", {},
        el("a", {
          href: urlDe({ q: c.consulta, pagina: 1, indice: c.indice, evaluador: estado.evaluador }),
          onclick: (ev) => {
            ev.preventDefault();
            navegar({ q: c.consulta, pagina: 1, indice: c.indice, evaluador: estado.evaluador });
          },
        },
          c.consulta,
          el("span", { class: "meta" },
            NOMBRES_INDICE[c.indice] ?? c.indice,
            el("span", { class: completa ? "completa" : "avance" }, avance),
          ),
        ),
      );
    }));
  } catch {
    $("lista-cola").replaceChildren(
      el("li", { class: "cola-vacia" }, "No se pudo cargar el historial de consultas"),
    );
  }
}

// --------------------------------------------------------------- Flujo

async function mostrar() {
  const e = leerEstadoUrl();
  if (e.evaluador && e.evaluador !== estado.evaluador) ponerEvaluador(e.evaluador, { refrescar: false });
  if (!e.evaluador && estado.evaluador) {
    // el nombre guardado no está en la URL: se agrega sin recargar
    history.replaceState(null, "", urlDe({ ...e, evaluador: estado.evaluador }));
  }
  $("evaluador").value = estado.evaluador;
  pintarIdentidad();

  if (e.indice) $("indice").value = e.indice;
  else {
    const indice = estado.indiceInicial || estado.indicePorDefecto;
    if (indice) $("indice").value = indice;
  }

  const enInicio = !e.q.trim();
  $("inicio-eval").hidden = !enInicio;
  $("zona").hidden = enInicio;
  $("q").value = e.q;

  if (enInicio) {
    estado.consulta = null;
    document.title = "Evaluar resultados — Encuadre";
    await cargarCola();
    return;
  }
  await cargarResultados();
}

// --------------------------------------------------------------- Eventos

$("evaluador").addEventListener("input", () => {
  ponerEvaluador($("evaluador").value, { refrescar: false });
  const e = leerEstadoUrl();
  history.replaceState(null, "", urlDe({ ...e, indice: $("indice").value || e.indice, evaluador: estado.evaluador }));
});

$("form-busqueda").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  navegar({ q, pagina: 1, indice: $("indice").value, evaluador: estado.evaluador });
});

$("indice").addEventListener("change", () => {
  const e = leerEstadoUrl();
  if (e.q) navegar({ ...e, indice: $("indice").value, pagina: 1, evaluador: estado.evaluador });
});

$("solo-pendientes").addEventListener("change", () => {
  estado.soloPendientes = $("solo-pendientes").checked;
  pintarLista();
});

document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.target instanceof Element && ev.target.closest("input, select, textarea, button, a")) return;
  if (!["0", "1", "2"].includes(ev.key) || !estado.consulta) return;
  const pendiente = estado.consulta.resultados.find(
    (r) => !estado.juicios.has(String(r.id)) && !estado.pendientes.has(String(r.id)),
  );
  if (!pendiente) return;
  ev.preventDefault();
  marcar(pendiente, Number(ev.key));
  buscarLi(pendiente.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
});

window.addEventListener("popstate", mostrar);

// --------------------------------------------------------------- Arranque

const deUrl = leerEstadoUrl().evaluador;
estado.evaluador = deUrl || leerEvaluadorLocal();
$("evaluador").value = estado.evaluador;
pintarIdentidad();
await cargarIndices();
await mostrar();
