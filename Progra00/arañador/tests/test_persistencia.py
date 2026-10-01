"""Pruebas aisladas de la capa de persistencia SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arañador.implementacion.bd import (
    ConexionSQLite,
    MetadatoDocumento,
    RegistroBitacora,
    RepositorioBitacora,
    RepositorioDocumentos,
)


def _metadato(
    identificador: int,
    *,
    hash_url: str | None = None,
    hash_contenido: str | None = None,
    ruta: str | None = None,
    codigo_http: int | None = 200,
) -> MetadatoDocumento:
    """Construye metadata válida para las pruebas de persistencia."""
    return MetadatoDocumento(
        url=f"https://ejemplo.test/{identificador}",
        hash_url=hash_url or f"{identificador:064x}",
        dominio="ejemplo.test",
        categoria="guiones",
        ruta_relativa=ruta or f"ejemplo.test/guiones/{identificador}.txt",
        tamano_texto_bytes=10 + identificador,
        hash_contenido=hash_contenido or f"{identificador + 100:064x}",
        codigo_http=codigo_http,
        profundidad=1,
        fecha_descarga=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_esquema_wal_claves_y_migracion_idempotente(tmp_path: Path) -> None:
    """La conexión crea el esquema versionado y mantiene WAL en un archivo."""
    ruta = tmp_path / "nested" / "metadatos.db"

    with ConexionSQLite(ruta) as conexion:
        assert conexion.modo_journal == "wal"
        assert conexion.foreign_keys_activas is True
        assert conexion.busy_timeout_ms == 5_000
        assert conexion.version_esquema == 2
        assert conexion.esta_inicializada() is True
        assert conexion.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conexion.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
        assert conexion.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        columnas = {
            fila["name"]: fila["type"]
            for fila in conexion.fetchall("PRAGMA table_info(documentos)")
        }
        assert columnas["fecha_descarga"] == "TIMESTAMP"
        assert columnas["codigo_http"] == "INTEGER"

        primera = conexion.inicializar()
        segunda = conexion.inicializar()
        assert primera == segunda == 2
        aplicadas = conexion.fetchone("SELECT COUNT(*) AS n FROM schema_migrations")
        assert aplicadas is not None
        assert aplicadas["n"] == 2


def test_no_reduce_un_esquema_desconocido(tmp_path: Path) -> None:
    """Una base creada por una versión más nueva no se degrada al abrirla."""

    with ConexionSQLite(tmp_path / "futuro.db") as conexion:
        conexion.execute("PRAGMA user_version = 3")

        with pytest.raises(sqlite3.OperationalError, match="más reciente"):
            conexion.inicializar()

        assert conexion.version_esquema == 3


def test_deduplicacion_de_url_y_contenido_es_atomica(tmp_path: Path) -> None:
    """No se sobrescribe la primera fila cuando cambia URL o contenido."""
    with ConexionSQLite(tmp_path / "deduplicacion.db") as conexion:
        repositorio = RepositorioDocumentos(conexion)
        primero = _metadato(1, hash_url="a" * 64, hash_contenido="b" * 64)

        assert repositorio.guardar_documento(primero) is True
        assert repositorio.guardar_documento(primero) is False

        mismo_contenido = _metadato(
            2,
            hash_url="c" * 64,
            hash_contenido="b" * 64,
        )
        assert repositorio.guardar_documento(mismo_contenido) is False

        misma_url = MetadatoDocumento(
            url=primero.url,
            hash_url="d" * 64,
            dominio="ejemplo.test",
            categoria="guiones",
            ruta_relativa="ejemplo.test/guiones/3.txt",
            tamano_texto_bytes=13,
            hash_contenido="e" * 64,
            codigo_http=200,
            profundidad=1,
            fecha_descarga=primero.fecha_descarga,
        )
        # La URL exacta se cambia para comprobar también la protección del
        # índice de hash; la colisión de contenido ya cubre la segunda clave.
        assert repositorio.guardar_documento(misma_url) is False
        assert repositorio.contar_documentos() == 1
        assert repositorio.obtener_por_hash_contenido("b" * 64) == primero


def test_revisita_renueva_frescura_solo_si_el_contenido_no_cambio(tmp_path: Path) -> None:
    """La relectura idéntica actualiza fechas y no crea una segunda fila."""

    with ConexionSQLite(tmp_path / "revisita.db") as conexion:
        repositorio = RepositorioDocumentos(conexion)
        inicial = _metadato(1, hash_url="a" * 64, hash_contenido="b" * 64)
        inicial = replace(
            inicial,
            fecha_revisitacion=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert repositorio.guardar_documento(inicial)
        assert repositorio.necesita_revisita(inicial.hash_url) is True

        posterior = replace(
            inicial,
            fecha_descarga=datetime(2026, 1, 2, tzinfo=UTC),
            fecha_revisitacion=datetime(2099, 2, 1, tzinfo=UTC),
        )
        assert repositorio.registrar_revisita_exitosa(posterior) is True
        assert repositorio.necesita_revisita(inicial.hash_url) is False
        assert repositorio.contar_documentos() == 1

        diferente = replace(posterior, hash_contenido="c" * 64)
        assert repositorio.registrar_revisita_exitosa(diferente) is False


def test_lote_atomico_y_archivo_inexistente_no_deja_metadata(tmp_path: Path) -> None:
    """Un lote conserva filas válidas y omite rutas sin archivo sin corrupciones."""
    raiz = tmp_path / "texto"
    raiz.mkdir()
    (raiz / "uno.txt").write_text("uno", encoding="utf-8")
    presente = _metadato(1, ruta="uno.txt")
    ausente = _metadato(2, ruta="dos.txt")

    with ConexionSQLite(tmp_path / "lote.db") as conexion:
        repositorio = RepositorioDocumentos(conexion)
        resultado = repositorio.procesar_lote_documentos(
            [presente, ausente], raiz_almacenamiento=raiz
        )

        assert resultado.insertados == 1
        assert resultado.omitidos_sin_archivo == 1
        assert repositorio.contar_documentos() == 1
        assert repositorio.obtener_por_ruta("uno.txt") == presente


def test_integrity_error_revierte_el_lote_completo(tmp_path: Path) -> None:
    """Una violación no deduplicable no deja filas parciales."""
    with ConexionSQLite(tmp_path / "integridad.db") as conexion:
        repositorio = RepositorioDocumentos(conexion)
        valido = _metadato(1)
        invalido = _metadato(2)
        object.__setattr__(invalido, "tamano_texto_bytes", -1)

        with pytest.raises(sqlite3.IntegrityError):
            repositorio.procesar_lote_documentos([valido, invalido])

        assert repositorio.contar_documentos() == 0
        assert repositorio.ultimo_id is None


def test_bitacora_admite_error_de_red_y_batch(tmp_path: Path) -> None:
    """``None`` representa una respuesta que nunca llegó desde la red."""
    momento = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    registros = [
        RegistroBitacora(
            timestamp=momento,
            dominio="ejemplo.test",
            url_origen=None,
            url_destino="https://ejemplo.test/a",
            tiempo_respuesta_ms=12.5,
            codigo_http=200,
            accion="ALMACENADO",
        ),
        RegistroBitacora(
            timestamp=momento + timedelta(seconds=1),
            dominio="ejemplo.test",
            url_origen="https://ejemplo.test/a",
            url_destino="https://ejemplo.test/b",
            tiempo_respuesta_ms=0.0,
            codigo_http=None,
            accion="ERROR_RED",
        ),
    ]

    with ConexionSQLite(tmp_path / "bitacora.db") as conexion:
        repositorio = RepositorioBitacora(conexion)
        assert repositorio.registrar_lote(registros) == 2
        assert repositorio.contar() == 2
        segundo = repositorio.obtener_por_id(2)
        assert segundo is not None
        assert segundo.codigo_http is None
        assert repositorio.obtener_resumen_estadistico()["registros_sin_codigo_http"] == 1


def test_rutas_absolutas_y_travesia_se_rechazan() -> None:
    """El modelo protege la raíz de almacenamiento de rutas peligrosas."""
    with pytest.raises(ValueError):
        _metadato(1, ruta="/tmp/fuera.txt")
    with pytest.raises(ValueError):
        _metadato(2, ruta="../fuera.txt")
    with pytest.raises(ValueError, match="SHA-256"):
        _metadato(3, hash_url="no-es-un-hash")
