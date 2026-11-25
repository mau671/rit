# Descripción del buscador Encuadre

Base para la sección del documento sobre la interfaz de búsqueda y resultados,
y para la discusión de resultados.

## Qué hace

Encuadre es la interfaz web del motor de búsqueda sobre la colección que
recorrió la araña (Progra00) y que indexó Progra01-ES. Permite:

1. Buscar con el modelo vectorial (similitud coseno con pesos TF/IDF).
2. Usar búsqueda avanzada: frases exactas, palabras obligatorias, exclusiones
   y filtros por sitio e idioma.
3. Escoger el índice (políticas mínima, estándar o avanzada) para comparar
   cómo cambian los resultados según la normalización.
4. Ver cada resultado con su título, la URL de la **página original** (el
   enlace abre la página en el sitio, no el archivo de texto), fragmentos con
   las coincidencias resaltadas, la similitud y cuántas veces aparece cada término.
5. Filtrar por idioma y sitio desde la barra lateral, con la cantidad de
   documentos de cada opción.

Es un programa independiente de la araña y del indexador: solo lee los índices
de Elasticsearch y los textos del repositorio.

## Arquitectura

```
navegador (public/)
   │  GET /api/buscar?q=...
   ▼
servidor Node.js (src/servidor.js)
   ├─ consulta.js     interpreta la sintaxis avanzada
   ├─ busqueda.js     normaliza la consulta, arma la query y calcula |q|
   │     │  _mtermvectors, _search
   │     ▼
   │  Elasticsearch (índices rit_minimo / rit_estandar / rit_avanzado)
   └─ fragmentos.js   lee el .txt del repositorio y resalta con _analyze
```

## Decisiones

**El ranking usa la misma fórmula que el índice.** La similitud
`tfidf_propio` definida en Progra01-ES calcula `w(t,q)·w(t,d)` por término; el
buscador pone el peso TF de la consulta como *boost* de cada término y divide
el total entre `|q|·|d|` con `script_score` (`|d|` está guardada en el campo
`norma`). El resultado es la similitud coseno del modelo vectorial, entre 0 y 1
(Manning, Raghavan y Schütze, 2008, cap. 6).

**La consulta se normaliza con el mismo analyzer que los documentos.** Si la
consulta se normalizara distinto, "películas" no encontraría "film". Por eso la
consulta se pasa por el analyzer de cada campo con `_mtermvectors` (documento
artificial), que además devuelve el `df` de cada término para calcular `|q|`.
La interfaz muestra los términos que quedaron después de normalizar
(por ejemplo, *director* se convierte en *realiz*), lo que hace visible el
efecto de cada política.

**Se busca en ambos idiomas.** Cada documento está en `contenido_es` o
`contenido_en` según el idioma que detectó el indexador. La consulta se analiza
con los dos analyzers y se buscan ambos campos, así una consulta en español
también encuentra guiones en inglés cuando el tesauro une los términos
("película" y "movie" quedan como "film").

**Fragmentos con el analyzer del índice.** El índice no guarda el texto (para
mantenerlo pequeño), así que los fragmentos se arman con el `.txt` del
repositorio. Para resaltar, el texto se pasa por `_analyze`, que devuelve cada
término normalizado con su posición en el texto original. Se resaltan los
tokens cuyo término está en la consulta. Así, buscar *director* resalta
*directores*, *cineasta* y *filmmaker*, cosa que una búsqueda literal de la
palabra no haría. Se escoge la ventana de 40 tokens con más coincidencias y se
muestran hasta dos fragmentos por resultado.

**Filtros en `post_filter`.** Los filtros de sitio e idioma se aplican después
de calcular los contadores, para que la barra lateral siga mostrando las otras
opciones con su cantidad (patrón de búsqueda facetada; Hearst, 2009, cap. 8).

**Frases y posiciones.** El índice se construyó sin posiciones para que fuera
comparable con el propio (`guardar_posiciones = false`). Sin posiciones,
Elasticsearch no puede verificar frases exactas; en ese caso el buscador
busca las palabras sueltas y muestra un aviso. Si el grupo reconstruye el
índice con `guardar_posiciones = true`, las frases funcionan sin cambiar nada
del buscador.

**El estado vive en la URL.** Cada búsqueda tiene su dirección
(`/?q=director&p=2&indice=rit_minimo`), así que se puede compartir, recargar y
navegar con los botones del navegador. La persona 2 puede usar esas mismas
direcciones para preparar las consultas de evaluación.

**Registro de consultas.** Cada búsqueda queda en `salida/consultas.jsonl`
para las estadísticas de uso que pide el documento.

## Diseño visual

Los fragmentos se muestran en Courier Prime, la tipografía de los guiones de
cine, y las coincidencias con un trazo de resaltador amarillo, como un actor
que marca su parte en el guion. El resto de la interfaz es sobria (Archivo
para títulos y controles) para que la atención quede en los resultados. El
nombre y la marca, la palabra entre las esquinas de un visor, vienen de
*encuadre*: lo que la cámara deja dentro del cuadro. La página tiene modo
oscuro automático, funciona en celulares y se puede usar solo con teclado.

## Fuentes

- Manning, C., Raghavan, P. y Schütze, H. *Introduction to Information
  Retrieval*. Cambridge University Press, 2008. Cap. 6 (TF/IDF y modelo
  vectorial) y cap. 8 (presentación de resultados y fragmentos).
- Hearst, M. *Search User Interfaces*. Cambridge University Press, 2009.
  Cap. 5 (presentación de resultados) y cap. 8 (navegación facetada).
  Disponible en https://searchuserinterfaces.com/book/
- Elasticsearch Reference 8.15: [Script score query](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/query-dsl-script-score-query.html),
  [Post filter](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/filter-search-results.html#post-filter),
  [Analyze API](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/indices-analyze.html),
  [Term vectors API](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/docs-termvectors.html).
- Tombros, A. y Sanderson, M. "Advantages of query biased summaries in
  information retrieval". *SIGIR '98*, 1998 (fragmentos según la consulta).
