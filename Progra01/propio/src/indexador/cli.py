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
import sys
from dataclasses import asdict
from pathlib import Path

from .benchmark import (
    ejecutar_benchmark_politica,
    generar_tabla_comparativa_texto,
)
from .buscador import BuscadorVectorial
from .configuracion import ConfiguracionIndexador
from .lector_binario import LectorIndiceBinario
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

    print(LectorIndiceBinario.inspeccionar(ruta_idx, num_muestras=args.muestras))

    with LectorIndiceBinario(ruta_idx) as lector:
        normalizacion = lector.normalizacion
        activos = [
            nombre
            for nombre, activo in (
                ("minúsculas", normalizacion.minusculas),
                ("eliminar puntuación", normalizacion.eliminar_puntuacion),
                ("remover acentos", normalizacion.remover_acentos),
                ("stopwords", normalizacion.stopwords),
                ("stemming", normalizacion.stemming),
                ("números", normalizacion.normalizar_numeros),
                ("fechas", normalizacion.normalizar_fechas),
                ("sinónimos", normalizacion.expandir_sinonimos),
            )
            if activo
        ]
        print("\nNORMALIZACIÓN DECLARADA EN EL ENCABEZADO:")
        print(
            f"  - Flags 0x{lector.encabezado.flags_normalizacion:04X}: "
            f"{', '.join(activos) or 'ninguna'}"
        )

        if args.verificar:
            problemas = lector.verificar_estructura()
            if problemas:
                print("\nVERIFICACIÓN DE INTEGRIDAD: FALLÓ")
                for problema in problemas:
                    print(f"  - {problema}")
                return 1
            print("\nVERIFICACIÓN DE INTEGRIDAD: OK (offsets, vocabulario y postings consistentes)")

        if args.termino:
            termino = lector.buscar_termino(args.termino)
            if not termino:
                print(f"\n[!] El término '{args.termino}' NO se encuentra en el vocabulario.")
                parecidos = lector.terminos_con_prefijo(args.termino[:4])
                if parecidos:
                    print("    Términos parecidos: " + ", ".join(t.termino for t in parecidos))
            else:
                print(f"\n[Término: '{termino.termino}']")
                print(f"  - DF: {termino.df:,} documentos")
                print(f"  - IDF: {termino.idf:.4f}")
                print(f"  - Offset postings: 0x{termino.offset_postings:08x} ({termino.offset_postings:,} bytes)")
                print(f"  - Tamaño postings: {termino.bytes_postings:,} bytes")

                postings = lector.leer_postings(termino)
                print(f"  - Muestra de primeros {min(len(postings), 10)} postings:")
                for posting in postings[:10]:
                    print(
                        f"      doc_id={posting.doc_id:<6} tf={posting.tf:<4} "
                        f"peso_tfidf={posting.peso_tfidf:.4f}"
                    )
    return 0


def comando_buscar(args: argparse.Namespace) -> int:
    """Ejecuta una búsqueda con el modelo vectorial y similitud coseno."""
    ruta_idx = Path(args.archivo)
    if not ruta_idx.is_file():
        print(f"Error: Archivo de índice no encontrado: {ruta_idx}", file=sys.stderr)
        return 1

    with BuscadorVectorial.desde_archivo(ruta_idx) as buscador:
        encabezado = buscador.lector.encabezado
        print(
            f"==> Índice: {ruta_idx.name} "
            f"(Docs: {encabezado.total_docs:,}, Vocabulario: {encabezado.total_terminos:,})"
        )
        respuesta = buscador.buscar(args.consulta, top=args.top)
        print(f"==> Consulta: '{respuesta.consulta}' -> Tokens: {respuesta.tokens}")

        if not respuesta.tokens:
            print("No se extrajeron términos válidos de la consulta.")
            return 0

        if respuesta.terminos_ausentes:
            print(f"==> Términos ausentes del vocabulario: {respuesta.terminos_ausentes}")

        if not respuesta.resultados:
            print("Ninguno de los términos de la consulta se encuentra en el índice.")
            if respuesta.sugerencias:
                print(f"Sugerencias del vocabulario: {', '.join(respuesta.sugerencias)}")
            return 0

        print(f"\nResultados más relevantes (Top {len(respuesta.resultados)}):")
        print(f"{'Doc ID':<8} | {'Similitud':>10} | {'Tokens':>8} | {'Hash URL (SHA-256)':<64}")
        print("-" * 98)
        for resultado in respuesta.resultados:
            print(
                f"{resultado.doc_id:<8} | {resultado.similitud:>10.4f} | "
                f"{resultado.num_tokens:>8,} | {resultado.hash_url:<64}"
            )
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
    print(" INICIANDO BENCHMARK DE LAS 3 POLÍTICAS DE INDEXACIÓN")
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
    p_insp.add_argument(
        "--verificar",
        action="store_true",
        help="Comprobar offsets, orden del vocabulario y bloques de postings",
    )
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
