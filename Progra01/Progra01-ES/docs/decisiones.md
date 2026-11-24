# Decisiones de diseño del indexador con Elasticsearch

Este documento justifica cada decisión del indexador con Elasticsearch y
explica cómo se replicó la normalización del indexador propio. Sirve como base
para las secciones del documento del proyecto y para la discusión de resultados.

## 1. Por qué Elasticsearch y no Lucene directo

El profesor autorizó usar Elasticsearch en lugar de Apache Lucene. Elasticsearch
no reemplaza a Lucene: cada shard de un índice de Elasticsearch **es** un índice
de Lucene, y el análisis de texto lo hacen las mismas clases de Lucene
(`PatternTokenizer`, `StopFilter`, `SnowballFilter`, `SynonymFilter`, etc.).
Elasticsearch agrega una capa de servicio (API REST con JSON, configuración
declarativa de analyzers, replicación y distribución).

Para que la comparación con el propio sea justa, el índice se configura con
**1 shard y 0 réplicas**, de modo que quede exactamente un índice de Lucene.

## 2. Arquitectura

```
metadatos_araña.db + repositorio/*.txt
              │
              ▼
     indexador Node.js  ── detecta idioma (idioma.js)
              │            elige campo contenido_es / contenido_en
              │  bulk (HTTP + JSON)
              ▼
     Elasticsearch ── analyzer politica_es / politica_en ── Lucene (segmentos)
```

El programa en Node.js **no normaliza**: solo lee la colección y la envía. Toda
la normalización la hace el analyzer de Elasticsearch. La única decisión que
se toma fuera es el idioma de cada documento, porque un analyzer no puede
elegir un stemmer distinto según el documento (ver sección 4).

Es un programa independiente de la araña: lee la base y el repositorio que la
araña ya dejó, y puede correrse en cualquier momento.

## 3. Equivalencia de la normalización paso a paso

El propio normaliza en `Normalizador.normalizar_texto`. Cada paso se replicó
con un componente del analyzer, en el mismo orden:

| # | Propio (`normalizador.py`) | Elasticsearch | Políticas |
|---|---|---|---|
| 1 | `unicodedata.normalize("NFKC")` | char filter `icu_normalizer` (`nfkc`, plugin analysis-icu) | todas |
| 1 | Comillas y rayas tipográficas | char filter `mapping` | todas |
| 2 | `RE_FECHA_COMPLETA` → `<FECHA>` | char filter `pattern_replace` | avanzado |
| 2 | `RE_ANIO_CINE` → `<ANIO_1994>` | char filter `pattern_replace` | avanzado |
| 2 | `RE_NUMERO_GENERAL` → `<NUM>` | char filter `pattern_replace` | avanzado |
| 3 | `RE_TOKEN.findall` | tokenizer `pattern` con `group: 0` | todas |
| 4 | `str.lower()` sin tocar marcadores | filtro `lowercase` + 3 `pattern_replace` que restauran `<FECHA>`, `<NUM>`, `<ANIO_x>` | todas |
| 6 | Longitud 2 a 50 | filtro `length` | todas |
| 6 | Stopwords propias ES + EN | filtro `stop` con la misma lista | estándar, avanzado |
| 6 | Tesauro (reemplazo) | filtro `synonym` con reglas `variantes => canónico` | avanzado |
| 6 | Raíz fija de términos canónicos | filtro `stemmer_override` | estándar, avanzado |
| 6 | Marcadores sin stemming | filtro `keyword_marker` | estándar, avanzado |
| 6 | Snowball según idioma | filtro `snowball` (`Spanish` o `English`) | estándar, avanzado |

Detalles importantes:

- **Expresiones regulares.** Son copia literal de las del propio. Java y Python
  difieren en `\w`, `\d` y `\b`: en Java solo reconocen ASCII por defecto, y
  "josé" se partiría en dos tokens. Por eso se usa el flag
  `UNICODE_CHARACTER_CLASS`, que hace que se comporten como en Python. Se
  comprobó con un programa en Java que, para los textos de prueba (incluyendo
  ligaduras, dígitos de ancho completo, ñ, ß y dígitos árabes), los tokens son
  idénticos a los del propio.
- **El tokenizer `pattern` describe separadores por defecto.** El propio usa
  `findall`, que describe palabras. Con `group: 0` el tokenizer emite cada
  coincidencia completa como token, que es el equivalente de `findall`.
- **El tesauro reemplaza, no expande.** El propio convierte "película" en
  "film" y "película" deja de existir. Por eso se usan reglas con flecha
  (`película, movie, ... => film`) y no reglas con comas, que agregarían todos
  los sinónimos en la misma posición. Las reglas están en `recursos/tesauro.txt`,
  exportadas directamente del diccionario `TESAURO_AUDIOVISUAL` del propio.
- **Stopwords propias.** No se usa la lista `_spanish_` de Elasticsearch sino
  exactamente las listas del propio (`recursos/stopwords_es.txt` y
  `stopwords_en.txt`).
- **Snowball y no `stemmer`.** El propio usa la biblioteca `snowballstemmer`,
  que para inglés implementa Porter2. El filtro `stemmer` con `english` de
  Elasticsearch usa el Porter original; el filtro `snowball` con `English` usa
  Porter2, que es el equivalente correcto.
- **Raíces fijas.** El propio aplica siempre el stemmer de español a términos
  como "realizador" o "documental" y siempre el de inglés a "film" o
  "producer", sin importar el idioma del documento. `stemmer_override` asigna
  esas raíces y marca el token para que el `snowball` posterior no lo vuelva a
  tocar. Las raíces (`recursos/raices_fijas.txt`) se calcularon con el mismo
  algoritmo Snowball.
- **Marcadores en mayúsculas.** El filtro `condition` (que permitiría saltarse
  los marcadores al pasar a minúsculas) no se puede usar antes de un filtro de
  sinónimos, así que se pasa todo a minúsculas y luego tres `pattern_replace`
  restauran `<FECHA>`, `<NUM>` y `<ANIO_xxxx>`.

## 4. Detección de idioma

El propio decide para cada documento si aplica el stemmer de español o de
inglés: cuenta cuántos de los primeros 300 tokens son stopwords de cada idioma
y gana inglés solo si tiene estrictamente más.

Un analyzer de Elasticsearch es una cadena fija y no puede tomar esa decisión.
Se resolvió así:

1. `idioma.js` replica la heurística del propio (con las mismas listas y los
   mismos pasos previos: limpieza, marcadores, tokenización y minúsculas).
2. El documento se envía al campo `contenido_es` o `contenido_en`.
3. Cada campo tiene su analyzer (`politica_es` con Snowball español y
   `politica_en` con Snowball inglés); el resto de la cadena es idéntico.

La detección se probó contra el propio sobre 59 documentos sintéticos mezclados
en las tres políticas: coincidió en 177 de 177 casos. El comando `verificar`
repite la prueba con documentos reales de la colección.

## 5. Modelo vectorial TF/IDF

Elasticsearch eliminó la similitud clásica (TF/IDF) en la versión 7 y usa BM25
por defecto. Para cumplir con el modelo vectorial se definió una similitud
`scripted` en Painless con **la misma fórmula del propio**:

- peso TF: `1 + ln(tf)` (esquema `logaritmico`)
- IDF: `ln(N/df + 1)` (esquema `estandar` con suavizado)
- similitud coseno: `Σ w(t,q)·w(t,d) / (|q|·|d|)`

Lucene calcula el puntaje término por término y no conoce todos los términos
del documento, así que no puede calcular `|d|`. Por eso:

1. Después de cargar los documentos, el indexador pide a Elasticsearch los
   vectores de términos de cada documento (`_mtermvectors`), que traen `tf` y
   `df` de cada término, y calcula `|d|` con la misma fórmula del propio.
2. La norma se guarda en el campo `norma` de cada documento.
3. Al buscar, `script_score` divide el puntaje entre `|q|·|d|`
   (ver `src/buscador.js`).

La fórmula se configura con `esquema_tf` y `esquema_idf` en el `.conf`, igual
que en el propio.

Diferencia principal con el propio: Lucene **no guarda el peso TF/IDF**, guarda
estadísticas (`tf` por documento, `df` y cantidad de documentos por campo) y
calcula el puntaje al momento de buscar. El propio guarda el peso ya calculado
en cada posting. Ventaja de Lucene: si se agregan documentos no hay que
recalcular los pesos (el IDF cambia). Ventaja del propio: buscar es solo
multiplicar y sumar.

## 6. Decisiones para que la comparación de tamaño y tiempo sea justa

| Opción | Valor | Motivo |
|---|---|---|
| `number_of_shards` | 1 | un solo índice de Lucene, como el único archivo del propio |
| `number_of_replicas` | 0 | las réplicas duplican el tamaño |
| `_source` | sin el texto | el propio no guarda el texto, solo metadatos; la URL sí se guarda porque el Proyecto 3 debe enlazar a la página original |
| `index_options` | `freqs` | el propio guarda doc_id y tf, sin posiciones |
| `norms` | `false` | la norma de longitud de Lucene no se usa; la norma coseno va en el campo `norma` |
| `codec` | `default` (LZ4) | se puede cambiar a `best_compression` para comparar |
| `forcemerge` | 1 segmento | elimina los documentos reemplazados al guardar las normas y deja el índice en su forma más compacta |

Con `guardar_posiciones = true` se pueden hacer búsquedas de frases en el
Proyecto 3, a cambio de un índice más grande.

## 7. Diferencias conocidas con el propio

Estas diferencias son pequeñas y el comando `verificar` permite medirlas:

1. **IDF por campo de idioma.** El propio tiene un solo diccionario y calcula
   el IDF con todos los documentos. En Elasticsearch, cada campo lleva sus
   propias estadísticas, así que el IDF de un término en `contenido_es` se
   calcula sobre los documentos en español. No afecta la normalización ni el
   vocabulario (el reporte une ambos vocabularios), pero sí los valores de los
   pesos. La alternativa, un solo campo, obligaría a usar un único stemmer para
   toda la colección.
2. **Tesauro sin tildes.** Si una palabra no está en el tesauro, el propio
   prueba también sin tildes. En Elasticsearch solo se aplican las formas que
   aparecen en el tesauro (que ya incluye las variantes con y sin tilde de casi
   todas las palabras).
3. **Entradas con espacios.** "motion picture" y "short film" se omitieron del
   tesauro. El propio tampoco las aplica nunca, porque su tokenizador produce
   palabras sueltas; si se incluyeran, Elasticsearch sí las aplicaría y los
   índices dejarían de coincidir.
4. **Minúsculas y longitud en caracteres raros.** Java y Python pasan a
   minúsculas de forma distinta algunos caracteres especiales (por ejemplo, la
   İ turca), y Lucene mide la longitud en unidades UTF-16 mientras que Python
   mide en caracteres (afecta solo a emojis y caracteres fuera del plano básico).
5. **Etiquetas en mayúsculas en el texto original.** Un texto que traiga algo
   como `<BR>` lo conserva así en el propio; en Elasticsearch queda `<br>`.
6. **Versión de Snowball.** Lucene y `snowballstemmer` usan el mismo algoritmo,
   pero pueden venir de versiones distintas del proyecto Snowball.

## 8. Fuentes

- Elasticsearch Reference 8.15: [Text analysis](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis.html),
  [Pattern tokenizer](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis-pattern-tokenizer.html),
  [Pattern replace character filter](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis-pattern-replace-charfilter.html),
  [Synonym token filter](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis-synonym-tokenfilter.html),
  [Snowball token filter](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis-snowball-tokenfilter.html),
  [Stemmer override](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/analysis-stemmer-override-tokenfilter.html),
  [Similarity module (scripted)](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/index-modules-similarity.html),
  [Term vectors API](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/docs-termvectors.html).
- Plugin ICU: [ICU analysis plugin](https://www.elastic.co/guide/en/elasticsearch/plugins/8.15/analysis-icu.html).
- Java: [java.util.regex.Pattern, UNICODE_CHARACTER_CLASS](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/regex/Pattern.html#UNICODE_CHARACTER_CLASS).
- Snowball: [The English (Porter2) stemming algorithm](https://snowballstem.org/algorithms/english/stemmer.html) y
  [Spanish stemming algorithm](https://snowballstem.org/algorithms/spanish/stemmer.html).
- Manning, Raghavan y Schütze, *Introduction to Information Retrieval*,
  Cambridge University Press, 2008: cap. 2 (normalización), cap. 4 (SPIMI) y
  cap. 6 (TF/IDF y modelo vectorial).
