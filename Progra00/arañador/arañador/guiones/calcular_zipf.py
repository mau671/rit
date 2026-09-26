"""Calcula frecuencias de palabras y ajusta la ley de Zipf sobre archivos de texto.

El corpus se lee archivo por archivo, línea por línea, sin concatenar documentos. La
gráfica se genera con el backend ``Agg`` de Matplotlib y, por tanto, no necesita GUI,
escritorio ni conexión de red.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

if __package__ in {None, ""}:  # Permite ejecutar el archivo directamente.
    _RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
    if str(_RAIZ_PROYECTO) not in sys.path:
        sys.path.insert(0, str(_RAIZ_PROYECTO))

from arañador.implementacion.utilidades.rutas import resolver_ruta_datos  # noqa: E402

matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt  # noqa: E402  (el backend debe fijarse primero)

RUTA_ENTRADA_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "repositorio")
RUTA_SALIDA_PREDETERMINADA = resolver_ruta_datos("resultados", "zipf")
PATRON_PALABRA = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", flags=re.UNICODE)


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


def tokenizar(texto: str) -> Iterator[str]:
    """Tokeniza texto Unicode como palabras del español o inglés.

    Se normaliza a NFC y se aplica ``casefold`` para reconocer palabras Unicode con
    mayúsculas o acentos combinantes. Las comillas rectas y tipográficas pueden aparecer
    dentro de una palabra;
    los signos de puntuación funcionan como separadores.
    """
    normalizado = unicodedata.normalize("NFC", texto).casefold()
    for coincidencia in PATRON_PALABRA.finditer(normalizado):
        palabra = coincidencia.group(0)
        if palabra:
            yield palabra


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


def analizar_corpus(entrada: str | Path, limite_top: int = 100) -> ResultadoZipf:
    """Recorre el corpus en flujo continuo y calcula totales, vocabulario y constante de Zipf.

    Args:
        entrada: Archivo ``.txt`` o directorio que contiene los documentos.
        limite_top: Cantidad de frecuencias iniciales que se conservan; la
            curva añade muestras logarítmicas de la cola hasta el rango final.

    Returns:
        Un resultado inmutable con las principales frecuencias y los totales del corpus.

    Raises:
        ErrorZipf: Si la ruta es inválida, no hay documentos o no contienen palabras.
        ValueError: Si ``limite_top`` no es positivo.
    """
    if limite_top <= 0:
        raise ValueError("limite_top debe ser mayor que cero")

    documentos = descubrir_documentos(entrada)
    contador: Counter[str] = Counter()
    total_palabras = 0
    bytes_totales = 0

    for documento in documentos:
        try:
            bytes_totales += documento.stat().st_size
            with documento.open("r", encoding="utf-8-sig", errors="replace") as archivo:
                for linea in archivo:
                    palabras_linea = list(tokenizar(linea))
                    total_palabras += len(palabras_linea)
                    contador.update(palabras_linea)
        except OSError as exc:
            raise ErrorZipf(f"no se pudo procesar '{documento}': {exc}") from exc

    if total_palabras == 0:
        raise ErrorZipf(f"el corpus '{entrada}' no contiene palabras tokenizables")

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
) -> ResultadoZipf:
    """Calcula Zipf y genera las tres salidas solicitadas dentro de ``salida``."""
    resultado = analizar_corpus(entrada, limite_top=limite_top)
    directorio = Path(salida).expanduser()
    escribir_json(resultado, directorio / nombre_json)
    escribir_tex(resultado, directorio / nombre_tex)
    generar_grafica(resultado, directorio / nombre_png)
    return resultado


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

    try:
        resultado = ejecutar_analisis(
            opciones.entrada,
            opciones.salida,
            limite_top=opciones.limite_top,
            nombre_png=opciones.nombre_png,
            nombre_json=opciones.nombre_json,
            nombre_tex=opciones.nombre_tex,
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
