# Progra02 · Búsqueda y evaluación (Encuadre)

Módulo de **métricas y documento**. Es independiente de la araña y del indexador y solo
depende del formato de datos de abajo. Biblioteca estándar de Python, sin dependencias.

```text
Progra02/
├── metricas/    # cálculo, estadísticas de uso, página HTML y CLI
├── tests/       # pytest (usa datos sintéticos propios; no son resultados)
├── documento/   # Progra02.tex + tablas/ y datos/ (plantillas vacías hasta correr el CLI)
└── salida/      # metricas.html generada (no se versiona)
```

## Formato de datos acordado

`busquedas.json`: lo que devuelve el buscador.

```json
[{"query_id": "q01", "consulta": "texto de la consulta",
  "resultados": [{"rank": 1, "doc_id": "<hash_url>", "url": "https://...",
                  "titulo": "...", "score": 0.83}]}]
```

`juicios.json`: lo que guarda la interfaz de evaluación.

```json
[{"query_id": "q01", "doc_id": "<hash_url>", "evaluador_id": "e1",
  "relevante": 0, "timestamp": "2026-11-24T10:15:00"}]
```

- `doc_id` es el `hash_url` del documento (estable entre índices), no el entero interno
  de `ResultadoBusqueda.doc_id`, que cambia al reindexar.
- `relevante` es una calificación: `0` no, `1` sí, `2` muy relevante. Si prefieren juicio
  binario basta con 0 y 1.
- **Hay que guardar `evaluador_id` y `timestamp`**: sin ellos no hay acuerdo entre
  evaluadores ni estadísticas de uso para el documento.
- Si cambian el formato, solo se toca `metricas/datos.py`.

## Uso

```bash
python -m metricas -b busquedas.json -j juicios.json                       # salida/metricas.html
python -m metricas -b busquedas.json -j juicios.json --documento documento # + tablas y CSV del .tex
python -m pytest                                                           # 16 pruebas
xelatex Progra02.tex   # dentro de documento/, dos pasadas; usa ../../Progra01/imgs/logo.png
```

Mientras no se corra el comando con los datos reales, las tablas del documento muestran
`--` y las figuras salen vacías. Todo lo que falta en el texto aparece en rojo como
`PENDIENTE`.

Para servir la página desde el backend:

```python
from metricas.datos import cargar_json, evaluar
from metricas.uso import estadisticas_uso
from metricas.pagina import render_html

informe = evaluar(busquedas, juicios, k=10)
html = render_html(informe, estadisticas_uso(busquedas, juicios, k=10))
```

## Decisiones de métricas

| Decisión | Motivo |
|---|---|
| P@10, MAP, nDCG@10, MRR (y Recall@10 como apoyo) | Cubren el top visible, el orden global, la relevancia graduada y el primer acierto |
| Documento sin juzgar = no relevante | Estándar con juicios incompletos; las medidas pierden robustez si hay pocos juicios (se reporta la cobertura) |
| Varios evaluadores: ganancia = promedio; relevante si ≥ 50 % lo marcó ≥ 1 | Permite nDCG graduado y deja un voto binario para P, AP y MRR |
| Consultas sin relevantes conocidos | AP, Recall y nDCG quedan indefinidos y no entran al promedio |
| Acuerdo entre evaluadores con κ de Cohen (promedio por pares) | Mide la fiabilidad de los juicios |

**Cuidado con Recall@k:** si solo se juzga el top-k vale 1 por construcción. Úsenlo solo
si juzgan más profundo o juntan resultados de varias configuraciones.

## Referencias

Las 8 referencias de `documento/Progra02.tex` se verificaron (autores, año, publicación y
páginas) contra DOI, NIST, ACM y las editoriales el 24/11/2026.
