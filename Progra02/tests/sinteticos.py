"""Datos SINTÉTICOS (semilla fija) solo para las pruebas; no son resultados del proyecto."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

CONSULTAS = [
    "guion película ciencia ficción distópica",
    "análisis narrativo serie de misterio",
    "crítica anime mecha años noventa",
    "estructura de tres actos guion",
    "diálogos de cine negro",
    "adaptación de novela a serie",
    "guion corto de terror psicológico",
    "personajes femeninos en el cine de animación",
    "cómo escribir un episodio piloto",
    "reseña película documental naturaleza",
]
EVALUADORES = {"e1": 1.0, "e2": 0.7, "e3": 0.5}  # fracción de consultas que juzga cada quien
K = 10
RUIDO = 0.12  # probabilidad de que un evaluador discrepe de la "verdad" simulada


def generar(semilla: int = 7) -> tuple[list[dict], list[dict]]:
    rng = random.Random(semilla)
    busquedas, juicios = [], []
    inicio = datetime(2026, 11, 20, 9, 0)
    for n, texto in enumerate(CONSULTAS, start=1):
        qid = f"q{n:02d}"
        resultados = [
            {
                "rank": r,
                "doc_id": f"{qid}d{r:02d}".ljust(16, "0"),
                "url": f"https://ejemplo.cr/{qid}/{r}",
                "titulo": f"Documento {r} de {qid}",
                "score": round(0.9 - 0.06 * r + rng.uniform(-0.02, 0.02), 4),
            }
            for r in range(1, K + 1)
        ]
        busquedas.append({"query_id": qid, "consulta": texto, "resultados": resultados})
        verdad = {r["doc_id"]: rng.random() < max(0.15, 0.85 - 0.07 * r["rank"]) for r in resultados}
        for e, fraccion in EVALUADORES.items():
            if rng.random() > fraccion:
                continue
            for r in resultados:
                opinion = verdad[r["doc_id"]] != (rng.random() < RUIDO)
                nota = (2 if rng.random() < 0.35 else 1) if opinion else 0
                inicio += timedelta(seconds=rng.randint(15, 90))
                juicios.append(
                    {
                        "query_id": qid,
                        "doc_id": r["doc_id"],
                        "evaluador_id": e,
                        "relevante": nota,
                        "timestamp": inicio.isoformat(timespec="seconds"),
                    }
                )
    return busquedas, juicios
