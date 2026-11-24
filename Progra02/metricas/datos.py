"""Contrato de datos compartido entre búsqueda (A), evaluación (B) y métricas (C).

busquedas.json  -> lista de
    {"query_id": "q01", "consulta": "texto", "resultados": [
        {"rank": 1, "doc_id": "<hash_url>", "url": "https://...", "titulo": "...", "score": 0.83}]}

juicios.json    -> lista de
    {"query_id": "q01", "doc_id": "<hash_url>", "evaluador_id": "e1",
     "relevante": 0 | 1 | 2, "timestamp": "2026-11-24T10:15:00"}

``doc_id`` es el ``hash_url`` del documento (estable entre índices), no el entero
interno del índice. ``relevante`` es una calificación: 0 no, 1 sí, 2 muy relevante
(si el grupo prefiere juicio binario, basta con usar 0 y 1).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .calculo import (
    media,
    ndcg_en_k,
    precision_en_k,
    precision_promedio,
    rango_reciproco,
    recall_en_k,
)


@dataclass(slots=True)
class Consolidado:
    """Juicio de un documento para una consulta, ya combinado entre evaluadores."""

    ganancia: float  # promedio de calificaciones (para nDCG)
    relevante: bool  # al menos la mitad de los evaluadores lo marcó >= 1
    n_evaluadores: int


def cargar_json(ruta: str | Path) -> list[dict]:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def consolidar(juicios: list[dict]) -> dict[str, dict[str, Consolidado]]:
    """Combina evaluadores: ganancia = promedio; relevante si la fracción de votos >= 1 es >= 0.5."""
    notas: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for j in juicios:
        notas[j["query_id"]][j["doc_id"]].append(int(j["relevante"]))
    return {
        q: {
            d: Consolidado(
                ganancia=sum(v) / len(v),
                relevante=sum(1 for x in v if x >= 1) / len(v) >= 0.5,
                n_evaluadores=len(v),
            )
            for d, v in docs.items()
        }
        for q, docs in notas.items()
    }


def evaluar(busquedas: list[dict], juicios: list[dict], k: int = 10) -> dict:
    """Calcula las métricas por consulta y globales. Solo cuentan las consultas con juicios."""
    cons = consolidar(juicios)
    filas = []
    curva_por_consulta = []
    for b in busquedas:
        q = b["query_id"]
        j = cons.get(q)
        if not j:
            continue  # sin juicios no hay qué evaluar
        ranking = [r["doc_id"] for r in sorted(b["resultados"], key=lambda r: r["rank"])]
        rel = [bool(j.get(d) and j[d].relevante) for d in ranking]
        gan = [j[d].ganancia if d in j else 0.0 for d in ranking]
        total_rel = sum(1 for c in j.values() if c.relevante)
        filas.append(
            {
                "query_id": q,
                "consulta": b["consulta"],
                "juzgados": sum(1 for d in ranking[:k] if d in j),
                "relevantes_conocidos": total_rel,
                f"P@{k}": precision_en_k(rel, k),
                f"Recall@{k}": recall_en_k(rel, k, total_rel),
                "AP": precision_promedio(rel, total_rel),
                f"nDCG@{k}": ndcg_en_k(gan, k, [c.ganancia for c in j.values()]),
                "RR": rango_reciproco(rel),
            }
        )
        curva_por_consulta.append([precision_en_k(rel, i) for i in range(1, k + 1)])

    nombres = {f"P@{k}": f"P@{k}", "MAP": "AP", f"nDCG@{k}": f"nDCG@{k}", "MRR": "RR", f"Recall@{k}": f"Recall@{k}"}
    globales = {n: media([f[c] for f in filas]) for n, c in nombres.items()}
    curva = [media([c[i] for c in curva_por_consulta]) for i in range(k)]
    return {"k": k, "por_consulta": filas, "global": globales, "curva_precision": curva}
