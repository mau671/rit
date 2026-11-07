"""Línea de comandos (CLI) del indexador binario Encuadre.

Comandos disponibles:
- `indexar`: Ejecuta la creación del índice binario según un archivo .conf.
- `inspeccionar`: Muestra la estructura interna de un archivo .idx (encabezado, offsets, términos).
- `buscar`: Realiza una consulta puntual con similitud coseno contra un archivo .idx.
- `benchmark`: Ejecuta y compara las 3 políticas de indexación registrando tiempos y tamaños.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

from .benchmark import (
    ejecutar_benchmark_politica,
    generar_tabla_comparativa_texto,
)
from .configuracion import ConfiguracionIndexador
from .lector_binario import LectorIndiceBinario
from .normalizador import Normalizador
from .spimi import MotorIndexadorSPIMI


def comando_indexar(args: argparse.Namespace) -> int:
    """Ejecuta el pipeline de indexación."""
    ruta_conf = Path(args.config)
    if not ruta_conf.is_file():
        print(f"Error: Archivo de configuración no encontrado: {ruta_conf}", file=sys.stderr)
        return 1

    print(f"==> Cargando configuración: {ruta_conf}")
    config = ConfiguracionIndexador.cargar_desde_archivo(ruta_conf)

    if args.salida:
        config.rutas.indice_salida = args.salida

    print(f"==> Política: {config.nombre_politica} ({config.descripcion})")
    print(f"==> Base de datos: {config.rutas.base_datos}")
    print(f"==> Repositorio: {config.rutas.repositorio}")
    print(f"==> Archivo de salida: {config.rutas.indice_salida}")
    if args.limite > 0:
        print(f"==> Límite de documentos: {args.limite:,}")

    def progreso(n_docs: int, msg: str) -> None:
        print(f"  [{n_docs:,} docs] {msg}")

    stats = MotorIndexadorSPIMI.indexar_desde_configuracion(
        config=config,
        limite_documentos=args.limite,
        callback_progreso=progreso,
    )

    tam_mb = stats["tamano_bytes"] / (1024 * 1024)
    print("\n✓ Indexación completada exitosamente:")
    print(f"  - Documentos indexados: {stats['total_docs']:,}")
    print(f"  - Términos únicos (|V|): {stats['total_terminos']:,}")
    print(f"  - Total postings: {stats['total_postings']:,}")
    print(f"  - Tamaño archivo: {tam_mb:.2f} MB ({stats['tamano_bytes']:,} bytes)")
    print(f"  - Tiempo total: {stats['tiempo_segundos']:.2f} segundos")
    print(f"  - Archivo binario: {config.rutas.indice_salida}")
    return 0


def comando_inspeccionar(args: argparse.Namespace) -> int:
    """Inspecciona el formato binario a bajo nivel de un archivo .idx."""
    ruta_idx = Path(args.archivo)
    if not ruta_idx.is_file():
        print(f"Error: Archivo no encontrado: {ruta_idx}", file=sys.stderr)
        return 1

    informe = LectorIndiceBinario.inspeccionar(ruta_idx, num_muestras=args.muestras)
    print(informe)

    if args.termino:
        with LectorIndiceBinario(ruta_idx) as lector:
            termino = lector.buscar_termino(args.termino)
            if not termino:
                print(f"\n[!] El término '{args.termino}' NO se encuentra en el vocabulario.")
            else:
                print(f"\n[Término: '{termino.termino}']")
                print(f"  - DF: {termino.df:,} documentos")
                print(f"  - IDF: {termino.idf:.4f}")
                print(f"  - Offset postings: 0x{termino.offset_postings:08x} ({termino.offset_postings:,} bytes)")
                print(f"  - Tamaño postings: {termino.bytes_postings:,} bytes")

                postings = lector.leer_postings(termino)
                print(f"  - Muestra de primeros {min(len(postings), 10)} postings:")
                for p in postings[:10]:
                    print(f"      doc_id={p.doc_id:<6} tf={p.tf:<4} peso_tfidf={p.peso_tfidf:.4f}")
    return 0


def comando_buscar(args: argparse.Namespace) -> int:
    """Ejecuta una búsqueda con el modelo vectorial y similitud coseno."""
    ruta_idx = Path(args.archivo)
    if not ruta_idx.is_file():
        print(f"Error: Archivo de índice no encontrado: {ruta_idx}", file=sys.stderr)
        return 1

    with LectorIndiceBinario(ruta_idx) as lector:
        enc = lector.encabezado
        print(f"==> Índice: {ruta_idx.name} (Docs: {enc.total_docs:,}, Vocabulario: {enc.total_terminos:,})")

        # Normalizar consulta con la misma configuración del índice
        # Reconstruir normalizador a partir de los flags del encabezado
        normalizador = Normalizador()
        tokens_consulta = normalizador.normalizar_texto(args.consulta)
        print(f"==> Consulta: '{args.consulta}' -> Tokens: {tokens_consulta}")

        if not tokens_consulta:
            print("No se extrajeron términos válidos de la consulta.")
            return 0

        # Ponderación de la consulta
        conteo_q: dict[str, int] = {}
        for t in tokens_consulta:
            conteo_q[t] = conteo_q.get(t, 0) + 1

        pesos_q: dict[str, float] = {}
        acum_q_norm = 0.0

        terminos_validos = []
        for t, tf_q in conteo_q.items():
            entrada_t = lector.buscar_termino(t)
            if entrada_t:
                peso = (1.0 + math.log(tf_q)) * entrada_t.idf
                pesos_q[t] = peso
                acum_q_norm += peso * peso
                terminos_validos.append(entrada_t)

        if not terminos_validos:
            print("Ninguno de los términos de la consulta se encuentra en el índice.")
            return 0

        norma_q = math.sqrt(acum_q_norm)
        scores_docs: dict[int, float] = {}

        for entrada_t in terminos_validos:
            peso_t_q = pesos_q[entrada_t.termino]
            postings = lector.leer_postings(entrada_t)
            for p in postings:
                scores_docs[p.doc_id] = scores_docs.get(p.doc_id, 0.0) + (p.peso_tfidf * peso_t_q)

        # Normalizar por norma del documento y de la consulta
        top_k = []
        for doc_id, dot_prod in scores_docs.items():
            doc_meta = lector.leer_documento(doc_id)
            norma_d = doc_meta.norma_vectorial if doc_meta.norma_vectorial > 0 else 1.0
            similitud = dot_prod / (norma_q * norma_d)
            top_k.append((doc_id, similitud, doc_meta.hash_url.hex()))

        top_k.sort(key=lambda x: x[1], reverse=True)

        print(f"\nResultados más relevantes (Top {min(args.top, len(top_k))}):")
        print(f"{'Doc ID':<8} | {'Similitud':>10} | {'Hash URL (SHA-256)':<64}")
        print("-" * 88)
        for d_id, sim, hash_hex in top_k[: args.top]:
            print(f"{d_id:<8} | {sim:>10.4f} | {hash_hex}")
    return 0


def comando_benchmark(args: argparse.Namespace) -> int:
    """Ejecuta y compara las 3 políticas de indexación."""
    dir_confs = Path("configuraciones")
    rutas_confs = [
        dir_confs / "minimo.conf",
        dir_confs / "estandar.conf",
        dir_confs / "avanzado.conf",
    ]

    metricas_todas = []
    print("================================================================================")
    print(" INICIANDO BENCHMARK DE LAS 3 POLÍTICAS DE INDEXACIÓN (PERSONA A)")
    print("================================================================================")

    for ruta in rutas_confs:
        if not ruta.is_file():
            print(f"[!] Archivo {ruta} no encontrado. Creando configuración por defecto...")
            nombre = ruta.stem
            cfg = ConfiguracionIndexador.crear_defecto(nombre)
            cfg.rutas.indice_salida = f"salida/indice_{nombre}.idx"
            cfg.guardar_en_archivo(ruta)
        else:
            cfg = ConfiguracionIndexador.cargar_desde_archivo(ruta)

        print(f"\n--> Ejecutando política: {cfg.nombre_politica.upper()} ({cfg.descripcion})")
        m = ejecutar_benchmark_politica(cfg, limite_documentos=args.limite, callback_progreso=True)
        metricas_todas.append(m)

    tabla = generar_tabla_comparativa_texto(metricas_todas)
    print("\n" + tabla)

    # Guardar métricas en JSON
    ruta_json = Path(args.salida_json)
    ruta_json.parent.mkdir(parents=True, exist_ok=True)
    datos_exportar = [asdict(m) for m in metricas_todas]
    ruta_json.write_text(json.dumps(datos_exportar, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Métricas guardadas en: {ruta_json}")
    return 0


def main() -> None:
    """Punto de entrada principal de la CLI."""
    parser = argparse.ArgumentParser(
        prog="indexador",
        description="Indexador Propio en Formato Binario Manual - Motor Encuadre (IC-8060)",
    )
    subparsers = parser.add_subparsers(dest="subcomando", required=True)

    # Subcomando: indexar
    p_indexar = subparsers.add_parser("indexar", help="Construir índice binario desde .conf")
    p_indexar.add_argument("-c", "--config", required=True, help="Ruta al archivo .conf estilo Linux")
    p_indexar.add_argument("-o", "--salida", default=None, help="Sobrescribir ruta del archivo .idx")
    p_indexar.add_argument("-l", "--limite", type=int, default=0, help="Límite de documentos (0 = todos)")
    p_indexar.set_defaults(func=comando_indexar)

    # Subcomando: inspeccionar
    p_insp = subparsers.add_parser("inspeccionar", help="Examinar estructura binaria y hexdump de un .idx")
    p_insp.add_argument("archivo", help="Ruta al archivo binario .idx")
    p_insp.add_argument("-t", "--termino", default=None, help="Buscar término específico en el vocabulario")
    p_insp.add_argument("-m", "--muestras", type=int, default=5, help="Número de muestras a listar")
    p_insp.set_defaults(func=comando_inspeccionar)

    # Subcomando: buscar
    p_buscar = subparsers.add_parser("buscar", help="Consultar índice con similitud coseno")
    p_buscar.add_argument("archivo", help="Ruta al archivo binario .idx")
    p_buscar.add_argument("consulta", help="Texto de la consulta de búsqueda")
    p_buscar.add_argument("-k", "--top", type=int, default=10, help="Cantidad de resultados a retornar")
    p_buscar.set_defaults(func=comando_buscar)

    # Subcomando: benchmark
    p_bench = subparsers.add_parser("benchmark", help="Ejecutar y medir las 3 políticas de indexación")
    p_bench.add_argument("-l", "--limite", type=int, default=0, help="Límite de documentos para prueba rápida")
    p_bench.add_argument(
        "-o",
        "--salida-json",
        default="salida/benchmark_indices.json",
        help="Archivo JSON donde registrar las métricas",
    )
    p_bench.set_defaults(func=comando_benchmark)

    args = parser.parse_args()
    codigo = args.func(args)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
