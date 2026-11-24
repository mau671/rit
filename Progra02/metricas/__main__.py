"""CLI: genera la página de métricas y, opcionalmente, las tablas/CSV del documento.

    python -m metricas -b busquedas.json -j juicios.json            # escribe salida/metricas.html
    python -m metricas -b busquedas.json -j juicios.json --documento documento
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .datos import cargar_json, evaluar
from .pagina import render_html
from .uso import estadisticas_uso

RAIZ = Path(__file__).resolve().parent.parent


def _tex(s: str) -> str:
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_")):
        s = s.replace(a, b)
    return s


def _f(v: float | None) -> str:
    return "--" if v is None else f"{v:.3f}"


def exportar_documento(informe: dict, uso: dict, destino: Path) -> None:
    """Escribe filas para ``\\filas{...}`` (estilo Progra01) y CSV para pgfplots."""
    k = informe["k"]
    tablas, datos = destino / "tablas", destino / "datos"
    tablas.mkdir(parents=True, exist_ok=True)
    datos.mkdir(parents=True, exist_ok=True)

    (tablas / "metricas_global.tex").write_text(
        "".join(f"{_tex(n)} & {_f(v)} \\\\\n" for n, v in informe["global"].items()), encoding="utf-8"
    )
    cols = [f"P@{k}", f"Recall@{k}", "AP", f"nDCG@{k}", "RR"]
    (tablas / "metricas_consulta.tex").write_text(
        "".join(f"{f['query_id']} & " + " & ".join(_f(f[c]) for c in cols) + " \\\\\n" for f in informe["por_consulta"]),
        encoding="utf-8",
    )
    (tablas / "consultas.tex").write_text(
        "".join(f"{f['query_id']} & {_tex(f['consulta'])} \\\\\n" for f in informe["por_consulta"]), encoding="utf-8"
    )
    pct = lambda v: "--" if v is None else f"{v * 100:.1f}\\,\\%"  # noqa: E731
    filas = [
        ("Consultas evaluadas", f"{uso['consultas_con_juicios']} de {uso['consultas']}"),
        ("Evaluadores", str(uso["evaluadores"])),
        ("Juicios guardados", str(uso["juicios"])),
        ("Juicios por consulta (media)", f"{uso['juicios_por_consulta_media']:.1f}"),
        ("Documentos marcados relevantes", pct(uso["porcentaje_relevantes"])),
        (f"Cobertura de juicios en el top {k}", pct(uso["cobertura_en_k"])),
        ("$\\kappa$ de Cohen promedio", f"{_f(uso['kappa_promedio'])} ({uso['kappa_lectura']})"),
    ]
    (tablas / "uso.tex").write_text("".join(f"{a} & {b} \\\\\n" for a, b in filas), encoding="utf-8")

    with (datos / "curva_precision.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["posicion", "precision"])
        w.writerows((i, f"{v:.4f}") for i, v in enumerate(informe["curva_precision"], start=1) if v is not None)
    with (datos / "por_consulta.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["n", "query_id", "p", "ap", "ndcg"])
        w.writerows(
            (i, f["query_id"], f[f"P@{k}"], f["AP"] or 0, f[f"nDCG@{k}"] or 0)
            for i, f in enumerate(informe["por_consulta"], start=1)
        )
    with (datos / "juicios_por_evaluador.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["n", "evaluador", "juicios"])
        w.writerows((i, e, n) for i, (e, n) in enumerate(uso["juicios_por_evaluador"].items(), start=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--busquedas", required=True, help="busquedas.json del buscador")
    ap.add_argument("-j", "--juicios", required=True, help="juicios.json de la interfaz de evaluación")
    ap.add_argument("-k", type=int, default=10, help="profundidad de P@k, Recall@k y nDCG@k")
    ap.add_argument("--html", default=RAIZ / "salida" / "metricas.html")
    ap.add_argument("--documento", help="carpeta del documento: escribe tablas/ y datos/ para LaTeX")
    a = ap.parse_args()

    busquedas, juicios = cargar_json(a.busquedas), cargar_json(a.juicios)
    informe = evaluar(busquedas, juicios, a.k)
    uso = estadisticas_uso(busquedas, juicios, a.k)

    html = Path(a.html)
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(render_html(informe, uso), encoding="utf-8")
    print(f"Página: {html}")
    if a.documento:
        exportar_documento(informe, uso, Path(a.documento))
        print(f"Tablas y datos para LaTeX: {a.documento}")
    print({n: (None if v is None else round(v, 3)) for n, v in informe["global"].items()})


if __name__ == "__main__":
    main()
