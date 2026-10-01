"""Pruebas de la frontera persistente en SQLite (misma tabla que Scrapy).

Cubren el contrato congelado de ``frontera``: idempotencia de ``registrar``,
resurrección de ``visitada``/``error``, tope de intentos, orden BFS/FIFO y la
reanudación entre dos ``Arana`` sobre la misma base.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from propio.arana import Arana
from propio.bd import SENTENCIAS_ESQUEMA, BaseDatos, Documento
from propio.generador_hash import hash_contenido, hash_url
from propio.rutas import ruta_relativa_documento

URL_A = "https://ejemplo.com/a"
URL_B = "https://ejemplo.com/b"
URL_C = "https://ejemplo.com/c"
URL_D = "https://ejemplo.com/d"
ARCHIVO_BD = "metadatos_araña.db"


def _doc(url: str, texto: str) -> Documento:
    h = hash_contenido(texto)
    return Documento(
        url=url,
        hash_url=hash_url(url),
        dominio="ejemplo.com",
        categoria="documentos",
        ruta_relativa=ruta_relativa_documento("ejemplo.com", "documentos", h),
        tamano_texto_bytes=len(texto.encode()),
        hash_contenido=h,
        codigo_http=200,
        profundidad=0,
    )


def _id_de(ruta: Path, hash_: str) -> int:
    con = sqlite3.connect(ruta)
    try:
        fila = con.execute("SELECT id FROM frontera WHERE hash_url = ?", (hash_,)).fetchone()
    finally:
        con.close()
    assert fila is not None
    return int(fila[0])


def test_registrar_idempotente_conserva_id(tmp_path: Path) -> None:
    ruta = tmp_path / ARCHIVO_BD
    bd = BaseDatos(ruta)
    fr = bd.frontera
    fr.registrar(URL_A, 0, None)
    primero = _id_de(ruta, hash_url(URL_A))

    # Misma URL, otra profundidad: no se crea fila nueva ni cambia el id.
    fr.registrar(URL_A, 2, "https://ejemplo.com/")
    assert _id_de(ruta, hash_url(URL_A)) == primero

    pendientes = fr.pendientes()
    assert len(pendientes) == 1
    assert pendientes[0].profundidad == 2
    assert pendientes[0].prioridad == -2.0
    bd.cerrar()


def test_resurreccion_de_visitada_y_error(tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / ARCHIVO_BD)
    fr = bd.frontera
    clave = hash_url(URL_A)

    fr.registrar(URL_A, 0, None)
    fr.marcar_visitada(clave)
    assert fr.pendientes() == []

    # Volver a registrarla la resucita para una revisita.
    fr.registrar(URL_A, 0, None)
    assert [e.hash_url for e in fr.pendientes()] == [clave]

    fr.marcar_error(clave)
    bd.frontera.registrar(URL_A, 0, None)
    assert [e.hash_url for e in fr.pendientes()] == [clave]
    bd.cerrar()


def test_marcar_error_con_tope(tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / ARCHIVO_BD)
    fr = bd.frontera
    clave = hash_url(URL_A)
    fr.registrar(URL_A, 0, None)

    fr.marcar_error(clave, max_intentos=3)  # intentos=1 -> error, sigue elegible
    assert [e.hash_url for e in fr.pendientes()] == [clave]

    fr.marcar_error(clave, max_intentos=3)  # intentos=2 -> error, sigue elegible
    assert [e.hash_url for e in fr.pendientes()] == [clave]

    fr.marcar_error(clave, max_intentos=3)  # intentos=3 -> visitada, sale de la cola
    assert fr.pendientes() == []

    con = sqlite3.connect(tmp_path / ARCHIVO_BD)
    try:
        estado, intentos = con.execute(
            "SELECT estado, intentos FROM frontera WHERE hash_url = ?", (clave,)
        ).fetchone()
    finally:
        con.close()
    assert estado == "visitada" and intentos == 3
    bd.cerrar()


def test_orden_bfs_y_fifo(tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / ARCHIVO_BD)
    fr = bd.frontera
    # Se insertan desordenadas a propósito.
    fr.registrar(URL_B, 1, URL_A)
    fr.registrar(URL_A, 0, None)
    fr.registrar(URL_D, 1, URL_C)
    fr.registrar(URL_C, 0, None)

    assert [e.url for e in fr.pendientes()] == [URL_A, URL_C, URL_B, URL_D]
    bd.cerrar()


def test_reconciliar_descarta_frescas(tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / ARCHIVO_BD)
    bd.guardar_documento(_doc(URL_A, "texto ya guardado y vigente"))
    fr = bd.frontera
    fr.registrar(URL_A, 0, None)
    fr.registrar(URL_B, 0, None)

    descartadas = fr.reconciliar(lambda h: not bd.necesita_revisita(h))
    assert descartadas == 1
    assert [e.url for e in fr.pendientes()] == [URL_B]
    bd.cerrar()


def test_migracion_desde_base_v1(tmp_path: Path) -> None:
    ruta = tmp_path / ARCHIVO_BD
    con = sqlite3.connect(ruta)
    try:
        con.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                nombre TEXT NOT NULL,
                aplicada_en TEXT NOT NULL
            )
            """
        )
        for sentencia in SENTENCIAS_ESQUEMA:  # esquema v1: sin ``frontera``
            con.execute(sentencia)
        con.execute(
            "INSERT INTO schema_migrations (version, nombre, aplicada_en) VALUES (1, ?, ?)",
            ("esquema_documentos_y_bitacora", "2026-01-01T00:00:00Z"),
        )
        con.execute("PRAGMA user_version = 1")
        con.commit()
    finally:
        con.close()

    bd = BaseDatos(ruta)
    con = sqlite3.connect(ruta)
    try:
        tablas = {
            nombre
            for (nombre,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    assert "frontera" in tablas
    assert version == 2
    # Idempotente: una segunda inicialización no falla ni duplica migraciones.
    bd.inicializar()
    bd.frontera.registrar(URL_A, 0, None)
    assert [e.url for e in bd.frontera.pendientes()] == [URL_A]
    bd.cerrar()


def test_reanudacion_entre_ejecuciones(tmp_path: Path) -> None:
    ruta = tmp_path / ARCHIVO_BD
    repositorio = tmp_path / "repositorio"
    semilla = "https://ejemplo.com/"

    bd1 = BaseDatos(ruta)
    arana1 = Arana([semilla], bd1, repositorio=repositorio)
    assert arana1.encolar(URL_A, 1, semilla)
    assert arana1.encolar(URL_B, 1, semilla)
    assert arana1.pendientes() == 2
    bd1.cerrar()

    # Una segunda araña sobre la misma base recupera lo pendiente.
    bd2 = BaseDatos(ruta)
    arana2 = Arana([semilla], bd2, repositorio=repositorio)
    assert arana2._cargar_frontera() == 2
    assert arana2.pendientes() == 2
    bd2.cerrar()


def test_reanudacion_descarta_las_frescas(tmp_path: Path) -> None:
    ruta = tmp_path / ARCHIVO_BD
    repositorio = tmp_path / "repositorio"
    semilla = "https://ejemplo.com/"

    bd1 = BaseDatos(ruta)
    bd1.guardar_documento(_doc(URL_A, "documento vigente que no necesita revisita"))
    arana1 = Arana([semilla], bd1, repositorio=repositorio)
    assert arana1.encolar(URL_A, 1, semilla, forzar=True)  # forzada: entra igual
    assert arana1.encolar(URL_B, 1, semilla)
    bd1.cerrar()

    bd2 = BaseDatos(ruta)
    arana2 = Arana([semilla], bd2, repositorio=repositorio)
    # Solo reanuda la que sigue necesitando revisita; la fresca se descarta.
    assert arana2._cargar_frontera() == 1
    assert arana2.pendientes() == 1
    assert [e.url for e in bd2.frontera.pendientes()] == [URL_B]
    bd2.cerrar()
