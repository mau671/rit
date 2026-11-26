# Documento de Progra02

Informe LaTeX del proyecto de búsqueda y evaluación (`Encuadre`).

## Recompilar

Desde esta carpeta:

```bash
python3 scripts/tablas.py
tectonic -X compile Progra02.tex
```

El resultado es `Progra02.pdf`. Hacen falta `tectonic` y Python 3 (solo
biblioteca estándar).

## De dónde salen las tablas

`scripts/tablas.py` lee `../salida/`:

- `consultas.jsonl` → resumen de uso, consultas frecuentes, uso por índice;
- `juicios.json` (o `juicios.jsonl`) → resumen de juicios;
- `metricas.json` / `metricas.csv` → valores de métricas.

Escribe los fragmentos `*.tex` en `tablas/`, que `Progra02.tex` incluye con
`\input`. Si no hay datos, las tablas dicen explícitamente **sin datos** y los
gráficos se sustituyen por un recuadro que lo indica; no se inventan cifras.
Vuelve a correr el script después de usar el buscador y la evaluación.
