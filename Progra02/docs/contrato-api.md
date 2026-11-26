# Contrato de datos del buscador

Formato que entrega el buscador para que la interfaz de evaluación (persona 2)
y la página de métricas (persona 3) lo reutilicen sin depender de cómo está
hecho por dentro. Si algo de aquí cambia, hay que avisar al grupo.

## `GET /api/buscar`

| Parámetro | Obligatorio | Descripción |
|---|---|---|
| `q` | sí | Texto de la consulta, con la sintaxis avanzada (`"frase"`, `+palabra`, `-palabra`, `dominio:x`, `idioma:es`) |
| `p` | no | Página, desde 1 |
| `indice` | no | `rit_avanzado`, `rit_estandar` o `rit_minimo` (por defecto el de `buscador.conf`). También se puede habilitar un índice de muestra como `rit_evaluacion` |

Respuesta (200):

```json
{
  "consulta": "director película -remake",
  "indice": "rit_avanzado",
  "pagina": 1,
  "por_pagina": 10,
  "total": 37,
  "paginas": 4,
  "tiempo_ms": 84,
  "interpretacion": {
    "libres": ["director", "película"],
    "obligatorios": [], "frases": [], "excluidos": ["remake"],
    "dominios": [], "idiomas": []
  },
  "terminos": {
    "es": [{ "termino": "realiz", "tf": 1, "df": 12, "peso": 3.21, "boost": 1 }],
    "en": [{ "termino": "realiz", "tf": 1, "df": 30, "peso": 2.4, "boost": 1 }]
  },
  "resultados": [
    {
      "rango": 1,
      "id": "152",
      "doc_id": 151,
      "db_id": 152,
      "titulo": "Pulp Fiction",
      "url": "https://imsdb.com/scripts/Pulp-Fiction.html",
      "dominio": "imsdb.com",
      "idioma": "en",
      "tamano_bytes": 182340,
      "similitud": 0.4123,
      "fragmentos": [
        {
          "piezas": [
            { "texto": "The ", "resaltado": false },
            { "texto": "director", "resaltado": true },
            { "texto": " wanted the scene...", "resaltado": false }
          ],
          "antes": true,
          "despues": true
        }
      ],
      "coincidencias": { "realiz": 3, "film": 2 },
      "texto_disponible": true
    }
  ],
  "facetas": {
    "dominio": [{ "valor": "imsdb.com", "cantidad": 30 }],
    "idioma": [{ "valor": "en", "cantidad": 25 }, { "valor": "es", "cantidad": 12 }]
  },
  "avisos": []
}
```

Notas para el resto del grupo:

- **Identificador del documento:** usen `id` (es el `_id` en Elasticsearch y
  el `id` de la tabla `documentos` de la araña). Es estable aunque se
  reconstruya el índice. `doc_id` es el secuencial del indexador y puede cambiar.
  En los juicios y en el registro de consultas ese valor viaja como `doc_id`
  (texto) y es el que hay que comparar.
- **Juicios de relevancia:** la interfaz de evaluación guarda un arreglo JSON en
  `salida/juicios.json` (ver más abajo). La relevancia es graduada 0/1/2 y la
  clave no duplica: volver a marcar el mismo documento para la misma consulta,
  índice y evaluador actualiza el juicio.
- **Métricas:** se calculan desde `juicios.json` + el ranking que realmente se
  mostró, reconstruido con `salida/consultas.jsonl`. Un documento devuelto que
  nadie juzgó cuenta como no relevante; un relevante conocido que el sistema no
  devolvió también penaliza AP. Detalle en `docs/buscador.md`.
- `similitud` es la similitud coseno (0 a 1).
- `total` puede ser mayor que lo que se puede recorrer: solo se navegan los
  primeros `maximo_resultados` (1000 por defecto).

Errores: código 400 (consulta vacía o índice no habilitado) o 502 (problema con
Elasticsearch), con cuerpo `{ "error": "mensaje" }`.

## `GET /api/indices`

```json
{
  "por_defecto": "rit_avanzado",
  "indices": [
    { "nombre": "rit_avanzado", "documentos": 1532, "disponible": true },
    { "nombre": "rit_minimo", "documentos": 0, "disponible": false }
  ]
}
```

## Registro de consultas

Si `registrar_consultas = true`, cada búsqueda se agrega como una línea JSON a
`salida/consultas.jsonl`:

```json
{"fecha":"2026-11-21T05:29:59.812Z","consulta":"director","indice":"rit_avanzado","pagina":1,"total":37,"tiempo_ms":84,"resultados":["152","98","13"]}
```

Sirve para la sección de estadísticas de uso del documento: consultas más
frecuentes, consultas sin resultados, tiempos de respuesta y uso de cada índice.
También es la fuente del **ranking real** para las métricas: se toma la última
búsqueda de cada página y se concatenan en orden. Si una consulta no está en el
registro (por ejemplo, `registrar_consultas = false`), las métricas usan el
`rango` guardado en cada juicio y lo avisan.

## Interfaz de evaluación (modo independiente)

Página: `GET /evaluar`. Es un modo separado del buscador (`/`), como pide la
especificación. La persona se identifica con un nombre o código (campo de la
página, parámetro `?evaluador=` o `localStorage`), busca y marca cada resultado
como 0, 1 o 2. No hay login ni contraseña: el nombre solo separa los juicios.

### El juicio

`salida/juicios.json` es un arreglo JSON. Cada elemento:

```json
{
  "consulta": "director película terror",
  "indice": "rit_avanzado",
  "doc_id": "152",
  "id": "152",
  "url": "https://imsdb.com/scripts/Pulp-Fiction.html",
  "titulo": "Pulp Fiction",
  "evaluador": "mau",
  "relevante": 2,
  "rango": 3,
  "timestamp": "2026-11-25T18:40:12.345Z"
}
```

| Campo | Obligatorio | Descripción |
|---|---|---|
| `consulta` | sí | texto tal como se buscó (máx. 500 caracteres) |
| `indice` | sí | índice en el que se vio el resultado |
| `doc_id` | sí | `id` del resultado en `/api/buscar` (texto) |
| `id` | no (lo pone el servidor) | alias de `doc_id`; `documento/scripts/tablas.py` cuenta documentos con esta clave |
| `url` | no | URL original del documento (solo `http`/`https`) |
| `titulo` | no | título mostrado, para leer el archivo sin volver a buscar |
| `evaluador` | sí | nombre o código (máx. 80 caracteres) |
| `relevante` | sí | `0` no relevante, `1` relevante, `2` muy relevante |
| `rango` | no | posición que ocupaba al juzgarlo (respaldo sin registro) |
| `timestamp` | sí (lo pone el servidor) | ISO 8601 de la última modificación |

**No duplica:** la clave es (`consulta` normalizada a minúsculas y espacios
simples, `indice`, `doc_id`, `evaluador`). Si se repite, se actualiza la nota y
el `timestamp`; la respuesta indica `"creado": false`. Volver a pulsar la misma
nota en la interfaz borra el juicio (`DELETE`).

### `GET /api/evaluacion/juicios`

Filtros opcionales por query string: `consulta`, `indice`, `evaluador`,
`doc_id`. La consulta se compara sin distinguir mayúsculas ni espacios extra.

```json
{
  "juicios": [
    {
      "consulta": "director",
      "indice": "rit_avanzado",
      "doc_id": "152",
      "id": "152",
      "url": "https://imsdb.com/scripts/Pulp-Fiction.html",
      "titulo": "Pulp Fiction",
      "evaluador": "mau",
      "relevante": 2,
      "rango": 1,
      "timestamp": "2026-11-25T18:40:12.345Z"
    }
  ],
  "total": 1
}
```

### `POST /api/evaluacion/juicios`

Cuerpo `application/json` con los campos del juicio (sin `timestamp`).
Responde `201` si lo creó y `200` si actualizó uno existente:

```json
{ "juicio": { "...": "..." }, "creado": true }
```

Errores: `400` (campo faltante, nota fuera de 0..2, índice no habilitado, URL no
http), `413` (cuerpo mayor de 64 KB) y `415` (sin `content-type:
application/json`). El cuerpo del error es `{ "error": "mensaje" }`.

### `DELETE /api/evaluacion/juicios`

Parámetros obligatorios: `consulta`, `doc_id`, `evaluador`; `indice` es
opcional. Responde `200` con `{ "eliminado": true|false, "juicio": {...}|null }`.

### `GET /api/evaluacion/resumen`

Mismos filtros que la lista. Devuelve los conteos que usa la interfaz para el
avance:

```json
{
  "total": 18,
  "consultas": 2,
  "por_relevante": { "0": 7, "1": 9, "2": 2 },
  "por_evaluador": { "mau": 18 },
  "ultimo": "2026-11-25T18:40:12.345Z"
}
```

### `GET /api/evaluacion/consultas`

Cola de evaluación construida con el registro de búsquedas. Para cada
`consulta + indice`:

```json
{
  "objetivo": 10,
  "consultas": [
    {
      "consulta": "director",
      "indice": "rit_avanzado",
      "veces": 4,
      "ultima": "2026-11-25T18:20:00.000Z",
      "total": 37,
      "objetivos": 10,
      "documentos_juzgados": 8,
      "juicios": 9,
      "relevantes": 4,
      "juzgados_por_evaluador": 8
    }
  ],
  "avisos": []
}
```

`objetivos` es `min(por_pagina, total)` y `juzgados_por_evaluador` cuenta
solo a la persona indicada en `?evaluador=` (vale 0 si no se pasó).
`documentos_juzgados` cuenta a todos los evaluadores.

## Página e informe de métricas

### `GET /metricas`

Página HTML autocontenida (CSS y SVG en línea) que se recalcula en cada visita.
Parámetros opcionales: `k` (1..50, por defecto `profundidad_metricas` de
`buscador.conf`), `evaluador`, `indice` y `consulta`. Si no hay juicios muestra
el estado vacío y la lista de consultas reales pendientes. El formulario de la
página vuelve a pedir `/metricas` con los filtros elegidos; ese es el
"regenerar".

### `GET /api/metricas`

Mismos parámetros; devuelve el mismo informe en JSON:

```json
{
  "k": 10,
  "generado": "2026-11-25T19:10:00.000Z",
  "filtros": { "evaluador": null, "indice": null, "consulta": null },
  "global": {
    "P@10": 0.42, "MAP": 0.51, "nDCG@10": 0.63, "MRR": 0.78, "Recall@10": 0.57
  },
  "porConsulta": [
    {
      "clave": "director\u0000rit_avanzado",
      "consulta": "director",
      "indice": "rit_avanzado",
      "fuente": "registro",
      "evaluadores": ["mau"],
      "juzgados": 9,
      "juzgadosEnK": 9,
      "relevantesConocidos": 4,
      "cobertura": 0.9,
      "resultados": 10,
      "P@10": 0.4,
      "Recall@10": 1,
      "AP": 0.62,
      "nDCG@10": 0.71,
      "RR": 1,
      "detalle": [
        { "posicion": 1, "doc_id": "152", "juzgado": true, "relevante": true, "ganancia": 2, "url": "...", "titulo": "..." }
      ]
    }
  ],
  "porEvaluador": [
    { "evaluador": "mau", "consultas": 1, "juicios": 9, "P@10": 0.4, "MAP": 0.62, "nDCG@10": 0.71, "MRR": 1, "Recall@10": 1 }
  ],
  "curvaPrecision": [1, 0.5, 0.33, "..."],
  "uso": {
    "consultasRegistradas": 6, "busquedasRegistradas": 9, "consultasConJuicios": 1,
    "consultasSinJuicios": 5, "evaluadores": 1, "juicios": 9,
    "juiciosPorConsultaMedia": 9, "juiciosPorEvaluador": { "mau": 9 },
    "juiciosPorIndice": { "rit_avanzado": 9 },
    "distribucion": { "0": 5, "1": 3, "2": 1 },
    "porcentajeRelevantes": 0.444, "coberturaEnK": 0.9,
    "kappaPromedio": null, "kappaLectura": "no definido", "paresKappa": [],
    "primerJuicio": "...", "ultimoJuicio": "...", "filtros": {}, "k": 10
  },
  "sinJuicios": [{ "consulta": "perros", "indice": "rit_avanzado", "veces": 2, "total": 12, "ultima": "..." }],
  "avisos": []
}
```

Valores `null` significan métrica no definida (por ejemplo, recall sin ningún
relevante conocido o κ con un solo evaluador); los promedios las ignoran.

Convenciones del cálculo:

- `P@k` divide entre `k` aunque el ranking tenga menos puestos: lo que falta
  cuenta como fallo.
- `AP` promedia P@i en cada acierto y divide entre **todos** los relevantes
  conocidos de la consulta; `MAP` es su media.
- `nDCG@k` usa ganancia lineal (`0/1/2`) y descuento `log2(i+1)`; el ideal se
  arma con las ganancias conocidas. `Recall@k` es relativo al pool juzgado.
- `MRR` es `1/posición` del primer relevante, 0 si no hay.
- Entre varios evaluadores se consolida: ganancia = promedio de notas y
  relevante si al menos la mitad marcó 1 o 2. `κ` de Cohen se calcula por pares
  sobre los documentos que ambos juzgaron (juicio binario: nota ≥ 1).

## Archivo `salida/metricas.json`

Cada petición a `/metricas` o `/api/metricas` reescribe
`salida/metricas.json` (ruta configurable en `[evaluacion] archivo_metricas`).
La escritura es atómica (temporal + `rename`), así `documento/scripts/tablas.py`
nunca lee un archivo a medias.

Si la página se pide con filtros (`evaluador`, `indice` o `consulta`), el
archivo guardado **se recalcula sin filtros**: la vista filtrada se muestra en
la respuesta, pero el archivo que alimenta el documento siempre contiene la
foto global. Así una consulta filtrada hecha mientras se redacta el informe no
cambia las cifras publicadas.

Formato estable (`version` 1):

```json
{
  "version": 1,
  "generado": "2026-11-25T19:10:00.000Z",
  "k": 10,
  "filtros": { "evaluador": null, "indice": null, "consulta": null },
  "metricas_globales": {
    "P@10": 0.42,
    "MAP": 0.51,
    "nDCG@10": 0.63,
    "MRR": 0.78,
    "Recall@10": 0.57,
    "kappa": 0.41
  },
  "agregados": {
    "por_consulta": [
      {
        "clave": "director\u0000rit_avanzado",
        "consulta": "director",
        "indice": "rit_avanzado",
        "fuente": "registro",
        "evaluadores": ["mau"],
        "juzgados": 9,
        "juzgados_en_k": 9,
        "relevantes": 4,
        "resultados": 10,
        "cobertura": 0.9,
        "precision": 0.4,
        "precision_promedio": 0.62,
        "ndcg": 0.71,
        "rango_reciproco": 1,
        "recall": 1
      }
    ],
    "por_indice": [
      {
        "indice": "rit_avanzado",
        "consultas": 1,
        "juzgados": 9,
        "relevantes": 4,
        "precision": 0.4,
        "precision_promedio": 0.62,
        "ndcg": 0.71,
        "rango_reciproco": 1,
        "recall": 1
      }
    ],
    "por_evaluador": [
      {
        "evaluador": "mau",
        "consultas": 1,
        "juicios": 9,
        "precision": 0.4,
        "precision_promedio": 0.62,
        "ndcg": 0.71,
        "rango_reciproco": 1,
        "recall": 1
      }
    ],
    "curva_precision": [1, 0.5, 0.33, 0.25]
  },
  "uso": {
    "consultasRegistradas": 6,
    "busquedasRegistradas": 9,
    "consultasConJuicios": 1,
    "consultasSinJuicios": 5,
    "evaluadores": 1,
    "juicios": 9,
    "juiciosPorConsultaMedia": 9,
    "juiciosPorEvaluador": [{ "evaluador": "mau", "juicios": 9 }],
    "juiciosPorIndice": [{ "indice": "rit_avanzado", "juicios": 9 }],
    "distribucion": [
      { "relevancia": 0, "juicios": 5 },
      { "relevancia": 1, "juicios": 3 },
      { "relevancia": 2, "juicios": 1 }
    ],
    "porcentajeRelevantes": 0.444,
    "coberturaEnK": 0.9,
    "acuerdo": {
      "valor": 0.41,
      "lectura": "moderado",
      "pares": [{ "evaluadores": ["e1", "e2"], "documentos_comunes": 8, "valor": 0.41 }]
    },
    "primerJuicio": "2026-11-24T09:12:00.000Z",
    "ultimoJuicio": "2026-11-25T18:40:12.345Z"
  },
  "sinJuicios": [
    { "consulta": "perros", "indice": "rit_avanzado", "veces": 2, "total": 12, "ultima": "2026-11-25T18:20:00.000Z" }
  ],
  "avisos": []
}
```

`metricas_globales` es el mismo bloque `global` de `/api/metricas` más κ, con
**una sola vez** cada métrica que promedia el documento. El nombre incluye
«métricas» para que `documento/scripts/tablas.py` lo reconozca: ese script
canoniza `P@10` a `p@k` antes de comprobar el patrón, y sin el contenedor
`metricas_*` dejaría fuera P@k, nDCG@k y Recall@k. Las agregaciones usan
nombres descriptivos (`precision`, `precision_promedio`, `ndcg`,
`rango_reciproco`, `recall`) y no repiten `P@k`/`MAP`/`nDCG@k`/`MRR` a
propósito: si se repitieran dentro de `agregados`, el generador mezclaría el
promedio global con el de cada consulta. κ se guarda una sola vez en
`metricas_globales.kappa` y se repite solo como `uso.acuerdo.valor` y
`pares[].valor`, claves que el generador no interpreta como métricas.

El archivo se crea aunque no haya juicios: `metricas_globales` queda con
`null`, los arreglos vacíos y `avisos` explica el estado. `tablas.py` convierte
esos `null` en «sin datos», nunca en ceros.
