# Progra01-ES — Indexador TF/IDF con Elasticsearch

Segunda implementación del índice del Proyecto 2 (IC-8060 Recuperación de
Información Textual, TEC). Reemplaza la implementación con Apache Lucene
(aprobado por el profesor) y construye índices **equivalentes** a los del
indexador propio: misma colección, mismas tres políticas de normalización y
misma fórmula TF/IDF con similitud coseno.

- Cómo se replicó cada paso de la normalización y por qué: [`docs/decisiones.md`](docs/decisiones.md)
- Comparación del funcionamiento interno con el propio: [`docs/comparacion-interna.md`](docs/comparacion-interna.md)

## Estructura

```
Progra01-ES/
├── Dockerfile               Elasticsearch 8.15.3 + plugin analysis-icu
├── docker-compose.yaml      Elasticsearch y Kibana
├── configuraciones/         minimo.conf, estandar.conf, avanzado.conf (formato Linux)
├── recursos/                raices_fijas.txt (la genera generar_casos.py)
├── src/
│   ├── cli.js               punto de entrada (indexar, verificar, buscar, benchmark)
│   ├── configuracion.js     lector de .conf (mismo formato que el propio)
│   ├── analizadores.js      construye el analyzer de cada política
│   ├── similitud.js         similitud TF/IDF en Painless
│   ├── idioma.js            detección de idioma por documento
│   ├── coleccion.js         lectura de metadatos_araña.db y los .txt
│   ├── indexador.js         carga, estadísticas, normas y compactación
│   ├── verificador.js       compara tokens contra el propio
│   ├── buscador.js          consulta de prueba con similitud coseno
│   └── benchmark.js         compara tamaño y tiempo con el propio
├── herramientas/
│   ├── generar_casos.py     genera los tokens esperados con el propio
│   └── archivos_lucene.sh   lista los archivos de Lucene de un índice
├── test/fixtures/           copia mínima del propio para las pruebas
├── casos/textos_prueba.txt  textos para la verificación
├── docs/                    decisiones y comparación interna
└── test/unidad.test.js      pruebas unitarias (no requieren Elasticsearch)
```

Debe quedar dentro de `Progra01`, al lado del indexador propio:

```
rit/
├── Progra00/almacenamiento/      metadatos_araña.db y repositorio/
└── Progra01/
    ├── configuraciones/   políticas (las usan los dos indexadores)
    ├── recursos/          stopwords, sinónimos y tesauro (los usan los dos)
    ├── src/  salida/      indexador propio
    └── Progra01-ES/       este proyecto
```

## Requisitos

- Docker Desktop con al menos 4 GB de RAM asignados.
- Node.js 22.13 o superior (se usa `node:sqlite`, incluido en Node).
- Para generar los casos de verificación: el entorno `uv` del propio, con
  `snowballstemmer>=2.2,<3` en su `pyproject.toml` (Lucene usa el algoritmo
  de Snowball 2.x; la versión 3 cambia algunas raíces en inglés).

## Uso paso a paso

Todos los comandos se corren desde `Progra01-ES`, salvo donde se indica.

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

Primero se generan los tokens esperados con el propio (desde la carpeta
`Progra01`). Este paso también escribe `recursos/raices_fijas.txt`, así que
hay que repetirlo cada vez que el grupo cambie el tesauro o el stemmer, y
siempre antes de indexar:

```bash
cd ..
uv run python Progra01-ES/herramientas/generar_casos.py --politica minimo --muestras 20
uv run python Progra01-ES/herramientas/generar_casos.py --politica estandar --muestras 20
uv run python Progra01-ES/herramientas/generar_casos.py --politica avanzado --muestras 20
cd Progra01-ES
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
node src/cli.js indexar -c configuraciones/estandar.conf --limite 500
```

Las tres, comparadas con el propio (usá el mismo límite que el benchmark del
propio, que en el repo es 500):

```bash
npm run benchmark -- --limite 500
```

### 5. Consultar

```bash
node src/cli.js buscar -c configuraciones/avanzado.conf "film director" --top 5
```

## Resultados que genera (`salida/`)

| Archivo | Contenido |
|---|---|
| `configuracion_<politica>.json` | settings y mappings exactos enviados a Elasticsearch |
| `estadisticas_<politica>.json` | documentos por idioma, vocabulario, postings, tamaño, tiempos por fase, memoria de la JVM, términos más frecuentes |
| `zipf_<politica>.csv` | rango, término y frecuencias, para graficar la ley de Zipf |
| `benchmark_elasticsearch.json` | estadísticas de las tres políticas |
| `comparacion.md` | tablas propio vs. Elasticsearch, listas para el documento |
| `verificacion_<politica>.json` | resultado de comparar tokens con el propio |

## Archivo de configuración

Las políticas no se repiten aquí: `configuracion_propio` (en `[general]`)
apunta al `.conf` del propio y de ahí se leen `[normalizacion]` y `[modelo]`.
`recursos` (en `[rutas]`) apunta a la carpeta de stopwords, sinónimos y
tesauro del propio. Si un `.conf` no trae `configuracion_propio`, se usan sus
propias secciones `[normalizacion]` y `[modelo]`.

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
- **Elasticsearch se cae al indexar**: subir la RAM de Docker Desktop o el heap
  en `ES_JAVA_OPTS` del `docker-compose.yaml`.
