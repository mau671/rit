import pytest

from metricas.uso import estadisticas_uso, interpretar_kappa, kappa_cohen


def test_kappa_ejemplo_de_wikipedia():
    # 50 ítems: sí-sí 20, sí-no 5, no-sí 10, no-no 15 -> po 0.7, pe 0.5, κ 0.4
    a = [True] * 20 + [True] * 5 + [False] * 10 + [False] * 15
    b = [True] * 20 + [False] * 5 + [True] * 10 + [False] * 15
    assert kappa_cohen(a, b) == pytest.approx(0.4)


def test_kappa_no_definido_y_lectura():
    assert kappa_cohen([True, True], [True, True]) is None  # sin variación no hay κ
    assert kappa_cohen([], []) is None
    assert interpretar_kappa(0.4) == "aceptable"
    assert interpretar_kappa(0.9) == "casi perfecto"


def test_estadisticas_uso():
    busquedas = [
        {"query_id": "q1", "consulta": "x", "resultados": [{"rank": 1, "doc_id": "a"}, {"rank": 2, "doc_id": "b"}]},
        {"query_id": "q2", "consulta": "y", "resultados": [{"rank": 1, "doc_id": "c"}]},
    ]
    juicios = [
        {"query_id": "q1", "doc_id": "a", "evaluador_id": "e1", "relevante": 1, "timestamp": "2026-11-24T10:00:00"},
        {"query_id": "q1", "doc_id": "a", "evaluador_id": "e2", "relevante": 1, "timestamp": "2026-11-24T11:00:00"},
        {"query_id": "q1", "doc_id": "b", "evaluador_id": "e1", "relevante": 0, "timestamp": "2026-11-24T10:01:00"},
        {"query_id": "q1", "doc_id": "b", "evaluador_id": "e2", "relevante": 0, "timestamp": "2026-11-24T11:01:00"},
    ]
    u = estadisticas_uso(busquedas, juicios, k=10)
    assert u["consultas"] == 2 and u["consultas_con_juicios"] == 1
    assert u["evaluadores"] == 2 and u["juicios"] == 4
    assert u["porcentaje_relevantes"] == 0.5
    assert u["cobertura_en_k"] == pytest.approx(2 / 3)  # c nunca se juzgó
    assert u["kappa_promedio"] == pytest.approx(1.0)  # acuerdo total
    assert u["primer_juicio"] == "2026-11-24T10:00:00"
