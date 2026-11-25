# Contrato de datos del buscador

Formato que entrega el buscador para que la interfaz de evaluación (persona 2)
y la página de métricas (persona 3) lo reutilicen sin depender de cómo está
hecho por dentro. Si algo de aquí cambia, hay que avisar al grupo.

## `GET /api/buscar`

| Parámetro | Obligatorio | Descripción |
|---|---|---|
| `q` | sí | Texto de la consulta, con la sintaxis avanzada (`"frase"`, `+palabra`, `-palabra`, `dominio:x`, `idioma:es`) |
| `p` | no | Página, desde 1 |
| `indice` | no | `rit_avanzado`, `rit_estandar` o `rit_minimo` (por defecto el de `buscador.conf`) |

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
- **Juicios de relevancia (persona 2):** guardar al menos
  `{ consulta, indice, id, relevante, rango, fecha, evaluador }`. Con `rango`
  se calculan P@k, MRR y nDCG sin volver a buscar.
- **Métricas (persona 3):** para recalcular con los juicios guardados basta
  repetir la búsqueda con la misma `consulta` e `indice` y comparar los `id`
  de `resultados` con los marcados como relevantes.
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
