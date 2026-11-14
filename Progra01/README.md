# Indexador — Motor Encuadre

Índice invertido binario construido a mano para el corpus audiovisual de
[`../Progra00/`](../Progra00/). Escribe un archivo `.idx` con `struct.pack` y
`struct.unpack` en Little-Endian, sin bibliotecas de indexación, y responde
consultas con el modelo vectorial TF/IDF y similitud coseno.

Curso IC-8060, Recuperación de Información Textual — Tecnológico de Costa Rica.

## Requisitos

Python 3.13+ y [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
```

El indexador lee la colección desde `../Progra00/almacenamiento/` (la base
SQLite `metadatos_araña.db` y el repositorio de texto plano), por lo que ambas
carpetas deben estar al mismo nivel.

## Uso

```bash
# 1. Construir un índice con una política específica
uv run indexador indexar --config configuraciones/estandar.conf --limite 1000

# 2. Inspeccionar la estructura binaria (offsets, términos, postings, hexdump)
uv run indexador inspeccionar salida/indice_estandar.idx --termino "film"

# 2b. Comprobar la integridad del archivo (offsets, orden, bloques de postings)
uv run indexador inspeccionar salida/indice_estandar.idx --verificar

# 3. Consultar con similitud coseno
uv run indexador buscar salida/indice_estandar.idx "film director" --top 5

# 4. Comparar el rendimiento de las 3 políticas
uv run indexador benchmark --limite 500
```

Todo comando imprime primero la normalización que declara el encabezado del
archivo, de modo que se ve con qué filtros se construyó el índice consultado.

`benchmark` escribe `salida/benchmark_indices.json` con vocabulario, postings,
tamaño, tiempo, CPU y memoria pico de cada política. El pico se mide con
`VmHWM` y se reinicia antes de cada política: sin ese reinicio, la segunda y la
tercera heredarían el pico de la primera y la comparación no diría nada.

## Políticas de normalización

Cada archivo en `configuraciones/` es un `.conf` estilo Linux que fija la
normalización, el modelo y los parámetros de memoria de SPIMI:

| Archivo | Stopwords | Stemming | Números | Fechas | Sinónimos |
|---|---|---|---|---|---|
| `minimo.conf` | No | No | No | No | No |
| `estandar.conf` | ES+EN | Snowball | No | No | No |
| `avanzado.conf` | ES+EN | Snowball | Sí | Sí | Sí |

Las tres políticas comparten TF logarítmico, IDF estándar con suavizado y
normalización coseno.

## Formato binario `.idx`

Tres secciones consecutivas, todas Little-Endian:

| Sección | Tamaño | Contenido |
|---|---|---|
| Encabezado | 64 bytes fijos | Firma `ENCUADRE`, versión, flags de normalización, totales, offsets de cada sección y modelo $tf$/$idf$ |
| Documentos | 44 bytes por documento | `doc_id`, hash SHA-256 de la URL, número de tokens y norma vectorial `‖d‖` |
| Diccionario | Variable por término | Término en UTF-8, `df`, `idf`, offset y tamaño de su bloque de postings |
| Postings | Variable | `doc_id` y peso TF/IDF de cada aparición del término |

Los doce bytes que cierran el encabezado no son relleno: guardan el esquema
$tf$, el esquema $idf$ y las banderas de normalización coseno e IDF suavizado.
Con la máscara de normalización, eso permite que una consulta se procese con
exactamente los mismos filtros y la misma ponderación con los que se construyó
el archivo, sin depender del `.conf` que se tenga a mano.

La firma ASCII `ENCUADRE` y los términos en UTF-8 son legibles con
`hexdump -C`, que es justamente lo que hace el subcomando `inspeccionar`.

## Módulos

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
uv run pytest
uv run ruff check
```

## Documento

El informe está en `Progra01.pdf`. El enunciado es `Instrucciones.pdf`.
