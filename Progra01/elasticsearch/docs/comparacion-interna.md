# Comparación del funcionamiento interno: propio vs. Elasticsearch (Lucene)

Base para la sección del documento que pide comparar el funcionamiento interno
de ambos indexadores. Los números se completan con `salida/comparacion.md`
después de correr `npm run benchmark -- --limite 10000`.

## Construcción del índice

| Aspecto | Propio | Elasticsearch / Lucene |
|---|---|---|
| Algoritmo | SPIMI: arma bloques en memoria, los escribe a disco y los fusiona | Similar en espíritu: cada documento entra a un búfer en memoria; al llenarse se escribe un **segmento** inmutable; los segmentos se fusionan en segundo plano (*merge policy*) |
| Unidad de escritura | Bloques SPIMI temporales y un único `.idx` final | Segmentos; cada uno es un mini índice completo |
| Cálculo de pesos | Se calcula TF/IDF y la norma al fusionar | No se calcula nada al indexar: se guardan estadísticas (tf, df, cantidad de documentos) y el puntaje se calcula al buscar |
| Normalización | Código propio en Python | Cadena de componentes de Lucene configurada en JSON |
| Paralelismo | Un proceso | Varios hilos de indexación; el cliente envía lotes concurrentes |
| Agregar documentos | Hay que reconstruir el índice (cambian IDF y normas) | Se agregan en segmentos nuevos sin tocar los anteriores |

## Estructura en disco

El propio guarda todo en un único archivo binario con un formato fijo y
documentado (`formato_binario.py`), que se puede leer con `hexdump -C`.

Lucene reparte el índice en varios archivos por segmento. Con
`bash herramientas/archivos_lucene.sh rit_estandar` se listan. Los más
relevantes (formato Lucene 9 que usa Elasticsearch 8.15):

| Archivo | Contenido | Equivalente en el propio |
|---|---|---|
| `.tim` | Diccionario de términos, en bloques ordenados con prefijos compartidos | Sección del diccionario |
| `.tip` | Índice del diccionario: un **FST** (autómata de estados finitos) que lleva rápido al bloque correcto de `.tim` | Búsqueda binaria sobre el diccionario cargado en memoria |
| `.doc` | Postings: identificadores de documento y frecuencias, comprimidos | Sección de postings (doc_id, tf, peso) |
| `.pos` | Posiciones (no existe aquí porque `index_options = freqs`) | El propio no guarda posiciones |
| `.fdt` / `.fdx` | Campos almacenados (`_source` con los metadatos) comprimidos con LZ4 | Tabla de documentos |
| `.dvd` / `.dvm` | *Doc values*: datos columnares para ordenar y para leer `norma` e `idioma` al calcular el puntaje | Normas guardadas por documento |
| `.si`, `segments_N` | Información del segmento y lista de segmentos activos | Encabezado del archivo |
| `.liv` | Documentos borrados (desaparece tras `forcemerge`) | No aplica |
| `.cfs` / `.cfe` | *Compound file*: Elasticsearch puede empaquetar los archivos de un segmento pequeño en uno solo | No aplica |

## Compresión de los postings

- **Propio:** cada posting ocupa un tamaño fijo (doc_id `uint32`, tf `uint32`,
  peso `float32`, empaquetados con `struct` en little-endian). Es simple de
  leer con `seek` y fácil de inspeccionar, pero no comprime.
- **Lucene:** guarda las **diferencias** entre identificadores de documento
  consecutivos (*delta encoding*, que producen números pequeños) y las
  empaqueta en bloques de 128 con *Frame of Reference* / *Patched FOR*: cada
  bloque usa solo los bits necesarios para su número más grande. Las
  frecuencias se comprimen igual. Además guarda datos de salto (*skip data*)
  para avanzar por listas largas sin descomprimir todo.

Por eso se espera que los postings de Lucene ocupen menos por posting, aunque
Elasticsearch agrega espacio por los metadatos en `_source` y los *doc values*.

## Diccionario de términos

- **Propio:** lista ordenada de términos con su df, idf y la posición de sus
  postings; se busca con búsqueda binaria (`bisect`) después de cargarla en memoria.
- **Lucene:** los términos se agrupan en bloques que comparten prefijo
  (*BlockTree*) y en memoria solo vive el FST del índice de bloques, que
  comparte tanto prefijos como sufijos. Así se pueden manejar vocabularios
  muy grandes con poca memoria.

## Búsqueda

- **Propio:** lee los postings de cada término de la consulta, multiplica los
  pesos ya calculados y divide entre las normas.
- **Elasticsearch:** recorre los postings con la similitud `scripted`, que
  calcula `w(t,q)·w(t,d)` en el momento con las estadísticas guardadas, y
  `script_score` divide entre `|q|·|d|`.

## Ventajas y desventajas

**Propio**
- Ventajas: formato totalmente controlado y documentado, inspeccionable con
  `hexdump`; búsqueda muy simple porque los pesos ya están calculados; sin
  dependencias de servicios externos.
- Desventajas: sin compresión; hay que reconstruir todo para agregar
  documentos; un solo proceso; toda la normalización hay que programarla y
  probarla a mano.

**Elasticsearch / Lucene**
- Ventajas: postings y diccionario comprimidos; indexación incremental y
  concurrente; normalización declarativa con componentes ya probados; API
  lista para el buscador del Proyecto 3; herramientas como Kibana y `_analyze`.
- Desventajas: requiere un servidor (JVM con al menos 1 GB de heap); el formato
  interno es opaco y difícil de inspeccionar; no trae TF/IDF clásico y hay que
  programarlo con scripts; algunas reglas (idioma por documento, cálculo de la
  norma coseno) quedan fuera del analyzer y obligan a pasadas adicionales; el
  tiempo incluye el costo de HTTP y JSON.

## Fuentes

- Apache Lucene 9: [Index file formats](https://lucene.apache.org/core/9_11_0/core/org/apache/lucene/codecs/lucene99/package-summary.html)
  y [Lucene99PostingsFormat](https://lucene.apache.org/core/9_11_0/core/org/apache/lucene/codecs/lucene99/Lucene99PostingsFormat.html).
- McCandless, M. [Using Finite State Transducers in Lucene](https://blog.mikemccandless.com/2010/12/using-finite-state-transducers-in.html).
- Lemire, D. y Boytsov, L. "Decoding billions of integers per second through
  vectorization". *Software: Practice and Experience*, 45(1), 2015.
- Elasticsearch Reference 8.15: [Merge](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/index-modules-merge.html)
  y [Force merge API](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/indices-forcemerge.html).
- Manning, Raghavan y Schütze, *Introduction to Information Retrieval*, 2008,
  cap. 4 (construcción del índice) y cap. 5 (compresión).
