#!/usr/bin/env bash
# Lista los archivos de Lucene que forman un índice de Elasticsearch, para la
# sección de comparación del funcionamiento interno.
# Uso: bash herramientas/archivos_lucene.sh rit_estandar
set -euo pipefail
INDICE="${1:-rit_estandar}"
UUID=$(curl -s "http://localhost:9200/${INDICE}/_settings" | grep -o '"uuid":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$UUID" ]; then
  echo "No se encontró el índice ${INDICE}" >&2
  exit 1
fi
echo "Índice ${INDICE} (uuid ${UUID}), shard 0:"
docker exec rit-elasticsearch ls -la "/usr/share/elasticsearch/data/indices/${UUID}/0/index"
echo
echo "Segmentos:"
curl -s "http://localhost:9200/_cat/segments/${INDICE}?v&h=segment,docs.count,docs.deleted,size,committed,searchable"
