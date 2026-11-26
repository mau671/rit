"""Compara el indexador propio (Python) con el de Lucene bajo las mismas condiciones.

Cada corrida se mide desde afuera, en un proceso aparte, con lo que reporta el
sistema operativo para ese proceso y sus hijos (tiempo de reloj, CPU y pico de
memoria residente). Así Python y la JVM se miden con la misma vara; la memoria
que el propio indexador reporta por dentro no sirve para comparar.

El indexador de Lucene se invoca con la plantilla --lucene-cmd, donde se
sustituyen {politica}, {config}, {salida} (carpeta del índice) y {limite}.
Debe escribir el índice en {salida} y, opcionalmente, imprimir como última línea
de stdout un JSON {"docs": N, "terminos": V, "postings": P}.

    uv run python scripts/benchmark_comparativo.py --limite 500 --repeticiones 3 \
        --lucene-cmd "java -jar ../lucene/target/indexador-lucene.jar indexar --config {config} --salida {salida} --limite {limite}"

Resultados: salida/bench/comparativo.json y tablas/comparativo_externo.tex.

La tabla comparativa del informe (tablas/comparativo.tex) ya no la escribe este
script: la genera scripts/comparativo_es.py con el benchmark de Elasticsearch.
"""

from __future__ import annotations

import argparse
import json
import resource
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[1]
RAIZ = PROYECTO.parent
POLITICAS = ("minimo", "estandar", "avanzado")


def medir(comando: list[str]) -> dict:
    """Modo interno: corre ``comando`` y devuelve lo que el SO midió para él."""
    t0 = time.perf_counter()
    res = subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, cwd=PROYECTO)
    pared = time.perf_counter() - t0
    uso = resource.getrusage(resource.RUSAGE_CHILDREN)
    ultima = res.stdout.strip().splitlines()[-1:] or [""]
    return {
        "codigo": res.returncode,
        "pared_s": pared,
        "cpu_s": uso.ru_utime + uso.ru_stime,
        "rss_pico_mb": uso.ru_maxrss / 1024,  # Linux: KiB
        "ultima_linea": ultima[0],
        "stderr": res.stderr[-500:],
    }


def tamano(ruta: Path) -> int:
    if ruta.is_file():
        return ruta.stat().st_size
    return sum(f.stat().st_size for f in ruta.rglob("*") if f.is_file())


def correr(comando: list[str]) -> dict:
    # ru_maxrss de RUSAGE_CHILDREN es un máximo acumulado; un intermediario por corrida lo aísla.
    res = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--medir", *comando],
        capture_output=True, text=True, check=False,
    )
    return json.loads(res.stdout)


def estadisticas_propio(idx: Path) -> dict:
    from indexador.lector_binario import LectorIndiceBinario

    with LectorIndiceBinario(idx) as lector:
        e = lector.encabezado
        return {"docs": e.total_docs, "terminos": e.total_terminos, "postings": e.total_postings}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limite", type=int, default=500, help="documentos a indexar (0 = toda la colección)")
    ap.add_argument("--repeticiones", type=int, default=3, help="corridas por política; se reporta la mediana")
    ap.add_argument("--lucene-cmd", help="plantilla del comando de Lucene; sin ella solo se mide el propio")
    ap.add_argument("--politicas", nargs="+", default=list(POLITICAS))
    ap.add_argument("--salida", default=str(PROYECTO / "salida" / "bench"))
    args = ap.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    resultados: list[dict] = []

    for politica in args.politicas:
        conf = PROYECTO / "configuraciones" / f"{politica}.conf"
        implementaciones = {"propio": None}
        if args.lucene_cmd:
            implementaciones["lucene"] = args.lucene_cmd
        for nombre, plantilla in implementaciones.items():
            destino = salida / f"{nombre}_{politica}{'.idx' if nombre == 'propio' else ''}"
            if nombre == "propio":
                comando = [sys.executable, "-m", "indexador.cli", "indexar", "--config", str(conf),
                           "--salida", str(destino), "--limite", str(args.limite)]
            else:
                comando = shlex.split(plantilla.format(politica=politica, config=conf, salida=destino, limite=args.limite))
            corridas = []
            for i in range(args.repeticiones):
                if destino.is_file():
                    destino.unlink()
                elif destino.is_dir():
                    import shutil

                    shutil.rmtree(destino)
                print(f"[{politica}] {nombre} corrida {i + 1}/{args.repeticiones} ...", flush=True)
                r = correr(comando)
                if r["codigo"] != 0:
                    sys.exit(f"Falló {nombre}/{politica}: código {r['codigo']}\n{r['stderr']}")
                r["tamano_bytes"] = tamano(destino)
                corridas.append(r)
            extra: dict = {}
            if nombre == "propio":
                extra = estadisticas_propio(destino)
            else:
                try:
                    extra = json.loads(corridas[-1]["ultima_linea"])
                except json.JSONDecodeError:
                    pass
            resultados.append({
                "implementacion": nombre,
                "politica": politica,
                "documentos": args.limite,
                "repeticiones": args.repeticiones,
                "pared_s": statistics.median(c["pared_s"] for c in corridas),
                "pared_s_min": min(c["pared_s"] for c in corridas),
                "pared_s_max": max(c["pared_s"] for c in corridas),
                "cpu_s": statistics.median(c["cpu_s"] for c in corridas),
                "rss_pico_mb": statistics.median(c["rss_pico_mb"] for c in corridas),
                "tamano_bytes": corridas[-1]["tamano_bytes"],
                **extra,
            })

    (salida / "comparativo.json").write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    escribir_tex(resultados)
    imprimir(resultados)
    return 0


def imprimir(rs: list[dict]) -> None:
    print(f"\n{'impl':<7} {'política':<9} {'pared (s)':>10} {'CPU (s)':>9} {'RAM pico (MB)':>14} {'tamaño (MB)':>12} {'|V|':>10} {'postings':>10}")
    for r in rs:
        print(f"{r['implementacion']:<7} {r['politica']:<9} {r['pared_s']:>10.2f} {r['cpu_s']:>9.2f} "
              f"{r['rss_pico_mb']:>14.1f} {r['tamano_bytes'] / 2**20:>12.2f} {r.get('terminos', '-'):>10} {r.get('postings', '-'):>10}")


def escribir_tex(rs: list[dict]) -> None:
    """Filas de tabla para \\input en tablas/comparativo_externo.tex (no el del informe)."""
    def n(x: object) -> str:
        return f"{x:,}".replace(",", r"\,") if isinstance(x, int) else "--"

    filas = [
        f"\\texttt{{{r['implementacion']}}} & \\texttt{{{r['politica']}}} & {r['pared_s']:.2f} & {r['cpu_s']:.2f} & "
        f"{r['rss_pico_mb']:.0f} & {r['tamano_bytes'] / 2**20:.2f} & {n(r.get('terminos'))} & {n(r.get('postings'))} \\\\"
        for r in rs
    ]
    destino = RAIZ / "tablas"
    destino.mkdir(exist_ok=True)
    (destino / "comparativo_externo.tex").write_text("\n".join(filas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--medir":
        print(json.dumps(medir(sys.argv[2:])))
        sys.exit(0)
    sys.exit(main())
