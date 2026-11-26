# Indexador propio — índice invertido binario

Primera implementación del Proyecto 2: indexador TF/IDF con SPIMI que escribe
un archivo binario propio (`.idx`), sin bibliotecas de recuperación de
información. La segunda implementación vive en [`../elasticsearch/`](../elasticsearch/README.md)
y usa Elasticsearch con las mismas políticas de normalización.

## Uso

```bash
cd Progra01/propio
uv sync
uv run pytest
uv run indexador benchmark --limite 10000     # construye y mide las 3 políticas
uv run indexador inspeccionar salida/indice_estandar.idx --verificar
uv run indexador buscar "guion pulp fiction" --politica estandar
```

El flujo completo (pruebas, índices, estadísticas del informe, comparación con
Elasticsearch y recompilación del PDF) es un solo comando, desde esta carpeta:

```bash
bash scripts/regenerar_todo.sh 10000
```

## Estructura

```
propio/
├── configuraciones/     minimo.conf, estandar.conf, avanzado.conf (formato Linux)
├── scripts/             verificación compartida, estadísticas y comparación
├── src/indexador/       motor: normalización, SPIMI, formato binario, búsqueda
├── tests/               pruebas unitarias e integración con los .idx reales
└── salida/              índices construidos y benchmark (no se versionan)
```

Las listas de palabras vacías, sinónimos y tesauro se comparten con la
implementación de Elasticsearch y viven en [`../recursos/`](../recursos). Los
casos de normalización compartidos están en
[`../casos_prueba/normalizacion.json`](../casos_prueba/normalizacion.json).

El formato binario está documentado en el informe, `Progra01.pdf`, sección 4.
