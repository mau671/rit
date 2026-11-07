# Progra01 - Indexador Propio (Motor Encuadre)

Implementación propia de bajo nivel de un índice invertido en formato binario (`.idx`) estructurado a mano con `struct.pack/unpack` Little-Endian, modelo vectorial $\text{TF}/\text{IDF}$, cálculo de normas euclidianas para similitud coseno y algoritmo SPIMI (*Single-Pass In-Memory Indexing*).

Curso: IC-8060 Recuperación de Información Textual  
Tecnológico de Costa Rica (TEC)

## Requisitos y entorno

El proyecto se gestiona con `uv` y Python 3.13+:

```bash
uv sync
```

## Comandos disponibles

```bash
# 1. Construir un índice con una política específica
uv run indexador indexar --config configuraciones/estandar.conf --limite 1000

# 2. Inspeccionar la estructura binaria a bajo nivel (offsets, términos, postings)
uv run indexador inspeccionar salida/indice_estandar.idx --termino "film"

# 3. Realizar una consulta vectorial con similitud coseno
uv run indexador buscar salida/indice_estandar.idx "film director" --top 5

# 4. Ejecutar el benchmark comparativo de las 3 políticas
uv run indexador benchmark --limite 500
```

## Pruebas automatizadas y linters

```bash
uv run pytest
uv run ruff check
```

## Documento del proyecto

El informe formal se encuentra en `Progra01.tex` y su versión compilada en `Progra01.pdf`.
Para recompilar con Tectonic:

```bash
tectonic Progra01.tex
```
