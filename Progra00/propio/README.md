# Implementación propia

Arañador sin bibliotecas de arañado: solo biblioteca estándar de Python
(`urllib`, `html.parser`, `urllib.robotparser`, `threading`, `queue`, `sqlite3`).
No tiene dependencias.

## Uso

```bash
cp .env.example .env
uv sync
uv run propio verificar      # revisa semillas, hosts y base
uv run propio                # cosecha; Ctrl+C para detener
uv run propio                # otra vez: reanuda desde almacenamiento/frontera.jsonl
tail -f bitacoras/recorrido.log
uv run pytest
```

## Arquitectura

| Archivo | Responsabilidad |
|---|---|
| `configuracion.py` | Lectura de `.env` y valores de todas las políticas |
| `semillas.py` | Semillas compartidas y hosts permitidos |
| `descargador.py` | HTTP con `urllib`, robots.txt, cortesía por host, reintentos |
| `extraccion.py` | Texto limpio, enlaces y meta robots con `html.parser`; validación |
| `arana.py` | Frontera en amplitud, hilos trabajadores, guardado atómico |
| `bd.py` | SQLite con el mismo esquema que la versión con Scrapy |
| `cli.py` | Comando `propio` y log de texto |

## Políticas y dónde se implementan

| Política | Tipo | Valor | Dónde |
|---|---|---|---|
| Hosts permitidos | Selección | Derivados de las semillas | `semillas.host_permitido`, `Arana.url_aceptable` |
| Límite de profundidad | Selección | 4 saltos | `Arana.encolar`, `Arana._procesar` (paso 5) |
| Exclusión de binarios | Selección | 49 extensiones + `Content-Type` HTML | `Arana.url_aceptable`, `Respuesta.es_html` |
| Tamaño y largo de URL | Selección | 5 MB, 2048 caracteres | `Descargador._descargar_con_turno`, `Arana.url_aceptable` |
| Calidad textual | Selección | 300 caracteres, 35 % alfanumérico | `extraccion.motivo_rechazo` |
| Recorrido en amplitud | Selección | Prioridad = profundidad | `Arana.frontera` (`PriorityQueue`) |
| robots.txt | Cortesía | Obligatorio, incluye `Crawl-delay` | `Descargador.permitido_por_robots` |
| meta robots / X-Robots-Tag | Cortesía | noindex, nofollow, none | `extraccion._Analizador`, `Arana._procesar` pasos 5 y 6 |
| Identificación | Cortesía | `AranadorAudiovisualPropioBot/1.0` | `configuracion.USER_AGENT` |
| Retardo y concurrencia | Cortesía | 0.75 s, 4 simultáneas por host, adaptativo hasta 5 s | `ControlCortesia` |
| Reintentos | Cortesía | 3, espera exponencial, 429/5xx | `Descargador.descargar` |
| Deduplicación | Revisitación | SHA-256 de URL canónica y de contenido | `Arana.encolar`, `BaseDatos.guardar_documento` |
| Frescura | Revisitación | 30 días | `BaseDatos.necesita_revisita`, `guardar_documento` |
| Paralelización | Paralelización | 16 hilos, URL de hosts ocupados se reencolan | `Arana._trabajador` |
| Auditoría | Auditoría | Tabla `bitacora_recorrido` + `bitacoras/recorrido.log` | `Arana._procesar`, `cli.configurar_log` |

## Compatibilidad con `../arañador/`

Mismas semillas (se lee `../arañador/arañador/semillas.txt`), mismo esquema
SQLite, mismas huellas (`normalizador_url.py` y `generador_hash.py` copiados) y
mismo árbol `{dominio}/documentos/{hash[:2]}/{hash}.txt`. Cada implementación
escribe en su propia carpeta `almacenamiento/`.

Estadísticas y Zipf, desde `../arañador/`:

```bash
uv run python -m arañador.guiones.exportar_estadisticas \
  --bd ../propio/almacenamiento/metadatos_araña.db \
  --repositorio ../propio/almacenamiento/repositorio \
  --salida ../propio/resultados/estadisticas
uv run python -m arañador.guiones.calcular_zipf \
  --entrada ../propio/almacenamiento/repositorio \
  --salida ../propio/resultados/zipf
```
