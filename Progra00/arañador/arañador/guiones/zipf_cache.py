"""Caché incremental del conteo de frecuencias para el análisis de Zipf.

La primera corrida del análisis guarda el :class:`~collections.Counter` de frecuencias
junto con una *huella* del corpus. En corridas posteriores, si el corpus no cambió, la
huella coincide y el contador se recupera sin volver a tokenizar los documentos. Esto
evita reprocesar corpus grandes (del orden de gigabytes) cuando nada cambió.

Formato de la caché
-------------------
El conteo se persiste en una base de datos SQLite con dos tablas::

    meta(huella TEXT NOT NULL, total_palabras INTEGER NOT NULL)  -- una sola fila
    conteo(palabra TEXT PRIMARY KEY, frecuencia INTEGER NOT NULL)

``meta`` guarda la huella vigente y el total de palabras; ``conteo`` guarda la
frecuencia absoluta de cada palabra del vocabulario.

Semántica de directorio o archivo
---------------------------------
El parámetro ``cache`` puede ser un directorio o un archivo:

* Si el nombre termina en ``.db``, ``.sqlite`` o ``.sqlite3`` se trata como archivo.
* En cualquier otro caso se trata como directorio y el archivo será
  ``<cache>/frecuencias_zipf.sqlite``.

Limitación sobre mover el corpus
--------------------------------
La huella incorpora la ruta completa de cada archivo, además de su tamaño y su
``mtime``. Por eso, mover o renombrar el corpus (aunque su contenido no cambie)
invalida la caché: la próxima corrida la considerará obsoleta y recalculará el
conteo. La caché también se invalida si cambia el tamaño o la fecha de modificación
de cualquier archivo, o si se usa un ``salt`` distinto.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

_EXTENSIONES_ARCHIVO = (".db", ".sqlite", ".sqlite3")
_NOMBRE_ARCHIVO_PREDETERMINADO = "frecuencias_zipf.sqlite"
_SEPARADOR = b"\x00"


class ErrorCacheZipf(RuntimeError):
    """Error de lectura o escritura de la caché de frecuencias."""


def _resolver_archivo(cache: str | Path) -> Path:
    """Resuelve si ``cache`` es un archivo o un directorio y devuelve la ruta del archivo.

    Un nombre con extensión ``.db``, ``.sqlite`` o ``.sqlite3`` se interpreta como
    archivo; cualquier otro valor se interpreta como directorio y el archivo será
    ``<cache>/frecuencias_zipf.sqlite``.
    """
    ruta = Path(cache).expanduser()
    if ruta.suffix.lower() in _EXTENSIONES_ARCHIVO:
        return ruta
    return ruta / _NOMBRE_ARCHIVO_PREDETERMINADO


def calcular_huella(archivos: Sequence[Path], salt: str = "") -> str:
    """Calcula la huella SHA-256 de un corpus a partir de sus metadatos.

    La huella combina, en orden: ``salt``, y por cada archivo (en el orden recibido)
    su ruta completa, su tamaño en bytes y su ``st_mtime_ns``. Todos los fragmentos se
    separan con bytes nulos para evitar colisiones por concatenación. Si ``stat()``
    falla para un archivo (por ejemplo, porque ya no existe), igualmente se incorpora
    su ruta para que el resultado siga siendo determinista y no reviente.

    Args:
        archivos: Rutas de los archivos que componen el corpus.
        salt: Cadena opcional para forzar la invalidación de la caché.

    Returns:
        La huella como cadena hexadecimal de 64 caracteres.

    Nota:
        Como la ruta completa forma parte de la huella, mover o renombrar el corpus
        invalida la caché aunque el contenido no haya cambiado.
    """
    resumen = hashlib.sha256()
    resumen.update(salt.encode("utf-8"))
    for archivo in archivos:
        ruta = Path(archivo)
        resumen.update(_SEPARADOR)
        resumen.update(str(ruta).encode("utf-8", errors="surrogatepass"))
        try:
            info = ruta.stat()
        except OSError:
            continue
        resumen.update(_SEPARADOR)
        resumen.update(str(info.st_size).encode("ascii"))
        resumen.update(_SEPARADOR)
        resumen.update(str(info.st_mtime_ns).encode("ascii"))
    return resumen.hexdigest()


def cargar_conteo(cache: str | Path, huella: str) -> tuple[Counter[str], int] | None:
    """Recupera el conteo cacheado si la caché existe y la huella coincide.

    Es una operación *best-effort*: nunca propaga errores al llamador. Ante cualquier
    problema (archivo ausente, huella distinta, base corrupta, error de lectura o un
    total de palabras no positivo) devuelve ``None`` para que el llamador recalcule.

    Args:
        cache: Directorio o archivo de caché.
        huella: Huella del corpus calculada con :func:`calcular_huella`.

    Returns:
        Una tupla ``(contador, total_palabras)`` si la caché es válida; si no, ``None``.
    """
    archivo = _resolver_archivo(cache)
    if not archivo.is_file():
        return None

    conexion: sqlite3.Connection | None = None
    try:
        conexion = sqlite3.connect(archivo)
        fila = conexion.execute("SELECT huella, total_palabras FROM meta LIMIT 1").fetchone()
        if fila is None:
            return None
        huella_guardada, total_guardado = fila
        if huella_guardada != huella:
            return None

        contador: Counter[str] = Counter()
        for palabra, frecuencia in conexion.execute("SELECT palabra, frecuencia FROM conteo"):
            contador[palabra] = frecuencia

        total_palabras = int(total_guardado)
        if total_palabras <= 0:
            total_palabras = sum(contador.values())
        if total_palabras <= 0:
            return None
        return contador, total_palabras
    except (sqlite3.DatabaseError, OSError):
        # Un fallo de caché se trata como ausencia de caché, no como excepción.
        return None
    finally:
        if conexion is not None:
            conexion.close()


def _escribir_cache(
    destino: Path,
    huella: str,
    contador: Counter[str],
    total_palabras: int,
) -> None:
    """Escribe el volumen SQLite temporal con el esquema de la caché."""
    if destino.exists():
        destino.unlink()
    conexion = sqlite3.connect(destino)
    try:
        # El archivo es desechable hasta que ``os.replace`` lo publique: no hace
        # falta diario ni sincronización, así que se desactivan para ganar velocidad.
        conexion.execute("PRAGMA journal_mode=OFF")
        conexion.execute("PRAGMA synchronous=OFF")
        conexion.execute(
            """
            CREATE TABLE meta (
                huella TEXT NOT NULL,
                total_palabras INTEGER NOT NULL
            )
            """
        )
        conexion.execute(
            """
            CREATE TABLE conteo (
                palabra TEXT PRIMARY KEY,
                frecuencia INTEGER NOT NULL
            )
            """
        )
        conexion.execute(
            "INSERT INTO meta (huella, total_palabras) VALUES (?, ?)",
            (huella, total_palabras),
        )
        conexion.executemany(
            "INSERT INTO conteo (palabra, frecuencia) VALUES (?, ?)",
            ((palabra, int(frecuencia)) for palabra, frecuencia in contador.items()),
        )
        conexion.commit()
    finally:
        conexion.close()


def _descartar_temporal(temporal: Path) -> None:
    """Elimina el archivo temporal y sus posibles acompañantes de SQLite."""
    for candidato in (temporal, Path(f"{temporal}-journal"), Path(f"{temporal}-wal")):
        try:
            candidato.unlink(missing_ok=True)
        except OSError:
            continue


def guardar_conteo(
    cache: str | Path,
    huella: str,
    contador: Counter[str],
    total_palabras: int,
) -> Path:
    """Persiste el contador y el total de palabras asociados a una huella.

    La escritura es atómica: el contenido se escribe primero en un archivo temporal
    del mismo directorio (``<archivo>.tmp-<pid>``) y, al terminar, se publica con
    :func:`os.replace`. Así una interrupción nunca deja una caché a medio escribir.

    Args:
        cache: Directorio o archivo de caché.
        huella: Huella del corpus calculada con :func:`calcular_huella`.
        contador: Frecuencia absoluta de cada palabra del vocabulario.
        total_palabras: Total de palabras procesadas en el corpus.

    Returns:
        La ruta del archivo de caché ya publicado.

    Raises:
        ErrorCacheZipf: Si no se puede crear el directorio o escribir la caché.
    """
    archivo = _resolver_archivo(cache)
    try:
        archivo.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErrorCacheZipf(f"no se pudo crear el directorio '{archivo.parent}': {exc}") from exc

    total = total_palabras if total_palabras > 0 else sum(contador.values())
    temporal = archivo.with_name(f"{archivo.name}.tmp-{os.getpid()}")
    try:
        _escribir_cache(temporal, huella, contador, total)
        os.replace(temporal, archivo)
    except (sqlite3.DatabaseError, OSError, ValueError) as exc:
        _descartar_temporal(temporal)
        raise ErrorCacheZipf(f"no se pudo escribir la caché '{archivo}': {exc}") from exc
    return archivo
