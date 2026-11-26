# Progra01 — Creación del índice de búsqueda (Encuadre)

Primera entrega del motor de búsqueda **Encuadre** del curso IC-8060,
Recuperación de Información Textual (TEC). Construye un índice invertido sobre
el corpus audiovisual de [`../Progra00/`](../Progra00/) con **dos
implementaciones equivalentes** —misma colección, mismas tres políticas de
normalización, misma fórmula TF/IDF con similitud coseno— y documenta el
resultado en [`Progra01.pdf`](Progra01.pdf).

## Estructura

```
Progra01/
├── Progra01.tex / Progra01.pdf   informe del curso
├── tablas/  datos/  imgs/        tablas y series que alimenta el informe
├── recursos/                     stopwords, sinónimos y tesauro (los leen las dos implementaciones)
├── casos_prueba/                 casos de normalización compartidos (28)
├── propio/                       implementación 1: índice binario hecho a mano (Python + SPIMI)
│   ├── configuraciones/          minimo.conf, estandar.conf, avanzado.conf
│   ├── src/indexador/            motor: normalización, SPIMI, formato binario, búsqueda
│   ├── scripts/                  pruebas compartidas, estadísticas y comparación
│   ├── tests/                    pruebas unitarias e integración
│   └── salida/                   índices .idx y benchmark_indices.json (no se versionan)
└── elasticsearch/                implementación 2: Elasticsearch/Lucene (Node.js)
    └── salida/                   benchmark_elasticsearch.json y estadísticas por política
```

La colección debe estar en `../Progra00/almacenamiento/` (la base SQLite
`metadatos_araña.db` y el repositorio de texto plano).

## Regenerar todo el informe

Desde `propio/`, con Python 3.13+ y [`uv`](https://docs.astral.sh/uv/):

```bash
cd propio
uv sync
bash scripts/regenerar_todo.sh 10000
```

El script corre las pruebas, construye y verifica los tres índices, recalcula
las estadísticas del informe (`tablas/` y `datos/`), integra la comparación
con Elasticsearch y recompila `Progra01.pdf`. Se corre tras cambiar el
normalizador, una lista de `recursos/` o una política.

La parte de Elasticsearch se mide aparte (requiere Docker): ver
[`elasticsearch/README.md`](elasticsearch/README.md). Con el mismo límite de
documentos, `npm run benchmark` escribe `elasticsearch/salida/` y
`propio/scripts/comparativo_es.py` lo convierte en la tabla comparativa del
informe. Los `.idx` no se versionan: este script los deja en disco.

## Uso rápido del indexador propio

```bash
cd propio
uv run indexador indexar --config configuraciones/estandar.conf --limite 1000
uv run indexador inspeccionar salida/indice_estandar.idx --verificar
uv run indexador buscar salida/indice_estandar.idx "film director" --top 5
uv run indexador benchmark --limite 10000
```

## Políticas de normalización

Cada archivo en `propio/configuraciones/` es un `.conf` estilo Linux que fija
la normalización, el modelo y los parámetros de memoria de SPIMI. Las tres
comparten TF logarítmico, IDF estándar con suavizado y normalización coseno.

| Archivo | Stopwords | Stemming | Tildes | Números | Fechas | Sinónimos |
|---|---|---|---|---|---|---|
| `minimo.conf` | No | No | Se conservan | No | No | No |
| `estandar.conf` | ES+EN | Snowball | Se conservan | No | No | No |
| `avanzado.conf` | ES+EN | Snowball | Se pliegan (conserva la ñ) | Sí | Sí | Sí |

`recursos/README.md` fija el contrato de normalización paso a paso, y
`casos_prueba/normalizacion.json` los casos que cualquier implementación debe
reproducir: el propio con `uv run python scripts/verificar_casos.py`, y
Elasticsearch con `herramientas/generar_casos.py` + `node src/cli.js verificar`.

## Formato binario `.idx`

Cuatro secciones consecutivas, todas Little-Endian:

| Sección | Tamaño | Contenido |
|---|---|---|
| Encabezado | 64 bytes fijos | Firma `ENCUADRE`, versión, flags de normalización, totales, offsets de cada sección y modelo $tf$/$idf$ |
| Documentos | 44 bytes por documento | `doc_id`, hash SHA-256 de la URL, número de tokens y norma vectorial `‖d‖` |
| Diccionario | Variable por término | Término en UTF-8, `df`, `idf`, offset y tamaño de su bloque de postings |
| Postings | Variable | `doc_id` y peso TF/IDF de cada aparición del término |

Todo se escribe y lee con `struct` a nivel de bytes, sin bibliotecas de
indexación ni bases de datos embebidas. La firma ASCII `ENCUADRE` y los
términos en UTF-8 son legibles con `hexdump -C`, que es justamente lo que hace
el subcomando `inspeccionar`. El detalle campo por campo está en la sección 4
del informe.

## Módulos (propio/src/indexador)

| Archivo | Responsabilidad |
|---|---|
| `formato_binario.py` | Estructuras, constantes y empaquetado de cada sección |
| `escritor_binario.py` / `lector_binario.py` | Escritura y lectura de bajo nivel del `.idx` |
| `spimi.py` | Construcción por bloques con SPIMI (*Single-Pass In-Memory Indexing*) |
| `buscador.py` | Consulta vectorial, resolución de variantes morfológicas y sugerencias |
| `modelo.py` | Fórmulas de TF, IDF y similitud, compartidas por indexador y buscador |
| `memoria.py` | Lectura y reinicio del pico de memoria del proceso |
| `normalizador.py` | Tokenización, stopwords, stemming y normalización multilingüe |
| `lector_coleccion.py` | Recorrido de la colección desde SQLite y el repositorio de texto |
| `configuracion.py` | Lectura de los `.conf` y traducción entre flags y normalización |
| `benchmark.py` | Medición comparativa de las políticas |
| `cli.py` | Comandos `indexar`, `inspeccionar`, `buscar` y `benchmark` |

## Pruebas

```bash
cd propio
uv run pytest
uv run ruff check src scripts tests
cd ../elasticsearch
npm test
```

## Scripts de `propio/scripts/`

| Archivo | Qué hace |
|---|---|
| `verificar_casos.py` | comprueba los 28 casos compartidos (`--comando` para un indexador externo) |
| `estadisticas_indices.py` | calcula las estadísticas de los tres `.idx` y escribe `tablas/` y `datos/` |
| `comparativo_es.py` | escribe `tablas/comparativo.tex` a partir del benchmark propio y del de Elasticsearch |
| `benchmark_comparativo.py` | medidor genérico contra un indexador externo (opcional; no lo usa el informe) |
| `regenerar_todo.sh` | el flujo completo de regeneración descrito arriba |
