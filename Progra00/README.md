# Almacenamiento — Arañador audiovisual

Recolección y preprocesamiento del corpus que alimenta al resto del sistema
Encuadre: recorre la web desde semillas del dominio audiovisual, extrae el texto
principal, deduplica y guarda documentos planos con sus metadatos en SQLite.

Curso IC-8060, Recuperación de Información Textual — Tecnológico de Costa Rica.

## Dos implementaciones

La misma necesidad de información resuelta dos veces, para poder compararlas:

| Carpeta | Implementación | Dependencias |
|---|---|---|
| [`arañador/`](arañador/README.md) | Con bibliotecas: Scrapy y Trafilatura | Scrapy, Trafilatura, matplotlib |
| [`propio/`](propio/README.md) | Propia, solo biblioteca estándar | Ninguna |

Ambas comparten las semillas, el esquema de SQLite, la tabla `frontera` y el
árbol de repositorio, así que se pueden alternar sin volver a descargar lo ya
guardado.

## Inicio rápido

```bash
cd "arañador"
cp .env.example .env
uv sync
uv run arañador              # cosecha; Ctrl+C para detener y reanudar después
uv run arañador informe      # estadísticas y curva de Zipf
```

```bash
cd propio
cp .env.example .env
uv sync
uv run propio verificar      # valida semillas, hosts y base
uv run propio                # cosecha con hilos
```

## Salidas

```text
almacenamiento/
├── metadatos_araña.db          # documentos, frontera y bitácora de recorrido
└── repositorio/<dominio>/documentos/<hash[:2]>/<hash>.txt

resultados/
├── estadisticas/               # estadisticas.json, distribuciones.csv, tablas .tex
└── zipf/                       # curva de Zipf, estadísticas y caché de conteo
```

Las rutas se resuelven contra `RAIZ_DATOS`, que en el `.env` apunta a `..`, de
modo que ambas implementaciones escriben en esta misma carpeta.

## Políticas de arañado

| Política | Tipo | Valor |
|---|---|---|
| Hosts permitidos | Selección | Derivados de las semillas |
| Límite de profundidad | Selección | 4 saltos |
| Exclusión de binarios | Selección | Extensiones multimedia y ejecutables |
| Calidad textual | Selección | 500 caracteres y 100 palabras; listas: 300 y 50 |
| Recorrido en amplitud | Selección | Prioridad según profundidad |
| `robots.txt` | Cortesía | Obligatorio, incluido `Crawl-delay` |
| Meta robots / `X-Robots-Tag` | Cortesía | `noindex`, `nofollow`, `none` |
| Identificación | Cortesía | User-Agent propio con enlace institucional |
| Retardo y concurrencia | Cortesía | Adaptativo por host |
| Reintentos | Cortesía | 3 con espera exponencial ante 429/5xx |
| Deduplicación | Revisitación | SHA-256 de URL canónica y de contenido |
| Frescura | Revisitación | Revisita a los 30 días |
| Auditoría | Auditoría | Tabla `bitacora_recorrido` |

El detalle de dónde se implementa cada política está en el README de cada
implementación.

## Frontera compartida

La cola de URLs pendientes es la tabla `frontera` de la base compartida, no un
archivo por implementación. Se atiende en anchura, con FIFO entre URLs del mismo
nivel, y cada implementación la reconcilia al arrancar: descarta lo que ya está
fresco y encola el resto. Un fallo transitorio se reintenta hasta 3 veces antes
de darse por visitado.

## Documento

El informe está en `Progra00.pdf`. El enunciado es `Instrucciones.pdf`.
