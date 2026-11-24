"""Estadísticas de uso para la sección 2 del documento: participación y acuerdo entre evaluadores."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from statistics import mean


def kappa_cohen(a: list[bool], b: list[bool]) -> float | None:
    """κ de Cohen entre dos evaluadores (Cohen, 1960). ``None`` si el azar esperado es 1."""
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return None
    return (po - pe) / (1 - pe)


def interpretar_kappa(k: float | None) -> str:
    """Escala de Landis y Koch (1977)."""
    if k is None:
        return "no definido"
    for tope, nombre in ((0, "pobre"), (0.20, "leve"), (0.40, "aceptable"), (0.60, "moderado"), (0.80, "sustancial")):
        if k <= tope:
            return nombre
    return "casi perfecto"


def estadisticas_uso(busquedas: list[dict], juicios: list[dict], k: int = 10) -> dict:
    por_consulta = Counter(j["query_id"] for j in juicios)
    por_evaluador = Counter(j["evaluador_id"] for j in juicios)
    votos: dict[tuple[str, str], dict[str, bool]] = defaultdict(dict)
    for j in juicios:
        votos[(j["query_id"], j["doc_id"])][j["evaluador_id"]] = int(j["relevante"]) >= 1

    # κ promedio sobre todos los pares de evaluadores que juzgaron los mismos documentos
    kappas = []
    for e1, e2 in combinations(sorted(por_evaluador), 2):
        comunes = [v for v in votos.values() if e1 in v and e2 in v]
        kp = kappa_cohen([v[e1] for v in comunes], [v[e2] for v in comunes])
        if kp is not None:
            kappas.append(kp)

    # cobertura: qué parte de los k primeros resultados de cada consulta tiene al menos un juicio
    juzgados = {(j["query_id"], j["doc_id"]) for j in juicios}
    mostrados = [(b["query_id"], r["doc_id"]) for b in busquedas for r in b["resultados"] if r["rank"] <= k]
    cobertura = sum(m in juzgados for m in mostrados) / len(mostrados) if mostrados else None

    marcas = sorted(j["timestamp"] for j in juicios if j.get("timestamp"))
    n_relevantes = sum(1 for j in juicios if int(j["relevante"]) >= 1)
    kappa = mean(kappas) if kappas else None
    return {
        "consultas": len(busquedas),
        "consultas_con_juicios": len(por_consulta),
        "evaluadores": len(por_evaluador),
        "juicios": len(juicios),
        "juicios_por_consulta_media": mean(por_consulta.values()) if por_consulta else 0.0,
        "juicios_por_evaluador": dict(sorted(por_evaluador.items())),
        "porcentaje_relevantes": n_relevantes / len(juicios) if juicios else None,
        "kappa_promedio": kappa,
        "kappa_lectura": interpretar_kappa(kappa),
        "cobertura_en_k": cobertura,
        "primer_juicio": marcas[0] if marcas else None,
        "ultimo_juicio": marcas[-1] if marcas else None,
    }
