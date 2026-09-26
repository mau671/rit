"""Pruebas de extremo a extremo entre araña, extracción y persistencia."""

from __future__ import annotations

from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse

from arañador.implementacion.aranas import AranaSemillas
from arañador.implementacion.bd import ConexionSQLite, RepositorioDocumentos
from arañador.implementacion.tuberias import (
    TuberiaAlmacenamiento,
    TuberiaExtraccion,
    TuberiaValidacion,
)


def test_item_de_spider_pasa_por_trafilatura_y_llega_a_sqlite(tmp_path: Path) -> None:
    """Verifica el contrato integrado sin realizar solicitudes de red."""

    url = "https://imsdb.com/scripts/pelicula-sintetica.html"
    parrafo = "Una historia audiovisual contiene diálogo, acciones y análisis. " * 30
    html = f"""
    <html lang="en">
      <head><title>Película sintética</title></head>
      <body>
        <nav>Menú que debe eliminarse durante la extracción</nav>
        <article>
          <h1>Película sintética</h1>
          <p>{parrafo}</p>
        </article>
      </body>
    </html>
    """
    respuesta = HtmlResponse(
        url=url,
        body=html.encode("utf-8"),
        encoding="utf-8",
        request=Request(url),
    )
    spider = AranaSemillas()
    item = next(resultado for resultado in spider.parse(respuesta) if hasattr(resultado, "fields"))

    extraido = TuberiaExtraccion().process_item(item)
    validado = TuberiaValidacion().process_item(extraido)

    conexion = ConexionSQLite(tmp_path / "metadatos.db")
    repositorio = RepositorioDocumentos(conexion)
    almacenamiento = TuberiaAlmacenamiento(
        tmp_path / "repositorio",
        repositorio=repositorio,
    )
    try:
        almacenamiento.open_spider()
        almacenado = almacenamiento.process_item(validado)
        filas = list((tmp_path / "repositorio").rglob("*.txt"))

        assert almacenado is validado
        assert "Menú que debe eliminarse" not in almacenado["texto"]
        assert almacenado["titulo"] == "Película sintética"
        assert almacenado["url"] == url
        assert repositorio.contar_documentos() == 1
        assert len(filas) == 1
        assert filas[0].read_text(encoding="utf-8") == almacenado["texto"]

        duplicado = dict(validado)
        relectura = almacenamiento.process_item(duplicado)
        assert relectura["revisitado"] is True
        assert repositorio.contar_documentos() == 1
    finally:
        almacenamiento.close_spider()
        conexion.cerrar()
