#!/usr/bin/env bash
# Reconstruye los tres índices, mide, calcula estadísticas y recompila el informe.
# Uso: scripts/regenerar_todo.sh [limite]   (por defecto 500 documentos)
set -euo pipefail
cd "$(dirname "$0")/.."
LIMITE="${1:-500}"

echo "==> 1/5 pruebas y casos compartidos"
uv run pytest -q
uv run python scripts/verificar_casos.py

echo "==> 2/5 índices y benchmark de las tres políticas ($LIMITE documentos)"
uv run indexador benchmark --limite "$LIMITE"
for p in minimo estandar avanzado; do uv run indexador inspeccionar "salida/indice_$p.idx" --verificar | tail -1; done

echo "==> 3/5 estadísticas (tablas/ y datos/)"
uv run python scripts/estadisticas_indices.py

echo "==> 4/5 comparación con Lucene (solo si se define LUCENE_CMD)"
if [[ -n "${LUCENE_CMD:-}" ]]; then
  uv run python scripts/benchmark_comparativo.py --limite "$LIMITE" --repeticiones 3 --lucene-cmd "$LUCENE_CMD"
else
  echo "    omitido: exporte LUCENE_CMD con la plantilla del comando de Lucene (ver el docstring del script)"
fi

echo "==> 5/5 informe"
if command -v tectonic >/dev/null; then tectonic Progra01.tex
else xelatex -interaction=nonstopmode Progra01.tex >/dev/null && xelatex -interaction=nonstopmode Progra01.tex >/dev/null; fi
echo "Listo: Progra01.pdf"
