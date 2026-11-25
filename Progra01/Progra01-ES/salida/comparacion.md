# Comparación Propio vs. Elasticsearch

### Política minimo

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1× |
| Vocabulario (términos) | 497 158 | 497 160 | 1× |
| Postings | 11 400 363 | 11 400 365 | 1× |
| Tamaño del índice (MB) | 145,49 | 215,84 | 1,48× |
| Tiempo total (s) | 115,33 | 77,49 | 0,67× |
| Bytes por documento | 15 255,69 | 22 632,26 | 1,48× |
| Postings por término | 22,93 | 22,93 | 1× |

Fases de Elasticsearch (s): carga 13,52, estadísticas 49,3, normas 11,95, compactación 2,65. Documentos en español: 1941, en inglés: 8059.

### Política estandar

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1× |
| Vocabulario (términos) | 374 111 | 374 196 | 1× |
| Postings | 8 706 794 | 8 705 413 | 1× |
| Tamaño del índice (MB) | 110,81 | 210,6 | 1,9× |
| Tiempo total (s) | 51,27 | 86,55 | 1,69× |
| Bytes por documento | 11 619,33 | 22 083,47 | 1,9× |
| Postings por término | 23,27 | 23,26 | 1× |

Fases de Elasticsearch (s): carga 16,87, estadísticas 52,18, normas 15,81, compactación 1,61. Documentos en español: 1941, en inglés: 8059.

### Política avanzado

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1× |
| Vocabulario (términos) | 352 638 | 353 503 | 1× |
| Postings | 8 516 348 | 8 521 796 | 1× |
| Tamaño del índice (MB) | 108,02 | 376,05 | 3,48× |
| Tiempo total (s) | 98,23 | 231,32 | 2,36× |
| Bytes por documento | 11 326,88 | 39 431,99 | 3,48× |
| Postings por término | 24,15 | 24,11 | 1× |

Fases de Elasticsearch (s): carga 53,25, estadísticas 123,44, normas 52,88, compactación 1,65. Documentos en español: 1942, en inglés: 8058.
