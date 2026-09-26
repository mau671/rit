"""Exporta métricas reproducibles de SQLite y del repositorio de texto.

Las salidas JSON, CSV y LaTeX se construyen con orden determinista, por lo que dos
ejecuciones sobre el mismo estado producen archivos idénticos. La base de datos se abre
exclusivamente en modo de solo lectura y el tamaño de disco se obtiene con ``stat``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if __package__ is None:  # Permite ejecutar el archivo directamente.
    _RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_RAIZ_PROYECTO))

from arañador.guiones.monitorear_progreso import (  # noqa: E402
    BYTES_POR_GIB,
    ErrorMonitoreo,
    abrir_sqlite_solo_lectura,
    iterar_archivos_txt,
)
from arañador.implementacion.utilidades.rutas import resolver_ruta_datos  # noqa: E402

RUTA_BASE_DATOS_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "metadatos_araña.db")
RUTA_REPOSITORIO_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "repositorio")
RUTA_SALIDA_PREDETERMINADA = resolver_ruta_datos("resultados", "estadisticas")


class ErrorExportador(RuntimeError):
    """Error de validación o exportación que puede mostrarse directamente al usuario."""


@dataclass(frozen=True, slots=True)
class MetricaDistribucion:
    """Métricas agregadas para un dominio o una categoría."""

    ambito: str
    nombre: str
    documentos_metadatos: int
    bytes_metadatos: int
    documentos_disco: int
    bytes_disco: int

    def to_dict(self) -> dict[str, str | int]:
        """Convierte la métrica a una representación serializable."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EstadisticasRepositorio:
    """Conjunto completo de estadísticas exportables del repositorio."""

    documentos_metadatos: int
    documentos_disco: int
    diferencia_documentos: int
    documentos_faltantes: int
    archivos_huerfanos: int
    bytes_metadatos: int
    bytes_disco: int
    diferencia_bytes: int
    distribucion_dominios: tuple[MetricaDistribucion, ...]
    distribucion_categorias: tuple[MetricaDistribucion, ...]

    @property
    def gib_disco(self) -> float:
        """Devuelve el tamaño real expresado en GiB."""
        return self.bytes_disco / BYTES_POR_GIB

    @property
    def cobertura_documentos(self) -> float | None:
        """Calcula la proporción de documentos de SQLite encontrados en disco."""
        if self.documentos_metadatos == 0:
            return None
        return (self.documentos_metadatos - self.documentos_faltantes) / self.documentos_metadatos

    @property
    def numero_dominios_metadatos(self) -> int:
        """Cuenta dominios con al menos un documento en SQLite."""
        return sum(metrica.documentos_metadatos > 0 for metrica in self.distribucion_dominios)

    @property
    def numero_categorias_metadatos(self) -> int:
        """Cuenta categorías con al menos un documento en SQLite."""
        return sum(metrica.documentos_metadatos > 0 for metrica in self.distribucion_categorias)

    def to_dict(self) -> dict[str, Any]:
        """Construye el diccionario canónico que se almacena en JSON."""
        datos: dict[str, Any] = {
            "documentos_metadatos": self.documentos_metadatos,
            "documentos_disco": self.documentos_disco,
            "diferencia_documentos": self.diferencia_documentos,
            "documentos_faltantes": self.documentos_faltantes,
            "archivos_huerfanos": self.archivos_huerfanos,
            "bytes_metadatos": self.bytes_metadatos,
            "bytes_disco": self.bytes_disco,
            "diferencia_bytes": self.diferencia_bytes,
            "gib_disco": round(self.gib_disco, 9),
            "dominios_metadatos": self.numero_dominios_metadatos,
            "categorias_metadatos": self.numero_categorias_metadatos,
            "cobertura_documentos": (
                None if self.cobertura_documentos is None else round(self.cobertura_documentos, 9)
            ),
            "distribucion_por_dominio": [
                metrica.to_dict() for metrica in self.distribucion_dominios
            ],
            "distribucion_por_categoria": [
                metrica.to_dict() for metrica in self.distribucion_categorias
            ],
        }
        return datos


@dataclass(slots=True)
class _FilaDocumento:
    """Fila mínima de SQLite necesaria para conciliar los archivos con sus metadatos."""

    ruta_relativa: str
    dominio: str
    categoria: str
    bytes_metadatos: int


@dataclass(slots=True)
class _AcumuladorGrupo:
    """Contadores mutables usados mientras se construyen las distribuciones."""

    documentos_metadatos: int = 0
    bytes_metadatos: int = 0
    documentos_disco: int = 0
    bytes_disco: int = 0


@dataclass(slots=True)
class _Distribuciones:
    """Acumuladores para los dos ejes solicitados."""

    dominios: dict[str, _AcumuladorGrupo] = field(
        default_factory=lambda: defaultdict(_AcumuladorGrupo)
    )
    categorias: dict[str, _AcumuladorGrupo] = field(
        default_factory=lambda: defaultdict(_AcumuladorGrupo)
    )


def _normalizar_nombre(valor: object, sustituto: str) -> str:
    """Sustituye valores nulos o vacíos por una etiqueta estable."""
    texto = str(valor).strip() if valor is not None else ""
    return texto or sustituto


def consultar_filas_documentos(ruta_base_datos: str | Path) -> list[_FilaDocumento]:
    """Lee los campos de ruta y clasificación sin alterar la conexión SQLite."""
    with abrir_sqlite_solo_lectura(ruta_base_datos) as conexion:
        try:
            filas = conexion.execute(
                """
                SELECT ruta_relativa, dominio, categoria, tamano_texto_bytes
                  FROM documentos
                 ORDER BY ruta_relativa ASC, id ASC
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ErrorExportador(
                f"la tabla 'documentos' no puede aportar las métricas requeridas: {exc}"
            ) from exc

    return [
        _FilaDocumento(
            ruta_relativa=str(fila["ruta_relativa"] or ""),
            dominio=_normalizar_nombre(fila["dominio"], "(sin dominio)"),
            categoria=_normalizar_nombre(fila["categoria"], "(sin categoría)"),
            bytes_metadatos=max(0, int(fila["tamano_texto_bytes"] or 0)),
        )
        for fila in filas
    ]


def _resolver_ruta(ruta_relativa: str, raiz: Path) -> Path | None:
    """Resuelve una ruta de metadatos sin aceptar archivos fuera del repositorio.

    Se admiten rutas relativas a la raíz del repositorio y rutas que incluyan
    ``repositorio`` como primer componente, que fue una representación habitual en etapas
    tempranas del
    proyecto.
    """
    if not ruta_relativa.strip():
        return None
    ruta = Path(ruta_relativa).expanduser()
    raiz_absoluta = raiz.resolve()

    if ruta.is_absolute():
        candidata = ruta.resolve()
    else:
        candidata = (raiz_absoluta / ruta).resolve()
        alternativa = (raiz_absoluta.parent / ruta).resolve()
        if not candidata.exists() and alternativa.exists():
            candidata = alternativa

    return candidata if candidata.is_relative_to(raiz_absoluta) else None


def _deducir_clasificacion(ruta: Path, raiz: Path) -> tuple[str, str]:
    """Deduce dominio y categoría a partir de la partición ``dominio/categoria/...``."""
    try:
        partes = ruta.resolve().relative_to(raiz.resolve()).parts
    except ValueError:
        partes = ()
    dominio = _normalizar_nombre(partes[0] if partes else None, "(sin dominio)")
    categoria = _normalizar_nombre(partes[1] if len(partes) > 1 else None, "(sin categoría)")
    return dominio, categoria


def _crear_metrica(ambito: str, nombre: str, acumulador: _AcumuladorGrupo) -> MetricaDistribucion:
    """Convierte un acumulador interno en una métrica inmutable."""
    return MetricaDistribucion(
        ambito=ambito,
        nombre=nombre,
        documentos_metadatos=acumulador.documentos_metadatos,
        bytes_metadatos=acumulador.bytes_metadatos,
        documentos_disco=acumulador.documentos_disco,
        bytes_disco=acumulador.bytes_disco,
    )


def calcular_estadisticas(
    ruta_base_datos: str | Path,
    repositorio: str | Path,
) -> EstadisticasRepositorio:
    """Calcula totales y distribuciones por dominio y categoría desde SQLite y disco."""
    raiz = Path(repositorio).expanduser()
    if not raiz.exists():
        raise ErrorExportador(f"no existe el repositorio de texto '{raiz}'")
    if not raiz.is_dir():
        raise ErrorExportador(f"el repositorio de texto '{raiz}' debe ser un directorio")

    filas = consultar_filas_documentos(ruta_base_datos)
    acumuladores = _Distribuciones()
    filas_por_ruta: dict[Path, list[int]] = defaultdict(list)

    for indice, fila in enumerate(filas):
        acumuladores.dominios[fila.dominio].documentos_metadatos += 1
        acumuladores.dominios[fila.dominio].bytes_metadatos += fila.bytes_metadatos
        acumuladores.categorias[fila.categoria].documentos_metadatos += 1
        acumuladores.categorias[fila.categoria].bytes_metadatos += fila.bytes_metadatos

        ruta_resuelta = _resolver_ruta(fila.ruta_relativa, raiz)
        if ruta_resuelta is not None:
            filas_por_ruta[ruta_resuelta].append(indice)

    documentos_disco = 0
    bytes_disco = 0
    archivos_huerfanos = 0
    filas_encontradas: set[int] = set()

    for archivo in iterar_archivos_txt(raiz):
        documentos_disco += 1
        bytes_disco += archivo.bytes_reales
        ruta_absoluta = archivo.ruta.resolve()
        indices = filas_por_ruta.get(ruta_absoluta, [])

        if not indices:
            archivos_huerfanos += 1
            dominio, categoria = _deducir_clasificacion(archivo.ruta, raiz)
            acumuladores.dominios[dominio].documentos_disco += 1
            acumuladores.dominios[dominio].bytes_disco += archivo.bytes_reales
            acumuladores.categorias[categoria].documentos_disco += 1
            acumuladores.categorias[categoria].bytes_disco += archivo.bytes_reales
            continue

        filas_encontradas.update(indices)
        # Una ruta física se contabiliza una vez en distribuciones de disco. Las filas
        # duplicadas que apuntan a ella sí se consideran encontradas para la cobertura.
        representativa = filas[indices[0]]
        acumuladores.dominios[representativa.dominio].documentos_disco += 1
        acumuladores.dominios[representativa.dominio].bytes_disco += archivo.bytes_reales
        acumuladores.categorias[representativa.categoria].documentos_disco += 1
        acumuladores.categorias[representativa.categoria].bytes_disco += archivo.bytes_reales

    documentos_faltantes = len(filas) - len(filas_encontradas)
    bytes_metadatos = sum(fila.bytes_metadatos for fila in filas)
    metricas_dominios = tuple(
        sorted(
            (
                _crear_metrica("dominio", nombre, acumulador)
                for nombre, acumulador in acumuladores.dominios.items()
            ),
            key=lambda metrica: (
                -metrica.documentos_metadatos,
                -metrica.documentos_disco,
                metrica.nombre.casefold(),
            ),
        )
    )
    metricas_categorias = tuple(
        sorted(
            (
                _crear_metrica("categoria", nombre, acumulador)
                for nombre, acumulador in acumuladores.categorias.items()
            ),
            key=lambda metrica: (
                -metrica.documentos_metadatos,
                -metrica.documentos_disco,
                metrica.nombre.casefold(),
            ),
        )
    )

    return EstadisticasRepositorio(
        documentos_metadatos=len(filas),
        documentos_disco=documentos_disco,
        diferencia_documentos=documentos_disco - len(filas),
        documentos_faltantes=documentos_faltantes,
        archivos_huerfanos=archivos_huerfanos,
        bytes_metadatos=bytes_metadatos,
        bytes_disco=bytes_disco,
        diferencia_bytes=bytes_disco - bytes_metadatos,
        distribucion_dominios=metricas_dominios,
        distribucion_categorias=metricas_categorias,
    )


def _preparar_archivo(ruta: str | Path) -> Path:
    """Crea el directorio de una salida y devuelve su ruta absoluta."""
    archivo = Path(ruta).expanduser().resolve()
    try:
        archivo.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErrorExportador(f"no se pudo crear el directorio '{archivo.parent}': {exc}") from exc
    return archivo


def escribir_json(estadisticas: EstadisticasRepositorio, ruta: str | Path) -> Path:
    """Escribe el reporte completo en JSON UTF-8 con claves y filas ordenadas."""
    archivo = _preparar_archivo(ruta)
    try:
        with archivo.open("w", encoding="utf-8", newline="\n") as archivo_json:
            json.dump(
                estadisticas.to_dict(),
                archivo_json,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            archivo_json.write("\n")
    except OSError as exc:
        raise ErrorExportador(f"no se pudo escribir '{archivo}': {exc}") from exc
    return archivo


def escribir_csv(estadisticas: EstadisticasRepositorio, ruta: str | Path) -> Path:
    """Escribe ambas distribuciones en un CSV estable y compatible con hojas de cálculo."""
    archivo = _preparar_archivo(ruta)
    campos = [
        "ambito",
        "nombre",
        "documentos_metadatos",
        "bytes_metadatos",
        "documentos_disco",
        "bytes_disco",
    ]
    metricas = (
        MetricaDistribucion(
            ambito="total",
            nombre="corpus",
            documentos_metadatos=estadisticas.documentos_metadatos,
            bytes_metadatos=estadisticas.bytes_metadatos,
            documentos_disco=estadisticas.documentos_disco,
            bytes_disco=estadisticas.bytes_disco,
        ),
        *estadisticas.distribucion_dominios,
        *estadisticas.distribucion_categorias,
    )
    try:
        with archivo.open("w", encoding="utf-8", newline="") as archivo_csv:
            escritor = csv.DictWriter(archivo_csv, fieldnames=campos, lineterminator="\n")
            escritor.writeheader()
            for metrica in metricas:
                escritor.writerow(metrica.to_dict())
    except OSError as exc:
        raise ErrorExportador(f"no se pudo escribir '{archivo}': {exc}") from exc
    return archivo


def _escapar_tex(texto: str) -> str:
    """Escapa los caracteres con significado especial en una celda LaTeX."""
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


def escribir_tex(estadisticas: EstadisticasRepositorio, ruta: str | Path) -> Path:
    """Escribe un documento LaTeX autocontenido con totales y distribuciones."""
    archivo = _preparar_archivo(ruta)
    cobertura = (
        "No aplica (base sin documentos)"
        if estadisticas.cobertura_documentos is None
        else f"{estadisticas.cobertura_documentos * 100.0:.2f} \\%"
    )
    lineas = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{Estadísticas reproducibles del corpus audiovisual.}",
        r"  \label{tab:estadisticas-corpus}",
        r"  \begin{tabular}{lr}",
        r"    \hline",
        r"    \textbf{Métrica} & \textbf{Valor} \\",
        r"    \hline",
        f"    Documentos en metadatos & {estadisticas.documentos_metadatos} \\",
        f"    Archivos de texto en disco & {estadisticas.documentos_disco} \\",
        f"    Bytes registrados & {estadisticas.bytes_metadatos} \\",
        f"    Bytes reales en disco & {estadisticas.bytes_disco} \\",
        f"    Gibabytes reales & {estadisticas.gib_disco:.6f} \\",
        f"    Documentos faltantes & {estadisticas.documentos_faltantes} \\",
        f"    Archivos sin metadatos & {estadisticas.archivos_huerfanos} \\",
        f"    Cobertura documental & {cobertura} \\",
        r"    \hline",
        r"  \end{tabular}",
        r"\end{table}",
        "",
    ]

    for titulo, metricas in (
        ("Distribución por dominio", estadisticas.distribucion_dominios),
        ("Distribución por categoría", estadisticas.distribucion_categorias),
    ):
        lineas.extend(
            [
                r"\begin{table}[htbp]",
                r"  \centering",
                rf"  \caption{{{_escapar_tex(titulo)}.}}",
                r"  \begin{tabular}{lrrrr}",
                r"    \hline",
                r"    \textbf{Grupo} & \textbf{Docs. BD} & \textbf{Bytes BD} & "
                r"\textbf{Docs. disco} & \textbf{Bytes disco} \\",
                r"    \hline",
            ]
        )
        if metricas:
            for metrica in metricas:
                lineas.append(
                    f"    {_escapar_tex(metrica.nombre)} & {metrica.documentos_metadatos} & "
                    f"{metrica.bytes_metadatos} & {metrica.documentos_disco} & "
                    f"{metrica.bytes_disco} \\\\"
                )
        else:
            lineas.append(r"    \multicolumn{5}{c}{Sin datos} \\")
        lineas.extend(
            [
                r"    \hline",
                r"  \end{tabular}",
                r"\end{table}",
                "",
            ]
        )

    try:
        archivo.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ErrorExportador(f"no se pudo escribir '{archivo}': {exc}") from exc
    return archivo


def exportar_estadisticas(
    ruta_base_datos: str | Path,
    repositorio: str | Path,
    salida: str | Path,
) -> EstadisticasRepositorio:
    """Calcula y exporta las métricas en JSON, CSV y LaTeX dentro de ``salida``."""
    directorio_salida = Path(salida).expanduser()
    estadisticas = calcular_estadisticas(ruta_base_datos, repositorio)
    escribir_json(estadisticas, directorio_salida / "estadisticas.json")
    escribir_csv(estadisticas, directorio_salida / "distribuciones.csv")
    escribir_tex(estadisticas, directorio_salida / "estadisticas.tex")
    return estadisticas


def construir_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos del exportador."""
    parser = argparse.ArgumentParser(
        description="Exporta métricas de SQLite y del repositorio en JSON, CSV y LaTeX."
    )
    parser.add_argument(
        "--bd",
        type=Path,
        default=RUTA_BASE_DATOS_PREDETERMINADA,
        help=f"ruta de SQLite (predeterminada: {RUTA_BASE_DATOS_PREDETERMINADA})",
    )
    parser.add_argument(
        "--repositorio",
        type=Path,
        default=RUTA_REPOSITORIO_PREDETERMINADA,
        help=f"raíz de los .txt (predeterminada: {RUTA_REPOSITORIO_PREDETERMINADA})",
    )
    parser.add_argument(
        "--salida",
        type=Path,
        default=RUTA_SALIDA_PREDETERMINADA,
        help=f"directorio de resultados (predeterminado: {RUTA_SALIDA_PREDETERMINADA})",
    )
    return parser


def main(argumentos: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI de exportación y devuelve un código de salida."""
    opciones = construir_parser().parse_args(argumentos)
    try:
        estadisticas = exportar_estadisticas(opciones.bd, opciones.repositorio, opciones.salida)
    except (ErrorExportador, ErrorMonitoreo) as exc:
        print(f"Error de exportación: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error de exportación: no se pudo escribir la salida: {exc}", file=sys.stderr)
        return 1

    print(f"Estadísticas exportadas en '{opciones.salida}'.")
    print(
        f"Documentos: {estadisticas.documentos_metadatos} en metadatos, "
        f"{estadisticas.documentos_disco} en disco; bytes reales: {estadisticas.bytes_disco}."
    )
    if estadisticas.documentos_faltantes or estadisticas.archivos_huerfanos:
        print(
            "Advertencia de conciliación: "
            f"{estadisticas.documentos_faltantes} documentos faltantes y "
            f"{estadisticas.archivos_huerfanos} archivos huérfanos."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
