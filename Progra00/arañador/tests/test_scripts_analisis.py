"""Pruebas rápidas de los guiones de monitoreo, Zipf y exportación."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import TypedDict

import matplotlib
import pytest

from arañador.guiones.calcular_zipf import ErrorZipf, analizar_corpus, ejecutar_analisis, tokenizar
from arañador.guiones.exportar_estadisticas import exportar_estadisticas
from arañador.guiones.monitorear_progreso import (
    ErrorMonitoreo,
    consultar_resumen_base_datos,
    main,
    obtener_instantanea,
)

matplotlib.use("Agg", force=True)


class _FilaTemporal(TypedDict):
    """Datos mínimos para insertar un documento en la base de prueba."""

    dominio: str
    categoria: str
    ruta_relativa: str
    bytes: int
    fecha: str


def _crear_base_documentos(ruta: Path, filas: list[_FilaTemporal]) -> None:
    """Crea una base SQLite temporal con el esquema público usado por los guiones."""
    with sqlite3.connect(ruta) as conexion:
        conexion.execute(
            """
            CREATE TABLE documentos (
                id INTEGER PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                hash_url TEXT UNIQUE NOT NULL,
                dominio TEXT NOT NULL,
                categoria TEXT NOT NULL,
                ruta_relativa TEXT UNIQUE NOT NULL,
                tamano_texto_bytes INTEGER NOT NULL,
                hash_contenido TEXT NOT NULL,
                codigo_http INTEGER NOT NULL,
                profundidad INTEGER NOT NULL,
                fecha_descarga TEXT NOT NULL,
                fecha_revisitacion TEXT
            )
            """
        )
        conexion.executemany(
            """
            INSERT INTO documentos (
                url, hash_url, dominio, categoria, ruta_relativa,
                tamano_texto_bytes, hash_contenido, codigo_http,
                profundidad, fecha_descarga
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 200, 1, ?)
            """,
            [
                (
                    f"https://ejemplo.test/{indice}",
                    f"{indice:064x}",
                    str(fila["dominio"]),
                    str(fila["categoria"]),
                    str(fila["ruta_relativa"]),
                    fila["bytes"],
                    f"{indice + 100:064x}",
                    str(fila["fecha"]),
                )
                for indice, fila in enumerate(filas, start=1)
            ],
        )


def test_tokenizacion_unicode_y_palabras_inglesas() -> None:
    """Separa puntuación y números sin perder acentos ni apóstrofos internos."""
    assert list(tokenizar("Acción, DON'T 123 ñandú")) == ["acción", "don't", "ñandú"]
    assert list(tokenizar(unicodedata.normalize("NFD", "acción"))) == ["acción"]


def test_zipf_flujo_y_salidas_sin_gui(tmp_path: Path) -> None:
    """Calcula métricas deterministas y genera PNG, JSON y TeX con Agg."""
    assert matplotlib.get_backend().lower() == "agg"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("casa gato casa acción don't 123\n", encoding="utf-8")
    (corpus / "b.txt").write_text("CASA, perro. ñandú\n", encoding="utf-8")

    resultado = ejecutar_analisis(corpus, tmp_path / "salida", limite_top=5)

    assert resultado.documentos == 2
    assert resultado.total_palabras == 8
    assert resultado.vocabulario == 6
    assert resultado.constante_c == 3
    assert resultado.frecuencias[0].palabra == "casa"
    assert resultado.frecuencias[0].frecuencia_relativa == pytest.approx(3 / 8)

    salida = tmp_path / "salida"
    assert (salida / "grafica_ley_zipf.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    datos = json.loads((salida / "estadisticas_zipf.json").read_text(encoding="utf-8"))
    assert datos["total_palabras"] == 8
    assert datos["frecuencias_curva"][0]["palabra"] == "casa"
    assert r"\begin{table}" in (salida / "tabla_estadisticas.tex").read_text(encoding="utf-8")


def test_zipf_incluye_muestras_de_la_cola_de_la_curva(tmp_path: Path) -> None:
    """La gráfica no queda truncada al límite de palabras iniciales."""

    corpus = tmp_path / "cola"
    corpus.mkdir()
    (corpus / "cola.txt").write_text(
        " ".join(chr(0x4E00 + indice) for indice in range(200)),
        encoding="utf-8",
    )

    resultado = analizar_corpus(corpus, limite_top=5)

    assert resultado.frecuencias[0].rango == 1
    assert resultado.frecuencias[-1].rango == 200


def test_zipf_rechaza_corpus_vacio(tmp_path: Path) -> None:
    """Informa de forma específica cuando no existe texto tokenizable."""
    corpus = tmp_path / "vacio"
    corpus.mkdir()
    documento = corpus / "vacio.txt"
    documento.write_text("123 -- !!!\n", encoding="utf-8")

    with pytest.raises(ErrorZipf, match="no contiene palabras"):
        ejecutar_analisis(corpus, tmp_path / "salida")


def test_monitoreo_consulta_solo_lectura_y_calcula_velocidad(tmp_path: Path) -> None:
    """Concilia bytes de disco, dominios y velocidad media sin escribir la base."""
    base = tmp_path / "datos.db"
    repositorio = tmp_path / "repositorio"
    (repositorio / "a").mkdir(parents=True)
    (repositorio / "b").mkdir()
    (repositorio / "a" / "uno.txt").write_text("12345", encoding="utf-8")
    (repositorio / "b" / "dos.txt").write_text("1234567", encoding="utf-8")
    (repositorio / "ignorado.md").write_text("no cuenta", encoding="utf-8")
    _crear_base_documentos(
        base,
        [
            {
                "dominio": "uno.test",
                "categoria": "guiones",
                "ruta_relativa": "a/uno.txt",
                "bytes": 6,
                "fecha": "2026-01-01T00:00:00+00:00",
            },
            {
                "dominio": "dos.test",
                "categoria": "criticas",
                "ruta_relativa": "b/dos.txt",
                "bytes": 8,
                "fecha": "2026-01-01T00:01:00+00:00",
            },
        ],
    )
    contenido_inicial = base.read_bytes()

    instantanea = obtener_instantanea(base, repositorio)

    assert instantanea.documentos == 2
    assert instantanea.documentos_en_disco == 2
    assert instantanea.numero_dominios == 2
    assert instantanea.bytes_totales == 12
    assert instantanea.bytes_metadatos == 14
    assert instantanea.origen_bytes == "disco"
    assert instantanea.velocidad_bytes_por_segundo == pytest.approx(14 / 60)
    assert instantanea.velocidad_documentos_por_segundo == pytest.approx(2 / 60)
    assert [dominio.dominio for dominio in instantanea.distribucion_dominios] == [
        "dos.test",
        "uno.test",
    ]
    assert base.read_bytes() == contenido_inicial


def test_base_inexistente_produce_error_claro(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La CLI informa la ruta inexistente y devuelve un código de error."""
    with pytest.raises(ErrorMonitoreo, match="no existe la base de datos"):
        consultar_resumen_base_datos(tmp_path / "ausente.db")

    codigo = main(["--bd", str(tmp_path / "ausente.db"), "--repositorio", str(tmp_path)])
    assert codigo == 1
    assert "Error de monitoreo" in capsys.readouterr().err


def test_exportacion_reproducible_y_distribuciones(tmp_path: Path) -> None:
    """Cruza metadatos y disco, detecta huérfanos y produce CSV, JSON y TeX estables."""
    base = tmp_path / "datos.db"
    repositorio = tmp_path / "repositorio"
    (repositorio / "uno.test" / "guiones").mkdir(parents=True)
    (repositorio / "dos.test" / "criticas").mkdir(parents=True)
    (repositorio / "huerfano.test" / "varios").mkdir(parents=True)
    (repositorio / "uno.test" / "guiones" / "uno.txt").write_text("12345", encoding="utf-8")
    (repositorio / "dos.test" / "criticas" / "dos.txt").write_text("123456", encoding="utf-8")
    (repositorio / "huerfano.test" / "varios" / "tres.txt").write_text("1234", encoding="utf-8")
    _crear_base_documentos(
        base,
        [
            {
                "dominio": "uno.test",
                "categoria": "guiones",
                "ruta_relativa": "uno.test/guiones/uno.txt",
                "bytes": 5,
                "fecha": "2026-01-01T00:00:00+00:00",
            },
            {
                "dominio": "dos.test",
                "categoria": "criticas",
                "ruta_relativa": "dos.test/criticas/dos.txt",
                "bytes": 7,
                "fecha": "2026-01-01T00:00:01+00:00",
            },
            {
                "dominio": "ausente.test",
                "categoria": "series",
                "ruta_relativa": "ausente.test/series/ausente.txt",
                "bytes": 7,
                "fecha": "2026-01-01T00:00:02+00:00",
            },
        ],
    )

    salida = tmp_path / "salida"
    estadisticas = exportar_estadisticas(base, repositorio, salida)

    assert estadisticas.documentos_metadatos == 3
    assert estadisticas.documentos_disco == 3
    assert estadisticas.bytes_metadatos == 19
    assert estadisticas.bytes_disco == 15
    assert estadisticas.documentos_faltantes == 1
    assert estadisticas.archivos_huerfanos == 1
    assert {metrica.nombre for metrica in estadisticas.distribucion_dominios} == {
        "uno.test",
        "dos.test",
        "ausente.test",
        "huerfano.test",
    }
    assert {metrica.nombre for metrica in estadisticas.distribucion_categorias} == {
        "guiones",
        "criticas",
        "series",
        "varios",
    }

    archivos = {
        nombre: (salida / nombre).read_bytes()
        for nombre in ("estadisticas.json", "distribuciones.csv", "estadisticas.tex")
    }
    datos = json.loads(archivos["estadisticas.json"].decode("utf-8"))
    assert datos["bytes_disco"] == 15
    assert datos["dominios_metadatos"] == 3
    assert datos["categorias_metadatos"] == 3
    assert "total,corpus,3,19,3,15" in archivos["distribuciones.csv"].decode("utf-8")
    assert "huerfano.test" in archivos["distribuciones.csv"].decode("utf-8")
    assert r"\begin{table}" in archivos["estadisticas.tex"].decode("utf-8")

    exportar_estadisticas(base, repositorio, salida)
    assert all(
        (salida / nombre).read_bytes() == contenido for nombre, contenido in archivos.items()
    )
