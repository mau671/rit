# Elasticsearch 8.15.3 con el plugin analysis-icu, necesario para replicar
# la normalización Unicode NFKC (icu_normalizer) y la eliminación de tildes
# (icu_transform) que hace el indexador propio.
FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.3
RUN bin/elasticsearch-plugin install --batch analysis-icu
