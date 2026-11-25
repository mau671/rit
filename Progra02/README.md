# Progra02 — Buscador Encuadre

Interfaz de búsqueda y resultados del Proyecto 3 (IC-8060 Recuperación de
Información Textual, TEC). Busca sobre los índices de Elasticsearch que
construye `Progra01-ES` y enlaza a las páginas originales.

- Descripción y decisiones de diseño: [`docs/buscador.md`](docs/buscador.md)
- Formato de la API para las interfaces de evaluación y métricas: [`docs/contrato-api.md`](docs/contrato-api.md)

## Estructura

```
Progra02/
├── buscador.conf        configuración (formato Linux)
├── src/
│   ├── servidor.js      servidor web y API
│   ├── busqueda.js      ranking coseno, búsqueda avanzada y facetas
│   ├── consulta.js      sintaxis avanzada
│   ├── fragmentos.js    fragmentos con coincidencias resaltadas
│   └── configuracion.js lector del .conf
├── public/              página (HTML, CSS y JavaScript sin dependencias)
├── docs/
├── test/
└── salida/consultas.jsonl   registro de búsquedas (se crea al usarlo)
```

Va en la raíz del repositorio, al lado de la araña:

```
rit/
├── Progra00/almacenamiento/repositorio/
├── Progra01/Progra01-ES/      (construye los índices)
└── Progra02/                  (este proyecto)
```

## Uso

1. Elasticsearch corriendo y los índices construidos (desde `Progra01/Progra01-ES`):

   ```bash
   docker compose up -d --build
   npm run benchmark          # o indexar solo la política que se vaya a usar
   ```

2. Instalar y levantar el buscador (desde `Progra02`):

   ```bash
   npm install
   npm test
   npm start
   ```

3. Abrir http://localhost:3000

Para usar otro archivo de configuración: `node src/servidor.js otra.conf`.
También se puede cambiar el puerto o el servidor de Elasticsearch con las
variables de entorno `PUERTO` y `ELASTICSEARCH_URL`.

## Sintaxis de búsqueda

| Escribí | Para |
|---|---|
| `director terror` | buscar por relevancia (similitud coseno) |
| `"frase exacta"` | que las palabras aparezcan juntas y en orden* |
| `+kubrick` | que la palabra sí o sí aparezca |
| `-remake` | descartar documentos que la tengan |
| `dominio:imsdb.com` | solo resultados de ese sitio |
| `idioma:es` / `idioma:en` | solo documentos en ese idioma |

\* Requiere que el índice se haya construido con `guardar_posiciones = true`.
Si no, las palabras se buscan sueltas y la página lo avisa.

## Configuración (`buscador.conf`)

| Sección | Clave | Para qué |
|---|---|---|
| `[servidor]` | `puerto` | puerto de la página |
| | `registrar_consultas`, `archivo_registro` | guardar cada búsqueda en JSONL |
| `[elasticsearch]` | `url` | servidor de Elasticsearch |
| | `indice_por_defecto`, `indices_disponibles` | índices que se pueden escoger |
| `[coleccion]` | `repositorio` | textos de la araña, para los fragmentos |
| `[modelo]` | `esquema_tf`, `esquema_idf` | deben coincidir con los del índice |
| `[resultados]` | `por_pagina`, `caracteres_fragmento`, `fragmentos_por_resultado` | presentación |

## Problemas comunes

- **"No hay conexión con Elasticsearch"**: levantarlo con
  `docker compose up -d` desde `Progra01/Progra01-ES`.
- **"El índice no existe"** o un índice aparece como "no construido":
  construirlo con `node src/cli.js indexar -c configuraciones/<politica>.conf`
  en `Progra01-ES`.
- **Resultados sin vista previa**: la ruta de `repositorio` en
  `buscador.conf` no apunta a la carpeta de la araña.
