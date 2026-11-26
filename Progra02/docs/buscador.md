# Descripción del buscador Encuadre

Base para la sección del documento sobre la interfaz de búsqueda y resultados,
y para la discusión de resultados.

## Qué hace

Encuadre es la interfaz web del motor de búsqueda sobre la colección que
recorrió la araña (Progra00) y que indexó Progra01/elasticsearch. Permite:

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
6. Evaluar resultados en un **modo independiente** (`/evaluar`): la persona se
   identifica con un nombre o código y marca cada resultado como no relevante,
   relevante o muy relevante. Los juicios se guardan y se pueden reanudar sin
   duplicarse.
7. Ver la **página de métricas** (`/metricas`): P@k, MAP, nDCG@k, MRR, recall,
   κ de Cohen, estadísticas de uso y gráficos, calculados con esos juicios y con
   el registro de búsquedas.

Es un programa independiente de la araña y del indexador: solo lee los índices
de Elasticsearch, los textos del repositorio y los archivos de salida.

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
   ├─ fragmentos.js   lee el .txt del repositorio y resalta con _analyze
   ├─ juicios.js      guarda salida/juicios.json (clave única, escritura atómica)
   ├─ metricas.js     fórmulas, estadísticas y salida/metricas.json
   └─ pagina-metricas.js  HTML + SVG autocontenido de /metricas
```

Los dos modos nuevos comparten el mismo `/api/buscar`: evaluar no es una copia
del buscador, sino una vista distinta sobre la misma búsqueda y sus resultados.

## Decisiones

**El ranking usa la misma fórmula que el índice.** La similitud
`tfidf_propio` definida en Progra01/elasticsearch calcula `w(t,q)·w(t,d)` por término; el
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
para las estadísticas de uso que pide el documento y para reconstruir el
ranking que realmente se mostró al calcular métricas.

## Modo de evaluación

**Es un modo independiente, no un panel del buscador.** `/evaluar` tiene su
propia dirección y su propio JavaScript; comparte `/api/buscar` para no
duplicar el motor, pero la persona que evalúa ve la consulta, los resultados y
los botones de nota, sin los filtros ni la paginación del modo de búsqueda
normal. La especificación del curso pide explícitamente esa separación para que
la recolección de juicios no se confunda con el uso cotidiano del buscador.

**La identidad es un nombre, no una cuenta.** El servidor no tiene usuarios ni
sesiones: pedir login para una actividad de una tarde agrega fricción y no
aporta trazabilidad (el juicio ya guarda el nombre). El nombre viaja en la URL
(`?evaluador=mau`) y en `localStorage` para que se pueda retomar la sesión,
compartir el enlace con otra persona del grupo o separar los juicios de dos
evaluadores sin volver a escribirlo. La página avisa que el nombre es lo único
que distingue a una persona; no es un mecanismo de seguridad.

**Un juicio por documento, consulta, índice y evaluador.** El registro guarda
`doc_id` (el identificador estable del resultado) y además un alias `id` con el
mismo valor, porque `documento/scripts/tablas.py` cuenta los documentos
distintos con la clave `id`. La clave de deduplicación se
normaliza (minúsculas y espacios simples) porque buscar `Director  terror` y
`director terror` debe producir la misma consulta a efectos de evaluación. Al
repetir la marca se actualiza la nota y el `timestamp`, no se agrega otra fila.
Así se puede reanudar, corregir y comparar evaluadores sin que las métricas
cuenten dos veces el mismo documento. En la interfaz, volver a pulsar la nota
activa la borra (`DELETE`), que es la forma natural de deshacer un clic
equivocado.

**La escala es graduada (0/1/2).** Una escala de tres niveles permite usar
nDCG con ganancia y distinguir «menciona el tema» de «responde la necesidad de
información», sin obligar a una calibración fina que una persona no sostiene
entre sesiones. Para P@k, MAP, MRR y κ se binariza en «relevante» = nota 1 o 2;
para nDCG se usa la nota completa. Con varios evaluadores se consolida por
promedio de ganancia y mayoría simple (≥ 50 % de votos ≥ 1), igual que el
módulo de la rama `metricas`.

**Escritura atómica y cola por archivo.** `salida/juicios.json` es un arreglo
JSON legible, fácil de revisar y de versionar en el informe. Cada guardado se
serializa en una cola del proceso y se escribe en un archivo temporal que luego
se renombra: dos clics rápidos o dos pestañas no dejan el archivo a medias ni
pierden juicios. Es suficiente para el volumen del curso; una base de datos
sería infraestructura innecesaria.

**La cola de consultas sale del uso real.** `/api/evaluacion/consultas` agrupa
`consultas.jsonl` para mostrar qué se buscó, cuántas veces y cuánto falta
juzgar. Evaluar consultas reales evita el sesgo de inventar ejemplos cómodos y
alimenta la sección de estadísticas de uso del documento.

**Arranque con índices incompletos.** `buscador.conf` puede listar un índice de
muestra (`rit_evaluacion`, construido con la política avanzada y un límite
menor) para probar la evaluación sin esperar a los tres índices oficiales. El
servidor sigue siendo estricto: si se pide un índice que no existe, responde
502. La decisión de qué índice usar cuando el de por defecto no está construido
está en las interfaces: `cargarIndices()` elige el primero disponible **con
documentos** y lo deja seleccionado. Así, mientras Progra01 reconstruye
`rit_avanzado`, el buscador y la evaluación siguen funcionando sobre
`rit_minimo` o `rit_evaluacion`, y vuelven solos al índice por defecto cuando
está disponible. La URL no se modifica para ocultar esa elección: el selector
muestra cuál se está usando.

## Métricas

**Qué se mide y por qué.** Se eligieron cinco métricas que cubren aspectos
distintos del ranking, más el acuerdo entre evaluadores:

- **P@k** (precisión en los primeros k): qué proporción del top k es relevante.
  Es la lectura directa de la primera pantalla que ve una persona.
- **MAP** (media de la precisión promedio): premia poner los relevantes arriba
  en toda la lista, no solo al inicio.
- **nDCG@k**: usa la relevancia graduada y descuenta posiciones con
  `log2(i+1)`, así que distingue un «muy relevante» en el primer lugar de uno
  en el décimo.
- **MRR**: cuánto tarda en aparecer el primer relevante; importa cuando la
  persona abandona si el primer resultado no sirve.
- **Recall@k**: qué parte del pool de relevantes conocidos entró al top k.
- **κ de Cohen**: acuerdo entre evaluadores más allá del azar; sin esto no se
  sabe si las diferencias entre personas son criterio o ruido.

La página muestra las cinco primeras como tarjetas y las tres más
representativas (P@k, AP, nDCG@k) en un gráfico por consulta, además de la
curva de precisión P@1..P@k y el detalle numérico. Se escogieron gráficos de
barras y de línea porque comparan valores acotados entre 0 y 1 sin exagerar
diferencias; los SVG se generan en el servidor y no dependen de una biblioteca
externa.

**De dónde sale el ranking.** Las métricas necesitan el orden que el sistema
mostró, no solo los documentos juzgados. Se reconstruye con
`salida/consultas.jsonl`: se toma la última búsqueda de cada página de esa
consulta e índice y se concatenan en orden. Si la consulta nunca se registró
(por ejemplo, el registro está apagado), se usa el `rango` guardado en cada
juicio y la página lo advierte. Es una decisión de trazabilidad: el juicio no
puede «inventar» un orden que el buscador no produjo.

**Documentos sin juzgar.** Un resultado que nadie marcó cuenta como no
relevante, que es la convención de los pools de evaluación (Buckley y Voorhees,
2004) y la que usa `trec_eval`. Tiene un sesgo conocido: si solo se juzga el
top 10, el recall y la cobertura se miden contra ese pool y no contra toda la
colección. Por eso la página muestra la cobertura de juicios en el top k y
`Recall@k` se acompaña de esa advertencia. También se listan las consultas
reales que todavía no tienen juicios.

**Fórmulas portadas, no improvisadas.** La rama `origin/metricas` tenía un
módulo Python probado (`metricas/calculo.py` y `metricas/datos.py`). Como el
servidor es Node y no se quería exigir Python para arrancar el proyecto, las
fórmulas se portaron a `src/metricas.js` con las mismas convenciones
(no devolver métricas indefinidas, ignorarlas al promediar, consolidar
evaluadores por mayoría). Las pruebas de Node incluyen los mismos casos
conocidos del módulo Python (P@k, AP, nDCG del ejemplo de Wikipedia, MRR, κ), de
modo que ambos cálculos coinciden. El cálculo vive en un solo lugar y se prueba
sin Elasticsearch ni navegador.

**El informe se recalcula, no se cachea.** `/metricas` y `/api/metricas` leen
`juicios.json` y `consultas.jsonl` en cada petición. «Regenerar» es recargar la
página o cambiar `k`/evaluador/índice con el formulario; no hay un paso de
compilación que pueda quedar desactualizado. La contrapartida (leer dos
archivos pequeños por visita) es irrelevante para el tamaño del curso.

**Persistencia para el documento.** Además de responder, cada generación
escribe `salida/metricas.json` (ruta configurable en `[evaluacion]
archivo_metricas`) con una escritura atómica. Es la fuente que ya espera
`documento/scripts/tablas.py`: un bloque `metricas_globales` con P@k, MAP,
nDCG@k, MRR, recall y κ —una sola vez cada uno—, más agregados por consulta, índice y
evaluador, la curva de precisión y las estadísticas de uso. Los agregados usan
nombres descriptivos (`precision`, `precision_promedio`, `ndcg`,
`rango_reciproco`, `recall`) para que el generador de tablas no los confunda con
los globales: ese script promedia cualquier número cuya clave se parezca a una
métrica, y repetir `P@10` en cada consulta produciría una media que no es la
del sistema. El bloque se llama `metricas_globales` (y no `global`) porque
`tablas.py` canoniza `P@10` a `p@k` antes de buscar métricas y solo reconoce
P@k/nDCG@k/Recall@k dentro de una clave que contenga «métrica»; el nombre es
parte del contrato con el generador. Si se mira `/metricas` con filtros, se guarda la versión **sin
filtros**: escribir un subconjunto como si fuera el global sería una forma
sutil de publicar cifras sesgadas. El archivo se escribe también cuando no hay
juicios, con `null` y avisos, para que la tabla del informe diga «sin datos» y
no invente ceros.

**Limitaciones que conviene decir en el documento.** (1) Con un solo
evaluador, κ no está definido; la página muestra las métricas por evaluador
para que eso sea visible y no un cero. (2) Si se juzgan páginas distintas de
una misma consulta, el ranking se arma con la última versión de cada página;
una consulta que cambió de resultados entre visitas puede mezclar versiones.
(3) El registro no guarda los resultados de páginas nunca visitadas, así que
las métricas solo evalúan la profundidad efectivamente recorrida. (4) La
relevancia es una opinión: por eso se guarda el evaluador y se mide el acuerdo
en vez de presentar una única «verdad».

## Diseño visual

Los fragmentos se muestran en Courier Prime, la tipografía de los guiones de
cine, y las coincidencias con un trazo de resaltador amarillo, como un actor
que marca su parte en el guion. El resto de la interfaz es sobria (Archivo
para títulos y controles) para que la atención quede en los resultados. El
nombre y la marca, la palabra entre las esquinas de un visor, vienen de
*encuadre*: lo que la cámara deja dentro del cuadro. La página tiene modo
oscuro automático, funciona en celulares y se puede usar solo con teclado.

El modo de evaluación conserva esa misma marca y los fragmentos, pero agrega
una barra de avance y tres botones grandes por resultado (0/1/2) con el estado
visible; los atajos <kbd>0</kbd>, <kbd>1</kbd> y <kbd>2</kbd> marcan el primer
resultado pendiente. La página de métricas usa tarjetas para las cifras y
paneles para los gráficos y las tablas: el objetivo es que se pueda leer de
arriba abajo (resumen → evolución → detalle) sin depender de interacción. Las
tres páginas comparten los colores, la tipografía y el foco visible.

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
- Järvelin, K. y Kekäläinen, J. "Cumulated gain-based evaluation of IR
  techniques". *ACM Transactions on Information Systems*, 20(4), 2002
  (nDCG y ganancia con descuento).
- Buckley, C. y Voorhees, E. M. "Retrieval evaluation with incomplete
  information". *SIGIR '04*, 2004 (documentos no juzgados y sesgo del pool).
- Cohen, J. "A coefficient of agreement for nominal scales". *Educational and
  Psychological Measurement*, 20(1), 1960 (κ).
- Landis, J. R. y Koch, G. G. "The measurement of observer agreement for
  categorical data". *Biometrics*, 33(1), 1977 (escala de lectura de κ).
