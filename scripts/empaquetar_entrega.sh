#!/usr/bin/env bash
# Genera el paquete de entrega del curso IC-8060 (formato tar.gz).
#
# Incluye el repositorio Git completo (carpeta rit/ con .git) y los tres
# proyectos con sus fuentes, PDFs, tablas, datos, índices .idx y artefactos de
# Progra01/elasticsearch/salida/. Deja fuera entornos virtuales, dependencias,
# cachés, bitácoras, respaldos y el corpus descargado Progra00/almacenamiento/.
#
# Uso:  bash scripts/empaquetar_entrega.sh
# Salida: entrega_rit.tar.gz en la raíz del repositorio.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_NAME="$(basename "$REPO_ROOT")"
PARENT_DIR="$(dirname "$REPO_ROOT")"
OUTPUT="$REPO_ROOT/entrega_rit.tar.gz"

if [[ ! -d "$REPO_ROOT/.git" ]]; then
    echo "ERROR: no se encontró un repositorio Git en $REPO_ROOT" >&2
    exit 1
fi

for cmd in tar gzip; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "ERROR: falta el comando '$cmd'" >&2
        exit 1
    }
done

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/entrega-rit.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

PARTIAL="$TMP_DIR/entrega_rit.tar.gz"
LISTING="$TMP_DIR/contenido.txt"

# Rutas relativas a la raíz del repositorio. Los patrones con */ cubren
# cualquier profundidad; se listan también sin */ por si el miembro coincide
# exactamente con la raíz.
EXCLUDES=(
    # Entornos y dependencias
    --exclude='.venv' --exclude='*/.venv'
    --exclude='venv' --exclude='*/venv'
    --exclude='node_modules' --exclude='*/node_modules'
    --exclude='*.egg-info'
    # Cachés y artefactos de herramientas
    --exclude='__pycache__' --exclude='*/__pycache__'
    --exclude='*.py[cod]'
    --exclude='.pytest_cache' --exclude='*/.pytest_cache'
    --exclude='.ruff_cache' --exclude='*/.ruff_cache'
    --exclude='.mypy_cache' --exclude='*/.mypy_cache'
    --exclude='.ty_cache' --exclude='*/.ty_cache'
    --exclude='.coverage' --exclude='*/.coverage'
    --exclude='htmlcov' --exclude='*/htmlcov'
    --exclude='build' --exclude='*/build'
    --exclude='dist' --exclude='*/dist'
    # Configuración local (los .env.example sí se incluyen)
    --exclude='.env' --exclude='*/.env'
    # Metadatos de agentes, no forman parte de la entrega
    --exclude='.agents' --exclude='*/.agents'
    --exclude='skills-lock.json' --exclude='*/skills-lock.json'
    # Corpus descargado: demasiado grande, se restaura aparte
    --exclude='*/Progra00/almacenamiento'
    # Bitácoras de cosecha y caché regenerable del informe de Zipf
    --exclude='*/bitacoras'
    --exclude='*/Progra00/resultados/zipf/cache'
    # Respaldos, temporales y artefactos de compilación LaTeX
    --exclude='*.respaldo-*'
    --exclude='*.bak' --exclude='*.tmp' --exclude='*.orig'
    --exclude='*.swp' --exclude='*~' --exclude='.DS_Store'
    --exclude='*.log'
    --exclude='*.aux' --exclude='*.toc' --exclude='*.out'
    --exclude='*.fls' --exclude='*.fdb_latexmk' --exclude='*.synctex.gz'
    # Paquetes ya generados (evita anidar entregas anteriores)
    --exclude='*.tar.gz'
)

# Fecha fija para que dos ejecuciones sobre el mismo árbol produzcan el mismo
# archivo byte a byte (gzip -n ya omite su propia marca de tiempo).
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    ARCHIVE_MTIME="@$SOURCE_DATE_EPOCH"
else
    ARCHIVE_MTIME="@$(git -C "$REPO_ROOT" log -1 --format=%ct)"
fi

echo "==> Empaquetando '$REPO_NAME/' (esto puede tardar unos minutos)..."

set +e
LC_ALL=C tar -C "$PARENT_DIR" \
    --sort=name \
    --mtime="$ARCHIVE_MTIME" \
    --numeric-owner --owner=0 --group=0 \
    "${EXCLUDES[@]}" \
    -cf - "$REPO_NAME" | gzip -6 -n > "$PARTIAL"
STATUSES=("${PIPESTATUS[@]}")
TAR_STATUS=${STATUSES[0]}
GZIP_STATUS=${STATUSES[1]}
set -e

if [[ $GZIP_STATUS -ne 0 ]]; then
    echo "ERROR: gzip falló (código $GZIP_STATUS)" >&2
    exit 1
fi
if [[ $TAR_STATUS -gt 1 ]]; then
    echo "ERROR: tar falló (código $TAR_STATUS)" >&2
    exit 1
fi
if [[ $TAR_STATUS -eq 1 ]]; then
    echo "AVISO: tar reportó archivos que cambiaron durante la lectura; se continúa."
fi

echo "==> Verificando el contenido..."
tar tzf "$PARTIAL" > "$LISTING"

missing=0
require_member() {
    if ! grep -Fxq "$REPO_NAME/$1" "$LISTING"; then
        echo "ERROR: falta en el paquete: $1" >&2
        missing=1
    fi
}

require_member ".git/HEAD"
require_member "README.md"
require_member ".gitignore"
require_member "Progra00/Progra00.tex"
require_member "Progra00/Progra00.pdf"
require_member "Progra00/Instrucciones.pdf"
require_member "Progra01/Progra01.tex"
require_member "Progra01/Progra01.pdf"
require_member "Progra01/Instrucciones.md"
require_member "Progra01/Instrucciones.pdf"
require_member "Progra02/documento/Progra02.tex"
require_member "Progra02/documento/Progra02.pdf"
require_member "Progra02/Instrucciones.md"
require_member "Progra02/Instrucciones.pdf"
require_member "Progra01/propio/salida/indice_minimo.idx"
require_member "Progra01/propio/salida/indice_estandar.idx"
require_member "Progra01/propio/salida/indice_avanzado.idx"
require_member "Progra01/elasticsearch/salida/benchmark_elasticsearch.json"

# Comprobaciones por prefijo: tablas y artefactos de Elasticsearch.
if ! grep -q "^$REPO_NAME/Progra01/tablas/." "$LISTING"; then
    echo "ERROR: no hay archivos en Progra01/tablas/" >&2
    missing=1
fi
if ! grep -q "^$REPO_NAME/Progra02/documento/tablas/." "$LISTING"; then
    echo "ERROR: no hay archivos en Progra02/documento/tablas/" >&2
    missing=1
fi
if ! grep -q "^$REPO_NAME/Progra01/elasticsearch/salida/." "$LISTING"; then
    echo "ERROR: no hay artefactos en Progra01/elasticsearch/salida/" >&2
    missing=1
fi

# Nada de lo excluido debe haberse colado.
for bad in "/.venv/" "/node_modules/" "/__pycache__/" "/Progra00/almacenamiento/"; do
    if grep -q "$bad" "$LISTING"; then
        echo "ERROR: se incluyó contenido excluido: *$bad*" >&2
        missing=1
    fi
done

if [[ $missing -ne 0 ]]; then
    echo "ERROR: la verificación del paquete falló; no se generó $OUTPUT" >&2
    exit 1
fi

mv -f "$PARTIAL" "$OUTPUT"

SIZE_HUMAN="$(du -h "$OUTPUT" | cut -f1)"
SIZE_BYTES="$(stat -c '%s' "$OUTPUT")"
ENTRIES="$(wc -l < "$LISTING")"

echo
echo "==> Paquete generado: $OUTPUT"
echo "    Tamaño:   $SIZE_HUMAN ($SIZE_BYTES bytes)"
echo "    Entradas: $ENTRIES"
echo "    Raíz interna: $REPO_NAME/"
echo
echo "NOTA: el corpus NO se incluye en el paquete."
echo "  Se excluyó Progra00/almacenamiento/ por tamaño (varios GiB)."
echo "  Los indexadores (Progra01/propio y Progra01/elasticsearch) leen los"
echo "  documentos desde esa ruta, así que al descomprimir el paquete hay que"
echo "  restaurar o copiar Progra00/almacenamiento/ (metadatos_araña.db y"
echo "  repositorio/) antes de volver a indexar. Los índices .idx y los"
echo "  artefactos de salida sí vienen incluidos."
