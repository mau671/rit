"""Pruebas sin red de la frontera compartida: migración, repositorio,
extensión de Scrapy y middleware de arranque.

La frontera es el contrato congelado entre las dos implementaciones del
proyecto; estas pruebas fijan su esquema, su semántica de cola y su integración
con las señales y el arranque de Scrapy.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scrapy import Request, signals
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.settings import Settings
from scrapy.signalmanager import SignalManager
from twisted.python.failure import Failure

from arañador.bibliotecas.sqlite import (
    MIGRACION_FRONTERA,
    MIGRACION_INICIAL,
    MIGRACIONES,
    ConexionSQLite,
)
from arañador.implementacion.bd import (
    MetadatoDocumento,
    RepositorioDocumentos,
    RepositorioFrontera,
)
from arañador.implementacion.extensiones.frontera_compartida import (
    ExtensionFronteraCompartida,
    MiddlewareFronteraInicial,
)
from arañador.implementacion.utilidades.generador_hash import generar_hash_url


def _url(numero: int) -> str:
    """Devuelve una URL estable para las pruebas de la frontera."""
    return f"https://sitio.test/pagina-{numero}"


def _fila(conexion: ConexionSQLite, consulta: str, parametros: tuple[Any, ...] = ()) -> Any:
    """Ejecuta ``fetchone`` y exige que exista al menos una fila."""
    fila = conexion.fetchone(consulta, parametros)
    assert fila is not None
    return fila


def _metadato(url: str, *, fecha_revisitacion: datetime | None) -> MetadatoDocumento:
    """Construye un documento válido para probar la reconciliación."""
    identificador = abs(hash(url)) % 10_000
    return MetadatoDocumento(
        url=url,
        hash_url=generar_hash_url(url),
        dominio="sitio.test",
        categoria="documentos",
        ruta_relativa=f"sitio.test/documentos/{identificador}.txt",
        tamano_texto_bytes=100,
        hash_contenido=f"{identificador + 1:064x}",
        codigo_http=200,
        profundidad=1,
        fecha_descarga=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        fecha_revisitacion=fecha_revisitacion,
    )


class SpiderFalso:
    """Doble de araña que expone ``parse`` como callback inyectable."""

    def parse(self, response: Any) -> None:
        """Callback de relleno, nunca se ejecuta en estas pruebas."""
        return None


class CrawlerFalso:
    """Doble mínimo de crawler con settings y gestor de señales real."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.signals = SignalManager()
        self.spider: Any = None


# --------------------------------------------------------------------------
# Migración
# --------------------------------------------------------------------------


def test_migracion_frontera_es_aditiva_sobre_una_base_v1(tmp_path: Path) -> None:
    """Una base v1 existente se actualiza a v2 sin perder sus filas."""

    ruta = tmp_path / "metadatos.db"
    with ConexionSQLite(ruta, migraciones=(MIGRACION_INICIAL,)) as conexion:
        assert conexion.version_esquema == 1
        tablas = {
            fila["name"]
            for fila in conexion.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "frontera" not in tablas
        assert _fila(conexion, "SELECT COUNT(*) AS n FROM schema_migrations")["n"] == 1
        # Una fila previa debe sobrevivir a la migración aditiva.
        conexion.execute(
            """
            INSERT INTO documentos (
                url, hash_url, dominio, categoria, ruta_relativa,
                tamano_texto_bytes, hash_contenido, codigo_http, profundidad,
                fecha_descarga
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _url(1),
                generar_hash_url(_url(1)),
                "sitio.test",
                "documentos",
                "sitio.test/documentos/1.txt",
                10,
                "a" * 64,
                200,
                1,
                "2026-01-01T12:00:00+00:00",
            ),
        )

    with ConexionSQLite(ruta) as conexion:
        assert conexion.version_esquema == 2
        tablas = {
            fila["name"]
            for fila in conexion.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "frontera" in tablas
        versiones = {
            int(fila["version"])
            for fila in conexion.fetchall("SELECT version FROM schema_migrations")
        }
        assert versiones == {1, 2}
        assert _fila(conexion, "SELECT COUNT(*) AS n FROM documentos")["n"] == 1
        assert (
            conexion.fetchone(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                ("idx_frontera_cola",),
            )
            is not None
        )


def test_esquema_frontera_coincide_con_el_contrato(tmp_path: Path) -> None:
    """Las columnas, restricciones y orden de la cola son el contrato congelado."""

    with ConexionSQLite(tmp_path / "esquema.db") as conexion:
        columnas = {
            fila["name"]: (fila["type"], fila["notnull"], fila["dflt_value"])
            for fila in conexion.fetchall("PRAGMA table_info(frontera)")
        }
        assert list(columnas) == [
            "id",
            "hash_url",
            "url",
            "profundidad",
            "prioridad",
            "url_origen",
            "estado",
            "intentos",
            "actualizada_en",
        ]
        assert columnas["hash_url"][1] == 1
        assert columnas["url"][1] == 1
        assert columnas["profundidad"][1] == 1
        assert columnas["prioridad"][1] == 1
        assert columnas["estado"][2] == "'pendiente'"
        assert columnas["intentos"][2] == "0"

        with pytest.raises(sqlite3.IntegrityError):
            conexion.execute(
                """
                INSERT INTO frontera (hash_url, url, profundidad, prioridad, estado)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("b" * 64, _url(9), -1, 1.0, "pendiente"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conexion.execute(
                """
                INSERT INTO frontera (hash_url, url, profundidad, prioridad, estado)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("c" * 64, _url(9), 0, 0.0, "desconocido"),
            )


# --------------------------------------------------------------------------
# RepositorioFrontera
# --------------------------------------------------------------------------


def test_registrar_es_idempotente_y_conserva_el_id(tmp_path: Path) -> None:
    """Registrar dos veces la misma URL no duplica ni cambia el turno."""

    with ConexionSQLite(tmp_path / "frontera.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        hash_url = frontera.registrar(_url(1), 1)
        frontera.registrar(_url(1), 1)

        assert hash_url == generar_hash_url(_url(1))
        assert _fila(conexion, "SELECT COUNT(*) AS n FROM frontera")["n"] == 1
        fila = _fila(conexion, "SELECT id, estado, intentos FROM frontera")
        assert fila["estado"] == "pendiente"
        assert fila["intentos"] == 0

        assert [entrada.hash_url for entrada in frontera.pendientes()] == [hash_url]


def test_resurreccion_de_visitada_y_error_vuelve_a_pendiente(tmp_path: Path) -> None:
    """Volver a registrar una URL resuelta la devuelve a la cola sin cambiar id."""

    with ConexionSQLite(tmp_path / "resurreccion.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        hash_url = frontera.registrar(_url(1), 0)
        primer_id = _fila(conexion, "SELECT id FROM frontera")["id"]

        assert frontera.marcar_visitada(hash_url) is True
        assert frontera.pendientes() == []

        frontera.registrar(_url(1), 0, url_origen=_url(2))
        fila = _fila(conexion, "SELECT id, estado, intentos, url_origen FROM frontera")
        assert fila["id"] == primer_id
        assert fila["estado"] == "pendiente"
        assert fila["intentos"] == 0
        assert fila["url_origen"] == _url(2)
        assert [entrada.hash_url for entrada in frontera.pendientes()] == [hash_url]

        frontera.marcar_error(hash_url)
        assert _fila(conexion, "SELECT estado FROM frontera")["estado"] == "error"
        frontera.registrar(_url(1), 0)
        assert _fila(conexion, "SELECT estado FROM frontera")["estado"] == "pendiente"
        assert _fila(conexion, "SELECT id FROM frontera")["id"] == primer_id


def test_pendientes_respeta_orden_bfs_y_fifo(tmp_path: Path) -> None:
    """Primero las URL menos profundas; a igual profundidad, la más antigua."""

    with ConexionSQLite(tmp_path / "orden.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        frontera.registrar(_url(10), 1)  # id 1, profundidad 1
        frontera.registrar(_url(20), 0)  # id 2, profundidad 0
        frontera.registrar(_url(30), 1)  # id 3, profundidad 1
        frontera.registrar(_url(40), 2)  # id 4, profundidad 2

        entradas = frontera.pendientes()

        assert [entrada.url for entrada in entradas] == [
            _url(20),
            _url(10),
            _url(30),
            _url(40),
        ]
        assert [entrada.prioridad for entrada in entradas] == [0.0, -1.0, -1.0, -2.0]
        assert [entrada.profundidad for entrada in entradas] == [0, 1, 1, 2]


def test_marcar_visitada_saca_de_la_cola(tmp_path: Path) -> None:
    """Una URL visitada no se vuelve a extraer."""

    with ConexionSQLite(tmp_path / "visitada.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        hash_url = frontera.registrar(_url(1), 0)

        assert frontera.marcar_visitada(hash_url) is True
        assert frontera.pendientes() == []
        assert frontera.marcar_visitada(hash_url) is True
        assert frontera.marcar_visitada("d" * 64) is False


def test_marcar_error_incrementa_y_respeta_el_tope(tmp_path: Path) -> None:
    """Los errores se reintentan hasta el tope y después se descartan."""

    with ConexionSQLite(tmp_path / "errores.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        hash_url = frontera.registrar(_url(1), 0)

        assert frontera.marcar_error(hash_url, max_intentos=3) is True
        assert _fila(conexion, "SELECT intentos, estado FROM frontera")["intentos"] == 1
        assert _fila(conexion, "SELECT estado FROM frontera")["estado"] == "error"
        # Sigue siendo extraíble mientras no agote los intentos.
        assert [entrada.hash_url for entrada in frontera.pendientes(max_intentos=3)] == [hash_url]

        frontera.marcar_error(hash_url, max_intentos=3)
        assert frontera.marcar_error(hash_url, max_intentos=3) is True
        fila = _fila(conexion, "SELECT intentos, estado FROM frontera")
        assert fila["intentos"] == 3
        assert fila["estado"] == "visitada"
        assert frontera.pendientes(max_intentos=3) == []
        assert frontera.marcar_error("e" * 64) is False


def test_pendientes_excluye_errores_agotados(tmp_path: Path) -> None:
    """El filtro de intentos de ``pendientes`` respeta el tope indicado."""

    with ConexionSQLite(tmp_path / "tope.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        agotada = frontera.registrar(_url(1), 0)
        vigente = frontera.registrar(_url(2), 0)

        # Ambas quedan en ``error`` con 2 y 1 intentos, sin alcanzar el tope de
        # 3; el filtro de ``pendientes`` decide según su propio ``max_intentos``.
        for _ in range(2):
            frontera.marcar_error(agotada, max_intentos=3)
        frontera.marcar_error(vigente, max_intentos=3)

        assert [entrada.hash_url for entrada in frontera.pendientes(max_intentos=2)] == [vigente]
        assert [entrada.hash_url for entrada in frontera.pendientes(max_intentos=3)] == [
            agotada,
            vigente,
        ]


def test_reconciliar_descarta_lo_ya_fresco(tmp_path: Path) -> None:
    """La reconciliación marca visitada lo que no necesita revisita."""

    with ConexionSQLite(tmp_path / "reconciliar.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        fresco = frontera.registrar(_url(1), 0)
        revisitable = frontera.registrar(_url(2), 1)

        descartadas = frontera.reconciliar(lambda hash_url: hash_url == fresco)

        assert descartadas == 1
        estados = {
            fila["hash_url"]: fila["estado"]
            for fila in conexion.fetchall("SELECT hash_url, estado FROM frontera")
        }
        assert estados[fresco] == "visitada"
        assert estados[revisitable] == "pendiente"
        assert [entrada.hash_url for entrada in frontera.pendientes()] == [revisitable]

        with pytest.raises(TypeError, match="invocable"):
            frontera.reconciliar("no-invocable")  # ty: ignore[invalid-argument-type]


def test_validaciones_del_repositorio(tmp_path: Path) -> None:
    """El repositorio rechaza entradas mal formadas."""

    with ConexionSQLite(tmp_path / "validacion.db") as conexion:
        frontera = RepositorioFrontera(conexion)
        with pytest.raises(ValueError, match="url"):
            frontera.registrar("   ", 0)
        with pytest.raises(TypeError, match="profundidad"):
            frontera.registrar(_url(1), True)
        with pytest.raises(ValueError, match="profundidad"):
            frontera.registrar(_url(1), -1)
        with pytest.raises(ValueError, match="hash_url"):
            frontera.marcar_visitada("")
        with pytest.raises(ValueError, match="max_intentos"):
            frontera.marcar_error("a" * 64, max_intentos=0)


# --------------------------------------------------------------------------
# MiddlewareFronteraInicial
# --------------------------------------------------------------------------


def _middleware(tmp_path: Path, **kwargs: Any) -> MiddlewareFronteraInicial:
    """Construye el middleware apuntando a una base temporal."""
    return MiddlewareFronteraInicial(str(tmp_path / "metadatos.db"), **kwargs)


def test_middleware_antepone_pendientes_y_descarta_frescas(tmp_path: Path) -> None:
    """El arranque reconcilia la cola y agrega las pendientes antes de las semillas."""

    ruta = tmp_path / "metadatos.db"
    with ConexionSQLite(ruta) as conexion:
        frontera = RepositorioFrontera(conexion)
        fresco = frontera.registrar(_url(1), 1)
        revisitable = frontera.registrar(_url(2), 0)
        documentos = RepositorioDocumentos(conexion)
        documentos.guardar_documento(
            _metadato(_url(1), fecha_revisitacion=datetime.now(UTC) + timedelta(days=30))
        )

    semilla = Request("https://sitio.test/semilla", meta={"depth": 0})
    spider = SpiderFalso()
    middleware = _middleware(tmp_path)

    solicitudes = list(
        middleware.process_start_requests(
            iter([semilla]),
            spider,  # ty: ignore[invalid-argument-type]
        )
    )

    assert [solicitud.url for solicitud in solicitudes] == [_url(2), "https://sitio.test/semilla"]
    pendiente, semilla_devuelta = solicitudes
    assert pendiente.callback == spider.parse
    assert pendiente.meta == {"depth": 0}
    assert pendiente.priority == 0.0
    assert semilla_devuelta is semilla

    # ``fresco`` quedó marcado como visitada (descartado) por la reconciliación.
    with ConexionSQLite(ruta) as conexion:
        estados = {
            fila["hash_url"]: fila["estado"]
            for fila in conexion.fetchall("SELECT hash_url, estado FROM frontera")
        }
        assert estados[fresco] == "visitada"
        assert estados[revisitable] == "pendiente"


def test_middleware_process_start_moderno_antepone_y_reconcilia(tmp_path: Path) -> None:
    """El enganche asíncrono de Scrapy 2.13+ expone la misma semántica."""

    ruta = tmp_path / "metadatos.db"
    with ConexionSQLite(ruta) as conexion:
        frontera = RepositorioFrontera(conexion)
        frontera.registrar(_url(1), 1)
        frontera.registrar(_url(2), 0)

    settings = Settings({"RUTA_BASE_DATOS": str(ruta)})
    crawler = CrawlerFalso(settings)
    crawler.spider = SpiderFalso()
    middleware = MiddlewareFronteraInicial.from_crawler(crawler)  # ty: ignore[invalid-argument-type]

    async def _start() -> Any:
        yield Request("https://sitio.test/semilla", meta={"depth": 0})

    async def _recolectar() -> list[Request]:
        return [elemento async for elemento in middleware.process_start(_start())]

    solicitudes = asyncio.run(_recolectar())

    assert [solicitud.url for solicitud in solicitudes] == [
        _url(2),
        _url(1),
        "https://sitio.test/semilla",
    ]
    assert solicitudes[0].priority == 0.0
    assert solicitudes[1].priority == -1.0
    assert solicitudes[0].callback == crawler.spider.parse
    assert solicitudes[0].meta == {"depth": 0}


def test_middleware_cierra_la_conexion_aunque_haya_error(tmp_path: Path) -> None:
    """Una base inválida no deja conexiones abiertas tras el arranque."""

    # Apuntar a un directorio fuerza un fallo de apertura de SQLite.
    middleware = MiddlewareFronteraInicial(str(tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        list(middleware.process_start_requests(iter([]), SpiderFalso()))  # ty: ignore[invalid-argument-type]


# --------------------------------------------------------------------------
# ExtensionFronteraCompartida y señales
# --------------------------------------------------------------------------


def test_extension_registra_y_marca_desde_las_senales(tmp_path: Path) -> None:
    """Las señales del motor actualizan la frontera sin acceso de red."""

    ruta = tmp_path / "metadatos.db"
    settings = Settings({"RUTA_BASE_DATOS": str(ruta)})
    crawler = CrawlerFalso(settings)
    extension = ExtensionFronteraCompartida.from_crawler(crawler)  # ty: ignore[invalid-argument-type]

    solicitud = Request(_url(1), meta={"depth": 2})
    crawler.signals.send_catch_log(signals.request_scheduled, request=solicitud, spider=None)

    with ConexionSQLite(ruta) as conexion:
        fila = _fila(conexion, "SELECT url, profundidad, prioridad, estado FROM frontera")
        assert fila["url"] == _url(1)
        assert fila["profundidad"] == 2
        assert fila["prioridad"] == -2.0
        assert fila["estado"] == "pendiente"

    respuesta = Response(_url(1), request=solicitud)
    crawler.signals.send_catch_log(
        signals.response_received,
        response=respuesta,
        request=solicitud,
        spider=None,
    )

    with ConexionSQLite(ruta) as conexion:
        assert _fila(conexion, "SELECT estado FROM frontera")["estado"] == "visitada"

    crawler.signals.send_catch_log(signals.spider_closed, spider=None, reason="finished")
    assert extension._conexion.cerrada is True
    # El cierre es idempotente aunque la señal se repita.
    extension.cerrar()


def test_extension_usa_profundidad_cero_si_falta(tmp_path: Path) -> None:
    """Una solicitud sin ``depth`` se registra en la profundidad cero."""

    ruta = tmp_path / "metadatos.db"
    settings = Settings({"RUTA_BASE_DATOS": str(ruta)})
    crawler = CrawlerFalso(settings)
    extension = ExtensionFronteraCompartida.from_crawler(crawler)  # ty: ignore[invalid-argument-type]

    extension.agendar(Request(_url(5)))
    extension.recibida(Response(_url(9)))  # sin request: no debe fallar

    with ConexionSQLite(ruta) as conexion:
        fila = _fila(conexion, "SELECT profundidad, prioridad FROM frontera")
        assert fila["profundidad"] == 0
        assert fila["prioridad"] == 0.0

    extension.cerrar()


def test_settings_de_frontera_declaran_migracion_y_componentes() -> None:
    """La configuración expone la migración v2 y los componentes nuevos."""

    from arañador.implementacion.configuracion import ConfiguracionAranador

    assert MIGRACION_FRONTERA.numero == 2
    assert MIGRACION_FRONTERA.nombre == "frontera_compartida"
    assert MIGRACIONES[-1] is MIGRACION_FRONTERA
    assert ConfiguracionAranador.SCHEDULER_PERSIST is False
    assert not hasattr(ConfiguracionAranador, "JOBDIR")
    assert ConfiguracionAranador.EXTENSIONS == {
        "arañador.implementacion.extensiones.frontera_compartida.ExtensionFronteraCompartida": 500
    }
    assert ConfiguracionAranador.SPIDER_MIDDLEWARES == {
        "arañador.implementacion.extensiones.frontera_compartida.MiddlewareFronteraInicial": 500
    }
    assert ConfiguracionAranador.FRONTERA_MAX_INTENTOS == 3


# --------------------------------------------------------------------------
# Errores: códigos reintentables, respuestas definitivas y fallos de descarga
# --------------------------------------------------------------------------


def _fallo_descarga(solicitud: Request, excepcion: BaseException | None = None) -> Failure:
    """Construye un ``Failure`` de Twisted con la solicitud asociada."""
    fallo = Failure(excepcion or TimeoutError("agotado"))
    fallo.request = solicitud  # ty: ignore[unresolved-attribute]
    return fallo


def _extension_con_ruta(ruta: Path) -> ExtensionFronteraCompartida:
    """Crea una extensión sobre una base temporal con topes explícitos."""
    settings = Settings({"RUTA_BASE_DATOS": str(ruta), "FRONTERA_MAX_INTENTOS": 3})
    crawler = CrawlerFalso(settings)
    return ExtensionFronteraCompartida.from_crawler(crawler)  # ty: ignore[invalid-argument-type]


def _estado(ruta: Path) -> tuple[str, int]:
    """Devuelve ``(estado, intentos)`` de la única fila de la frontera."""
    with ConexionSQLite(ruta) as conexion:
        fila = _fila(conexion, "SELECT estado, intentos FROM frontera")
        return str(fila["estado"]), int(fila["intentos"])


def test_extension_adjunta_errback_al_agendar(tmp_path: Path) -> None:
    """Cada solicitud agendada recibe el errback, sin pisar uno existente."""
    extension = _extension_con_ruta(tmp_path / "metadatos.db")

    solicitud = Request(_url(1), meta={"depth": 0})
    extension.agendar(solicitud)
    assert solicitud.errback is not None

    def errback_previo(failure: Any) -> None:
        """Callback de error previo del llamador que no debe sobreescribirse."""
        return None

    otra = Request(_url(2))
    otra.errback = errback_previo
    extension.agendar(otra)
    assert otra.errback is errback_previo
    extension.cerrar()


def test_extension_marca_error_en_codigos_reintentables(tmp_path: Path) -> None:
    """Un 503 suma intentos y al tercero pasa a ``visitada``."""
    ruta = tmp_path / "metadatos.db"
    extension = _extension_con_ruta(ruta)
    solicitud = Request(_url(1), meta={"depth": 1})
    extension.agendar(solicitud)

    extension.recibida(Response(_url(1), status=503, request=solicitud))
    assert _estado(ruta) == ("error", 1)
    extension.recibida(Response(_url(1), status=503, request=solicitud))
    assert _estado(ruta) == ("error", 2)
    extension.recibida(Response(_url(1), status=503, request=solicitud))
    assert _estado(ruta) == ("visitada", 3)
    extension.cerrar()


def test_extension_marca_visitada_en_respuesta_no_reintentable(tmp_path: Path) -> None:
    """Un 404 se considera definitivo y no suma intentos."""
    ruta = tmp_path / "metadatos.db"
    extension = _extension_con_ruta(ruta)
    solicitud = Request(_url(1), meta={"depth": 1})
    extension.agendar(solicitud)

    extension.recibida(Response(_url(1), status=404, request=solicitud))
    assert _estado(ruta) == ("visitada", 0)
    extension.cerrar()


def test_extension_errback_marca_error_en_fallo_de_descarga(tmp_path: Path) -> None:
    """Un fallo de descarga (timeout) agota un intento de la frontera."""
    ruta = tmp_path / "metadatos.db"
    extension = _extension_con_ruta(ruta)
    solicitud = Request(_url(1), meta={"depth": 1})
    extension.agendar(solicitud)

    extension._al_fallar_descarga(_fallo_descarga(solicitud))
    assert _estado(ruta) == ("error", 1)
    extension.cerrar()


def test_extension_errback_ignora_ignore_request(tmp_path: Path) -> None:
    """Un ``IgnoreRequest`` (p. ej. robots) no es error: se marca ``visitada``."""
    ruta = tmp_path / "metadatos.db"
    extension = _extension_con_ruta(ruta)
    solicitud = Request(_url(1), meta={"depth": 1})
    extension.agendar(solicitud)

    extension._al_fallar_descarga(_fallo_descarga(solicitud, IgnoreRequest("robots")))
    assert _estado(ruta) == ("visitada", 0)
    extension.cerrar()
