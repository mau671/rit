"""Genera ``tablas/comparativo.tex``: indexador propio frente a Elasticsearch.

Lee dos artefactos ya medidos (ninguno se recalcula aquí):

* ``salida/benchmark_indices.json``, que produce ``uv run indexador benchmark``
  desde esta carpeta;
* ``../elasticsearch/salida/benchmark_elasticsearch.json``, que produce
  ``npm run benchmark`` del proyecto Node con el mismo límite de documentos.

y escribe las filas que ``Progra01.tex`` incluye con ``\\filas{tablas/comparativo.tex}``:
un bloque por política (mínima, intermedia y agresiva) con documentos,
vocabulario, postings, tamaño del índice, tiempo total y bytes por documento,
cada métrica con su razón Elasticsearch/propio.

Métricas que se omiten a propósito (no se inventan; simplemente no hay columna):

* CPU: el propio reporta ``tiempo_cpu_segundos`` pero el JSON de Elasticsearch
  no trae tiempo de CPU.
* RAM: el propio mide el pico de RSS del proceso (``memoria_pico_mb``) y
  Elasticsearch solo expone el heap de la JVM (``memoria_jvm``): miden cosas
  distintas y no son comparables.

Si a algún JSON le falta una métrica, esa fila se omite con un aviso por
consola; si falta una política entera, se omite el bloque. No se estima nada.

    uv run python scripts/comparativo_es.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[1]
RAIZ = PROYECTO.parent
POLITICAS = ("minimo", "estandar", "avanzado")
NOMBRE = {"minimo": "mínima", "estandar": "intermedia", "avanzado": "agresiva"}

# (etiqueta, campo en el JSON del propio, campo en el JSON de Elasticsearch)
# El tamaño se llama distinto en cada artefacto; el resto comparte nombre.
METRICAS = (
    ("Documentos", "total_documentos", "total_documentos"),
    ("Vocabulario $|V|$", "tamano_vocabulario", "tamano_vocabulario"),
    ("Postings", "total_postings", "total_postings"),
    ("Tamaño del índice (MB)", "tamano_archivo_mb", "tamano_indice_mb"),
    ("Tiempo de indexación (s)", "tiempo_segundos", "tiempo_segundos"),
    ("Bytes por documento", "bytes_por_documento_promedio", "bytes_por_documento_promedio"),
)


def entero(n: int) -> str:
    return f"{n:,}".replace(",", r"\,")


def numero(x: float) -> str:
    """Número con coma decimal y separador de miles fino (formato del informe)."""
    return f"{x:,.2f}".replace(",", r"\,").replace(".", ",")


def razon(propio: float, es: float) -> str:
    return f"{es / propio:,.2f}".replace(",", r"\,").replace(".", ",") + r"$\times$"


def cargar(ruta: Path, etiqueta: str) -> dict[str, dict]:
    if not ruta.is_file():
        sys.exit(f"Falta {ruta} ({etiqueta}). Generelo y vuelva a correr este script.")
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if not isinstance(datos, list):
        sys.exit(f"{ruta} no es una lista de resultados; ¿es el archivo correcto?")
    return {m["nombre_politica"]: m for m in datos}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--propio", default=str(PROYECTO / "salida" / "benchmark_indices.json"))
    ap.add_argument("--es", default=str(RAIZ / "elasticsearch" / "salida" / "benchmark_elasticsearch.json"))
    args = ap.parse_args()

    propio = cargar(Path(args.propio), "benchmark del indexador propio")
    es = cargar(Path(args.es), "benchmark de Elasticsearch")

    filas: list[str] = []
    for politica in POLITICAS:
        faltantes = [n for n, t in (("propio", propio), ("Elasticsearch", es)) if politica not in t]
        if faltantes:
            print(f"aviso: la política {politica} no está en {' ni en '.join(faltantes)}; se omite el bloque")
            continue
        filas.append(r"\multicolumn{4}{@{}l}{\textbf{Política " + NOMBRE[politica] + "}} \\\\")
        for etiqueta, campo_p, campo_e in METRICAS:
            if campo_p not in propio[politica]:
                print(f"aviso: {etiqueta} falta en {args.propio}; se omite la fila")
                continue
            if campo_e not in es[politica]:
                print(f"aviso: {etiqueta} falta en {args.es}; se omite la fila")
                continue
            p, e = propio[politica][campo_p], es[politica][campo_e]
            cuerpo = f"{entero(p)} & {entero(e)}" if isinstance(p, int) else f"{numero(p)} & {numero(e)}"
            filas.append(f"{etiqueta} & {cuerpo} & {razon(float(p), float(e))} \\\\")
        filas.append(r"\addlinespace")
        m, n = propio[politica], es[politica]
        print(
            f"{politica:9} propio={m.get('total_documentos')} docs, {m.get('tamano_archivo_mb')} MB, "
            f"{m.get('tiempo_segundos')} s  |  es={n.get('total_documentos')} docs, "
            f"{n.get('tamano_indice_mb')} MB, {n.get('tiempo_segundos')} s"
        )
    if not filas:
        sys.exit("No hay políticas comunes entre los dos benchmark; no se escribió la tabla.")

    destino = RAIZ / "tablas" / "comparativo.tex"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text("\n".join(filas) + "\n", encoding="utf-8")
    print(f"Filas comparativas escritas en {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
