import sys

from sinteticos import generar
from metricas.__main__ import exportar_documento
from metricas.datos import evaluar
from metricas.pagina import render_html
from metricas.uso import estadisticas_uso


def _demo():
    b, j = generar()
    return b, j, evaluar(b, j, 10), estadisticas_uso(b, j, 10)


def test_demo_es_reproducible_y_las_metricas_estan_en_rango():
    assert generar() == generar()
    _, _, inf, _ = _demo()
    assert len(inf["por_consulta"]) == 10
    for n, v in inf["global"].items():
        assert v is not None and 0 <= v <= 1, n
    # la precisión debe decaer con la posición (el generador así lo simula)
    assert inf["curva_precision"][0] > inf["curva_precision"][-1]


def test_html_contiene_secciones_y_escapa_consultas():
    _, _, inf, uso = _demo()
    inf["por_consulta"][0]["consulta"] = "<script>alert(1)</script>"
    html = render_html(inf, uso)
    assert html.startswith("<!doctype html>") and "</html>" in html
    assert "MAP" in html and "nDCG@10" in html and "<svg" in html and "κ de Cohen" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_html_con_metricas_indefinidas_no_revienta():
    b = [{"query_id": "q1", "consulta": "x", "resultados": [{"rank": 1, "doc_id": "a"}]}]
    j = [{"query_id": "q1", "doc_id": "a", "evaluador_id": "e1", "relevante": 0, "timestamp": "2026-11-24T10:00:00"}]
    html = render_html(evaluar(b, j, 10), estadisticas_uso(b, j, 10))
    assert "—" in html  # sin relevantes: AP, Recall y nDCG quedan indefinidos


def test_exportar_documento(tmp_path):
    _, _, inf, uso = _demo()
    exportar_documento(inf, uso, tmp_path)
    assert (tmp_path / "tablas" / "metricas_global.tex").read_text().count("\\\\") == 5
    assert len((tmp_path / "datos" / "curva_precision.csv").read_text().splitlines()) == 11
    assert "_" not in (tmp_path / "tablas" / "consultas.tex").read_text().replace("\\_", "")
