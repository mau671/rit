<img src="Progra00/imgs/logo.svg" width="110" align="right" alt="Encuadre: lupa con cámara de cine">

# Encuadre

Sistema de recuperación de información textual sobre el dominio audiovisual:
guiones de cine, series, anime y crítica especializada. Recolecta documentos de
la web, los normaliza, construye un índice invertido binario y permite
consultarlos por similitud coseno.

Desarrollado para el curso **IC-8060, Recuperación de Información Textual** del
Tecnológico de Costa Rica.

## Componentes

El sistema está separado en tres piezas independientes que se comunican por el
almacenamiento compartido y el archivo de índice:

| Componente | Carpeta | Responsabilidad | Estado |
|---|---|---|---|
| **Almacenamiento** | `Progra00/` | Cosecha web, extracción de texto limpio, deduplicación y persistencia | Completo |
| **Indexador** | `Progra01/` | Índice invertido binario `.idx`, ponderación TF/IDF y ranking | Completo |
| **Buscador** | `Progra02/` | Interfaz de búsqueda, evaluación de relevancia y métricas | En curso |

```mermaid
flowchart LR
    Semillas["semillas.txt<br/>15 URLs"] --> Almacenamiento
    subgraph Almacenamiento["Almacenamiento · Progra00"]
        Arana["Arañador<br/>Scrapy / propio"] --> BD[("SQLite +<br/>texto plano")]
    end
    BD --> Indexador
    subgraph Indexador["Indexador · Progra01"]
        SPIMI["SPIMI"] --> IDX[(".idx binario")]
    end
    IDX --> Buscador
    subgraph Buscador["Buscador · Progra02"]
        Web["Búsqueda +<br/>evaluación"]
    end
```

## Almacenamiento

Recolecta el corpus desde 15 semillas del dominio audiovisual y guarda cada
documento como texto plano junto con sus metadatos en SQLite. Incluye dos
implementaciones de la misma necesidad de información, para poder compararlas:

| Implementación | Ruta | Dependencias |
|---|---|---|
| Con bibliotecas | `Progra00/arañador/` | Scrapy, Trafilatura, matplotlib |
| Propia | `Progra00/propio/` | Solo biblioteca estándar de Python |

Ambas respetan `robots.txt`, aplican cortesía por host, deduplican por SHA-256
de URL y de contenido, controlan la frescura a 30 días y comparten la tabla
`frontera`, de modo que se puede alternar entre las dos sin volver a descargar
lo ya guardado.

```bash
cd "Progra00/arañador"
cp .env.example .env
uv sync
uv run arañador              # cosecha; Ctrl+C para detener, repetir para reanudar
uv run arañador informe      # estadísticas + curva de Zipf
```

```bash
cd Progra00/propio
cp .env.example .env
uv sync
uv run propio verificar      # valida semillas, hosts y base
uv run propio                # cosecha con hilos
```

El almacenamiento resultante es único y compartido por todo el sistema:

```text
Progra00/almacenamiento/
├── metadatos_araña.db          # documentos, frontera y bitácora de recorrido
└── repositorio/<dominio>/documentos/<hash[:2]>/<hash>.txt
```

Los informes estadísticos y la curva de Zipf se escriben en
`Progra00/resultados/`. Detalles de políticas y discusión comparativa en
[`Progra00/README.md`](Progra00/README.md),
[`Progra00/arañador/README.md`](Progra00/arañador/README.md) y
[`Progra00/propio/README.md`](Progra00/propio/README.md).

## Indexador

El motor **Encuadre** construye un índice invertido en formato binario `.idx`
escrito a mano con `struct.pack` y `struct.unpack` en Little-Endian: cabecera de
64 bytes con la firma `ENCUADRE`, construcción por bloques con SPIMI,
ponderación TF/IDF y ranking por similitud coseno.

Tres políticas de normalización configurables en `Progra01/configuraciones/`:

| Política | Normalización |
|---|---|
| `minimo.conf` | Minúsculas y puntuación, sin stopwords ni stemming |
| `estandar.conf` | Stopwords ES+EN y stemming Snowball |
| `avanzado.conf` | Además números, fechas y expansión por sinónimos |

```bash
cd Progra01
uv sync

uv run indexador indexar --config configuraciones/estandar.conf --limite 1000
uv run indexador inspeccionar salida/indice_estandar.idx --termino "film"
uv run indexador buscar salida/indice_estandar.idx "film director" --top 5
uv run indexador benchmark --limite 500
```

`benchmark` compara las tres políticas (vocabulario, postings, tamaño, tiempo y
memoria pico) y escribe `Progra01/salida/benchmark_indices.json`.

> [!NOTE]
> El indexador lee directamente de `Progra00/almacenamiento/`, así que la ruta
> `base_datos` de los `.conf` asume que ambas carpetas están al mismo nivel.

## Buscador

Interfaz web de consulta sobre el índice, con un modo independiente para que la
persona usuaria marque resultados relevantes y una página de métricas de uso.
La especificación está en `Progra02/Instrucciones.md`.

## Requisitos

- Python 3.12+ para el almacenamiento, Python 3.13+ para el indexador
- [`uv`](https://docs.astral.sh/uv/) para gestionar entornos y dependencias

## Pruebas

Cada componente se prueba por separado:

```bash
cd Progra01 && uv run pytest && uv run ruff check
cd "Progra00/arañador" && uv run pytest
cd Progra00/propio && uv run pytest
```

## Datos y artefactos

Los corpus descargados, bases de datos, bitácoras, resultados e índices **no se
versionan**; las reglas comunes están en `.gitignore`. El `Progra00.tar.gz` de
la raíz es el paquete de entrega del componente de almacenamiento.

## Autores

Mauricio González Prendas — curso IC-8060, Tecnológico de Costa Rica.
