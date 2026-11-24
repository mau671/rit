"""Página de métricas: una función que devuelve HTML autocontenido (gráficos en SVG, sin JS ni CDN).

La pueden servir desde el backend de búsqueda con ``render_html(informe, uso)`` o
guardarse como archivo estático con ``python -m metricas``.
"""

from __future__ import annotations

from html import escape

PALETA = ["#2f6fdf", "#e08a1e", "#1f9d8b"]  # series: azul, naranja, verde azulado

DESCRIPCION = {
    "MAP": "Calidad del orden en toda la lista; premia poner los relevantes arriba.",
    "MRR": "Qué tan arriba aparece el primer relevante.",
    "nDCG": "Orden con relevancia graduada y descuento logarítmico por posición.",
    "P@": "Fracción de relevantes entre los primeros resultados.",
    "Recall@": "Fracción de los relevantes conocidos que llegaron al top.",
}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--tx:#1b1f27;--mut:#6a7180;--bd:#e3e6ec;--ac:#2f6fdf}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1a1f28;--tx:#e8ebf1;--mut:#9aa3b2;--bd:#2a313d;--ac:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:1.9rem;margin:0 0 4px}h2{font-size:1.2rem;margin:36px 0 12px}
.sub{color:var(--mut);margin:0 0 24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px}
.card b{display:block;font-size:1.9rem;font-variant-numeric:tabular-nums;color:var(--ac)}
.card span{color:var(--mut);font-size:.85rem}.card strong{font-size:.95rem}
.panel{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--bd);text-align:right}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--mut);font-weight:600}svg{max-width:100%;height:auto;display:block}
svg text{fill:var(--mut);font:12px system-ui,sans-serif}svg .eje{stroke:var(--bd)}
.leyenda{display:flex;gap:16px;margin:0 0 8px;font-size:.85rem;color:var(--mut)}
.leyenda i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.nota{color:var(--mut);font-size:.85rem;margin-top:10px}
"""


def _n(v: float | None, dec: int = 3) -> str:
    return "—" if v is None else f"{v:.{dec}f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f} %"


def _descripcion(nombre: str) -> str:
    for clave, texto in DESCRIPCION.items():
        if nombre.startswith(clave):
            return texto
    return ""


def _barras_agrupadas(etiquetas: list[str], series: dict[str, list[float | None]]) -> str:
    ancho, alto, mi, md, ms, mb = 900, 300, 40, 10, 10, 34
    w, h = ancho - mi - md, alto - ms - mb
    grupo = w / max(1, len(etiquetas))
    barra = min(22.0, grupo * 0.8 / max(1, len(series)))
    p = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Métricas por consulta">']
    for t in (0, 0.25, 0.5, 0.75, 1):
        y = ms + h * (1 - t)
        p.append(f'<line class="eje" x1="{mi}" x2="{ancho - md}" y1="{y:.1f}" y2="{y:.1f}"/>')
        p.append(f'<text x="{mi - 6}" y="{y + 4:.1f}" text-anchor="end">{t:g}</text>')
    for i, etq in enumerate(etiquetas):
        x0 = mi + i * grupo + (grupo - barra * len(series)) / 2
        for s, (nombre, vals) in enumerate(series.items()):
            v = vals[i] or 0.0
            bh = h * v
            p.append(
                f'<rect x="{x0 + s * barra:.1f}" y="{ms + h - bh:.1f}" width="{barra - 2:.1f}" height="{bh:.1f}" '
                f'rx="2" fill="{PALETA[s % 3]}"><title>{escape(etq)} · {escape(nombre)}: {_n(vals[i])}</title></rect>'
            )
        p.append(f'<text x="{mi + i * grupo + grupo / 2:.1f}" y="{alto - 12}" text-anchor="middle">{escape(etq)}</text>')
    p.append("</svg>")
    return "".join(p)


def _linea(valores: list[float | None], etiqueta_x: str) -> str:
    ancho, alto, mi, md, ms, mb = 900, 276, 40, 20, 14, 52
    w, h = ancho - mi - md, alto - ms - mb
    n = len(valores)
    xs = [mi + (w * i / max(1, n - 1)) for i in range(n)]
    ys = [ms + h * (1 - (v or 0.0)) for v in valores]
    p = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Precisión promedio por posición">']
    for t in (0, 0.25, 0.5, 0.75, 1):
        y = ms + h * (1 - t)
        p.append(f'<line class="eje" x1="{mi}" x2="{ancho - md}" y1="{y:.1f}" y2="{y:.1f}"/>')
        p.append(f'<text x="{mi - 6}" y="{y + 4:.1f}" text-anchor="end">{t:g}</text>')
    puntos = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    p.append(f'<polyline points="{puntos}" fill="none" stroke="{PALETA[0]}" stroke-width="2.5" stroke-linejoin="round"/>')
    for i, (x, y) in enumerate(zip(xs, ys), start=1):
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{PALETA[0]}"><title>P@{i}: {_n(valores[i - 1])}</title></circle>')
        p.append(f'<text x="{x:.1f}" y="{alto - 30}" text-anchor="middle">{i}</text>')
    p.append(f'<text x="{mi + w / 2:.1f}" y="{alto - 8}" text-anchor="middle">{escape(etiqueta_x)}</text></svg>')
    return "".join(p)


def _barras_simples(datos: dict[str, int]) -> str:
    if not datos:
        return "<p class='nota'>Sin juicios todavía.</p>"
    ancho, fila, mi = 900, 30, 90
    tope = max(datos.values()) or 1
    alto = fila * len(datos) + 10
    p = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Juicios por evaluador">']
    for i, (nombre, v) in enumerate(datos.items()):
        y = 5 + i * fila
        largo = (ancho - mi - 70) * v / tope
        p.append(f'<text x="{mi - 8}" y="{y + 17}" text-anchor="end">{escape(nombre)}</text>')
        p.append(f'<rect x="{mi}" y="{y + 3}" width="{largo:.1f}" height="20" rx="3" fill="{PALETA[2]}"><title>{escape(nombre)}: {v}</title></rect>')
        p.append(f'<text x="{mi + largo + 6:.1f}" y="{y + 17}">{v}</text>')
    p.append("</svg>")
    return "".join(p)


def render_html(informe: dict, uso: dict, titulo: str = "Encuadre · Métricas de evaluación") -> str:
    k = informe["k"]
    g = informe["global"]
    filas = informe["por_consulta"]

    tarjetas = "".join(
        f'<div class="card"><span>{escape(n)}</span><b>{_n(v)}</b><span>{escape(_descripcion(n))}</span></div>'
        for n, v in g.items()
    )
    etqs = [f["query_id"] for f in filas]
    series = {f"P@{k}": [f[f"P@{k}"] for f in filas], "AP": [f["AP"] for f in filas], f"nDCG@{k}": [f[f"nDCG@{k}"] for f in filas]}
    leyenda = "".join(f'<span><i style="background:{PALETA[i]}"></i>{escape(n)}</span>' for i, n in enumerate(series))

    cols = [f"P@{k}", f"Recall@{k}", "AP", f"nDCG@{k}", "RR"]
    cuerpo = "".join(
        f"<tr><td>{escape(f['query_id'])}</td><td>{escape(f['consulta'])}</td><td>{f['juzgados']}</td>"
        f"<td>{f['relevantes_conocidos']}</td>" + "".join(f"<td>{_n(f[c])}</td>" for c in cols) + "</tr>"
        for f in filas
    )
    cabecera = "".join(f"<th>{c}</th>" for c in ["Id", "Consulta", "Juzgados", "Relevantes", *cols])

    cifras = [
        ("Consultas evaluadas", f"{uso['consultas_con_juicios']} de {uso['consultas']}"),
        ("Evaluadores", str(uso["evaluadores"])),
        ("Juicios guardados", str(uso["juicios"])),
        ("Juicios por consulta (media)", _n(uso["juicios_por_consulta_media"], 1)),
        ("Documentos marcados relevantes", _pct(uso["porcentaje_relevantes"])),
        (f"Cobertura de juicios en el top {k}", _pct(uso["cobertura_en_k"])),
        ("Acuerdo entre evaluadores (κ de Cohen)", f"{_n(uso['kappa_promedio'], 2)} · {uso['kappa_lectura']}"),
    ]
    uso_html = "".join(f'<div class="card"><span>{escape(a)}</span><b style="font-size:1.4rem">{escape(b)}</b></div>' for a, b in cifras)

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(titulo)}</title><style>{CSS}</style></head>
<body><main>
<h1>{escape(titulo)}</h1>
<p class="sub">Promedios sobre {len(filas)} consultas con juicios de relevancia. Los documentos sin juzgar cuentan como no relevantes.</p>
<div class="cards">{tarjetas}</div>
<p class="nota">Recall@{k} es relativo al conjunto de documentos juzgados: si solo se juzga el top {k}, vale 1 por construcción.</p>

<h2>Precisión promedio según la posición</h2>
<div class="panel">{_linea(informe["curva_precision"], "posición en el ranking")}</div>

<h2>Métricas por consulta</h2>
<div class="panel"><div class="leyenda">{leyenda}</div>{_barras_agrupadas(etqs, series)}</div>

<h2>Detalle por consulta</h2>
<div class="panel"><table><thead><tr>{cabecera}</tr></thead><tbody>{cuerpo}</tbody></table>
<p class="nota">«—» indica métrica no definida (la consulta no tiene relevantes conocidos).</p></div>

<h2>Estadísticas de uso</h2>
<div class="cards">{uso_html}</div>
<h2>Juicios por evaluador</h2>
<div class="panel">{_barras_simples(uso["juicios_por_evaluador"])}</div>
</main></body></html>
"""
