import pytest

from metricas.calculo import (
    media,
    ndcg_en_k,
    precision_en_k,
    precision_promedio,
    rango_reciproco,
    recall_en_k,
)
from metricas.datos import consolidar, evaluar


def test_precision_en_k():
    assert precision_en_k([True, False, True, True], 3) == pytest.approx(2 / 3)
    # lo que no se devolvió cuenta como fallo
    assert precision_en_k([True, False, True, True], 10) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        precision_en_k([True], 0)


def test_recall_en_k():
    assert recall_en_k([True, False, True], 3, 4) == pytest.approx(0.5)
    assert recall_en_k([True], 3, 0) is None


def test_precision_promedio():
    # relevantes en 1 y 3 de 2 conocidos: (1/1 + 2/3) / 2
    assert precision_promedio([True, False, True, False], 2) == pytest.approx(5 / 6)
    # un relevante conocido que nunca se recuperó penaliza
    assert precision_promedio([True, False], 2) == pytest.approx(0.5)
    assert precision_promedio([False], 0) is None


def test_rango_reciproco():
    assert rango_reciproco([False, False, True]) == pytest.approx(1 / 3)
    assert rango_reciproco([False, False]) == 0.0


def test_ndcg_ejemplo_de_wikipedia():
    # rel = 3,2,3,0,1,2 -> DCG 6.861, IDCG 7.141, nDCG 0.961
    g = [3, 2, 3, 0, 1, 2]
    assert ndcg_en_k(g, 6, g) == pytest.approx(0.961, abs=1e-3)


def test_ndcg_ranking_ideal_es_uno_y_sin_relevantes_es_none():
    assert ndcg_en_k([2, 1, 0], 3, [0, 1, 2]) == pytest.approx(1.0)
    assert ndcg_en_k([0, 0], 2, [0, 0]) is None


def test_media_ignora_none():
    assert media([1.0, None, 0.0]) == 0.5
    assert media([None]) is None


def test_consolidar_mayoria():
    j = [
        {"query_id": "q", "doc_id": "a", "evaluador_id": "e1", "relevante": 2},
        {"query_id": "q", "doc_id": "a", "evaluador_id": "e2", "relevante": 0},
        {"query_id": "q", "doc_id": "b", "evaluador_id": "e1", "relevante": 0},
    ]
    c = consolidar(j)["q"]
    assert c["a"].ganancia == 1.0 and c["a"].relevante  # empate 1-1 cuenta como relevante
    assert not c["b"].relevante


def test_evaluar_consulta_pequena():
    busquedas = [
        {
            "query_id": "q1",
            "consulta": "x",
            "resultados": [{"rank": i, "doc_id": d} for i, d in enumerate("abcd", 1)],
        },
        {"query_id": "q2", "consulta": "sin juicios", "resultados": []},
    ]
    juicios = [
        {"query_id": "q1", "doc_id": "a", "evaluador_id": "e1", "relevante": 1},
        {"query_id": "q1", "doc_id": "b", "evaluador_id": "e1", "relevante": 0},
        {"query_id": "q1", "doc_id": "c", "evaluador_id": "e1", "relevante": 1},
    ]
    r = evaluar(busquedas, juicios, k=4)
    assert len(r["por_consulta"]) == 1  # q2 no tiene juicios y no se evalúa
    g = r["global"]
    assert g["P@4"] == pytest.approx(0.5)
    assert g["MAP"] == pytest.approx(5 / 6)
    assert g["MRR"] == 1.0
    assert g["Recall@4"] == 1.0
    # DCG = 1 + 1/log2(4) = 1.5; ideal = 1 + 1/log2(3)
    assert g["nDCG@4"] == pytest.approx(1.5 / (1 + 1 / 1.5849625), abs=1e-4)
    assert r["curva_precision"][0] == 1.0
