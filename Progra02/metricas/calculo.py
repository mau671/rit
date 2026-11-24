"""Métricas de evaluación de ranking (funciones puras, solo biblioteca estándar).

Convenciones (Manning, Raghavan y Schütze, 2008, cap. 8):

- ``rel`` es la lista de relevancia binaria del ranking devuelto, en orden.
- Un documento recuperado que nadie juzgó cuenta como NO relevante.
- Si una métrica no está definida (p. ej. no hay ningún relevante conocido para
  la consulta) se devuelve ``None`` y quien promedia debe ignorarla.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def precision_en_k(rel: Sequence[bool], k: int) -> float:
    """P@k: fracción de relevantes entre los k primeros (lo no devuelto cuenta como fallo)."""
    if k <= 0:
        raise ValueError("k debe ser positivo")
    return sum(rel[:k]) / k


def recall_en_k(rel: Sequence[bool], k: int, total_relevantes: int) -> float | None:
    """Recall@k respecto a los relevantes conocidos (el pool de juicios)."""
    if total_relevantes <= 0:
        return None
    return sum(rel[:k]) / total_relevantes


def precision_promedio(rel: Sequence[bool], total_relevantes: int) -> float | None:
    """AP: promedio de P@i en cada posición i con relevante, dividido por los relevantes conocidos.

    Los relevantes conocidos que el sistema no devolvió suman 0 al numerador, así
    que penalizan igual que si aparecieran muy abajo.
    """
    if total_relevantes <= 0:
        return None
    aciertos = 0
    suma = 0.0
    for i, es_rel in enumerate(rel, start=1):
        if es_rel:
            aciertos += 1
            suma += aciertos / i
    return suma / total_relevantes


def rango_reciproco(rel: Sequence[bool]) -> float:
    """RR: 1/posición del primer relevante, 0 si no hay ninguno."""
    for i, es_rel in enumerate(rel, start=1):
        if es_rel:
            return 1.0 / i
    return 0.0


def _dcg(ganancias: Sequence[float], k: int) -> float:
    # Ganancia lineal con descuento log2(i+1) (Järvelin y Kekäläinen, 2002).
    return sum(g / math.log2(i + 1) for i, g in enumerate(ganancias[:k], start=1))


def ndcg_en_k(
    ganancias: Sequence[float], k: int, ganancias_conocidas: Sequence[float]
) -> float | None:
    """nDCG@k: DCG del ranking entre el DCG del ranking ideal.

    ``ganancias`` son las calificaciones del ranking devuelto; el ideal se arma
    ordenando de mayor a menor todas las calificaciones conocidas de la consulta.
    """
    ideal = _dcg(sorted(ganancias_conocidas, reverse=True), k)
    if ideal <= 0:
        return None
    return _dcg(ganancias, k) / ideal


def media(valores: Sequence[float | None]) -> float | None:
    """Media ignorando los ``None``; ``None`` si no queda ningún valor."""
    utiles = [v for v in valores if v is not None]
    return sum(utiles) / len(utiles) if utiles else None
