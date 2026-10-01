"""Pruebas sin red de la araña única, sus semillas y el middleware de bitácora."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response
from scrapy.settings import Settings
from scrapy.spiderloader import SpiderLoader
from scrapy.utils.misc import load_object, walk_modules_iter
from scrapy.utils.project import get_project_settings
from scrapy.utils.spider import iter_spider_classes

import arañador.implementacion.configuracion as configuracion
from arañador.implementacion.aranas import (
    AranaSemillas,
    cargar_semillas,
    derivar_hosts,
    interpretar_semillas,
)
from arañador.implementacion.bd import RepositorioBitacora
from arañador.implementacion.configuracion import ConfiguracionAranador, obtener_ruta_base_datos
from arañador.implementacion.elementos import DocumentoItem
from arañador.implementacion.filtro_duplicados import FiltroDuplicadosPersistentes
from arañador.implementacion.intermediarios import IntermediarioBitacora

# Las quince URL del plan, en el orden declarado en ``arañador/semillas.txt``.
SEMILLAS_ESPERADAS = (
    "https://imsdb.com/all-scripts.html",
    "https://www.dailyscript.com/movie.html",
    "https://www.simplyscripts.com/movie-scripts.html",
    "https://subslikescript.com/movies",
    "https://subslikescript.com/series",
    "https://tvtropes.org/pmwiki/pmwiki.php/Main/Film",
    "https://tvtropes.org/pmwiki/pmwiki.php/Main/Anime",
    "https://www.filmaffinity.com/es/cat_new_th_es.html",
    "https://www.tvmaze.com/shows",
    "https://www.animenewsnetwork.com/encyclopedia/",
    "https://es.wikipedia.org/wiki/Portal:Cine",
    "https://es.wikipedia.org/wiki/Categor%C3%ADa:Series_de_anime",
    "https://www.sensesofcinema.com/",
    "https://es.wikipedia.org/wiki/Categor%C3%ADa:Pel%C3%ADculas_de_ciencia_ficci%C3%B3n",
    "https://tvtropes.org/pmwiki/pmwiki.php/Main/Plots",
)

# Hosts derivados de las semillas, en orden y sin el prefijo ``www.``.
HOSTS_ESPERADOS = (
    "imsdb.com",
    "dailyscript.com",
    "simplyscripts.com",
    "subslikescript.com",
    "tvtropes.org",
    "filmaffinity.com",
    "tvmaze.com",
    "animenewsnetwork.com",
    "es.wikipedia.org",
    "sensesofcinema.com",
)


class RepositorioFalso:
    """DAO en memoria que puede simular un bloqueo de SQLite."""

    def __init__(self, *, bloqueado: bool = False) -> None:
        self.registros = []
        self.bloqueado = bloqueado

    def registrar(self, registro):
        """Guarda el registro o reproduce el error operativo de SQLite."""

        if self.bloqueado:
            raise sqlite3.OperationalError("database is locked")
        self.registros.append(registro)
        return len(self.registros)


def html_sintetico() -> str:
    """Devuelve una página con contenido principal y enlaces controlados."""

    return """
    <html lang="es">
      <head>
        <title>Fixture audiovisual</title>
        <meta name="description" content="Descripción sintética">
        <meta name="author" content="Equipo de pruebas">
        <link rel="canonical" href="https://sitio.test/canonico">
      </head>
      <body>
        <nav>La navegación también forma parte del texto provisional.</nav>
        <article>
          <h1>Encabezado principal</h1>
          <p>Primera línea de contenido.</p>
          <p>Segunda línea de contenido.</p>
        </article>
        <a href="/detalle.html">Detalle permitido</a>
        <a href="https://otro.example/detalle">Fuera de los hosts derivados</a>
        <a href="/archivo.pdf">Binario excluido</a>
      </body>
    </html>
    """


def respuesta_html(url: str, *, cuerpo: str | None = None, profundidad: int = 1) -> HtmlResponse:
    """Construye una respuesta local sin realizar ninguna petición."""

    solicitud = Request(url, meta={"depth": profundidad})
    return HtmlResponse(
        url=url,
        body=cuerpo or html_sintetico(),
        encoding="utf-8",
        request=solicitud,
    )


def recolectar_solicitudes(araña) -> list[Request]:
    """Ejecuta ``start`` sin red y devuelve sus solicitudes iniciales."""

    return asyncio.run(recolectar_async(araña.start()))


async def recolectar_async(generador: AsyncIterator[Request]) -> list[Request]:
    """Materializa un ``AsyncIterator`` de Scrapy dentro de una corrutina."""

    return [elemento async for elemento in generador]


class RelojDeterminista:
    """Reloj monotonic que entrega dos instantes consecutivos."""

    def __init__(self) -> None:
        self.valores = iter((10.0, 10.125))

    def __call__(self) -> float:
        """Devuelve el siguiente valor de tiempo simulado."""

        return next(self.valores)


def test_configuracion_cumple_las_politicas_principales() -> None:
    """Comprueba límites, cortesía, reintentos y componentes importables."""

    assert ConfiguracionAranador.ROBOTSTXT_OBEY is True
    assert ConfiguracionAranador.DEPTH_LIMIT == 4
    assert ConfiguracionAranador.DOWNLOAD_MAXSIZE == 5 * 1024 * 1024
    assert ConfiguracionAranador.CONCURRENT_REQUESTS_PER_DOMAIN == 4
    assert ConfiguracionAranador.AUTOTHROTTLE_ENABLED is True
    assert ConfiguracionAranador.AUTOTHROTTLE_START_DELAY == 0.5
    assert ConfiguracionAranador.AUTOTHROTTLE_MAX_DELAY == 5.0
    assert ConfiguracionAranador.RETRY_ENABLED is True
    assert ConfiguracionAranador.RETRY_TIMES >= 1
    assert ConfiguracionAranador.DOWNLOAD_TIMEOUT > 0
    assert ConfiguracionAranador.REDIRECT_ENABLED is True
    # La cola propia de Scrapy se desactiva: la frontera vive en SQLite.
    assert ConfiguracionAranador.SCHEDULER_PERSIST is False
    assert not hasattr(ConfiguracionAranador, "JOBDIR")
    assert ".pdf" in ConfiguracionAranador.EXTENSIONES_EXCLUIDAS

    for ruta in ConfiguracionAranador.ITEM_PIPELINES:
        assert load_object(ruta) is not None
    for ruta in ConfiguracionAranador.DOWNLOADER_MIDDLEWARES:
        assert load_object(ruta) is IntermediarioBitacora
    for ruta in ConfiguracionAranador.EXTENSIONS:
        assert load_object(ruta) is not None
    for ruta in ConfiguracionAranador.SPIDER_MIDDLEWARES:
        assert load_object(ruta) is not None


def test_la_araña_refuerza_robots_y_limites_generales() -> None:
    """La araña no reemplaza la política de robots ni el control de profundidad."""

    settings = Settings()
    AranaSemillas.update_settings(settings)

    assert settings.getbool("ROBOTSTXT_OBEY") is True
    assert settings.getint("DEPTH_LIMIT") == 4
    assert settings.getfloat("DOWNLOAD_DELAY") > 0


def test_ruta_base_datos_tiene_precedencia_de_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las tuberías y el middleware pueden converger a la misma ruta."""

    canonica = tmp_path / "canonica.db"
    especifica = tmp_path / "especifica.db"
    settings = Settings({"RUTA_BASE_DATOS": str(canonica)})
    assert obtener_ruta_base_datos(settings) == str(canonica)

    settings.set("TUBERIA_RUTA_BASE_DATOS", str(especifica), priority="command")
    assert obtener_ruta_base_datos(settings) == str(especifica)

    otra = Settings({"INTERMEDIARIO_RUTA_BASE_DATOS": str(especifica)})
    assert obtener_ruta_base_datos(otra) == str(especifica)

    monkeypatch.setenv("RAIZ_DATOS", str(tmp_path))
    relativa = Settings({"TUBERIA_RUTA_BASE_DATOS": "relativa.db"})
    assert obtener_ruta_base_datos(relativa) == str(tmp_path / "relativa.db")


def test_user_agent_usa_entorno_y_no_tiene_credenciales_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica la identidad configurable y un valor sin credenciales."""

    monkeypatch.setenv("USER_AGENT", "Aranador academico de prueba/1.0")
    assert configuracion._obtener_user_agent() == "Aranador academico de prueba/1.0"

    monkeypatch.delenv("USER_AGENT", raising=False)
    predeterminado = configuracion._obtener_user_agent()
    assert predeterminado
    assert "@" not in predeterminado
    assert "password" not in predeterminado.lower()


def test_semillas_declara_las_quince_url_del_plan_sin_duplicados() -> None:
    """El archivo de semillas es la única fuente de las URL de inicio."""

    semillas = cargar_semillas()

    assert semillas == SEMILLAS_ESPERADAS
    assert len(semillas) == 15
    assert len(set(semillas)) == 15


def test_interpretar_semillas_admite_comentarios_vacios_y_duplicados() -> None:
    """Las líneas vacías y los comentarios no generan entradas."""

    contenido = """
    # Comentario completo.
    https://uno.test/guion   # Comentario al final de la línea.

    https://dos.test/guion
    https://uno.test/guion
    """

    assert interpretar_semillas(contenido) == (
        "https://uno.test/guion",
        "https://dos.test/guion",
    )


@pytest.mark.parametrize(
    "contenido",
    [
        "ftp://archivo.test/guion",
        "/ruta/relativa",
        "https://espacio test.test/guion",
    ],
)
def test_interpretar_semillas_rechaza_entradas_no_http(contenido: str) -> None:
    """Una entrada inválida se informa con el número de línea."""

    with pytest.raises(ValueError, match="línea 1"):
        interpretar_semillas(contenido)


def test_hosts_derivados_quitan_www_conservan_orden_y_no_se_repiten() -> None:
    """Los hosts permitidos proceden de las semillas, no de listas escritas."""

    hosts = derivar_hosts(cargar_semillas())

    assert hosts == HOSTS_ESPERADOS
    assert len(set(hosts)) == len(hosts)
    assert all(not host.startswith("www.") for host in hosts)
    assert AranaSemillas.allowed_domains == list(HOSTS_ESPERADOS)
    assert AranaSemillas.start_urls == list(SEMILLAS_ESPERADAS)


def test_solo_existe_una_araña_descubrible_llamada_aranador() -> None:
    """``scrapy list`` debe mostrar únicamente la araña agnóstica."""

    cargador = SpiderLoader.from_settings(get_project_settings())
    assert cargador.list() == ["arañador"]
    assert cargador.load("arañador") is AranaSemillas

    descubiertas = [
        clase.name
        for modulo in walk_modules_iter("arañador.implementacion.aranas")
        for clase in iter_spider_classes(modulo)
    ]
    assert descubiertas == ["arañador"]
    assert AranaSemillas.name == "arañador"


@pytest.mark.parametrize("semilla", SEMILLAS_ESPERADAS)
def test_cada_semilla_es_permitida_por_los_hosts_derivados(semilla: str) -> None:
    """Toda semilla pertenece a la allowlist derivada de sí misma."""

    assert AranaSemillas().es_url_permitida(semilla)


def test_las_url_ajenas_binarias_o_con_credenciales_no_se_permiten() -> None:
    """La allowlist genérica descarta hosts, esquemas, extensiones y credenciales."""

    araña = AranaSemillas()

    assert not araña.es_url_permitida("https://otro.example/guion")
    assert not araña.es_url_permitida("ftp://imsdb.com/scripts/guion.html")
    assert not araña.es_url_permitida("https://imsdb.com/scripts/guion.pdf")
    assert not araña.es_url_permitida("https://usuario:clave@imsdb.com/scripts/guion.html")
    assert araña.es_url_permitida("https://www.tvtropes.org/pmwiki/pmwiki.php/Main/Film")


def test_extraccion_generica_produce_el_documento_y_sigue_los_enlaces() -> None:
    """La extracción es agnóstica: cualquier host semilla usa el mismo contrato."""

    araña = AranaSemillas(
        start_urls=["https://sitio.test/inicio"],
        allowed_domains=["sitio.test"],
    )
    respuesta = respuesta_html("https://sitio.test/articulo")
    resultados = list(araña.parse(respuesta))
    items = [resultado for resultado in resultados if isinstance(resultado, DocumentoItem)]
    solicitudes = [resultado for resultado in resultados if isinstance(resultado, Request)]

    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://sitio.test/articulo"
    assert item["dominio"] == "sitio.test"
    assert item["categoria"] == "documentos"
    assert item["html"] == respuesta.text
    assert item["codigo_http"] == 200
    assert item["profundidad"] == 1
    assert item["titulo"] == "Encabezado principal"
    assert item["idioma"] == "es"
    assert item["metadatos"]["url_canonica"] == "https://sitio.test/canonico"
    assert item["metadatos"]["autor"] == "Equipo de pruebas"

    # El texto es provisional: aún incluye la navegación que Trafilatura quita.
    assert "Primera línea de contenido" in item["texto"]
    assert "La navegación también forma parte del texto provisional" in item["texto"]

    assert [solicitud.url for solicitud in solicitudes] == ["https://sitio.test/detalle.html"]
    assert all(solicitud.meta["depth"] == 2 for solicitud in solicitudes)
    assert all(solicitud.callback == araña.parse for solicitud in solicitudes)


def test_solo_se_siguen_enlaces_de_los_hosts_derivados() -> None:
    """El ``LinkExtractor`` genérico acepta hosts semilla y descarta el resto."""

    araña = AranaSemillas()
    cuerpo = """
    <html><body>
      <a href="/movie/1-pelicula">Detalle relativo</a>
      <a href="https://www.subslikescript.com/movie/2-pelicula">Detalle con www</a>
      <a href="https://otro.example/movie/externo">Host ajeno</a>
      <a href="/movie/3-pelicula.pdf">Binario excluido</a>
      <a href="https://imsdb.com/scripts/Arrival.html">Otro host semilla</a>
    </body></html>
    """
    respuesta = respuesta_html("https://subslikescript.com/movies", cuerpo=cuerpo, profundidad=1)
    solicitudes = [
        resultado for resultado in araña.parse(respuesta) if isinstance(resultado, Request)
    ]
    urls = {solicitud.url for solicitud in solicitudes}

    assert urls == {
        "https://subslikescript.com/movie/1-pelicula",
        "https://www.subslikescript.com/movie/2-pelicula",
        "https://imsdb.com/scripts/Arrival.html",
    }
    assert all(solicitud.meta["depth"] == 2 for solicitud in solicitudes)


def test_la_profundidad_cuatro_no_agrega_solicitudes() -> None:
    """La araña detiene localmente el enlace justo en el límite configurado."""

    araña = AranaSemillas()
    cuerpo = '<html><body><a href="/scripts/otro.html">Otro</a></body></html>'
    respuesta = respuesta_html(
        "https://imsdb.com/all-scripts.html",
        cuerpo=cuerpo,
        profundidad=4,
    )
    resultados = list(araña.parse(respuesta))

    assert not any(isinstance(resultado, Request) for resultado in resultados)
    assert any(isinstance(resultado, DocumentoItem) for resultado in resultados)


def test_una_respuesta_de_host_ajeno_se_omite_con_una_advertencia(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un host fuera de las semillas no revienta: se omite y se registra."""

    araña = AranaSemillas()
    respuesta = respuesta_html("https://otro.example/articulo")

    with caplog.at_level(logging.WARNING, logger="arañador"):
        primera = list(araña.parse(respuesta))
        segunda = list(araña.parse(respuesta))

    assert primera == []
    assert segunda == []
    advertencias = [registro for registro in caplog.records if registro.levelno == logging.WARNING]
    assert len(advertencias) == 1
    assert "semillas.txt" in advertencias[0].getMessage()


def test_start_genera_una_solicitud_por_semilla_sin_acceso_de_red() -> None:
    """Ejecuta la API ``start`` asíncrona sin abrir un crawler ni descargar URLs."""

    araña = AranaSemillas()
    solicitudes = recolectar_solicitudes(araña)

    assert [solicitud.url for solicitud in solicitudes] == list(SEMILLAS_ESPERADAS)
    assert len(solicitudes) == 15
    assert all(solicitud.meta["depth"] == 0 for solicitud in solicitudes)
    assert all(solicitud.callback == araña.parse for solicitud in solicitudes)
    assert all(solicitud.dont_filter for solicitud in solicitudes)


def test_middleware_registra_referer_dominio_status_y_tiempo() -> None:
    """Persiste una respuesta completa con reloj determinista."""

    repositorio = RepositorioFalso()
    middleware = IntermediarioBitacora(
        repositorio,
        reloj=RelojDeterminista(),
        reloj_utc=lambda: datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
    )
    solicitud = Request(
        "https://example.test/destino",
        headers={"Referer": "https://example.test/origen"},
    )
    respuesta = Response("https://example.test/destino", status=206, request=solicitud)

    middleware.process_request(solicitud)
    assert middleware.process_response(solicitud, respuesta) is respuesta
    assert middleware.process_response(solicitud, respuesta) is respuesta
    assert len(repositorio.registros) == 1
    registro = repositorio.registros[0]
    assert registro.dominio == "example.test"
    assert registro.url_origen == "https://example.test/origen"
    assert registro.url_destino == solicitud.url
    assert registro.codigo_http == 206
    assert registro.tiempo_respuesta_ms == pytest.approx(125.0)
    assert registro.accion == "RESPUESTA"


def test_middleware_registra_excepcion_y_evita_respuesta_recuperada_duplicada() -> None:
    """Registra un error una sola vez aunque otra capa recupere la respuesta."""

    repositorio = RepositorioFalso()
    middleware = IntermediarioBitacora(
        repositorio,
        reloj=RelojDeterminista(),
        reloj_utc=lambda: datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
    )
    solicitud = Request("https://example.test/fallo")
    respuesta = Response("https://example.test/fallo", status=200, request=solicitud)

    middleware.process_request(solicitud)
    assert middleware.process_exception(solicitud, TimeoutError("sintetico")) is None
    middleware.process_response(solicitud, respuesta)

    assert len(repositorio.registros) == 1
    assert repositorio.registros[0].codigo_http is None
    assert repositorio.registros[0].accion == "ERROR"


def test_bloqueo_sqlite_no_tumba_la_respuesta_ni_el_item() -> None:
    """Un fallo de auditoría se contiene y permite que Scrapy continúe."""

    repositorio = RepositorioFalso(bloqueado=True)
    middleware = IntermediarioBitacora(
        repositorio,
        obligatoria=False,
        reloj=RelojDeterminista(),
    )
    solicitud = Request("https://example.test/bloqueado")
    respuesta = Response("https://example.test/bloqueado", status=200, request=solicitud)

    middleware.process_request(solicitud)
    assert middleware.process_response(solicitud, respuesta) is respuesta
    assert repositorio.registros == []
    assert middleware.registros_fallidos == 1
    assert middleware.registros_perdidos == 1


def test_bloqueo_sqlite_mandatorio_hace_visible_la_perdida_de_auditoria() -> None:
    """La recolección no continúa ocultando que perdió la bitácora."""

    repositorio = RepositorioFalso(bloqueado=True)
    middleware = IntermediarioBitacora(repositorio, reloj=RelojDeterminista())
    solicitud = Request("https://example.test/bloqueado-obligatorio")
    respuesta = Response(
        "https://example.test/bloqueado-obligatorio",
        status=200,
        request=solicitud,
    )

    middleware.process_request(solicitud)
    with pytest.raises(RuntimeError, match="bitácora obligatoria"):
        middleware.process_response(solicitud, respuesta)

    assert middleware.registros_fallidos == 1
    assert middleware.registros_perdidos == 0


def test_bitacora_registra_acciones_de_procesamiento() -> None:
    """Las señales de item añaden ALMACENADO y duplicados a la bitácora."""

    repositorio = RepositorioFalso()
    middleware = IntermediarioBitacora(repositorio, reloj=RelojDeterminista())
    solicitud = Request("https://example.test/pelicula")
    respuesta = Response("https://example.test/pelicula", status=200, request=solicitud)

    middleware._al_procesar_item({"url": solicitud.url}, respuesta)
    middleware._al_esquivar_item(
        {"url": solicitud.url},
        respuesta,
        exception=Exception("Documento duplicado por contenido"),
    )

    assert [registro.accion for registro in repositorio.registros] == [
        "ALMACENADO",
        "DUPLICADO_CONTENIDO",
    ]


def test_timeout_de_bitacora_se_refleja_en_busy_timeout(tmp_path: Path) -> None:
    """La espera configurada no se sustituye por el valor global de cinco segundos."""

    middleware = IntermediarioBitacora(
        ruta_base_datos=tmp_path / "bitacora.db",
        timeout_sqlite=0.125,
        obligatoria=False,
    )
    try:
        repositorio = middleware._obtener_repositorio()
        assert isinstance(repositorio, RepositorioBitacora)
        assert repositorio._conexion.busy_timeout_ms == 125
    finally:
        middleware.cerrar()


def test_arana_respeta_directivas_inline_noindex_y_nofollow() -> None:
    """La araña respeta <meta name='robots'> para omitir documentos o enlaces salientes."""

    araña = AranaSemillas(
        start_urls=["https://sitio.test/inicio"],
        allowed_domains=["sitio.test"],
    )

    # 1. noindex: no produce DocumentoItem, pero sí sigue enlaces
    cuerpo_noindex = """
    <html><head>
      <meta name="robots" content="noindex, follow" />
    </head><body>
      <h1>Página no indexable</h1>
      <a href="https://sitio.test/siguiente">Enlace permitido</a>
    </body></html>
    """
    resp_noindex = respuesta_html(
        "https://sitio.test/noindex", cuerpo=cuerpo_noindex, profundidad=1
    )
    res_noindex = list(araña.parse(resp_noindex))
    assert not any(isinstance(r, DocumentoItem) for r in res_noindex)
    assert any(
        isinstance(r, Request) and r.url == "https://sitio.test/siguiente" for r in res_noindex
    )

    # 2. nofollow: produce DocumentoItem, pero no sigue enlaces
    cuerpo_nofollow = """
    <html><head>
      <meta name="robots" content="nofollow" />
    </head><body>
      <h1>Página con enlaces no permitidos</h1>
      <a href="https://sitio.test/no-seguir">Enlace no seguido</a>
    </body></html>
    """
    resp_nofollow = respuesta_html(
        "https://sitio.test/nofollow", cuerpo=cuerpo_nofollow, profundidad=1
    )
    res_nofollow = list(araña.parse(resp_nofollow))
    assert any(isinstance(r, DocumentoItem) for r in res_nofollow)
    assert not any(isinstance(r, Request) for r in res_nofollow)

    # 3. none (noindex, nofollow): no produce ítem ni solicitudes
    cuerpo_none = """
    <html><head>
      <meta name="robots" content="none" />
    </head><body>
      <a href="https://sitio.test/bloqueado">Bloqueado</a>
    </body></html>
    """
    resp_none = respuesta_html("https://sitio.test/none", cuerpo=cuerpo_none, profundidad=1)
    res_none = list(araña.parse(resp_none))
    assert len(res_none) == 0


def test_filtro_duplicados_asigna_prioridad_bfs_segun_profundidad(tmp_path: Path) -> None:
    """Las URLs más superficiales conservan mayor prioridad para estrategia BFS."""

    filtro = FiltroDuplicadosPersistentes(tmp_path / "filtro.db")
    filtro.open()
    try:
        req_d0 = Request("https://sitio.test/p0", meta={"depth": 0}, priority=0)
        req_d1 = Request("https://sitio.test/p1", meta={"depth": 1}, priority=0)
        req_d2 = Request("https://sitio.test/p2", meta={"depth": 2}, priority=0)

        filtro.request_seen(req_d0)
        filtro.request_seen(req_d1)
        filtro.request_seen(req_d2)

        # En Scrapy mayor prioridad se procesa primero: d0 > d1 > d2
        assert req_d0.priority > req_d1.priority > req_d2.priority
        assert req_d0.priority == 0
        assert req_d1.priority == -10
        assert req_d2.priority == -20
    finally:
        filtro.close("finished")
