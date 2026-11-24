"""Calcula las estadísticas de los tres índices .idx y las deja listas para el informe.

Lee salida/indice_{minimo,estandar,avanzado}.idx (y salida/benchmark_indices.json, si existe) y escribe:
  tablas/*.tex   tablas que el informe incluye con \\input
  datos/*.csv    series para las gráficas (pgfplots): Zipf, Heaps y distribución de df
No usa bibliotecas externas. Lee todos los postings, así que con índices de millones
de documentos conviene correrlo sobre una muestra (decenas de miles de documentos).

    uv run python scripts/estadisticas_indices.py
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from pathlib import Path

from indexador.benchmark import huella_normalizacion
from indexador.buscador import BuscadorVectorial
from indexador.configuracion import ConfiguracionIndexador
from indexador.lector_binario import LectorIndiceBinario

RAIZ = Path(__file__).resolve().parents[1]
POLITICAS = ("minimo", "estandar", "avanzado")
CONSULTAS = [
    "película", "movie", "director", "cineasta", "guion", "screenplay",
    "soundtrack", "Quentin Tarantino", "1994", "Star Wars", "anime", "science fiction",
]


def entero(n: int) -> str:
    return f"{n:,}".replace(",", r"\,")


def dec(x: float, d: int = 2) -> str:
    return f"{x:.{d}f}".replace(".", ",")


def ajuste_log(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Mínimos cuadrados de log10(y) = a + b log10(x); devuelve (a, b)."""
    lx, ly = [math.log10(x) for x in xs], [math.log10(y) for y in ys]
    mx, my = statistics.fmean(lx), statistics.fmean(ly)
    b = sum((p - mx) * (q - my) for p, q in zip(lx, ly, strict=True)) / sum((p - mx) ** 2 for p in lx)
    return my - b * mx, b


def muestreo_log(n: int, puntos: int = 120) -> list[int]:
    """Índices (0-based) espaciados en escala logarítmica, sin repetir."""
    return sorted({min(n - 1, int(10 ** (i * math.log10(n) / (puntos - 1))) - 1) for i in range(puntos)} | {0, n - 1})


def analizar(idx: Path) -> dict:
    with LectorIndiceBinario(idx) as lector:
        e = lector.encabezado
        docs = lector.iterar_documentos()
        terminos = lector.cargar_diccionario()
        cf, primera = {}, {}
        for t in terminos:
            postings = lector.leer_postings(t)
            cf[t.termino] = sum(p.tf for p in postings)
            primera[t.termino] = min(p.doc_id for p in postings)
        tam = idx.stat().st_size
        tokens_doc = [d.num_tokens for d in docs]
        total_tokens = sum(tokens_doc)
        dfs = sorted((t.df for t in terminos), reverse=True)
        # Zipf: frecuencia de colección contra ranking
        ordenado = sorted(cf.values(), reverse=True)
        r_ini, r_fin = 10, min(5000, len(ordenado))
        a_z, b_z = ajuste_log(list(range(r_ini, r_fin + 1)), [ordenado[r - 1] for r in range(r_ini, r_fin + 1)])
        # Heaps: vocabulario acumulado contra tokens acumulados, documento a documento
        nuevos = [0] * len(docs)
        for d in primera.values():
            nuevos[d] += 1
        acum_t = acum_v = 0
        heaps = []
        for n_tok, nuevos_doc in zip(tokens_doc, nuevos, strict=True):
            acum_t += n_tok
            acum_v += nuevos_doc
            heaps.append((acum_t, acum_v))
        puntos = [(t, v) for t, v in heaps if t >= 10_000 and v > 0]
        a_h, b_h = ajuste_log([t for t, _ in puntos], [v for _, v in puntos])
        top = sorted(terminos, key=lambda t: -t.df)[:10]
        return {
            "docs": e.total_docs, "vocab": e.total_terminos, "postings": e.total_postings, "tam": tam,
            "flags": e.flags_normalizacion,
            "sec_docs": e.offset_diccionario - e.offset_docs,
            "sec_dic": e.offset_postings - e.offset_diccionario,
            "sec_post": tam - e.offset_postings,
            "tokens": total_tokens, "tokens_doc_med": statistics.median(tokens_doc), "tokens_doc_max": max(tokens_doc),
            "hapax": sum(1 for d in dfs if d == 1), "df_max": dfs[0], "df_prom": statistics.fmean(dfs),
            "df_mitad": sum(1 for d in dfs if d >= e.total_docs / 2),
            "zipf_a": a_z, "zipf_b": b_z, "zipf_rango": (r_ini, r_fin),
            "heaps_k": 10**a_h, "heaps_b": b_h,
            "zipf_csv": [(i + 1, ordenado[i]) for i in muestreo_log(len(ordenado))],
            "heaps_csv": [heaps[i] for i in muestreo_log(len(heaps), 80)],
            "top": [(t.termino, t.df) for t in top],
            "df_hist": histograma_df(dfs),
        }


def histograma_df(dfs: list[int]) -> list[tuple[str, int]]:
    cortes = [1, 2, 5, 10, 50, 100, 250, 500]
    etiquetas, cuentas = [], []
    previo = 0
    for c in cortes:
        etiquetas.append("1" if c == 1 else f"{previo + 1}--{c}")
        cuentas.append(sum(1 for d in dfs if previo < d <= c))
        previo = c
    etiquetas.append(f"$>${cortes[-1]}")
    cuentas.append(sum(1 for d in dfs if d > cortes[-1]))
    return list(zip(etiquetas, cuentas, strict=True))


def consultas(idx: Path) -> dict[str, tuple[list[str], int]]:
    res = {}
    with BuscadorVectorial.desde_archivo(idx) as b:
        for q in CONSULTAS:
            r = b.buscar(q, top=10**9, sugerir=False)
            res[q] = (r.tokens, len(r.resultados))
    return res


def texto_coleccion(politica: str, n: int) -> tuple[int, list[tuple[str, int, int]]] | None:
    """Bytes de texto crudo y reparto por dominio de los primeros n documentos, si la colección está a mano."""
    cfg = ConfiguracionIndexador.cargar_desde_archivo(RAIZ / "configuraciones" / f"{politica}.conf")
    bd = (RAIZ / cfg.rutas.base_datos).resolve()
    if not bd.is_file():
        return None
    with sqlite3.connect(f"file:{bd}?mode=ro", uri=True) as con:
        filas = con.execute(
            "SELECT dominio, COUNT(*), SUM(tamano_texto_bytes) FROM "
            "(SELECT dominio, tamano_texto_bytes FROM documentos ORDER BY id LIMIT ?) GROUP BY dominio ORDER BY 2 DESC",
            (n,),
        ).fetchall()
    return sum(f[2] for f in filas), filas


def escribir(ruta: Path, lineas: list[str]) -> None:
    ruta.parent.mkdir(exist_ok=True)
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default=str(RAIZ / "salida"))
    args = ap.parse_args()
    sal = Path(args.salida)
    S = {p: analizar(sal / f"indice_{p}.idx") for p in POLITICAS}
    Q = {p: consultas(sal / f"indice_{p}.idx") for p in POLITICAS}
    bench = {}
    if (sal / "benchmark_indices.json").is_file():
        bench = {m["nombre_politica"]: m for m in json.loads((sal / "benchmark_indices.json").read_text(encoding="utf-8"))}
    T = RAIZ / "tablas"
    D = RAIZ / "datos"
    nombre = {"minimo": "mínima", "estandar": "intermedia", "avanzado": "agresiva"}

    # --- vigencia: ¿el benchmark se midió con las políticas y recursos actuales? ---
    viejas = [
        p for p in POLITICAS
        if bench.get(p, {}).get("huella_normalizacion")
        != huella_normalizacion(ConfiguracionIndexador.cargar_desde_archivo(RAIZ / "configuraciones" / f"{p}.conf"))
    ]
    aviso = (
        r"\newcommand{\AvisoCifras}{\par\smallskip\noindent\fbox{\parbox{0.94\linewidth}{\small\textbf{Cifras desactualizadas.} "
        r"Los índices de la política " + ", ".join(viejas) + r" se construyeron antes de los últimos cambios en el normalizador o en "
        r"\texttt{recursos/}. Ejecute \texttt{scripts/regenerar\_todo.sh} y recompile.}}\par\smallskip}"
        if viejas else r"\newcommand{\AvisoCifras}{}"
    )
    primera = bench.get("minimo", {})
    meta = f"{primera['fecha'][:10]}; {primera['maquina']}" if primera.get("fecha") else ""
    escribir(T / "vigencia.tex", [aviso, r"\newcommand{\MedicionMeta}{" + meta.replace("_", r"\_") + "}"])

    # --- tabla principal ---
    filas = []
    for etiqueta, f in [
        ("Documentos indexados", lambda s, p: entero(s["docs"])),
        ("Tokens indexados (tras normalizar)", lambda s, p: entero(s["tokens"])),
        ("Vocabulario $|V|$", lambda s, p: entero(s["vocab"])),
        ("Postings", lambda s, p: entero(s["postings"])),
        ("Términos con $df=1$ (\\%)", lambda s, p: dec(100 * s["hapax"] / s["vocab"], 1)),
        ("$df$ promedio", lambda s, p: dec(s["df_prom"], 2)),
        ("Tamaño del índice (MB)", lambda s, p: dec(s["tam"] / 2**20, 2)),
        ("Bytes por documento", lambda s, p: entero(round(s["tam"] / s["docs"]))),
        ("Bytes por posting", lambda s, p: dec(s["tam"] / s["postings"], 1)),
        ("Tiempo de indexación (s)", lambda s, p: dec(bench[p]["tiempo_segundos"], 2) if p in bench else "--"),
        ("Memoria pico (MB)", lambda s, p: dec(bench[p]["memoria_pico_mb"], 1) if p in bench else "--"),
    ]:
        filas.append(f"{etiqueta} & " + " & ".join(f(S[p], p) for p in POLITICAS) + r" \\")
    escribir(T / "indices.tex", filas)

    # --- reducciones frente a la política mínima ---
    m = S["minimo"]
    escribir(T / "reducciones.tex", [
        f"{nombre[p].capitalize()} & {dec(100 * (1 - S[p]['vocab'] / m['vocab']), 1)} & "
        f"{dec(100 * (1 - S[p]['postings'] / m['postings']), 1)} & {dec(100 * (1 - S[p]['tam'] / m['tam']), 1)} & "
        f"{dec(100 * (1 - S[p]['tokens'] / m['tokens']), 1)} \\\\"
        for p in ("estandar", "avanzado")
    ])

    # --- secciones del archivo ---
    escribir(T / "secciones.tex", [
        f"{nombre[p].capitalize()} & {entero(64)} & {entero(s['sec_docs'])} & {entero(s['sec_dic'])} & {entero(s['sec_post'])} & "
        f"{dec(100 * s['sec_post'] / s['tam'], 1)} \\\\"
        for p, s in S.items()
    ])

    # --- ajustes Zipf / Heaps ---
    escribir(T / "ajustes.tex", [
        f"{nombre[p].capitalize()} & {dec(-s['zipf_b'], 2)} & {dec(s['heaps_k'], 1)} & {dec(s['heaps_b'], 2)} \\\\"
        for p, s in S.items()
    ])
    r_ini, r_fin = S["minimo"]["zipf_rango"]
    escribir(T / "zipf_rango.tex", [r"\newcommand{\ZipfRango}{" + f"{r_ini}--{entero(r_fin)}" + "}"])

    # --- términos más frecuentes (por df) ---
    n_top = len(S["minimo"]["top"])
    escribir(T / "top_df.tex", [
        f"{i + 1} & " + " & ".join(f"\\texttt{{{S[p]['top'][i][0].replace('_', chr(92) + '_')}}} ({S[p]['top'][i][1]})" for p in POLITICAS) + r" \\"
        for i in range(n_top)
    ])

    # --- consultas ---
    escribir(T / "consultas.tex", [
        f"{q} & " + " & ".join(
            f"{entero(Q[p][q][1])}" for p in POLITICAS
        ) + r" \\"
        for q in CONSULTAS
    ])
    escribir(T / "consultas_tokens.tex", [
        f"{q} & " + " & ".join(
            r"\texttt{" + " ".join(Q[p][q][0]).replace("_", r"\_").replace("<", "{<}").replace(">", "{>}") + "}" if Q[p][q][0] else "--"
            for p in POLITICAS
        ) + r" \\"
        for q in CONSULTAS
    ])

    # --- histograma de df (para pgfplots: etiquetas y conteos) ---
    for p, s in S.items():
        escribir(D / f"df_{p}.csv", ["rango,terminos"] + [f"{a.replace('$>$', '>')},{c}" for a, c in s["df_hist"]])
        escribir(D / f"zipf_{p}.csv", ["rango,frecuencia"] + [f"{r},{f}" for r, f in s["zipf_csv"]])
        escribir(D / f"heaps_{p}.csv", ["tokens,vocabulario"] + [f"{t},{v}" for t, v in s["heaps_csv"]])

    # --- composición de la muestra y razón índice/texto, solo si la colección está disponible ---
    col = texto_coleccion("minimo", S["minimo"]["docs"])
    if col:
        total, por_dominio = col
        escribir(T / "dominios.tex", [
            f"\\texttt{{{d}}} & {entero(n)} & {dec(100 * n / S['minimo']['docs'], 1)} & {dec(b / 2**20, 2)} \\\\" for d, n, b in por_dominio
        ])
        escribir(T / "razon_texto.tex", [
            f"{nombre[p].capitalize()} & {dec(total / 2**20, 2)} & {dec(S[p]['tam'] / 2**20, 2)} & {dec(100 * S[p]['tam'] / total, 1)} \\\\"
            for p in POLITICAS
        ])
    else:
        for f in ("dominios.tex", "razon_texto.tex"):
            (T / f).unlink(missing_ok=True)

    print("Estadísticas escritas en tablas/ y datos/", "(cifras DESACTUALIZADAS: " + ", ".join(viejas) + ")" if viejas else "(vigentes)")
    for p, s in S.items():
        print(f"{p:9} |V|={s['vocab']:>7,} postings={s['postings']:>8,} tam={s['tam'] / 2**20:6.2f} MB "
              f"zipf={-s['zipf_b']:.2f} heaps k={s['heaps_k']:.1f} b={s['heaps_b']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
