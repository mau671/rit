#!/usr/bin/env python3
"""Genera las tablas LaTeX del informe a partir de Progra02/salida/.

Fuentes que reconoce (todas opcionales):
  salida/consultas.jsonl   una búsqueda por línea, según el contrato de Progra02.tex
  salida/juicios.json      juicios 0/1/2 (lista o diccionario con ``juicios``)
  salida/juicios.jsonl     la misma información, una línea por juicio
  salida/metricas.json     métricas ya calculadas (o CSV con métrica,valor)

Si no hay datos, escribe tablas explícitamente vacías con la etiqueta
"sin datos": el informe nunca muestra un cero inventado.  Los archivos
resultantes viven en documento/tablas/ y se incluyen con \\input.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ.parent / "salida"
TABLAS = RAIZ / "tablas"

SIN_DATOS = "sin datos"
INDICES = ["rit_avanzado", "rit_estandar", "rit_minimo"]

# Etiqueta en el informe -> clave canónica para buscar en las métricas calculadas
PLANIFICADAS = [
    (r"P@$k$", "p@k"),
    ("MAP", "map"),
    ("MRR", "mrr"),
    (r"nDCG@$k$", "ndcg@k"),
    (r"Recall@$k$", "recall@k"),
    (r"$\kappa$ de Cohen", "kappa"),
]

RE_METRICA = re.compile(
    r"^(p@\d+|precision@\d+|map|mrr|ndcg@\d+|recall@\d+|kappa|cohen|f1|accuracy)$",
    re.I,
)


def tex(texto: object) -> str:
    """Escapa texto para usarlo dentro de un entorno de LaTeX."""
    s = str(texto)
    reemplazos = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(reemplazos.get(ch, ch) for ch in s)


def numero(valor: object, decimales: int = 4) -> str:
    try:
        return f"{float(valor):.{decimales}f}".replace(".", ",")
    except (TypeError, ValueError):
        return tex(valor)


def canon_metrica(nombre: str) -> str:
    n = nombre.strip().lower().replace(" ", "").replace("_", "")
    n = re.sub(r"^(precision|p)@(\d+)$", r"p@k", n)
    n = re.sub(r"^ndcg@(\d+)$", "ndcg@k", n)
    n = re.sub(r"^recall@(\d+)$", "recall@k", n)
    if "kappa" in n or "cohen" in n:
        return "kappa"
    if n in {"map", "mrr", "f1", "accuracy"}:
        return n
    return n


def leer_json(ruta: Path):
    if not ruta.is_file():
        return None
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def leer_jsonl(ruta: Path) -> list[dict]:
    if not ruta.is_file():
        return []
    salida: list[dict] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            dato = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(dato, dict):
            salida.append(dato)
    return salida


def consultas_registradas() -> list[dict]:
    return [
        d
        for d in leer_jsonl(SALIDA / "consultas.jsonl")
        if any(k in d for k in ("consulta", "q", "total", "tiempo_ms"))
    ]


def iter_juicios(obj, contexto: dict | None = None):
    """Aplana las estructuras habituales de juicios en registros sueltos."""
    contexto = contexto or {}
    if isinstance(obj, list):
        for item in obj:
            yield from iter_juicios(item, contexto)
        return
    if not isinstance(obj, dict):
        return

    if any(k in obj for k in ("juicios", "resultados", "registros", "data")):
        hijo = dict(contexto)
        for k in ("consulta", "indice", "evaluador", "fecha"):
            if k in obj:
                hijo[k] = obj[k]
        for k in ("juicios", "resultados", "registros", "data"):
            if isinstance(obj.get(k), (list, dict)):
                yield from iter_juicios(obj[k], hijo)
        return

    if any(k in obj for k in ("relevante", "relevancia", "relevance", "grado")):
        registro = dict(contexto)
        registro.update(obj)
        yield registro


def juicios_registrados() -> list[dict]:
    datos = leer_json(SALIDA / "juicios.json")
    if datos is None:
        return list(leer_jsonl(SALIDA / "juicios.jsonl"))
    return list(iter_juicios(datos))


def extraer_metricas(obj, destino: dict[str, list[float]], dentro: bool = False) -> None:
    if isinstance(obj, dict):
        for clave, valor in obj.items():
            nombre = canon_metrica(str(clave))
            es_metrica = dentro or bool(RE_METRICA.match(nombre))
            if isinstance(valor, bool):
                continue
            if isinstance(valor, (int, float)) and es_metrica:
                destino.setdefault(nombre, []).append(float(valor))
            elif isinstance(valor, (dict, list)):
                extraer_metricas(
                    valor,
                    destino,
                    dentro or "metric" in str(clave).lower() or "metrica" in str(clave).lower(),
                )
    elif isinstance(obj, list):
        for valor in obj:
            extraer_metricas(valor, destino, dentro)


def metricas_registradas() -> dict[str, list[float]]:
    destino: dict[str, list[float]] = {}
    for nombre in ("metricas.json", "resultados.json", "evaluacion.json", "estadisticas.json"):
        datos = leer_json(SALIDA / nombre)
        if datos is not None:
            extraer_metricas(datos, destino)
    ruta_csv = SALIDA / "metricas.csv"
    if ruta_csv.is_file():
        with ruta_csv.open(encoding="utf-8", newline="") as fh:
            for fila in csv.DictReader(fh):
                clave = next((fila[c] for c in fila if c and c.lower() in {"metrica", "metric", "nombre", "name"}), None)
                valor = next((fila[c] for c in fila if c and c.lower() in {"valor", "value"}), None)
                if clave is None or valor is None:
                    continue
                try:
                    destino.setdefault(canon_metrica(str(clave)), []).append(float(valor))
                except ValueError:
                    pass
    return destino


def tabla(titulo: str, etiqueta: str, columnas: str, cabeceras: list[str], filas: list[list[str]], nota: str = "") -> str:
    lineas = [
        "% Archivo generado por scripts/tablas.py -- no editar a mano.",
        "\\begin{table}[htbp]",
        "  \\centering",
        "  \\footnotesize",
        f"  \\caption{{{titulo}}}",
        f"  \\label{{{etiqueta}}}",
        f"  \\begin{{tabular*}}{{\\linewidth}}{{@{{\\extracolsep{{\\fill}}}}{columnas}@{{}}}}",
        "    \\toprule",
        "    " + " & ".join(f"\\textbf{{{c}}}" for c in cabeceras) + " \\\\",
        "    \\midrule",
    ]
    for fila in filas:
        lineas.append("    " + " & ".join(fila) + " \\\\")
    if nota:
        lineas.append(
            f"    \\multicolumn{{{len(cabeceras)}}}{{@{{}}>{{\\RaggedRight}}p{{\\linewidth}}@{{}}}}"
            f"{{\\textit{{{nota}}}}} \\\\"
        )
    lineas += ["    \\bottomrule", "  \\end{tabular*}", "\\end{table}", ""]
    return "\n".join(lineas)


def escribir(nombre: str, contenido: str) -> None:
    (TABLAS / nombre).write_text(contenido, encoding="utf-8")


def generar_resumen_uso(consultas: list[dict]) -> None:
    if consultas:
        tiempos = [float(d["tiempo_ms"]) for d in consultas if isinstance(d.get("tiempo_ms"), (int, float))]
        totales = [int(d["total"]) for d in consultas if str(d.get("total", "")).lstrip("-").isdigit()]
        fechas = sorted(str(d["fecha"]) for d in consultas if d.get("fecha"))
        filas = [
            ["Búsquedas registradas", str(len(consultas))],
            ["Consultas distintas (texto literal)", str(len({str(d.get("consulta", "")) for d in consultas}))],
            ["Consultas sin resultados", str(sum(1 for t in totales if t == 0)) if totales else SIN_DATOS],
            ["Tiempo medio de respuesta (ms)", numero(statistics.mean(tiempos), 1) if tiempos else SIN_DATOS],
            ["Tiempo mediano de respuesta (ms)", numero(statistics.median(tiempos), 1) if tiempos else SIN_DATOS],
            ["Primera búsqueda registrada", tex(fechas[0][:19]) if fechas else SIN_DATOS],
            ["Última búsqueda registrada", tex(fechas[-1][:19]) if fechas else SIN_DATOS],
        ]
        nota = "Fuente: \\texttt{salida/consultas.jsonl}; los tiempos incluyen la búsqueda y la construcción de fragmentos."
    else:
        filas = [
            ["Búsquedas registradas", SIN_DATOS],
            ["Consultas distintas (texto literal)", SIN_DATOS],
            ["Consultas sin resultados", SIN_DATOS],
            ["Tiempo medio de respuesta (ms)", SIN_DATOS],
            ["Tiempo mediano de respuesta (ms)", SIN_DATOS],
            ["Primera búsqueda registrada", SIN_DATOS],
            ["Última búsqueda registrada", SIN_DATOS],
        ]
        nota = (
            "El archivo \\texttt{salida/consultas.jsonl} no contiene registros al generar esta tabla; "
            "se llena automáticamente al usar el buscador y volver a ejecutar \\texttt{scripts/tablas.py}."
        )
    escribir(
        "uso_resumen.tex",
        tabla("Resumen de uso del buscador.", "tab:uso-resumen", "lr", ["Indicador", "Valor"], filas, nota),
    )


def generar_consultas_frecuentes(consultas: list[dict]) -> None:
    if consultas:
        conteo = Counter(str(d.get("consulta", "")).strip() for d in consultas if str(d.get("consulta", "")).strip())
        totales: dict[str, list[int]] = {}
        for d in consultas:
            q = str(d.get("consulta", "")).strip()
            if str(d.get("total", "")).lstrip("-").isdigit():
                totales.setdefault(q, []).append(int(d["total"]))
        filas = []
        for puesto, (q, n) in enumerate(conteo.most_common(10), 1):
            t = totales.get(q, [])
            filas.append([str(puesto), tex(q), str(n), numero(statistics.median(t), 0) if t else SIN_DATOS])
        nota = "La última columna es la mediana del total de resultados devueltos por esa consulta."
    else:
        filas = [["--", "Sin datos de uso", "--", "--"]]
        nota = "Tabla vacía a la espera del registro de búsquedas."
    escribir(
        "consultas_frecuentes.tex",
        tabla(
            "Consultas más frecuentes.",
            "tab:consultas-frecuentes",
            "rlrr",
            ["Puesto", "Consulta", "Búsquedas", "Resultados (mediana)"],
            filas,
            nota,
        ),
    )


def generar_indices_uso(consultas: list[dict]) -> None:
    if consultas:
        conteo = Counter(str(d.get("indice", "")) for d in consultas)
        sin = Counter(str(d.get("indice", "")) for d in consultas if int(d.get("total", 0) or 0) == 0)
        filas = []
        for indice in INDICES:
            n = conteo.get(indice, 0)
            filas.append(
                [
                    tex(indice),
                    str(n),
                    f"{100 * n / len(consultas):.1f}".replace(".", ",") + r"\,\%",
                    str(sin.get(indice, 0)),
                ]
            )
        nota = "Los índices enumerados son los habilitados en \\texttt{buscador.conf}."
    else:
        filas = [[tex(i), SIN_DATOS, SIN_DATOS, SIN_DATOS] for i in INDICES]
        nota = "Sin registros: no se puede afirmar qué índice se usó más."
    escribir(
        "indices_uso.tex",
        tabla(
            "Uso de cada índice.",
            "tab:indices-uso",
            "lrrr",
            ["Índice", "Búsquedas", "Participación", "Sin resultados"],
            filas,
            nota,
        ),
    )


def generar_juicios(juicios: list[dict]) -> None:
    if juicios:
        consultas = {str(j.get("consulta", "")) for j in juicios if j.get("consulta")}
        documentos = {str(j.get("id", "")) for j in juicios if j.get("id") is not None}
        evaluadores = {str(j.get("evaluador", "")) for j in juicios if j.get("evaluador")}
        niveles = Counter()
        con_rango = 0
        for j in juicios:
            valor = j.get("relevante", j.get("relevancia", j.get("relevance", j.get("grado"))))
            if isinstance(valor, bool):
                valor = int(valor)
            try:
                niveles[int(valor)] += 1
            except (TypeError, ValueError):
                pass
            if j.get("rango") is not None:
                con_rango += 1
        filas = [
            ["Juicios guardados", str(len(juicios))],
            ["Consultas evaluadas", str(len(consultas)) if consultas else SIN_DATOS],
            ["Documentos distintos juzgados", str(len(documentos)) if documentos else SIN_DATOS],
            ["Evaluadores declarados", str(len(evaluadores)) if evaluadores else "no declarado"],
            ["Juicios con nivel 0", str(niveles.get(0, 0)) if niveles else SIN_DATOS],
            ["Juicios con nivel 1", str(niveles.get(1, 0)) if niveles else SIN_DATOS],
            ["Juicios con nivel 2", str(niveles.get(2, 0)) if niveles else SIN_DATOS],
            ["Juicios con rango registrado", str(con_rango)],
        ]
        nota = "Fuente: \\texttt{salida/juicios.json}; el campo \\texttt{evaluador} es obligatorio en el contrato de datos."
    else:
        filas = [
            ["Juicios guardados", SIN_DATOS],
            ["Consultas evaluadas", SIN_DATOS],
            ["Documentos distintos juzgados", SIN_DATOS],
            ["Evaluadores declarados", SIN_DATOS],
            ["Juicios con nivel 0", SIN_DATOS],
            ["Juicios con nivel 1", SIN_DATOS],
            ["Juicios con nivel 2", SIN_DATOS],
            ["Juicios con rango registrado", SIN_DATOS],
        ]
        nota = (
            "\\texttt{salida/juicios.json} aún no existe: la interfaz de evaluación está preparada, "
            "pero no hay juicios que resumir."
        )
    escribir(
        "juicios_resumen.tex",
        tabla("Juicios de relevancia capturados.", "tab:juicios-resumen", "lr", ["Concepto", "Valor"], filas, nota),
    )


def generar_metricas(metricas: dict[str, list[float]]) -> None:
    filas = []
    encontradas = False
    for etiqueta, clave in PLANIFICADAS:
        valores = metricas.get(clave)
        if valores:
            filas.append([etiqueta, numero(statistics.mean(valores)), "sí"])
            encontradas = True
        else:
            filas.append([etiqueta, SIN_DATOS, "requiere juicios"])
    for clave, valores in sorted(metricas.items()):
        if clave in {c for _, c in PLANIFICADAS} or not valores:
            continue
        filas.append([tex(clave), numero(statistics.mean(valores)), "sí"])
        encontradas = True
    nota = (
        "Los valores son promedios de las métricas calculadas por la interfaz; las celdas \\emph{sin datos} "
        "no son medición: se llenan al existir juicios y ejecutar \\texttt{scripts/tablas.py}."
        if encontradas
        else "Ninguna métrica tiene valor calculado todavía: faltan juicios de relevancia. Esta tabla se llena al correr la evaluación."
    )
    escribir(
        "metricas_resumen.tex",
        tabla(
            "Métricas de evaluación.",
            "tab:metricas-resumen",
            "lrl",
            ["Métrica", "Valor", "Calculada"],
            filas,
            nota,
        ),
    )


def generar_graficos(consultas: list[dict], metricas: dict[str, list[float]]) -> None:
    """Sólo genera gráficos cuando hay puntos reales que dibujar."""
    ruta_consultas = TABLAS / "grafico_consultas.tex"
    conteo = Counter(str(d.get("consulta", "")).strip() for d in consultas if str(d.get("consulta", "")).strip())
    top = [q for q, _ in conteo.most_common(8)]
    if len(top) >= 2:
        coords = ", ".join(tex(q) for q in top)
        puntos = "\n".join(f"      ({tex(q)}, {conteo[q]})" for q in top)
        contenido = f"""% Archivo generado por scripts/tablas.py -- no editar a mano.
\\begin{{figure}}[htbp]
  \\centering
  \\begin{{tikzpicture}}
    \\begin{{axis}}[ybar, width=0.92\\linewidth, height=6cm, bar width=14pt,
        symbolic x coords={{{coords}}}, xtick=data,
        x tick label style={{rotate=30, anchor=east, font=\\footnotesize}},
        ylabel={{Número de búsquedas}}, ymajorgrids, grid style={{gray!25}},
        enlarge x limits=0.08, legend style={{font=\\footnotesize}}]
      \\addplot[fill=blue!65] coordinates {{
{puntos}
      }};
    \\end{{axis}}
  \\end{{tikzpicture}}
  \\caption{{Consultas más frecuentes en el registro de uso.}}
  \\label{{fig:consultas-uso}}
\\end{{figure}}
"""
        ruta_consultas.write_text(contenido, encoding="utf-8")
    elif ruta_consultas.exists():
        ruta_consultas.unlink()

    ruta_metricas = TABLAS / "grafico_metricas.tex"
    pares = [(etiqueta, statistics.mean(v)) for etiqueta, clave in PLANIFICADAS if (v := metricas.get(clave))]
    if len(pares) >= 3:
        coords = ", ".join(tex(e) for e, _ in pares)
        puntos = "\n".join(f"      ({tex(e)}, {v:.4f})" for e, v in pares)
        contenido = f"""% Archivo generado por scripts/tablas.py -- no editar a mano.
\\begin{{figure}}[htbp]
  \\centering
  \\begin{{tikzpicture}}
    \\begin{{axis}}[ybar, width=0.92\\linewidth, height=6cm, bar width=18pt,
        symbolic x coords={{{coords}}}, xtick=data,
        x tick label style={{font=\\footnotesize}},
        ylabel={{Valor}}, ymajorgrids, grid style={{gray!25}},
        enlarge x limits=0.12, ymin=0, legend style={{font=\\footnotesize}}]
      \\addplot[fill=green!55!black] coordinates {{
{puntos}
      }};
    \\end{{axis}}
  \\end{{tikzpicture}}
  \\caption{{Valores promedio de las métricas calculadas.}}
  \\label{{fig:metricas}}
\\end{{figure}}
"""
        ruta_metricas.write_text(contenido, encoding="utf-8")
    elif ruta_metricas.exists():
        ruta_metricas.unlink()


def main() -> None:
    TABLAS.mkdir(parents=True, exist_ok=True)
    consultas = consultas_registradas()
    juicios = juicios_registrados()
    metricas = metricas_registradas()

    generar_resumen_uso(consultas)
    generar_consultas_frecuentes(consultas)
    generar_indices_uso(consultas)
    generar_juicios(juicios)
    generar_metricas(metricas)
    generar_graficos(consultas, metricas)

    print(f"consultas.jsonl: {len(consultas)} registros")
    print(f"juicios: {len(juicios)} registros")
    print(f"métricas con valor: {sorted(metricas) or 'ninguna'}")
    print(f"tablas escritas en {TABLAS}")


if __name__ == "__main__":
    main()
