"""Módulo para medir y comparar el rendimiento de los 3 índices creados.

Mide:
- Tiempo de ejecución (wall-clock y CPU time).
- Consumo de memoria RAM (pico RSS mediante resource.getrusage).
- Tamaño final del archivo binario (.idx) en disco.
- Tamaño del vocabulario (|V|) y número total de postings.
- Métricas derivadas (promedio de postings por término, bytes por documento).
"""

from __future__ import annotations

import resource
import time
from dataclasses import dataclass
from pathlib import Path

from .configuracion import ConfiguracionIndexador
from .spimi import MotorIndexadorSPIMI


@dataclass(slots=True)
class MetricasIndice:
    """Métricas de rendimiento y caracterización de un índice."""

    nombre_politica: str
    descripcion: str
    ruta_archivo: str
    total_documentos: int
    tamano_vocabulario: int
    total_postings: int
    tamano_archivo_bytes: int
    tamano_archivo_mb: float
    tiempo_segundos: float
    tiempo_cpu_segundos: float
    memoria_pico_mb: float
    postings_por_termino_promedio: float
    bytes_por_documento_promedio: float


def obtener_memoria_pico_mb() -> float:
    """Retorna el pico máximo de memoria residente (RSS) en MB en Linux."""
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    # En Linux ru_maxrss se expresa en kilobytes
    return float(rusage.ru_maxrss / 1024.0)


def ejecutar_benchmark_politica(
    config: ConfiguracionIndexador,
    limite_documentos: int = 0,
    callback_progreso: bool = True,
) -> MetricasIndice:
    """Ejecuta la indexación para una política específica y registra sus métricas."""
    ruta_salida = Path(config.rutas.indice_salida)
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)

    inicio_cpu = time.process_time()
    inicio_wall = time.perf_counter()

    stats = MotorIndexadorSPIMI.indexar_desde_configuracion(
        config=config,
        limite_documentos=limite_documentos,
        callback_progreso=(
            (lambda docs, msg: print(f"  [{config.nombre_politica}] {docs:,} docs - {msg}"))
            if callback_progreso
            else None
        ),
    )

    tiempo_wall = time.perf_counter() - inicio_wall
    tiempo_cpu = time.process_time() - inicio_cpu
    memoria_mb = obtener_memoria_pico_mb()

    total_docs = int(stats["total_docs"])
    total_terms = int(stats["total_terminos"])
    total_posts = int(stats["total_postings"])
    tam_bytes = int(stats["tamano_bytes"])

    postings_prom = float(total_posts / total_terms) if total_terms > 0 else 0.0
    bytes_doc_prom = float(tam_bytes / total_docs) if total_docs > 0 else 0.0

    return MetricasIndice(
        nombre_politica=config.nombre_politica,
        descripcion=config.descripcion,
        ruta_archivo=str(ruta_salida),
        total_documentos=total_docs,
        tamano_vocabulario=total_terms,
        total_postings=total_posts,
        tamano_archivo_bytes=tam_bytes,
        tamano_archivo_mb=round(tam_bytes / (1024 * 1024), 2),
        tiempo_segundos=round(tiempo_wall, 3),
        tiempo_cpu_segundos=round(tiempo_cpu, 3),
        memoria_pico_mb=round(memoria_mb, 2),
        postings_por_termino_promedio=round(postings_prom, 2),
        bytes_por_documento_promedio=round(bytes_doc_prom, 2),
    )


def generar_tabla_comparativa_texto(metricas: list[MetricasIndice]) -> str:
    """Genera una tabla ASCII legible con la comparativa de los índices."""
    lineas = [
        "=" * 105,
        f"{'COMPARACIÓN DE RENDIMIENTO DE LOS 3 ÍNDICES':^105}",
        "=" * 105,
        f"{'Política':<12} | {'Docs':>8} | {'Vocabulario':>12} | {'Postings':>12} | {'Tamaño':>10} | {'Tiempo (s)':>10} | {'RAM (MB)':>10} | {'B/Doc':>8}",
        "-" * 105,
    ]
    for m in metricas:
        lineas.append(
            f"{m.nombre_politica:<12} | {m.total_documentos:>8,d} | {m.tamano_vocabulario:>12,d} | "
            f"{m.total_postings:>12,d} | {f'{m.tamano_archivo_mb:.2f} MB':>10} | "
            f"{m.tiempo_segundos:>10.2f} | {m.memoria_pico_mb:>10.1f} | {m.bytes_por_documento_promedio:>8.1f}"
        )
    lineas.append("=" * 105)
    return "\n".join(lineas)
