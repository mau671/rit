# Comparación Propio vs. Elasticsearch

### Política minimo

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1,00× |
| Vocabulario (términos) | 519 119 | 519 119 | 1,00× |
| Postings | 12 119 321 | 12 119 321 | 1,00× |
| Tamaño del índice (MB) | 154,37 | 27,64 | 0,18× |
| Tiempo total (s) | 170,94 | 314,86 | 1,84× |
| Bytes por documento | 16 187,04 | 2 898,43 | 0,18× |
| Postings por término | 23,35 | 23,35 | 1,00× |

Fases de Elasticsearch (s): carga 46,98, estadísticas 199,63, normas 59,65, compactación 8,32. Documentos en español: 1095, en inglés: 8905.
### Política estandar

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1,00× |
| Vocabulario (términos) | 391 901 | 391 981 | 1,00× |
| Postings | 9 258 793 | 9 257 278 | 1,00× |
| Tamaño del índice (MB) | 117,65 | 20,31 | 0,17× |
| Tiempo total (s) | 221,01 | 484,80 | 2,19× |
| Bytes por documento | 12 336,05 | 2 129,75 | 0,17× |
| Postings por término | 23,63 | 23,62 | 1,00× |

Fases de Elasticsearch (s): carga 78,96, estadísticas 306,28, normas 87,89, compactación 11,30. Documentos en español: 1095, en inglés: 8905.

> Nota: La corrida del 25-11 capturó este tamaño a mitad de la fusión del forcemerge (store crudo en tamano_store_bytes, con recovery apenas restado). El neto se corrigió con la instantánea _disk_usage del índice ya reposado (descomposicion_disco.json): store 81554570 - _recovery_source 60257108. El resto de las métricas es el de esa corrida.
### Política avanzado

| Métrica | Propio | Elasticsearch | Elasticsearch / Propio |
|---|---:|---:|---:|
| Documentos | 10 000 | 10 000 | 1,00× |
| Vocabulario (términos) | 367 874 | 368 767 | 1,00× |
| Postings | 9 066 265 | 9 071 429 | 1,00× |
| Tamaño del índice (MB) | 114,76 | 19,26 | 0,17× |
| Tiempo total (s) | 398,27 | 1 129,83 | 2,84× |
| Bytes por documento | 12 033,36 | 2 019,32 | 0,17× |
| Postings por término | 24,65 | 24,60 | 1,00× |

Fases de Elasticsearch (s): carga 233,83, estadísticas 656,16, normas 232,71, compactación 6,64. Documentos en español: 1097, en inglés: 8903.

> Nota: el tamaño neto se toma de la instantánea `_disk_usage` del índice reposado (`descomposicion_disco.json`): `store` menos `_recovery_source`. `_recovery_source` es una copia temporal del documento original que Elasticsearch crea y poda por sí solo, así que no forma parte del índice y su tamaño varía con el tiempo. Por eso la comparación usa el neto y no el tamaño crudo del store.
