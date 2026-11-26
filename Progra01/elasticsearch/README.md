# Indexador con Elasticsearch (Progra01/elasticsearch)

Segunda implementación del índice del Proyecto 2 (IC-8060 Recuperación de
Información Textual, TEC). Reemplaza la implementación con Apache Lucene
(aprobado por el profesor) y construye índices **equivalentes** a los del
indexador propio: misma colección, mismas tres políticas de normalización y
misma fórmula TF/IDF con similitud coseno.

- Cómo se replicó cada paso de la normalización y por qué: [`docs/decisiones.md`](docs/decisiones.md)
- Comparación del funcionamiento interno con el propio: [`docs/comparacion-interna.md`](docs/comparacion-interna.md)
- El informe del curso está en [`../Progra01.pdf`](../Progra01.pdf); su flujo de
  regeneración está en [`../propio/scripts/regenerar_todo.sh`](../propio/scripts/regenerar_todo.sh).

## Estructura

```
Progra01/
├── Progra01.tex / Progra01.pdf   informe del curso
├── recursos/                     stopwords, sinónimos y tesauro (compartidos)
├── casos_prueba/                 casos de normalización compartidos
├── propio/                       primera implementación (Python + SPIMI)
└── elasticsearch/                este proyecto
    ├── Dockerfile               Elasticsearch 8.15.3 + plugin analysis-icu
    ├── docker-compose.yaml      Elasticsearch y Kibana
    ├── configuraciones/         minimo.conf, estandar.conf, avanzado.conf
    ├── recursos/                raices_fijas.txt (la genera generar_casos.py)
    ├── casos/textos_prueba.txt  textos para la verificación
    ├── src/
    │   ├── cli.js               punto de entrada (indexar, verificar, buscar, benchmark)
    │   ├── configuracion.js     lector de .conf (mismo formato que el propio)
    │   ├── analizadores.js      construye el analyzer de cada política
    │   ├── similitud.js         similitud TF/IDF en Painless
    │   ├── idioma.js            detección de idioma por documento
    │   ├── coleccion.js         lectura de metadatos_araña.db y los .txt
    │   ├── indexador.js         carga, estadísticas, normas y compactación
    │   ├── elastic.js           cliente: conexión, creación, disco y JVM
    │   ├── recursos.js          carga de stopwords/sinónimos/tesauro compartidos
    │   ├── verificador.js       compara tokens contra el propio
    │   ├── buscador.js          consulta de prueba con similitud coseno
    │   └── benchmark.js         compara tamaño y tiempo con el propio
    ├── herramientas/
    │   ├── generar_casos.py     genera los tokens esperados con el propio
    │   └── archivos_lucene.sh   lista los archivos de Lucene de un índice
    ├── test/fixtures/           copia mínima de Progra01 (propio/ y elasticsearch/)
    └── test/unidad.test.js      pruebas unitarias (no requieren Elasticsearch)
```

La colección la aporta [`Progra00/`](../../Progra00/): `almacenamiento/metadatos_araña.db`
y `almacenamiento/repositorio/`, al mismo nivel que `Progra01/`.

## Requisitos

- Docker Desktop con al menos 4 GB de RAM asignados.
- Node.js 22.13 o superior (se usa `node:sqlite`, incluido en Node).
- Para generar los casos de verificación: el entorno `uv` del propio
  (`../propio`), que ya trae `snowballstemmer`. Las raíces fijas de
  `recursos/raices_fijas.txt` se calculan con el mismo stemmer que usa el
  propio; el resto del stemming lo hace el filtro `snowball` de Lucene, que
  trae los algoritmos de la versión 2.x, por lo que algunas raíces pueden
  diferir de las de `snowballstemmer` 3.x (ver `docs/decisiones.md`).

## Uso paso a paso

Todos los comandos se corren desde `Progra01/elasticsearch`, salvo donde se
indica.

### 1. Levantar Elasticsearch y Kibana

```bash
docker compose up -d --build
curl http://localhost:9200
```

`--build` es necesario la primera vez: construye la imagen con el plugin
`analysis-icu`. Kibana queda en http://localhost:5601 (menú → Dev Tools).

### 2. Instalar dependencias y correr las pruebas

```bash
npm install
npm test
```

### 3. Verificar que la normalización es igual a la del propio

Primero se generan los tokens esperados con el normalizador del propio. Este
paso corre **desde `../propio`** (para que use su entorno de uv) y también
escribe `recursos/raices_fijas.txt`, así que hay que repetirlo cada vez que el
grupo cambie el tesauro o el stemmer, y siempre antes de indexar:

```bash
cd ../propio
uv run python ../elasticsearch/herramientas/generar_casos.py --politica minimo --muestras 20
uv run python ../elasticsearch/herramientas/generar_casos.py --politica estandar --muestras 20
uv run python ../elasticsearch/herramientas/generar_casos.py --politica avanzado --muestras 20
cd ../elasticsearch
```

Luego se comparan con Elasticsearch:

```bash
node src/cli.js verificar -c configuraciones/minimo.conf
node src/cli.js verificar -c configuraciones/estandar.conf
node src/cli.js verificar -c configuraciones/avanzado.conf
```

Muestra cuántos casos coinciden token por token y, para los que no, la
primera diferencia. El detalle queda en `salida/verificacion_<politica>.json`.

### 4. Construir los índices

Una política:

```bash
node src/cli.js indexar -c configuraciones/estandar.conf
node src/cli.js indexar -c configuraciones/estandar.conf --limite 10000
```

Las tres, comparadas con el propio (usá el mismo límite que el benchmark del
propio, que en el repo es 10 000):

```bash
npm run benchmark -- --limite 10000
```

El benchmark busca el propio en `../propio/salida/benchmark_indices.json`
(puede sobrescribirse con `--propio`).

### 5. Consultar

```bash
node src/cli.js buscar -c configuraciones/avanzado.conf "film director" --top 5
```

## Resultados que genera (`salida/`)

| Archivo | Contenido |
|---|---|
| `configuracion_<politica>.json` | settings y mappings exactos enviados a Elasticsearch |
| `estadisticas_<politica>.json` | documentos por idioma, vocabulario, postings, tamaño, tiempos por fase, memoria de la JVM, términos más frecuentes |
| `benchmark_elasticsearch.json` | estadísticas de las tres políticas |
| `comparacion.md` | tablas propio vs. Elasticsearch, listas para el documento |
| `verificacion_<politica>.json` | resultado de comparar tokens con el propio |

El tamaño se mide con `_disk_usage` descontando `_recovery_source`, la copia
interna del documento original que Elasticsearch 8 guarda mientras el
`_source` está filtrado (expira sola y no forma parte del índice); en
`estadisticas_<politica>.json` quedan además `tamano_store_bytes` (todo el
disco del shard) y `tamano_recuperacion_bytes` (lo descontado).

`tablas/comparativo.tex` (la tabla comparativa del informe) la genera
`../propio/scripts/comparativo_es.py` a partir de
`benchmark_elasticsearch.json` y del benchmark del propio.

## Archivo de configuración

Las políticas no se repiten aquí: `configuracion_propio` (en `[general]`)
apunta al `.conf` del propio (`../propio/configuraciones/<politica>.conf`) y
de ahí se leen `[normalizacion]` y `[modelo]`. `recursos` (en `[rutas]`) apunta
a la carpeta compartida `../recursos` de stopwords, sinónimos y tesauro. Si un
`.conf` no trae `configuracion_propio`, se usan sus propias secciones
`[normalizacion]` y `[modelo]`.

La sección `[elasticsearch]` controla la estructura del índice:

| Clave | Por defecto | Efecto |
|---|---|---|
| `indice` | `rit_<politica>` | nombre del índice |
| `shards` / `replicas` | 1 / 0 | un solo índice de Lucene |
| `codec` | `default` | `best_compression` usa DEFLATE en vez de LZ4 |
| `guardar_texto` | `false` | guardar el texto completo en `_source` |
| `guardar_posiciones` | `false` | permite búsquedas de frases (índice más grande) |
| `tamano_lote_bytes` | 5000000 | tamaño de cada envío bulk |
| `concurrencia` | 2 | envíos bulk simultáneos |
| `tamano_lote_vectores` | 50 | documentos por petición en la fase de estadísticas |

La fórmula TF/IDF se toma de `esquema_tf` (`logaritmico`, `natural`,
`binario`) y `esquema_idf` (`estandar`, `probabilistico`, `ninguno`) del
`.conf` del propio.

## Explorar en Kibana (Dev Tools)

```json
GET rit_avanzado/_mapping

POST rit_avanzado/_analyze
{
  "field": "contenido_es",
  "text": "Los Directores corrían a ver las Películas de Kurosawa en 1954"
}

GET _cat/segments/rit_avanzado?v
```

Para ver cómo se calculó el puntaje de un resultado, agregá `"explain": true`
a una búsqueda.

Archivos de Lucene en disco (para la comparación interna):

```bash
bash herramientas/archivos_lucene.sh rit_estandar
```

## Problemas comunes

- **`No se pudo conectar con Elasticsearch`**: `docker compose up -d` y esperar
  un minuto. `docker compose logs elasticsearch` muestra el error.
- **`Falta el plugin analysis-icu`**: la imagen se levantó sin construir;
  correr `docker compose up -d --build`.
- **`Elasticsearch se cae al indexar`**: subir la RAM de Docker Desktop o el heap
  en `ES_JAVA_OPTS` del `docker-compose.yaml`.
- **La indexación se detiene sin error**: casi siempre es el *flood-stage
  watermark*: con el disco por encima del 95 % de uso, Elasticsearch bloquea
  la escritura de todos los índices. Liberar espacio o subir los umbrales
  (`PUT _cluster/settings` con `cluster.routing.allocation.disk.watermark.flood_stage`).
- **El índice `rit_minimo` quedó vacío en el contenedor** tras una corrida de
  benchmark interrumpida; ya fue reindexado (2026-11-25) con sus 10 000
  documentos y vocabulario 519 119 (las cifras del informe no dependen de los
  índices vivos: salen de `salida/benchmark_elasticsearch.json`). Si vuelve a
  pasar, reconstruirlo con `docker compose up -d` y
  `node src/cli.js indexar --config configuraciones/minimo.conf --limite 10000`.
- **Los tamaños del store varían entre corridas**: el disco reportado incluye
  `_recovery_source`, una copia interna del documento original que expira sola
  mientras `_source` está filtrado. `estadisticas_*.json` descuenta esa copia
  (ver `tamano_nota` y `descomposicion_disco.json`); si se re-mide, hacer el
  `forcemerge` y esperar a que `_cat/segments` muestre un solo segmento antes
  de leer el tamaño.
