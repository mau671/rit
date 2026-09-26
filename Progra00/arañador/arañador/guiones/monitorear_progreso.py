"""Consulta el progreso del corpus sin modificar SQLite.

El módulo ofrece una instantánea no interactiva por defecto y, opcionalmente, repetible
con ``--seguir``. Todos los cálculos de tamaño se basan en el tamaño real de los archivos
``.txt``; el tamaño registrado en SQLite se conserva como referencia de contraste.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from arañador.entorno import leer_entero
from arañador.implementacion.utilidades.rutas import resolver_ruta_datos

RUTA_BASE_DATOS_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "metadatos_araña.db")
RUTA_REPOSITORIO_PREDETERMINADA = resolver_ruta_datos("almacenamiento", "repositorio")
BYTES_POR_GIB = 1024**3


class ErrorMonitoreo(RuntimeError):
    """Error operativo que puede mostrarse claramente en la consola."""


@dataclass(frozen=True, slots=True)
class DistribucionDominio:
    """Cantidad de documentos y bytes asociados a un dominio."""

    dominio: str
    documentos: int
    bytes_metadatos: int


@dataclass(frozen=True, slots=True)
class ArchivoTexto:
    """Un archivo de texto encontrado en el repositorio y su tamaño real."""

    ruta: Path
    bytes_reales: int


@dataclass(frozen=True, slots=True)
class EstadisticasDisco:
    """Totales calculados recorriendo los archivos de texto del repositorio."""

    archivos: int
    bytes_totales: int


@dataclass(frozen=True, slots=True)
class ResumenBaseDatos:
    """Resumen reproducible de la tabla de documentos consultada en modo de solo lectura."""

    documentos: int
    bytes_metadatos: int
    distribucion_dominios: tuple[DistribucionDominio, ...]
    fecha_inicio: datetime | None
    fecha_fin: datetime | None


@dataclass(frozen=True, slots=True)
class InstantaneaProgreso:
    """Estado del corpus en un instante concreto."""

    fecha_medicion: datetime
    documentos: int
    documentos_en_disco: int
    bytes_totales: int
    bytes_metadatos: int
    distribucion_dominios: tuple[DistribucionDominio, ...]
    velocidad_bytes_por_segundo: float | None
    velocidad_documentos_por_segundo: float | None
    velocidad_reciente_bytes_por_segundo: float | None
    velocidad_reciente_documentos_por_segundo: float | None
    duracion_observada_segundos: float | None
    fecha_inicio: datetime | None
    fecha_fin: datetime | None
    origen_bytes: Literal["disco", "metadatos"]

    @property
    def gib_totales(self) -> float:
        """Devuelve los bytes observados expresados en GiB."""
        return self.bytes_totales / BYTES_POR_GIB

    @property
    def diferencia_bytes(self) -> int:
        """Indica cuánto difieren los bytes reales de los metadatos."""
        return self.bytes_totales - self.bytes_metadatos

    @property
    def numero_dominios(self) -> int:
        """Cuenta los dominios presentes en los metadatos."""
        return sum(distribucion.documentos > 0 for distribucion in self.distribucion_dominios)


@contextmanager
def abrir_sqlite_solo_lectura(ruta_base_datos: str | Path) -> Iterator[sqlite3.Connection]:
    """Abre una base SQLite existente exclusivamente en modo de solo lectura.

    Args:
        ruta_base_datos: Ruta al archivo SQLite que se consultará.

    Yields:
        Una conexión SQLite con ``query_only`` activado.

    Raises:
        ErrorMonitoreo: Si el archivo no existe, no es un archivo o SQLite no puede abrirlo.
    """
    ruta = Path(ruta_base_datos).expanduser()
    if not ruta.exists():
        raise ErrorMonitoreo(
            f"no existe la base de datos '{ruta}'; se creará automáticamente al iniciar el arañador"
        )
    if not ruta.is_file():
        raise ErrorMonitoreo(f"la ruta de base de datos '{ruta}' no es un archivo")

    ruta_absoluta = ruta.resolve()
    uri_solo_lectura = f"{ruta_absoluta.as_uri()}?mode=ro"
    try:
        conexion = sqlite3.connect(uri_solo_lectura, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise ErrorMonitoreo(f"no se pudo abrir '{ruta}' en modo de solo lectura: {exc}") from exc

    conexion.row_factory = sqlite3.Row
    try:
        conexion.execute("PRAGMA query_only = ON")
        yield conexion
    except sqlite3.Error as exc:
        raise ErrorMonitoreo(f"error al consultar '{ruta}': {exc}") from exc
    finally:
        conexion.close()


def _convertir_timestamp(valor: object) -> datetime | None:
    """Convierte los formatos temporales habituales de SQLite a datetime UTC."""
    if isinstance(valor, datetime):
        instante = valor
    elif isinstance(valor, str) and valor.strip():
        texto = valor.strip()
        if texto.endswith("Z"):
            texto = f"{texto[:-1]}+00:00"
        try:
            instante = datetime.fromisoformat(texto)
        except ValueError:
            return None
    else:
        return None

    if instante.tzinfo is None:
        return instante.replace(tzinfo=UTC)
    return instante.astimezone(UTC)


def consultar_resumen_base_datos(ruta_base_datos: str | Path) -> ResumenBaseDatos:
    """Obtiene conteos, bytes, dominios y límites temporales de ``documentos``.

    La consulta es de solo lectura y no presupone que exista la tabla ``bitacora_recorrido``.
    """
    with abrir_sqlite_solo_lectura(ruta_base_datos) as conexion:
        try:
            fila_resumen = conexion.execute(
                """
                SELECT COUNT(*) AS documentos,
                       COALESCE(SUM(tamano_texto_bytes), 0) AS bytes_metadatos,
                       MIN(fecha_descarga) AS fecha_inicio,
                       MAX(fecha_descarga) AS fecha_fin
                  FROM documentos
                """
            ).fetchone()
            if fila_resumen is None:  # pragma: no cover - SUM y COUNT siempre producen una fila
                raise ErrorMonitoreo("la consulta de resumen no devolvió filas")

            filas_dominios = conexion.execute(
                """
                SELECT COALESCE(NULLIF(dominio, ''), '(sin dominio)') AS dominio,
                       COUNT(*) AS documentos,
                       COALESCE(SUM(tamano_texto_bytes), 0) AS bytes_metadatos
                  FROM documentos
                 GROUP BY COALESCE(NULLIF(dominio, ''), '(sin dominio)')
                 ORDER BY documentos DESC, dominio ASC
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "documentos" in str(exc).lower() and "no such table" in str(exc).lower():
                raise ErrorMonitoreo(
                    f"la base '{ruta_base_datos}' no contiene la tabla 'documentos'"
                ) from exc
            raise ErrorMonitoreo(f"no se pudo leer la tabla 'documentos': {exc}") from exc

    dominios = tuple(
        DistribucionDominio(
            dominio=str(fila["dominio"]),
            documentos=int(fila["documentos"]),
            bytes_metadatos=int(fila["bytes_metadatos"] or 0),
        )
        for fila in filas_dominios
    )
    return ResumenBaseDatos(
        documentos=int(fila_resumen["documentos"]),
        bytes_metadatos=int(fila_resumen["bytes_metadatos"] or 0),
        distribucion_dominios=dominios,
        fecha_inicio=_convertir_timestamp(fila_resumen["fecha_inicio"]),
        fecha_fin=_convertir_timestamp(fila_resumen["fecha_fin"]),
    )


def iterar_archivos_txt(repositorio: str | Path) -> Iterator[ArchivoTexto]:
    """Recorre en orden determinista los archivos ``.txt`` existentes bajo ``repositorio``.

    El tamaño se obtiene mediante ``stat`` en el momento de la medición, no desde los
    metadatos de SQLite.
    """
    raiz = Path(repositorio).expanduser()
    if not raiz.exists():
        raise ErrorMonitoreo(f"no existe el repositorio de texto '{raiz}'")
    if raiz.is_file():
        if raiz.suffix.lower() != ".txt":
            raise ErrorMonitoreo(f"el archivo de entrada '{raiz}' no tiene extensión .txt")
        candidatos = [raiz]
    elif raiz.is_dir():
        try:
            candidatos = sorted(
                (
                    ruta
                    for ruta in raiz.rglob("*")
                    if ruta.is_file() and ruta.suffix.lower() == ".txt"
                ),
                key=lambda ruta: str(ruta).casefold(),
            )
        except OSError as exc:
            raise ErrorMonitoreo(f"no se pudo recorrer el repositorio '{raiz}': {exc}") from exc
    else:
        raise ErrorMonitoreo(f"la ruta de repositorio '{raiz}' no es un archivo ni un directorio")

    for ruta in candidatos:
        try:
            bytes_reales = ruta.stat().st_size
        except OSError as exc:
            raise ErrorMonitoreo(f"no se pudo medir el archivo '{ruta}': {exc}") from exc
        yield ArchivoTexto(ruta=ruta, bytes_reales=bytes_reales)


def escanear_textos(repositorio: str | Path) -> EstadisticasDisco:
    """Calcula cantidad y bytes reales de todos los documentos de texto."""
    archivos = 0
    bytes_totales = 0
    for archivo in iterar_archivos_txt(repositorio):
        archivos += 1
        bytes_totales += archivo.bytes_reales
    return EstadisticasDisco(archivos=archivos, bytes_totales=bytes_totales)


def obtener_instantanea(
    ruta_base_datos: str | Path,
    repositorio: str | Path,
    anterior: InstantaneaProgreso | None = None,
) -> InstantaneaProgreso:
    """Construye una instantánea combinando metadatos SQLite y tamaño real de disco.

    Si el directorio de texto aún no existe, se muestran los bytes registrados en SQLite
    y se identifica el origen como ``metadatos``. Si existe, se prioriza la medición real,
    incluso cuando vale cero o revela archivos faltantes.
    """
    resumen = consultar_resumen_base_datos(ruta_base_datos)
    raiz_repositorio = Path(repositorio).expanduser()

    if raiz_repositorio.exists():
        estadisticas_disco = escanear_textos(raiz_repositorio)
        bytes_totales = estadisticas_disco.bytes_totales
        documentos_disco = estadisticas_disco.archivos
        origen_bytes: Literal["disco", "metadatos"] = "disco"
    else:
        bytes_totales = resumen.bytes_metadatos
        documentos_disco = 0
        origen_bytes = "metadatos"

    duracion: float | None = None
    velocidad_bytes: float | None = None
    velocidad_documentos: float | None = None
    if resumen.fecha_inicio is not None and resumen.fecha_fin is not None:
        duracion = (resumen.fecha_fin - resumen.fecha_inicio).total_seconds()
        if duracion > 0 and resumen.documentos > 1:
            velocidad_bytes = resumen.bytes_metadatos / duracion
            velocidad_documentos = resumen.documentos / duracion

    fecha_medicion = datetime.now(UTC)
    velocidad_reciente_bytes: float | None = None
    velocidad_reciente_documentos: float | None = None
    if anterior is not None:
        intervalo = fecha_medicion - anterior.fecha_medicion
        if intervalo.total_seconds() > 0:
            velocidad_reciente_bytes = max(
                0.0,
                (bytes_totales - anterior.bytes_totales) / intervalo.total_seconds(),
            )
            velocidad_reciente_documentos = max(
                0.0,
                (resumen.documentos - anterior.documentos) / intervalo.total_seconds(),
            )

    return InstantaneaProgreso(
        fecha_medicion=fecha_medicion,
        documentos=resumen.documentos,
        documentos_en_disco=documentos_disco,
        bytes_totales=bytes_totales,
        bytes_metadatos=resumen.bytes_metadatos,
        distribucion_dominios=resumen.distribucion_dominios,
        velocidad_bytes_por_segundo=velocidad_bytes,
        velocidad_documentos_por_segundo=velocidad_documentos,
        velocidad_reciente_bytes_por_segundo=velocidad_reciente_bytes,
        velocidad_reciente_documentos_por_segundo=velocidad_reciente_documentos,
        duracion_observada_segundos=duracion,
        fecha_inicio=resumen.fecha_inicio,
        fecha_fin=resumen.fecha_fin,
        origen_bytes=origen_bytes,
    )


def formatear_bytes(cantidad: int | float) -> str:
    """Formatea bytes con prefijos binarios y una precisión legible."""
    valor = float(cantidad)
    for unidad in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(valor) < 1024.0 or unidad == "TiB":
            decimales = 0 if unidad == "B" else 2
            return f"{valor:,.{decimales}f} {unidad}".replace(",", " ")
        valor /= 1024.0
    raise AssertionError("unidades de bytes agotadas")  # pragma: no cover


def formatear_instantanea(
    instantanea: InstantaneaProgreso,
    *,
    objetivo_gib: float = 10.0,
) -> str:
    """Construye el informe de texto mostrado por la interfaz de consola."""
    objetivo_bytes = objetivo_gib * BYTES_POR_GIB
    porcentaje = (instantanea.bytes_totales / objetivo_bytes * 100.0) if objetivo_bytes else 0.0
    tiempo = instantanea.fecha_medicion.astimezone().isoformat(timespec="seconds")

    lineas = [
        f"Instantánea: {tiempo}",
        (
            f"Bytes observados: {formatear_bytes(instantanea.bytes_totales)} "
            f"({instantanea.gib_totales:.3f} GiB; origen: {instantanea.origen_bytes})"
        ),
        f"Progreso frente a {objetivo_gib:g} GiB: {porcentaje:.2f} %",
        (
            f"Documentos en metadatos: {instantanea.documentos:,} | "
            f"archivos .txt en disco: {instantanea.documentos_en_disco:,}".replace(",", " ")
        ),
        f"Dominios observados: {instantanea.numero_dominios}",
        f"Diferencia disco-metadatos: {formatear_bytes(instantanea.diferencia_bytes)}",
    ]

    if instantanea.distribucion_dominios:
        lineas.append("Distribución por dominio:")
        for distribucion in instantanea.distribucion_dominios:
            lineas.append(
                f"  - {distribucion.dominio}: {distribucion.documentos:,} documentos, "
                f"{formatear_bytes(distribucion.bytes_metadatos)}".replace(",", " ")
            )
    else:
        lineas.append("Distribución por dominio: sin documentos.")

    if instantanea.velocidad_bytes_por_segundo is None:
        lineas.append(
            "Velocidad: no disponible (se requieren al menos dos marcas de tiempo distintas)."
        )
    else:
        megabytes_por_minuto = instantanea.velocidad_bytes_por_segundo * 60.0 / 1_000_000.0
        documentos_por_segundo = instantanea.velocidad_documentos_por_segundo or 0.0
        lineas.append(
            f"Velocidad media: {formatear_bytes(instantanea.velocidad_bytes_por_segundo)}/s, "
            f"{megabytes_por_minuto:.2f} MB/min, {documentos_por_segundo:.4f} documentos/s"
        )
    if instantanea.velocidad_reciente_bytes_por_segundo is not None:
        lineas.append(
            "Velocidad reciente: "
            f"{formatear_bytes(instantanea.velocidad_reciente_bytes_por_segundo)}/s, "
            f"{instantanea.velocidad_reciente_documentos_por_segundo or 0.0:.4f} documentos/s"
        )
    return "\n".join(lineas)


def construir_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos del monitor."""
    parser = argparse.ArgumentParser(
        description="Muestra el progreso del corpus mediante una consulta SQLite de solo lectura."
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
        "--objetivo-gib",
        type=float,
        default=float(leer_entero("OBJETIVO_GIB", 10)),
        help="objetivo del corpus en GiB (predeterminada: 10)",
    )
    parser.add_argument(
        "--seguir",
        action="store_true",
        help="actualiza continuamente la instantánea hasta presionar Ctrl+C",
    )
    parser.add_argument(
        "--intervalo",
        type=float,
        default=5.0,
        help="segundos entre actualizaciones con --seguir (predeterminado: 5)",
    )
    return parser


def main(argumentos: Sequence[str] | None = None) -> int:
    """Ejecuta la CLI y devuelve un código numérico apropiado para ``sys.exit``."""
    parser = construir_parser()
    opciones = parser.parse_args(argumentos)
    if not math.isfinite(opciones.objetivo_gib) or opciones.objetivo_gib <= 0:
        parser.error("--objetivo-gib debe ser un número finito mayor que cero")
    if not math.isfinite(opciones.intervalo) or opciones.intervalo <= 0:
        parser.error("--intervalo debe ser un número finito mayor que cero")

    anterior: InstantaneaProgreso | None = None
    try:
        while True:
            instantanea = obtener_instantanea(opciones.bd, opciones.repositorio, anterior)
            if opciones.seguir and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(
                formatear_instantanea(instantanea, objetivo_gib=opciones.objetivo_gib), flush=True
            )
            if not opciones.seguir:
                return 0
            anterior = instantanea
            print(f"Actualizando cada {opciones.intervalo:g} s; Ctrl+C para terminar.", flush=True)
            time.sleep(opciones.intervalo)
    except KeyboardInterrupt:
        print("\nMonitoreo detenido.", file=sys.stderr)
        return 0
    except ErrorMonitoreo as exc:
        print(f"Error de monitoreo: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
