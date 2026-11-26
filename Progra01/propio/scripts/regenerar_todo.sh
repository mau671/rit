#!/usr/bin/env bash
# Reconstruye los índices del indexador propio, mide, calcula estadísticas e
# integra la comparación con Elasticsearch antes de recompilar el informe.
# Uso: propio/scripts/regenerar_todo.sh [limite]   (por defecto 10 000 documentos)
set -euo pipefail

PROPIO="$(cd "$(dirname "$0")/.." && pwd)"
RAIZ="$(dirname "$PROPIO")"
LIMITE="${1:-10000}"

cd "$PROPIO"

echo "==> 1/6 pruebas y casos compartidos"
uv run pytest -q
uv run python scripts/verificar_casos.py

echo "==> 2/6 índices y benchmark de las tres políticas ($LIMITE documentos)"
uv run indexador benchmark --limite "$LIMITE"
for p in minimo estandar avanzado; do
    uv run indexador inspeccionar "salida/indice_$p.idx" --verificar | tail -1
done

echo "==> 3/6 estadísticas (tablas/ y datos/)"
uv run python scripts/estadisticas_indices.py

echo "==> 4/6 comparación con Elasticsearch"
if [[ -f "$RAIZ/elasticsearch/salida/benchmark_elasticsearch.json" ]]; then
    uv run python scripts/comparativo_es.py
else
    echo "    omitido: no hay elasticsearch/salida/benchmark_elasticsearch.json"
fi

echo "==> 5/6 comparación genérica (solo si se define LUCENE_CMD)"
if [[ -n "${LUCENE_CMD:-}" ]]; then
    uv run python scripts/benchmark_comparativo.py --limite "$LIMITE" --repeticiones 3 --lucene-cmd "$LUCENE_CMD"
else
    echo "    omitido: exporte LUCENE_CMD con la plantilla del comando externo"
fi

echo "==> 6/6 informe"
if command -v tectonic >/dev/null; then
    (cd "$RAIZ" && tectonic Progra01.tex)
else
    (cd "$RAIZ" && xelatex -interaction=nonstopmode Progra01.tex >/dev/null && xelatex -interaction=nonstopmode Progra01.tex >/dev/null)
fi
echo "Listo: Progra01.pdf"
