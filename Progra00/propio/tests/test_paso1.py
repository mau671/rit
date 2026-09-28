"""Pruebas del paso 1: semillas compartidas, rutas y base de datos."""

from __future__ import annotations

from pathlib import Path

from propio import configuracion
from propio.bd import (
    ACCION_ALMACENADO,
    ACCION_DUPLICADO_CONTENIDO,
    ACCION_DUPLICADO_URL,
    BaseDatos,
    Documento,
)
from propio.generador_hash import hash_contenido, hash_url
from propio.rutas import ruta_relativa_documento
from propio.semillas import cargar_semillas, derivar_hosts, host_permitido, interpretar_semillas


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


def test_lee_las_semillas_compartidas_con_scrapy() -> None:
    semillas = cargar_semillas(configuracion.RUTA_SEMILLAS)
    assert len(semillas) >= 10
    assert "imsdb.com" in derivar_hosts(semillas)


def test_semillas_ignora_comentarios_y_duplicados() -> None:
    texto = "# comentario\nhttps://a.com/x\nhttps://A.com/x  # repetida\n\nhttps://b.org/\n"
    assert interpretar_semillas(texto) == ("https://a.com/x", "https://b.org/")


def test_host_permitido_acepta_subdominios() -> None:
    assert host_permitido("es.wikipedia.org", ["wikipedia.org"])
    assert host_permitido("www.imsdb.com", ["imsdb.com"])
    assert not host_permitido("malimsdb.com", ["imsdb.com"])


def test_ruta_igual_a_la_de_scrapy() -> None:
    h = "ab" + "0" * 62
    assert ruta_relativa_documento("es.wikipedia.org", "documentos", h) == (
        f"es.wikipedia.org/documentos/ab/{h}.txt"
    )


def test_bd_idempotente_y_deduplica(tmp_path: Path) -> None:
    ruta = tmp_path / "metadatos_araña.db"
    bd = BaseDatos(ruta)
    bd.inicializar()  # segunda vez: no debe fallar
    assert bd.guardar_documento(_doc("https://ejemplo.com/a", "texto uno"))[0] == ACCION_ALMACENADO
    assert bd.guardar_documento(_doc("https://ejemplo.com/a", "otro"))[0] == ACCION_DUPLICADO_URL
    assert (
        bd.guardar_documento(_doc("https://ejemplo.com/b", "texto uno"))[0]
        == ACCION_DUPLICADO_CONTENIDO
    )
    assert not bd.necesita_revisita(hash_url("https://ejemplo.com/a"))
    assert bd.necesita_revisita(hash_url("https://ejemplo.com/nueva"))
    bd.registrar_bitacora(
        dominio="ejemplo.com", url_destino="https://ejemplo.com/a", accion="respuesta"
    )
    assert bd.resumen()["documentos"] == 1
    assert bd.resumen()["bitacora"] == 1
    bd.cerrar()
