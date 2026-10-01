"""Pruebas de la caché incremental de frecuencias para el análisis de Zipf."""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from arañador.guiones.zipf_cache import (
    ErrorCacheZipf,
    calcular_huella,
    cargar_conteo,
    guardar_conteo,
)


def _crear_corpus(directorio: Path, contenido: dict[str, str]) -> list[Path]:
    """Crea archivos de texto pequeños y devuelve sus rutas ordenadas."""
    directorio.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for nombre, texto in contenido.items():
        ruta = directorio / nombre
        ruta.write_text(texto, encoding="utf-8")
        rutas.append(ruta)
    return sorted(rutas, key=str)


def test_roundtrip_guarda_y_carga_el_conteo(tmp_path: Path) -> None:
    """Guardar y cargar devuelve el mismo contador y el mismo total."""
    contador: Counter[str] = Counter({"casa": 3, "gato": 2, "acción": 1})
    cache = tmp_path / "cache"

    ruta = guardar_conteo(cache, "huella-abc", contador, 6)

    assert ruta == cache / "frecuencias_zipf.sqlite"
    resultado = cargar_conteo(cache, "huella-abc")
    assert resultado is not None
    recuperado, total = resultado
    assert recuperado == contador
    assert total == 6


def test_huella_distinta_no_reutiliza_la_cache(tmp_path: Path) -> None:
    """Una huella que no coincide se considera caché obsoleta."""
    guardar_conteo(tmp_path / "c.db", "huella-uno", Counter({"a": 1}), 1)

    assert cargar_conteo(tmp_path / "c.db", "huella-dos") is None


def test_cache_inexistente_devuelve_none(tmp_path: Path) -> None:
    """Si la caché no existe, no hay nada que recuperar."""
    assert cargar_conteo(tmp_path / "no-existe", "huella") is None
    assert cargar_conteo(tmp_path / "no-existe.sqlite", "huella") is None


def test_calcular_huella_es_determinista_y_sensible(tmp_path: Path) -> None:
    """La huella es estable y cambia cuando cambia el contenido o el mtime."""
    archivos = _crear_corpus(
        tmp_path / "corpus",
        {"a.txt": "uno dos", "b.txt": "tres"},
    )

    primera = calcular_huella(archivos)
    assert primera == calcular_huella(archivos)
    assert len(primera) == 64
    # El salt incorpora variación explícita.
    assert calcular_huella(archivos, salt="v1") != calcular_huella(archivos, salt="v2")
    # El orden de la lista también forma parte de la huella.
    assert calcular_huella(list(reversed(archivos))) != primera

    # Cambiar el tamaño del archivo cambia la huella.
    archivos[0].write_text("uno dos tres cuatro", encoding="utf-8")
    tras_contenido = calcular_huella(archivos)
    assert tras_contenido != primera

    # Cambiar solo el mtime también cambia la huella.
    info = archivos[0].stat()
    os.utime(archivos[0], ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
    assert calcular_huella(archivos) != tras_contenido


def test_calcular_huella_no_falla_si_stat_falla(tmp_path: Path) -> None:
    """Un archivo inexistente igualmente aporta su ruta a la huella."""
    inexistente = tmp_path / "ausente.txt"

    huella = calcular_huella([inexistente])

    assert len(huella) == 64
    assert huella == calcular_huella([inexistente])


def test_guardar_no_deja_archivos_temporales(tmp_path: Path) -> None:
    """Tras un guardado exitoso no quedan temporales sueltos."""
    cache = tmp_path / "cache"

    guardar_conteo(cache, "huella", Counter({"a": 2}), 2)

    sobrantes = [ruta.name for ruta in cache.iterdir() if ".tmp-" in ruta.name]
    assert sobrantes == []
    assert [ruta.name for ruta in cache.iterdir()] == ["frecuencias_zipf.sqlite"]


def test_sobrescritura_deja_solo_la_ultima_version(tmp_path: Path) -> None:
    """Guardar dos veces reemplaza la caché y conserva una sola huella."""
    cache = tmp_path / "cache.sqlite"
    guardar_conteo(cache, "huella-uno", Counter({"a": 1}), 1)
    guardar_conteo(cache, "huella-dos", Counter({"b": 5, "c": 2}), 7)

    resultado = cargar_conteo(cache, "huella-dos")
    assert resultado is not None
    contador, total = resultado
    assert contador == Counter({"b": 5, "c": 2})
    assert total == 7
    assert cargar_conteo(cache, "huella-uno") is None

    with sqlite3.connect(cache) as conexion:
        assert conexion.execute("SELECT COUNT(*) FROM meta").fetchone() == (1,)
        assert conexion.execute("SELECT huella FROM meta").fetchone() == ("huella-dos",)


@pytest.mark.parametrize(
    "nombre",
    ["cache.sqlite", "cache.db", "cache.sqlite3"],
)
def test_cache_como_archivo_con_extension(tmp_path: Path, nombre: str) -> None:
    """Un nombre con extensión de base de datos se usa tal cual."""
    cache = tmp_path / nombre
    guardar_conteo(cache, "huella", Counter({"x": 4}), 4)

    assert cache.is_file()
    assert cargar_conteo(cache, "huella") == (Counter({"x": 4}), 4)


def test_cache_como_directorio_crea_archivo_predeterminado(tmp_path: Path) -> None:
    """Un nombre sin extensión se interpreta como directorio."""
    cache = tmp_path / "anidado" / "cache"
    guardar_conteo(cache, "huella", Counter({"y": 9}), 9)

    esperado = cache / "frecuencias_zipf.sqlite"
    assert esperado.is_file()
    assert cargar_conteo(cache, "huella") == (Counter({"y": 9}), 9)


def test_cache_corrupta_se_trata_como_ausencia(tmp_path: Path) -> None:
    """Una base de datos corrupta no rompe al llamador."""
    cache = tmp_path / "rota.sqlite"
    cache.write_bytes(b"esto no es una base de datos sqlite")

    assert cargar_conteo(cache, "huella") is None


def test_guardar_reporta_error_de_escritura(tmp_path: Path) -> None:
    """Si la ruta de destino es inválida, se lanza ErrorCacheZipf."""
    colision = tmp_path / "archivo.txt"
    colision.write_text("soy un archivo, no un directorio", encoding="utf-8")

    with pytest.raises(ErrorCacheZipf):
        guardar_conteo(colision, "huella", Counter({"a": 1}), 1)


def test_guardar_en_directorio_inexistente_crea_la_ruta(tmp_path: Path) -> None:
    """El guardado crea los directorios necesarios."""
    cache = tmp_path / "a" / "b" / "c"

    ruta = guardar_conteo(cache, "huella", Counter({"z": 1}), 1)

    assert ruta.parent.is_dir()
    assert ruta.is_file()
