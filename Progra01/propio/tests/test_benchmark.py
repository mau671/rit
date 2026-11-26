"""Pruebas del módulo de benchmark y de la medición de memoria por política."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from indexador.benchmark import (
    MetricasIndice,
    ejecutar_benchmark_politica,
    generar_tabla_comparativa_texto,
    obtener_memoria_pico_mb,
    reiniciar_memoria_pico,
)
from indexador.configuracion import ConfiguracionIndexador


def test_memoria_pico_es_positiva() -> None:
    assert obtener_memoria_pico_mb() > 0.0


@pytest.mark.skipif(sys.platform != "linux", reason="clear_refs solo existe en Linux")
def test_reiniciar_memoria_pico_en_linux() -> None:
    assert reiniciar_memoria_pico() is True


def test_tabla_comparativa_incluye_cada_politica() -> None:
    metricas = [
        MetricasIndice(
            nombre_politica="minimo",
            descripcion="p1",
            ruta_archivo="a.idx",
            total_documentos=10,
            tamano_vocabulario=100,
            total_postings=1000,
            tamano_archivo_bytes=2048,
            tamano_archivo_mb=0.0,
            tiempo_segundos=1.0,
            tiempo_cpu_segundos=1.0,
            memoria_pico_mb=10.0,
            postings_por_termino_promedio=10.0,
            bytes_por_documento_promedio=204.8,
        ),
        MetricasIndice(
            nombre_politica="estandar",
            descripcion="p2",
            ruta_archivo="b.idx",
            total_documentos=10,
            tamano_vocabulario=80,
            total_postings=800,
            tamano_archivo_bytes=1024,
            tamano_archivo_mb=0.0,
            tiempo_segundos=2.0,
            tiempo_cpu_segundos=2.0,
            memoria_pico_mb=12.0,
            postings_por_termino_promedio=10.0,
            bytes_por_documento_promedio=102.4,
        ),
    ]
    tabla = generar_tabla_comparativa_texto(metricas)
    assert "minimo" in tabla
    assert "estandar" in tabla
    assert "COMPARACIÓN" in tabla


def test_benchmark_sobre_coleccion_real(tmp_path: Path) -> None:
    """Indexa 5 documentos reales de Progra00 con la política mínima."""
    ruta_bd = Path("../../Progra00/almacenamiento/metadatos_araña.db")
    ruta_repo = Path("../../Progra00/almacenamiento/repositorio")
    if not ruta_bd.is_file() or not ruta_repo.is_dir():
        pytest.skip("La colección de Progra00 no está disponible")

    config = ConfiguracionIndexador.crear_defecto("minimo")
    config.rutas.base_datos = str(ruta_bd)
    config.rutas.repositorio = str(ruta_repo)
    config.rutas.indice_salida = str(tmp_path / "indice.idx")
    config.rutas.directorio_spimi = str(tmp_path / "spimi")
    config.spimi.docs_por_bloque = 2

    metricas = ejecutar_benchmark_politica(
        config, limite_documentos=5, callback_progreso=False
    )

    assert metricas.total_documentos == 5
    assert metricas.tamano_vocabulario > 0
    assert metricas.total_postings > 0
    assert metricas.tiempo_segundos > 0
    assert metricas.memoria_pico_mb > 0
    assert metricas.bytes_por_documento_promedio > 0
    assert Path(metricas.ruta_archivo).is_file()

    # El JSON que consume el informe debe ser serializable tal cual.
    assert json.loads(json.dumps(asdict(metricas)))
