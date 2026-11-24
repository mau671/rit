# Recursos y contrato de normalización

Estos archivos son la **única fuente** de las listas de normalización: los lee el
indexador propio (`src/indexador/normalizador.py`) y los debe leer el indexador de
Lucene. Si una lista cambia, cambia para ambos, y `scripts/regenerar_todo.sh`
reconstruye los índices.

| Archivo | Contenido | Formato |
|---|---|---|
| `stopwords_es.txt`, `stopwords_en.txt` | Palabras vacías | Una por línea, `#` comenta (lo que lee `WordlistLoader.getWordSet(reader, "#")` de Lucene) |
| `sinonimos.txt` | Variantes de obras, formatos y técnica → descriptor | `v1, v2 => descriptor` (formato Solr) |
| `tesauro.txt` | Oficios y créditos → descriptor | `v1, v2 => descriptor` (formato Solr) |

Los dos archivos de reglas se leen con `SolrSynonymParser` de Lucene. Solo hay reglas
de un token: no se mapean frases.

## Pipeline (el contrato)

Cada paso se aplica en este orden. Un indexador es correcto si pasa
`casos_prueba/normalizacion.json` (`uv run python scripts/verificar_casos.py`,
o con `--comando` para otro programa).

1. **Limpieza Unicode.** NFKC; `’ ‘ \`` → `'`; `“ ” « »` → `"`; `—` y `–` → ` - `.
2. **Fechas** (si `normalizar_fechas`). Cada fecha completa se reemplaza por ` <FECHA> `:
   `dd/mm/aaaa`, `dd-mm-aaaa`, `aaaa-mm-dd`, `25 de diciembre de 1977`,
   `December 25, 1977`, `25th of December 1977`. Luego cada año 1888–2099 suelto
   (`\b(188[89]|189\d|19\d{2}|20\d{2})\b`) pasa a ` <ANIO_aaaa> `.
3. **Números** (si `normalizar_numeros`). `\b\d+(?:[.,]\d+)?\b` → ` <NUM> ` (`modo_numeros = token`),
   se elimina (`descartar`) o se deja (`preservar`).
4. **Tokenización.** Regex `<[A-Z0-9_]+>|[^\W_]+(?:[-'_][^\W_]+)*`: los marcadores `<...>`
   y las palabras con guion, apóstrofe o guion bajo internos son un solo token.
5. **Minúsculas** (si `minusculas`), salvo los marcadores `<...>`.
6. **Plegado de diacríticos** (si `remover_acentos`). Se quitan las marcas combinantes
   (NFKD) y se recompone (NFC), **excepto la tilde de la `ñ`**: `año` ≠ `ano`.
7. **Clíticos del inglés** (si `stopwords`). Se quita `'s 're 've 'll 'd 'm` al final
   (`it's` → `it`, `O'Brien's` → `o'brien`); los marcadores no se tocan.
8. **Idioma para el stemming.** Fijo si `idioma = es|en`; con `auto`, se cuentan los
   tokens de los **primeros 300** que están en cada lista de stopwords (se usan las dos listas
   completas, aunque `stopwords = false`); gana `en` solo si tiene **más** votos que `es`
   (un empate o ningún voto da `es`). Un documento tiene un solo idioma.
9. **Por cada token**, en este orden:
   1. Se descarta si su longitud no está en `[longitud_minima_token, longitud_maxima_token]`
      (por defecto 2–50); los marcadores `<...>` no se miden.
   2. Se descarta si está en las stopwords de `idiomas_stopwords` (si `stopwords`).
   3. Si `expandir_sinonimos`, se reemplaza por su descriptor de `sinonimos.txt`/`tesauro.txt`
      (si no está, se prueba con el token plegado de diacríticos). Si una variante está en los
      dos archivos, gana `tesauro.txt`.
   4. Si `stemming`, se reduce con Snowball. Los descriptores `realizador, interprete,
      pelicula, largometraje, cortometraje, documental, animacion, suspenso, banda_sonora,
      fotografia, edicion` se reducen con el stemmer **español** y `film, screenplay,
      screenwriter, cast, producer` con el **inglés**, sin importar el idioma del documento.
      Los marcadores `<...>` no se reducen.

La consulta recibe exactamente el mismo pipeline que el documento.

## Las tres configuraciones

| Archivo | Nombre | Qué activa |
|---|---|---|
| `configuraciones/minimo.conf` | mínima | pasos 1, 4, 5 |
| `configuraciones/estandar.conf` | intermedia | + clíticos, stopwords ES+EN, Snowball |
| `configuraciones/avanzado.conf` | agresiva | + fechas, números, plegado de tildes, sinónimos y tesauro |

## Notas para el indexador de Lucene

- Los pasos 2, 3, 6 y 7 se pueden hacer con `PatternReplaceCharFilter` / `PatternReplaceFilter`
  con las mismas expresiones (la sintaxis de Java acepta las de arriba tal cual).
- Para la `ñ` hace falta un plegado propio (por ejemplo `MappingCharFilter` con las vocales
  acentuadas y la diéresis): `ASCIIFoldingFilter` también convierte `ñ` en `n`.
- El analizador que se le pasa a `SolrSynonymParser` para leer las reglas no debe quitar
  stopwords ni reducir: las reglas están escritas con las palabras tal cual.
- El idioma por documento (paso 8) se decide antes de analizar, con los 300 primeros tokens,
  y no se puede resolver con un `Analyzer` por campo sin ese conteo previo.
