"""Calcula frecuencias de palabras y ajusta la ley de Zipf sobre archivos de texto.

El corpus se lee archivo por archivo, completo, sin concatenar documentos. El conteo
puede repartirse entre varios procesos, informar avance por ``stderr`` y reutilizar una
caché incremental opcional. La gráfica se genera con el backend ``Agg`` de Matplotlib
(importado de forma perezosa) y, por tanto, no necesita GUI, escritorio ni conexión de
red.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # Permite ejecutar el archivo directamente.
    _RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
    if str(_RAIZ_PROYECTO) not in sys.path:
        sys.path.insert(0, str(_RAIZ_PROYECTO))

from arañador.entorno import leer_entero  # noqa: E402
from arañador.implementacion.utilidades.rutas import resolver_ruta_datos  # noqa: E402

RUTA_ENTRADA_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "repositorio")
RUTA_SALIDA_PREDETERMINADA = resolver_ruta_datos("resultados", "zipf")
PATRON_PALABRA = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", flags=re.UNICODE)

# Tamaños de lote: acotan la memoria de cada tarea y equilibran la carga entre procesos.
_MAX_ARCHIVOS_POR_LOTE = 1500
_MAX_BYTES_POR_LOTE = 8 * 1024 * 1024
# Cada cuántos documentos se informa progreso cuando se cuenta en un solo proceso.
_INTERVALO_PROGRESO = 512


class ErrorZipf(RuntimeError):
    """Error de entrada o procesamiento que puede mostrarse claramente en la consola."""


@dataclass(frozen=True, slots=True)
class FrecuenciaPalabra:
    """Entrada de la curva empírica de Zipf."""

    rango: int
    palabra: str
    frecuencia: int
    frecuencia_relativa: float


@dataclass(frozen=True, slots=True)
class ResultadoZipf:
    """Métricas globales y principales frecuencias de una colección de documentos."""

    documentos: int
    bytes_totales: int
    total_palabras: int
    vocabulario: int
    constante_c: int
    frecuencias: tuple[FrecuenciaPalabra, ...]

    @property
    def total_frecuencias_exportadas(self) -> int:
        """Indica cuántos rangos empiricos se conservaron para la curva y los archivos."""
        return len(self.frecuencias)


def _palabras_de(texto: str) -> list[str]:
    """Normaliza un texto completo y extrae sus palabras de una sola pasada.

    La normalización a NFC y ``casefold`` se aplica una única vez por cadena, no por
    línea, y ``findall`` evita materializar coincidencias intermedias.
    """
    normalizado = unicodedata.normalize("NFC", texto).casefold()
    return PATRON_PALABRA.findall(normalizado)


def tokenizar(texto: str) -> Iterator[str]:
    """Tokeniza texto Unicode como palabras del español o inglés.

    Se normaliza a NFC y se aplica ``casefold`` para reconocer palabras Unicode con
    mayúsculas o acentos combinantes. Las comillas rectas y tipográficas pueden aparecer
    dentro de una palabra;
    los signos de puntuación funcionan como separadores.
    """
    yield from _palabras_de(texto)


def descubrir_documentos(entrada: str | Path) -> tuple[Path, ...]:
    """Localiza los documentos ``.txt`` de un archivo o directorio en orden estable."""
    ruta = Path(entrada).expanduser()
    if not ruta.exists():
        raise ErrorZipf(f"no existe la ruta de entrada '{ruta}'")
    if ruta.is_file():
        if ruta.suffix.lower() != ".txt":
            raise ErrorZipf(f"el archivo de entrada '{ruta}' no tiene extensión .txt")
        documentos = (ruta,)
    elif ruta.is_dir():
        try:
            documentos = tuple(
                sorted(
                    (
                        candidato
                        for candidato in ruta.rglob("*")
                        if candidato.is_file() and candidato.suffix.lower() == ".txt"
                    ),
                    key=lambda candidato: str(candidato).casefold(),
                )
            )
        except OSError as exc:
            raise ErrorZipf(f"no se pudo recorrer la entrada '{ruta}': {exc}") from exc
    else:
        raise ErrorZipf(f"la entrada '{ruta}' no es un archivo ni un directorio")

    if not documentos:
        raise ErrorZipf(f"no se encontraron archivos .txt bajo '{ruta}'")
    return documentos


def _leer_documento(archivo: Path) -> tuple[str, int]:
    """Lee un archivo completo y devuelve su texto y su tamaño en bytes."""
    try:
        tamano = archivo.stat().st_size
        with archivo.open("r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), tamano
    except OSError as exc:
        raise ErrorZipf(f"no se pudo procesar '{archivo}': {exc}") from exc


def _contar_lote(archivos: tuple[Path, ...]) -> tuple[Counter[str], int]:
    """Cuenta un lote de archivos y devuelve ``(contador, bytes_leidos)``.

    Es una función de nivel de módulo, sin cierres ni lambdas, para que pueda
    serializarse y ejecutarse en un proceso trabajador.
    """
    contador: Counter[str] = Counter()
    bytes_leidos = 0
    for archivo in archivos:
        texto, tamano = _leer_documento(archivo)
        bytes_leidos += tamano
        contador.update(_palabras_de(texto))
    return contador, bytes_leidos


def _bytes_totales(documentos: tuple[Path, ...]) -> int:
    """Suma el tamaño en bytes de los documentos sin volver a leer su contenido."""
    total = 0
    for documento in documentos:
        try:
            total += documento.stat().st_size
        except OSError as exc:
            raise ErrorZipf(f"no se pudo procesar '{documento}': {exc}") from exc
    return total


def _dividir_en_lotes(documentos: tuple[Path, ...]) -> list[tuple[Path, ...]]:
    """Agrupa documentos en lotes acotados por cantidad o por bytes, lo que ocurra antes."""
    lotes: list[tuple[Path, ...]] = []
    actual: list[Path] = []
    bytes_actual = 0
    for documento in documentos:
        actual.append(documento)
        with contextlib.suppress(OSError):
            bytes_actual += documento.stat().st_size
        if len(actual) >= _MAX_ARCHIVOS_POR_LOTE or bytes_actual >= _MAX_BYTES_POR_LOTE:
            lotes.append(tuple(actual))
            actual = []
            bytes_actual = 0
    if actual:
        lotes.append(tuple(actual))
    return lotes


def _contar_secuencial(
    documentos: tuple[Path, ...],
    progreso: Callable[[int, int, int], None] | None,
) -> tuple[Counter[str], int]:
    """Cuenta los documentos en el proceso actual, avisando el progreso con regularidad."""
    contador: Counter[str] = Counter()
    bytes_leidos = 0
    total = len(documentos)
    for indice, documento in enumerate(documentos, start=1):
        texto, tamano = _leer_documento(documento)
        bytes_leidos += tamano
        contador.update(_palabras_de(texto))
        if progreso is not None and indice % _INTERVALO_PROGRESO == 0 and indice < total:
            progreso(indice, total, bytes_leidos)
    if progreso is not None:
        progreso(total, total, bytes_leidos)
    return contador, bytes_leidos


def _contar_paralelo(
    documentos: tuple[Path, ...],
    trabajadores: int,
    progreso: Callable[[int, int, int], None] | None,
) -> tuple[Counter[str], int]:
    """Reparte el conteo entre procesos y fusiona los contadores en cuanto llegan."""
    lotes = _dividir_en_lotes(documentos)
    total = len(documentos)
    contador: Counter[str] = Counter()
    bytes_leidos = 0
    procesados = 0
    # Enviar todos los lotes es seguro porque cada tarea está acotada (≈1500 archivos u
    # ≈8 MB); la fusión ocurre de forma incremental dentro de ``as_completed``.
    with ProcessPoolExecutor(max_workers=trabajadores) as ejecutor:
        futuros: dict[Future[tuple[Counter[str], int]], int] = {
            ejecutor.submit(_contar_lote, lote): len(lote) for lote in lotes
        }
        for futuro in as_completed(futuros):
            documentos_lote = futuros[futuro]
            parcial, bytes_parcial = futuro.result()
            contador.update(parcial)
            bytes_leidos += bytes_parcial
            procesados += documentos_lote
            if progreso is not None and procesados < total:
                progreso(procesados, total, bytes_leidos)
    if progreso is not None:
        progreso(total, total, bytes_leidos)
    return contador, bytes_leidos


def _contar_documentos(
    documentos: tuple[Path, ...],
    *,
    trabajadores: int | None,
    progreso: Callable[[int, int, int], None] | None,
) -> tuple[Counter[str], int]:
    """Cuenta el corpus eligiendo automáticamente entre uno y varios procesos."""
    if trabajadores is None:
        trabajadores = max(1, min(8, os.cpu_count() or 1))
    if trabajadores <= 1 or len(documentos) <= 1:
        return _contar_secuencial(documentos, progreso)
    return _contar_paralelo(documentos, trabajadores, progreso)


def _contar_con_cache(
    documentos: tuple[Path, ...],
    cache: str | Path,
    *,
    trabajadores: int | None,
    progreso: Callable[[int, int, int], None] | None,
) -> tuple[Counter[str], int]:
    """Reutiliza el conteo cacheado o lo calcula y lo guarda, sin romper ante errores.

    El módulo ``zipf_cache`` se importa de forma perezosa: el camino sin caché no depende
    de él. La caché es best-effort; ante cualquier fallo se continúa con el conteo real.
    """
    from arañador.guiones import zipf_cache

    huella: str | None = None
    acierto: tuple[Counter[str], int] | None = None
    try:
        huella = zipf_cache.calcular_huella(documentos, salt="zipf-v1")
        acierto = zipf_cache.cargar_conteo(cache, huella)
    except (zipf_cache.ErrorCacheZipf, OSError):
        acierto = None
    if acierto is not None:
        return acierto

    contador, _ = _contar_documentos(
        documentos,
        trabajadores=trabajadores,
        progreso=progreso,
    )
    total_palabras = sum(contador.values())
    if huella is not None:
        with contextlib.suppress(zipf_cache.ErrorCacheZipf, OSError):
            zipf_cache.guardar_conteo(cache, huella, contador, total_palabras)
    return contador, total_palabras


def analizar_corpus(
    entrada: str | Path,
    limite_top: int = 100,
    *,
    trabajadores: int | None = None,
    progreso: Callable[[int, int, int], None] | None = None,
    cache: str | Path | None = None,
) -> ResultadoZipf:
    """Recorre el corpus y calcula totales, vocabulario y constante de Zipf.

    Args:
        entrada: Archivo ``.txt`` o directorio que contiene los documentos.
        limite_top: Cantidad de frecuencias iniciales que se conservan; la
            curva añade muestras logarítmicas de la cola hasta el rango final.
        trabajadores: Número de procesos; ``None`` detecta automáticamente hasta 8 y
            un valor menor o igual a uno cuenta sin procesos.
        progreso: Callback ``(procesados, total, bytes_leidos)`` invocado de forma
            periódica y siempre al final con ``procesados == total``.
        cache: Ruta de caché incremental opcional; si hay acierto no se tokeniza nada.

    Returns:
        Un resultado inmutable con las principales frecuencias y los totales del corpus.

    Raises:
        ErrorZipf: Si la ruta es inválida, no hay documentos o no contienen palabras.
        ValueError: Si ``limite_top`` no es positivo.
    """
    if limite_top <= 0:
        raise ValueError("limite_top debe ser mayor que cero")

    documentos = descubrir_documentos(entrada)
    if cache is not None:
        contador, total_palabras = _contar_con_cache(
            documentos,
            cache,
            trabajadores=trabajadores,
            progreso=progreso,
        )
    else:
        contador, _ = _contar_documentos(
            documentos,
            trabajadores=trabajadores,
            progreso=progreso,
        )
        total_palabras = sum(contador.values())

    if total_palabras == 0:
        raise ErrorZipf(f"el corpus '{entrada}' no contiene palabras tokenizables")

    bytes_totales = _bytes_totales(documentos)

    # Se ordenan las frecuencias para conservar rangos reales y se agregan
    # muestras logarítmicas de la cola. Así la gráfica no termina artificialmente
    # en el rango ``limite_top`` cuando el vocabulario es grande.
    ordenadas = sorted(
        contador.items(),
        key=lambda elemento: (-elemento[1], elemento[0].casefold(), elemento[0]),
    )
    indices = set(range(min(limite_top, len(ordenadas))))
    if len(ordenadas) > limite_top:
        maximo = len(ordenadas)
        exponente = 0
        while 10**exponente <= maximo:
            for multiplicador in (1, 2, 5):
                indice = multiplicador * 10**exponente - 1
                if indice < maximo:
                    indices.add(indice)
            exponente += 1
    constante_c = ordenadas[0][1]
    frecuencias = tuple(
        FrecuenciaPalabra(
            rango=indice + 1,
            palabra=ordenadas[indice][0],
            frecuencia=ordenadas[indice][1],
            frecuencia_relativa=ordenadas[indice][1] / total_palabras,
        )
        for indice in sorted(indices)
    )
    return ResultadoZipf(
        documentos=len(documentos),
        bytes_totales=bytes_totales,
        total_palabras=total_palabras,
        vocabulario=len(contador),
        constante_c=constante_c,
        frecuencias=frecuencias,
    )


def _preparar_archivo(ruta: str | Path) -> Path:
    """Crea el directorio de una salida y devuelve su ruta resuelta."""
    archivo = Path(ruta).expanduser().resolve()
    try:
        archivo.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErrorZipf(f"no se pudo crear el directorio '{archivo.parent}': {exc}") from exc
    return archivo


def resultado_a_dict(resultado: ResultadoZipf) -> dict[str, Any]:
    """Construye la representación canónica y reproducible del análisis Zipf."""
    return {
        "documentos": resultado.documentos,
        "bytes_totales": resultado.bytes_totales,
        "total_palabras": resultado.total_palabras,
        "vocabulario": resultado.vocabulario,
        "constante_c": resultado.constante_c,
        "metodo_ajuste_c": "C = frecuencia empírica del rango 1",
        "frecuencias_curva": [
            {
                "rango": frecuencia.rango,
                "palabra": frecuencia.palabra,
                "frecuencia": frecuencia.frecuencia,
                "frecuencia_relativa": frecuencia.frecuencia_relativa,
            }
            for frecuencia in resultado.frecuencias
        ],
    }


def escribir_json(resultado: ResultadoZipf, ruta: str | Path) -> Path:
    """Escribe métricas y frecuencias principales en JSON UTF-8."""
    archivo = _preparar_archivo(ruta)
    try:
        with archivo.open("w", encoding="utf-8", newline="\n") as archivo_json:
            json.dump(
                resultado_a_dict(resultado),
                archivo_json,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            archivo_json.write("\n")
    except (OSError, ValueError) as exc:
        raise ErrorZipf(f"no se pudo escribir '{archivo}': {exc}") from exc
    return archivo


def generar_grafica(resultado: ResultadoZipf, ruta: str | Path) -> Path:
    """Genera una gráfica PNG log-log de frecuencias empíricas y ``f(r)=C/r``."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt

    archivo = _preparar_archivo(ruta)
    rangos = [frecuencia.rango for frecuencia in resultado.frecuencias]
    frecuencias = [frecuencia.frecuencia for frecuencia in resultado.frecuencias]
    teoricas = [resultado.constante_c / rango for rango in rangos]

    figura, ejes = plt.subplots(figsize=(8.0, 5.5))
    try:
        ejes.loglog(
            rangos,
            frecuencias,
            color="#1565c0",
            linewidth=1.4,
            marker="o",
            markersize=3.2,
            label="Frecuencia empírica",
        )
        ejes.loglog(
            rangos,
            teoricas,
            color="#c62828",
            linestyle="--",
            linewidth=1.4,
            label=r"Teórica $f(r)=C/r$",
        )
        ejes.set_title("Ley de Zipf del corpus audiovisual")
        ejes.set_xlabel("Rango de palabras (r)")
        ejes.set_ylabel("Frecuencia (f)")
        ejes.grid(which="both", alpha=0.22, linewidth=0.6)
        ejes.legend(loc="best")
        figura.tight_layout()
        figura.savefig(archivo, format="png", dpi=150, bbox_inches="tight")
    except (OSError, ValueError) as exc:
        raise ErrorZipf(f"no se pudo generar la gráfica '{archivo}': {exc}") from exc
    finally:
        plt.close(figura)
    return archivo


def _escapar_tex(texto: str) -> str:
    """Escapa caracteres especiales de LaTeX sin alterar caracteres Unicode."""
    reemplazos = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(reemplazos.get(caracter, caracter) for caracter in texto)


def escribir_tex(resultado: ResultadoZipf, ruta: str | Path) -> Path:
    """Escribe una tabla LaTeX con métricas y frecuencias principales."""
    archivo = _preparar_archivo(ruta)
    lineas = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{Métricas y frecuencias principales de la ley de Zipf.}",
        r"  \label{tab:zipf-corpus}",
        r"  \begin{tabular}{lr}",
        r"    \hline",
        r"    \textbf{Métrica} & \textbf{Valor} \\",
        r"    \hline",
        f"    Documentos procesados & {resultado.documentos} \\",
        f"    Bytes procesados & {resultado.bytes_totales} \\",
        f"    Palabras totales ($N$) & {resultado.total_palabras} \\",
        f"    Vocabulario ($|V|$) & {resultado.vocabulario} \\",
        f"    Constante de Zipf ($C$) & {resultado.constante_c} \\",
        r"    \hline",
        r"  \end{tabular}",
        r"\end{table}",
        "",
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{Frecuencias empíricas representativas ordenadas por rango.}",
        r"  \begin{tabular}{rrl}",
        r"    \hline",
        r"    \textbf{Rango} & \textbf{Frecuencia} & \textbf{Palabra} \\",
        r"    \hline",
    ]
    for frecuencia in resultado.frecuencias:
        lineas.append(
            f"    {frecuencia.rango} & {frecuencia.frecuencia} & "
            f"{_escapar_tex(frecuencia.palabra)} \\\\"
        )
    lineas.extend(
        [
            r"    \hline",
            r"  \end{tabular}",
            r"\end{table}",
        ]
    )

    try:
        archivo.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ErrorZipf(f"no se pudo escribir '{archivo}': {exc}") from exc
    return archivo


def ejecutar_analisis(
    entrada: str | Path,
    salida: str | Path,
    *,
    limite_top: int = 100,
    nombre_png: str = "grafica_ley_zipf.png",
    nombre_json: str = "estadisticas_zipf.json",
    nombre_tex: str = "tabla_estadisticas.tex",
    trabajadores: int | None = None,
    progreso: Callable[[int, int, int], None] | None = None,
    cache: str | Path | None = None,
) -> ResultadoZipf:
    """Calcula Zipf y genera las tres salidas solicitadas dentro de ``salida``."""
    resultado = analizar_corpus(
        entrada,
        limite_top=limite_top,
        trabajadores=trabajadores,
        progreso=progreso,
        cache=cache,
    )
    directorio = Path(salida).expanduser()
    escribir_json(resultado, directorio / nombre_json)
    escribir_tex(resultado, directorio / nombre_tex)
    generar_grafica(resultado, directorio / nombre_png)
    return resultado


def _formatear_bytes(cantidad: int) -> str:
    """Convierte un conteo de bytes a una unidad legible."""
    unidades = ("B", "KiB", "MiB", "GiB", "TiB")
    valor = float(max(cantidad, 0))
    indice = 0
    while valor >= 1024 and indice < len(unidades) - 1:
        valor /= 1024
        indice += 1
    return f"{valor:.1f} {unidades[indice]}"


def _crear_reportero_progreso(intervalo: float = 0.5) -> Callable[[int, int, int], None]:
    """Crea un callback que informa el avance del conteo por ``stderr``.

    Si ``stderr`` es una terminal se reescribe la misma línea con ``\\r``; en caso
    contrario se emite una línea por actualización. Siempre se imprime la línea final.
    """
    inicio = time.monotonic()
    ultimo_instante = inicio
    salida = sys.stderr
    interactivo = salida.isatty()

    def reportar(procesados: int, total: int, bytes_leidos: int) -> None:
        nonlocal ultimo_instante
        if total <= 0:
            return
        ahora = time.monotonic()
        final = procesados >= total
        if not final and ahora - ultimo_instante < intervalo:
            return
        ultimo_instante = ahora
        transcurrido = max(ahora - inicio, 1e-9)
        velocidad = bytes_leidos / transcurrido / (1024 * 1024)
        porcentaje = 100.0 * procesados / total
        mensaje = (
            f"Progreso Zipf: {procesados}/{total} documentos ({porcentaje:.1f} %), "
            f"{_formatear_bytes(bytes_leidos)}, ~{velocidad:.1f} MB/s"
        )
        if final or not interactivo:
            print(mensaje, file=salida, flush=True)
        else:
            print(mensaje, end="\r", file=salida, flush=True)

    return reportar


def construir_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos del cálculo Zipf."""
    parser = argparse.ArgumentParser(
        description="Calcula la ley de Zipf sobre archivos .txt y exporta PNG, TeX y JSON."
    )
    parser.add_argument(
        "--entrada",
        "--ruta",
        "-r",
        dest="entrada",
        type=Path,
        default=RUTA_ENTRADA_PREDETERMINADA,
        help=f"archivo o directorio .txt (predeterminado: {RUTA_ENTRADA_PREDETERMINADA})",
    )
    parser.add_argument(
        "--salida",
        "-s",
        type=Path,
        default=RUTA_SALIDA_PREDETERMINADA,
        help=f"directorio de resultados (predeterminado: {RUTA_SALIDA_PREDETERMINADA})",
    )
    parser.add_argument(
        "--top",
        "--limite-top",
        dest="limite_top",
        type=int,
        default=100,
        help="cantidad de frecuencias iniciales; la curva añade muestras de la cola (predeterminado: 100)",
    )
    parser.add_argument(
        "--trabajadores",
        dest="trabajadores",
        type=int,
        default=None,
        help="procesos paralelos; por defecto se detecta automáticamente (máximo 8)",
    )
    parser.add_argument(
        "--cache",
        dest="cache",
        type=Path,
        default=None,
        help="archivo de caché incremental opcional para reutilizar el conteo",
    )
    parser.add_argument(
        "--sin-progreso",
        dest="sin_progreso",
        action="store_true",
        help="no imprime el avance del conteo en stderr",
    )
    parser.add_argument(
        "--nombre-png",
        default="grafica_ley_zipf.png",
        help="nombre de la gráfica dentro del directorio de salida",
    )
    parser.add_argument(
        "--nombre-json",
        default="estadisticas_zipf.json",
        help="nombre del JSON dentro del directorio de salida",
    )
    parser.add_argument(
        "--nombre-tex",
        default="tabla_estadisticas.tex",
        help="nombre de la tabla TeX dentro del directorio de salida",
    )
    return parser


def main(argumentos: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI de Zipf y devuelve un código de salida."""
    parser = construir_parser()
    opciones = parser.parse_args(argumentos)
    if opciones.limite_top <= 0:
        parser.error("--top debe ser mayor que cero")

    trabajadores = opciones.trabajadores
    if trabajadores is None:
        trabajadores = leer_entero("TRABAJADORES_ZIPF", 0) or None
    progreso = None if opciones.sin_progreso else _crear_reportero_progreso()

    try:
        resultado = ejecutar_analisis(
            opciones.entrada,
            opciones.salida,
            limite_top=opciones.limite_top,
            nombre_png=opciones.nombre_png,
            nombre_json=opciones.nombre_json,
            nombre_tex=opciones.nombre_tex,
            trabajadores=trabajadores,
            progreso=progreso,
            cache=opciones.cache,
        )
    except (ErrorZipf, ValueError) as exc:
        print(f"Error de análisis Zipf: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error de análisis Zipf: no se pudo escribir la salida: {exc}", file=sys.stderr)
        return 1

    print(f"Análisis Zipf exportado en '{opciones.salida}'.")
    print(
        f"Documentos: {resultado.documentos}; palabras: {resultado.total_palabras}; "
        f"vocabulario: {resultado.vocabulario}; C: {resultado.constante_c}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
