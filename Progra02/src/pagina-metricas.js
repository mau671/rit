// pagina-metricas.js
// Página de métricas servida por el mismo servidor que el buscador.
//
// Se genera en cada visita con los datos actuales (juicios.json +
// consultas.jsonl), así que "regenerar" es simplemente recargar (o usar el
// formulario para cambiar la profundidad k o filtrar por evaluador o índice).
// El HTML es autocontenido: CSS y gráficos SVG en línea, sin dependencias ni
// CDN, para que funcione aunque la máquina que evalúa no tenga internet.

const PALETA = ["#2f6fdf", "#e08a1e", "#1f9d8b"];
const GRIS = "#9aa3b2";

const DESCRIPCION = {
  "P@": "De los primeros k resultados, cuántos son relevantes.",
  MAP: "Precisión promedio: premia los aciertos arriba en toda la lista.",
  nDCG: "Orden con relevancia graduada (0, 1, 2) y descuento por posición.",
  MRR: "1 dividido entre la posición del primer resultado relevante.",
  "Recall@": "Parte de los relevantes conocidos que entraron al top k.",
  "Recall ": "Parte de los relevantes conocidos que entraron al top k.",
};

const CSS = `
:root {
  --sala: #eef0f4; --hoja: #ffffff; --tinta: #1b2130; --tinta-suave: #586176;
  --linea: #d3d7e0; --enlace: #2c3ba8; --enlace-visto: #6b3d9e;
  --resaltador: #ffe14d; --ok: #1f7a4d; --aviso: #b45309;
  --radio: 6px; color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --sala: #141821; --hoja: #1d2230; --tinta: #e6e8ee; --tinta-suave: #a2a9ba;
    --linea: #333a4b; --enlace: #a4b0ff; --enlace-visto: #cfaaf5;
    --resaltador: #f5d63d; --ok: #5ecb98; --aviso: #f0b25c; color-scheme: dark;
  }
}
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; background: var(--sala); color: var(--tinta);
  font: 16px/1.5 Archivo, system-ui, -apple-system, "Segoe UI", sans-serif;
}
a { color: var(--enlace); }
a:visited { color: var(--enlace-visto); }
:focus-visible { outline: 3px solid var(--enlace); outline-offset: 2px; border-radius: 2px; }
.oculto { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.cabecera {
  width: 100%; max-width: 76rem; margin: 0 auto; padding: 1.1rem 1rem;
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.75rem 2rem;
}
.marca { position: relative; display: inline-block; padding: 0.35rem 0.7rem; color: var(--tinta); text-decoration: none; font-weight: 800; font-size: 1.3rem; line-height: 1; }
.marca:visited { color: var(--tinta); }
.marca::before, .marca::after { content: ""; position: absolute; width: 0.7rem; height: 0.7rem; border: 0 solid currentColor; }
.marca::before { top: 0; left: 0; border-width: 2px 0 0 2px; }
.marca::after { bottom: 0; right: 0; border-width: 0 2px 2px 0; }
.menu { display: flex; gap: 1.25rem; list-style: none; margin: 0; padding: 0; font-size: 0.95rem; }
.menu a { text-decoration: none; color: var(--tinta-suave); padding-bottom: 0.15rem; border-bottom: 2px solid transparent; }
.menu a[aria-current="page"] { color: var(--tinta); border-color: var(--tinta); font-weight: 600; }
.menu a:hover { color: var(--enlace); border-color: var(--enlace); }
main { max-width: 76rem; margin: 0 auto; padding: 0 1rem 4rem; }
h1 { font-size: clamp(1.7rem, 4vw, 2.3rem); margin: 0.75rem 0 0.3rem; letter-spacing: -0.02em; }
.sub { color: var(--tinta-suave); margin: 0 0 1.5rem; max-width: 62rem; }
.controles {
  display: flex; flex-wrap: wrap; align-items: end; gap: 0.75rem 1rem;
  background: var(--hoja); border: 1px solid var(--linea); border-radius: var(--radio);
  padding: 0.85rem 1rem; margin: 0 0 1.5rem;
}
.controles label { display: grid; gap: 0.2rem; font-size: 0.8rem; color: var(--tinta-suave); font-weight: 600; }
.controles select {
  font: inherit; height: 2.4rem; padding: 0 0.5rem; border-radius: var(--radio);
  border: 1px solid var(--linea); background: var(--hoja); color: var(--tinta); min-width: 9rem;
}
.controles button, .boton {
  font: inherit; height: 2.4rem; padding: 0 1.1rem; border-radius: var(--radio);
  border: 1px solid var(--tinta); background: var(--tinta); color: var(--sala);
  font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
}
.controles button:hover, .boton:hover { background: var(--enlace); border-color: var(--enlace); color: #fff; }
.controles .json { margin-left: auto; font-size: 0.9rem; align-self: center; }
.nota { color: var(--tinta-suave); font-size: 0.85rem; }
.aviso {
  border-left: 3px solid var(--resaltador); background: var(--hoja);
  padding: 0.5rem 0.9rem; margin: 0 0 1rem; border-radius: 0 var(--radio) var(--radio) 0;
}
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.75rem; margin: 0 0 1rem; }
.card {
  background: var(--hoja); border: 1px solid var(--linea); border-radius: var(--radio);
  padding: 0.85rem 1rem;
}
.card span { display: block; color: var(--tinta-suave); font-size: 0.8rem; }
.card b { display: block; font-size: 1.75rem; line-height: 1.15; font-variant-numeric: tabular-nums; color: var(--enlace); margin: 0.15rem 0; }
.card small { color: var(--tinta-suave); font-size: 0.75rem; line-height: 1.3; display: block; }
.card b.texto { font-size: 1.2rem; }
h2 { font-size: 1.2rem; margin: 2rem 0 0.3rem; }
h2 + .nota { margin-top: 0; }
.panel {
  background: var(--hoja); border: 1px solid var(--linea); border-radius: var(--radio);
  padding: 0.9rem; margin: 0.9rem 0 0; overflow-x: auto;
}
.panel > svg { display: block; width: 100%; height: auto; }
svg text { fill: var(--tinta-suave); font: 12px system-ui, sans-serif; }
svg .eje { stroke: var(--linea); }
table { border-collapse: collapse; width: 100%; font-size: 0.87rem; font-variant-numeric: tabular-nums; }
th, td { padding: 0.45rem 0.55rem; border-bottom: 1px solid var(--linea); text-align: right; white-space: nowrap; }
th { color: var(--tinta-suave); font-weight: 600; }
th:nth-child(-n+3), td:nth-child(-n+3) { text-align: left; }
td.consulta { max-width: 22rem; overflow: hidden; text-overflow: ellipsis; }
tbody tr:hover { background: color-mix(in srgb, var(--enlace) 6%, transparent); }
.leyenda { display: flex; flex-wrap: wrap; gap: 0.35rem 1rem; margin: 0 0 0.5rem; font-size: 0.82rem; color: var(--tinta-suave); }
.leyenda i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 0.35rem; }
.vacio { padding: 1.5rem; }
.vacio h2 { margin-top: 0; }
.vacio p { color: var(--tinta-suave); max-width: 50rem; }
.lista-consultas { list-style: none; margin: 0.5rem 0 0; padding: 0; }
.lista-consultas li { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid var(--linea); font-size: 0.9rem; }
.lista-consultas li:last-child { border-bottom: 0; }
.lista-consultas span { color: var(--tinta-suave); font-size: 0.82rem; }
.pie { max-width: 76rem; margin: 0 auto; padding: 1.5rem 1rem 2.5rem; color: var(--tinta-suave); font-size: 0.82rem; }
@media (max-width: 560px) { .menu { width: 100%; } td.consulta { max-width: 12rem; } }
`;

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmt = (v, dec = 3) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(dec) : "—");
const pct = (v) => (typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)} %` : "—");

function descripcion(nombre) {
  const clave = Object.keys(DESCRIPCION).find((k) => nombre.startsWith(k));
  return clave ? DESCRIPCION[clave] : "";
}

function tarjeta(nombre, valor, etiqueta = nombre) {
  return `<div class="card"><span>${esc(etiqueta)}</span><b>${fmt(valor)}</b><small>${esc(descripcion(nombre))}</small></div>`;
}

// ------------------------------------------------------------------ SVG

function svgLinea(valores, textoEje) {
  const ancho = 900;
  const alto = 270;
  const mi = 44;
  const md = 18;
  const ms = 16;
  const mb = 46;
  const w = ancho - mi - md;
  const h = alto - ms - mb;
  const n = valores.length;
  const x = (i) => mi + (n <= 1 ? w / 2 : (w * i) / (n - 1));
  const y = (v) => ms + h * (1 - Math.max(0, Math.min(1, v ?? 0)));
  const partes = [`<svg viewBox="0 0 ${ancho} ${alto}" role="img" aria-label="Precisión promedio por posición">`];
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    partes.push(`<line class="eje" x1="${mi}" x2="${ancho - md}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"/>`);
    partes.push(`<text x="${mi - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end">${t.toFixed(2)}</text>`);
  }
  if (n > 0) {
    const puntos = valores.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    partes.push(`<polyline points="${puntos}" fill="none" stroke="${PALETA[0]}" stroke-width="2.5" stroke-linejoin="round"/>`);
    valores.forEach((v, i) => {
      partes.push(
        `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" fill="${PALETA[0]}"><title>P@${i + 1}: ${fmt(v)}</title></circle>`,
      );
      const salto = n > 20 ? Math.ceil(n / 20) : 1;
      if ((i + 1) % salto === 0 || i === n - 1) {
        partes.push(`<text x="${x(i).toFixed(1)}" y="${alto - 26}" text-anchor="middle">${i + 1}</text>`);
      }
    });
  }
  partes.push(`<text x="${mi + w / 2}" y="${alto - 6}" text-anchor="middle">${esc(textoEje)}</text></svg>`);
  return partes.join("");
}

function svgBarras(etiquetas, series) {
  const ancho = 900;
  const alto = 300;
  const mi = 44;
  const md = 10;
  const ms = 14;
  const mb = 44;
  const w = ancho - mi - md;
  const h = alto - ms - mb;
  const grupo = w / Math.max(1, etiquetas.length);
  const barra = Math.min(20, (grupo * 0.8) / Math.max(1, series.length));
  const partes = [`<svg viewBox="0 0 ${ancho} ${alto}" role="img" aria-label="Comparación de tres métricas por consulta">`];
  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    const yy = ms + h * (1 - t);
    partes.push(`<line class="eje" x1="${mi}" x2="${ancho - md}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}"/>`);
    partes.push(`<text x="${mi - 8}" y="${(yy + 4).toFixed(1)}" text-anchor="end">${t.toFixed(2)}</text>`);
  }
  etiquetas.forEach((etq, i) => {
    const inicio = mi + i * grupo + (grupo - barra * series.length) / 2;
    series.forEach((serie, s) => {
      const valor = serie.valores[i];
      const bh = h * Math.max(0, Math.min(1, valor ?? 0));
      partes.push(
        `<rect x="${(inicio + s * barra).toFixed(1)}" y="${(ms + h - bh).toFixed(1)}" width="${(Math.max(1, barra - 2)).toFixed(1)}" ` +
          `height="${bh.toFixed(1)}" rx="2" fill="${PALETA[s % PALETA.length]}">` +
          `<title>${esc(etq)} · ${esc(serie.nombre)}: ${fmt(valor)}</title></rect>`,
      );
    });
    const salto = etiquetas.length > 30 ? Math.ceil(etiquetas.length / 30) : 1;
    if ((i + 1) % salto === 0 || i === etiquetas.length - 1) {
      partes.push(`<text x="${(mi + i * grupo + grupo / 2).toFixed(1)}" y="${alto - 22}" text-anchor="middle">${i + 1}</text>`);
    }
  });
  partes.push(`<text x="${mi + w / 2}" y="${alto - 4}" text-anchor="middle">número de consulta (el detalle está en la tabla)</text></svg>`);
  return partes.join("");
}

function svgBarrasHorizontales(datos, { etiqueta = "cantidad", colores = null } = {}) {
  const entradas = Object.entries(datos);
  if (!entradas.length) return `<p class="nota">Sin datos todavía.</p>`;
  const ancho = 900;
  const fila = 28;
  const mi = 150;
  const alto = entradas.length * fila + 12;
  const tope = Math.max(1, ...entradas.map(([, v]) => v));
  const partes = [`<svg viewBox="0 0 ${ancho} ${alto}" role="img" aria-label="${esc(etiqueta)}">`];
  entradas.forEach(([nombre, valor], i) => {
    const yy = 6 + i * fila;
    const largo = ((ancho - mi - 64) * valor) / tope;
    partes.push(`<text x="${mi - 10}" y="${yy + 17}" text-anchor="end">${esc(nombre)}</text>`);
    partes.push(
      `<rect x="${mi}" y="${yy + 3}" width="${Math.max(1, largo).toFixed(1)}" height="20" rx="3" ` +
        `fill="${colores?.[nombre] ?? PALETA[2]}"><title>${esc(nombre)}: ${valor}</title></rect>`,
    );
    partes.push(`<text x="${(mi + largo + 8).toFixed(1)}" y="${yy + 17}">${valor}</text>`);
  });
  partes.push("</svg>");
  return partes.join("");
}

// ------------------------------------------------------------------ HTML

function controles(informe, { indices = [], evaluadores = [], juiciosTotales = 0 } = {}) {
  const k = informe.k;
  const filtros = informe.filtros;
  const opcionesK = [...new Set([5, 10, 20, 50, k])].sort((a, b) => a - b);
  const params = new URLSearchParams({ k: String(k) });
  if (filtros.evaluador) params.set("evaluador", filtros.evaluador);
  if (filtros.indice) params.set("indice", filtros.indice);

  return `<form class="controles" method="get" action="/metricas">
    <label>Profundidad k
      <select name="k">${opcionesK.map((v) => `<option value="${v}"${v === k ? " selected" : ""}>${v}</option>`).join("")}</select>
    </label>
    <label>Evaluador
      <select name="evaluador">
        <option value="">Todos${juiciosTotales ? "" : " (sin juicios)"}</option>
        ${evaluadores
          .map((e) => `<option value="${esc(e)}"${filtros.evaluador === e ? " selected" : ""}>${esc(e)}</option>`)
          .join("")}
      </select>
    </label>
    <label>Índice
      <select name="indice">
        <option value="">Todos</option>
        ${indices.map((i) => `<option value="${esc(i)}"${filtros.indice === i ? " selected" : ""}>${esc(i)}</option>`).join("")}
      </select>
    </label>
    <button type="submit">Recalcular</button>
    <a class="json" href="/api/metricas?${params}">Ver JSON</a>
  </form>`;
}

function estadoVacio(informe, { juiciosTotales = 0 } = {}) {
  if (informe.filtros.evaluador || informe.filtros.indice) {
    return `<section class="panel vacio">
      <h2>No hay juicios con estos filtros</h2>
      <p>Probá con otro evaluador u otro índice, o quitá los filtros con <a href="/metricas?k=${informe.k}">Recalcular</a>.</p>
      <p><a class="boton" href="/evaluar">Ir a evaluar resultados</a></p>
    </section>`;
  }
  if (!juiciosTotales) {
    return `<section class="panel vacio">
      <h2>Todavía no hay juicios de relevancia</h2>
      <p>Las métricas necesitan que alguien marque resultados como <em>no relevante</em>, <em>relevante</em> o
      <em>muy relevante</em>. Abrí la <a href="/evaluar">interfaz de evaluación</a>, poné tu nombre o código,
      buscá una consulta y marcá al menos los primeros diez resultados.</p>
      <p class="nota">Esta página se recalcula sola en cada visita: en cuanto haya juicios guardados en
      <code>salida/juicios.json</code> van a aparecer P@k, MAP, nDCG@k, MRR, recall y κ.</p>
      <p><a class="boton" href="/evaluar">Empezar a evaluar</a></p>
    </section>`;
  }
  return `<section class="panel vacio">
    <h2>Los juicios no produjeron métricas</h2>
    <p>Hay juicios guardados, pero ninguno coincide con los filtros o con las consultas registradas.
    Revisá que la consulta evaluada se haya buscado con el mismo índice.</p>
  </section>`;
}

function tablaPorConsulta(informe) {
  const k = informe.k;
  const filas = informe.porConsulta;
  if (!filas.length) return "";
  const cols = [`P@${k}`, `Recall@${k}`, "AP", `nDCG@${k}`, "RR"];
  const cuerpo = filas
    .map((f) => {
      const enlace = `/evaluar?q=${encodeURIComponent(f.consulta)}&indice=${encodeURIComponent(f.indice)}`;
      return `<tr>
        <td><a href="${esc(enlace)}" title="Volver a evaluar esta consulta">${esc(f.consulta)}</a></td>
        <td>${esc(f.indice)}</td>
        <td>${f.juzgadosEnK}/${Math.min(k, f.resultados)}</td>
        ${cols.map((c) => `<td>${fmt(f[c])}</td>`).join("")}
      </tr>`;
    })
    .join("");
  return `<h2>Detalle por consulta</h2>
    <div class="panel"><table>
      <thead><tr><th>Consulta</th><th>Índice</th><th>Juzgados en el top ${k}</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${cuerpo}</tbody>
    </table>
    <p class="nota">Un documento recuperado que nadie juzgó cuenta como no relevante. «—» indica una métrica no
    definida, por ejemplo recall cuando la consulta no tiene ningún relevante conocido.</p></div>`;
}

function tablaPorEvaluador(informe) {
  const k = informe.k;
  if (informe.porEvaluador.length < 1) return "";
  const cols = [`P@${k}`, `Recall@${k}`, "AP", `nDCG@${k}`, "MRR"];
  const cuerpo = informe.porEvaluador
    .map(
      (e) => `<tr><td>${esc(e.evaluador)}</td><td>${e.consultas}</td><td>${e.juicios}</td>${cols
        .map((c) => `<td>${fmt(e[c] ?? e[c === "AP" ? "MAP" : c])}</td>`)
        .join("")}</tr>`,
    )
    .join("");
  return `<h2>Métricas por evaluador</h2>
    <div class="panel"><table>
      <thead><tr><th>Evaluador</th><th>Consultas</th><th>Juicios</th>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${cuerpo}</tbody>
    </table>
    <p class="nota">Sirve para comparar criterios: cada fila evalúa el ranking usando solo los juicios de esa persona.</p></div>`;
}

function bloqueKappa(uso) {
  if (uso.evaluadores < 2) {
    return `<section class="panel">
      <h2>Acuerdo entre evaluadores</h2>
      <p class="nota">κ de Cohen necesita al menos dos evaluadores que hayan juzgado los mismos documentos.
      Ahora hay ${uso.evaluadores} evaluador${uso.evaluadores === 1 ? "" : "es"}.</p>
    </section>`;
  }
  const filas = uso.paresKappa.length
    ? uso.paresKappa
        .map(
          (p) =>
            `<tr><td>${esc(p.evaluadores.join(" · "))}</td><td>${p.documentosComunes}</td><td>${fmt(p.kappa, 3)}</td><td>${
              p.kappa === null ? "—" : p.kappa <= 0.2 ? "leve" : p.kappa <= 0.4 ? "aceptable" : p.kappa <= 0.6 ? "moderado" : "sustancial"
            }</td></tr>`,
        )
        .join("")
    : `<tr><td colspan="4">Sin documentos juzgados por dos personas a la vez.</td></tr>`;
  return `<h2>Acuerdo entre evaluadores (κ de Cohen)</h2>
    <div class="panel"><table>
      <thead><tr><th>Par de evaluadores</th><th>Documentos en común</th><th>κ</th><th>Lectura</th></tr></thead>
      <tbody>${filas}</tbody>
    </table>
    <p class="nota">Promedio: <strong>${fmt(uso.kappaPromedio, 3)}</strong> (${esc(uso.kappaLectura)}).
    Escala de Landis y Koch (1977). El juicio binario es «relevante» = nota 1 o 2.</p></div>`;
}

function panelUso(uso) {
  const cifras = [
    ["Consultas evaluadas", `${uso.consultasConJuicios} de ${uso.consultasRegistradas} registradas`],
    ["Evaluadores", String(uso.evaluadores)],
    ["Juicios guardados", String(uso.juicios)],
    ["Juicios por consulta", fmt(uso.juiciosPorConsultaMedia, 1)],
    ["Documentos relevantes", pct(uso.porcentajeRelevantes)],
    [`Cobertura del top ${uso.k}`, pct(uso.coberturaEnK)],
    ["Búsquedas registradas", String(uso.busquedasRegistradas)],
    ["Último juicio", uso.ultimoJuicio ? esc(uso.ultimoJuicio.replace("T", " ").slice(0, 16)) : "—"],
  ];
  return `<h2>Estadísticas de uso</h2>
    <div class="cards">${cifras
      .map(([a, b]) => `<div class="card"><span>${esc(a)}</span><b class="texto">${b}</b></div>`)
      .join("")}</div>`;
}

function listaSinJuicios(informe) {
  if (!informe.sinJuicios.length) return "";
  const items = informe.sinJuicios
    .slice(0, 12)
    .map((c) => {
      const enlace = `/evaluar?q=${encodeURIComponent(c.consulta)}&indice=${encodeURIComponent(c.indice)}`;
      return `<li><a href="${esc(enlace)}">${esc(c.consulta)}</a>
        <span>${esc(c.indice)} · ${c.veces} búsqueda${c.veces === 1 ? "" : "s"}${c.total !== null ? ` · ${c.total} resultados` : ""}</span></li>`;
    })
    .join("");
  return `<h2>Consultas reales sin juicios</h2>
    <div class="panel"><p class="nota" style="margin-top:0">Salen de <code>salida/consultas.jsonl</code>: son las
    búsquedas que ya hizo el grupo. Marcarlas es lo que permite calcular métricas sobre usos reales.</p>
    <ol class="lista-consultas">${items}</ol></div>`;
}

/**
 * Devuelve el HTML completo de la página de métricas.
 *
 * @param {object} informe resultado de generarMetricas()
 * @param {object} [opciones]
 * @param {string[]} [opciones.indices] índices para el filtro
 * @param {string[]} [opciones.evaluadores] evaluadores para el filtro
 * @param {number} [opciones.juiciosTotales] juicios guardados sin filtrar
 */
export function renderMetricas(informe, { indices = [], evaluadores = [], juiciosTotales = 0 } = {}) {
  const k = informe.k;
  const g = informe.global;
  const filas = informe.porConsulta;
  const uso = informe.uso;
  const hayFilas = filas.length > 0;

  const tarjetas = [
    tarjeta(`P@${k}`, g[`P@${k}`], `Precisión P@${k}`),
    tarjeta("MAP", g.MAP, "MAP"),
    tarjeta(`nDCG@${k}`, g[`nDCG@${k}`], `nDCG@${k}`),
    tarjeta("MRR", g.MRR, "MRR"),
    tarjeta(`Recall@${k}`, g[`Recall@${k}`], `Recall@${k}`),
  ].join("");

  const serieMetricas = [
    { nombre: `P@${k}`, valores: filas.map((f) => f[`P@${k}`]) },
    { nombre: "AP", valores: filas.map((f) => f.AP) },
    { nombre: `nDCG@${k}`, valores: filas.map((f) => f[`nDCG@${k}`]) },
  ];
  const leyenda = serieMetricas
    .map((s, i) => `<span><i style="background:${PALETA[i]}"></i>${esc(s.nombre)}</span>`)
    .join("");

  const distribucion = {
    "0 · No relevante": uso.distribucion?.[0] ?? 0,
    "1 · Relevante": uso.distribucion?.[1] ?? 0,
    "2 · Muy relevante": uso.distribucion?.[2] ?? 0,
  };

  const avisos = informe.avisos.map((a) => `<p class="aviso">${esc(a)}</p>`).join("");
  const revision = informe.generado.replace("T", " ").slice(0, 19);

  return `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Métricas de evaluación — Encuadre</title>
<link rel="icon" href="/icono.svg" type="image/svg+xml">
<style>${CSS}</style>
</head>
<body>
<header class="cabecera">
  <a class="marca" href="/" aria-label="Encuadre, volver al buscador">Encuadre</a>
  <nav aria-label="Secciones">
    <ul class="menu">
      <li><a href="/">Buscar</a></li>
      <li><a href="/evaluar">Evaluar</a></li>
      <li><a href="/metricas" aria-current="page">Métricas</a></li>
    </ul>
  </nav>
</header>
<main>
  <h1>Métricas de evaluación</h1>
  <p class="sub">Calculadas con ${uso.juicios} juicio${uso.juicios === 1 ? "" : "s"} de
  ${uso.evaluadores} evaluador${uso.evaluadores === 1 ? "" : "es"} y ${uso.busquedasRegistradas} búsquedas del registro.
  Los documentos sin juzgar cuentan como no relevantes. Actualizado: ${esc(revision)} UTC.</p>
  ${controles(informe, { indices, evaluadores, juiciosTotales })}
  ${avisos}

  ${hayFilas ? `<div class="cards">${tarjetas}</div>
  <p class="nota">Promedios sobre ${filas.length} consulta${filas.length === 1 ? "" : "s"} con juicios.
  Recall@${k} se mide contra los relevantes que el pool de juicios conoce; si solo se juzgó el top ${k}, tiende a 1
  por construcción y conviene leerlo junto con la cobertura.</p>

  <h2>Precisión promedio según la posición</h2>
  <div class="panel">${svgLinea(informe.curvaPrecision, "posición en el ranking")}</div>

  <h2>P@${k}, AP y nDCG@${k} por consulta</h2>
  <div class="panel"><div class="leyenda">${leyenda}</div>${svgBarras(
    filas.map((f) => f.consulta),
    serieMetricas,
  )}</div>` : estadoVacio(informe, { juiciosTotales })}

  ${tablaPorConsulta(informe)}
  ${tablaPorEvaluador(informe)}

  ${panelUso(uso)}
  <h2>Juicios por calificación</h2>
  <div class="panel">${svgBarrasHorizontales(distribucion, {
    etiqueta: "Juicios por calificación",
    colores: { "0 · No relevante": GRIS, "1 · Relevante": PALETA[0], "2 · Muy relevante": PALETA[2] },
  })}</div>

  <h2>Juicios por evaluador</h2>
  <div class="panel">${svgBarrasHorizontales(uso.juiciosPorEvaluador, { etiqueta: "Juicios por evaluador" })}</div>

  ${bloqueKappa(uso)}
  ${listaSinJuicios(informe)}

  <h2>Cómo se calcula</h2>
  <div class="panel">
    <p class="nota" style="margin-top:0">P@k y recall usan los k primeros puestos del ranking que realmente se mostró
    para esa consulta e índice (reconstruido con <code>salida/consultas.jsonl</code>). AP promedia la precisión en cada
    acierto y divide entre los relevantes conocidos; su media es MAP. nDCG usa ganancia lineal 0/1/2 con descuento
    logarítmico (Järvelin y Kekäläinen, 2002) y un ideal armado con las notas conocidas. MRR usa el primer relevante.
    Si una consulta no tiene registro de búsqueda, el ranking se reconstruye con el rango guardado en el juicio y se avisa.
    Fórmulas: Manning, Raghavan y Schütze (2008, cap. 8); κ: Cohen (1960) con la escala de Landis y Koch (1977).</p>
  </div>
</main>
<footer class="pie">
  <p>Proyecto 3 del curso IC-8060 Recuperación de Información Textual, Tecnológico de Costa Rica.
  Los juicios se guardan en <code>Progra02/salida/juicios.json</code>.</p>
</footer>
</body>
</html>`;
}

/** Página mínima de error para problemas al leer los datos. */
export function renderErrorMetricas(mensaje) {
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Métricas — error</title><style>${CSS}</style></head>
<body><header class="cabecera"><a class="marca" href="/">Encuadre</a></header>
<main><h1>No se pudieron calcular las métricas</h1>
<p class="aviso">${esc(mensaje)}</p>
<p>Revisá <code>Progra02/salida/juicios.json</code> (debe ser un arreglo JSON) y volvé a intentar.</p>
<p><a class="boton" href="/metricas">Reintentar</a> <a href="/evaluar">Ir a evaluar</a></p></main></body></html>`;
}
