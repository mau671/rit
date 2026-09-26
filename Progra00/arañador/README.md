# Arañador

Herramienta de recuperación de información textual para el curso IC-8060. Lee un
archivo de semillas, recorre enlaces del mismo conjunto de hosts y guarda texto
limpio y metadatos en SQLite y en un repositorio local de archivos `.txt`.

## Qué hace

- Lee las URLs iniciales desde `arañador/semillas.txt`.
- Deriva de esas URLs los hosts permitidos; no hay dominios escritos en el
  código.
- Respeta `robots.txt`, `DEPTH_LIMIT`, el tamaño máximo de descarga y las
  extensiones no textuales.
- Extrae el cuerpo principal con Trafilatura.
- Deduplica por URL y contenido, y mantiene una fecha de re-visitación.
- Registra cada descarga en una bitácora SQLite.
- Genera el informe del repositorio: estadísticas y curva de la ley de Zipf.

## Cómo funciona

1. `arañador/semillas.txt` contiene una URL por línea. Se permiten comentarios
   con `#`.
2. La araña `arañador` lee el archivo, calcula los hosts permitidos y crea las
   solicitudes iniciales.
3. Mientras navega, sigue enlaces internos permitidos, con profundidad máxima
   de 4 y sin extensiones como `.jpg`, `.mp4`, `.pdf` o `.zip`.
4. Trafilatura elimina navegación, scripts y estilos.
5. La validación normaliza URL y texto, calcula hashes SHA-256 y asigna una
   fecha de re-visitación.
6. La persistencia escribe el `.txt` de forma atómica y guarda los metadatos en
   SQLite.
7. El filtro persistente evita volver a descargar documentos cuya relectura aún
   no vence.

## Estructura

```text
Progra00/
├── arañador/
│   ├── semillas.txt
│   ├── entorno.py
│   ├── implementacion/
│   │   ├── configuracion.py
│   │   ├── elementos.py
│   │   ├── filtro_duplicados.py
│   │   ├── aranas/
│   │   ├── intermediarios/
│   │   ├── tuberias/
│   │   ├── utilidades/
│   │   └── bd/
│   ├── bibliotecas/
│   │   ├── extraccion.py
│   │   └── sqlite.py
│   └── guiones/
├── tests/
├── pyproject.toml
├── scrapy.cfg
├── uv.lock
├── .env.example
└── README.md
```

`implementacion/` contiene el código propio. `bibliotecas/` contiene adaptadores
hacia dependencias externas. `bd/` contiene modelos y repositorios propios.

## Configuración

La configuración persistente vive en `.env` y se carga con `python-dotenv`:

```dotenv
RAIZ_DATOS=.
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
LOG_LEVEL=INFO
LOG_FILE=bitacoras/cosecha.log
INTERVALO_PROGRESO=15
OBJETIVO_GIB=10
```

`RAIZ_DATOS` es la carpeta local donde se crean `almacenamiento/`,
`resultados/` y `bitacoras/`. No es otro repositorio de código.

Los argumentos CLI se usan sólo para una ejecución concreta: rutas de entrada y
salida, límites de Scrapy o modo de monitoreo.

## Base de datos

SQLite se inicializa automáticamente. No existe un comando separado de
inicialización: la conexión aplica migraciones versionadas al abrirse y las
tuberías reutilizan esa misma base.

## Reanudación

La cosecha se puede detener y volver a iniciar sin repetir todo:

- Los documentos ya guardados se omiten hasta que venza su fecha de
  re-visitación (30 días por defecto).
- La cola de Scrapy se persiste en `almacenamiento/.scrapy-job`, por lo que al
  reiniciar se recuperan las solicitudes pendientes.
- Las semillas se vuelven a solicitar como puntos de entrada para redescubrir
  enlaces; su contenido se deduplica al guardarse.
- Las páginas descargadas pero descartadas por baja densidad no tienen fila en
  SQLite y pueden reintentarse en una ejecución futura.

Si se elimina `almacenamiento/`, también se pierde el estado de reanudación.

## Semillas

Las semillas viven únicamente en:

```text
arañador/semillas.txt
```

El archivo se versiona y no contiene lógica. Agregar una semilla nueva no exige
tocar código. Los hosts permitidos se derivan de las URLs del archivo.

La cosecha se ejecuta con un único comando:

```bash
uv run arañador
```

El comando lee la configuración de `.env`. No hace falta pasar rutas, niveles de
log ni opciones de Scrapy por línea de comandos.

## Políticas de cortesía

- `ROBOTSTXT_OBEY = True`.
- `ROBOTSTXT_USER_AGENT` usa el mismo User-Agent del proyecto.
- `DOWNLOAD_DELAY = 0.5` y AutoThrottle dinámico.
- `CONCURRENT_REQUESTS_PER_DOMAIN = 4`.
- Reintentos limitados con `RETRY_TIMES = 3` y respeto de `429`.
- Sin cookies y sin ejecución de JavaScript.
- `USER_AGENT` define la identificación; por defecto usa un User-Agent de
  navegador Firefox. Para un crawler académico es recomendable identificarse
  como bot con un contacto real.

No se debe iniciar una cosecha sin revisar permisos, condiciones de uso y la
política institucional del TEC.

## Uso

```bash
uv sync --python 3.12
uv run arañador
```

`uv run arañador` ejecuta la cosecha y muestra automáticamente el progreso cada
15 segundos, sin necesidad de otras terminales ni opciones adicionales.

Para generar el informe exigido por la rúbrica, después de la cosecha:

```bash
uv run arañador informe
```

Ese subcomando reúne las estadísticas del repositorio y la curva de la ley de
Zipf. Internamente son dos cálculos separados: uno lee SQLite y tamaños de
archivo; el otro recorre todo el texto para contar palabras y frecuencias.

## Problemas encontrados y cómo se resolvieron

- **La configuración dependía de una variable de entorno y de valores
  implícitos.** Se centralizó en `.env` y se cargó explícitamente con
  `python-dotenv`.
- **La base de datos requería un comando de inicialización separado.** Se
  eliminó y ahora las migraciones se aplican al abrir la conexión.
- **El paquete principal tenía un nombre largo y una estructura poco clara.** Se
  renombró a `arañador` y se separó `implementacion/`, `bibliotecas/`, `bd/` y
  `guiones/`.
- **Las arañas estaban divididas por dominio y tenían patrones hardcodeados.**
  Se reemplazaron por una sola araña agnóstica que lee `semillas.txt` y deriva
  los hosts permitidos.
- **La persistencia podía dejar archivos huérfanos o metadatos inconsistentes.**
  Se incorporó escritura atómica, `fsync`, deduplicación transaccional y
  reconciliación en los reportes.
- **La auditoría podía perderse silenciosamente.** La bitácora es obligatoria
  por defecto.
- **Las relecturas no actualizaban la frescura.** Se agregó filtro persistente y
  actualización de fechas o contenido cuando la revisita vence.

## Verificación

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

## Datos que no se versionan

`.gitignore` excluye `.env`, bases SQLite, corpus, bitácoras, resultados y
salidas locales. El código de `arañador/`, `tests/`, `.env.example`,
`pyproject.toml`, `scrapy.cfg` y `uv.lock` sí se versionan.
