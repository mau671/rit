"""Pruebas aisladas de las tuberías; no realizan acceso a red."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

from scrapy import Request, signals
from scrapy.exceptions import DropItem
from scrapy.settings import Settings

from arañador.implementacion.bd import MetadatoDocumento
from arañador.implementacion.elementos import DocumentoItem
from arañador.implementacion.filtro_duplicados import FiltroDuplicadosPersistentes
from arañador.implementacion.tuberias import (
    ConexionSQLite,
    RepositorioDocumentos,
    TuberiaAlmacenamiento,
    TuberiaMetricas,
    TuberiaValidacion,
)
from arañador.implementacion.utilidades.generador_hash import generar_hash_url

_CAMPOS_ESPERADOS = {
    "url",
    "hash_url",
    "dominio",
    "categoria",
    "texto",
    "ruta_relativa",
    "tamano_texto_bytes",
    "hash_contenido",
    "codigo_http",
    "profundidad",
    "fecha_descarga",
    "revisitado",
    "fecha_revisitacion",
    "titulo",
    "idioma",
}


class RepositorioFalso:
    """DAO en memoria con la API mínima del plan."""

    def __init__(self, *, fallar_al_guardar: bool = False) -> None:
        self.urls: set[str] = set()
        self.contenidos: set[str] = set()
        self.documentos: list[Any] = []
        self.fallar_al_guardar = fallar_al_guardar
        self._lock = Lock()

    def existe_hash_url(self, hash_url: str) -> bool:
        with self._lock:
            return hash_url in self.urls

    def existe_hash_contenido(self, hash_contenido: str) -> bool:
        with self._lock:
            return hash_contenido in self.contenidos

    def guardar_documento(self, metadato: Any) -> None:
        if self.fallar_al_guardar:
            raise OSError("fallo simulado de SQLite")
        with self._lock:
            if metadato.hash_url in self.urls or metadato.hash_contenido in self.contenidos:
                raise AssertionError("el DAO de prueba no recibió una deduplicación previa")
            self.urls.add(metadato.hash_url)
            self.contenidos.add(metadato.hash_contenido)
            self.documentos.append(metadato)


class SenalesFalsas:
    """Administra conexiones de señales sin levantar un crawler."""

    def __init__(self) -> None:
        self.conexiones: list[tuple[Any, Any]] = []

    def connect(self, receptor: Any, signal: Any, **_: Any) -> None:
        self.conexiones.append((receptor, signal))


def _documento(
    url: str = "https://example.com/analisis/pelicula",
    texto: str | None = None,
    categoria: str = "analisis",
) -> dict[str, Any]:
    contenido = texto or ("Un análisis cinematográfico detallado. " * 20)
    return {
        "url": url,
        "dominio": "example.com",
        "categoria": categoria,
        "texto": contenido,
        "codigo_http": 200,
        "profundidad": 2,
        "titulo": "Análisis de una película",
        "idioma": "es",
    }


def test_documento_item_declara_todos_los_campos_del_plan() -> None:
    assert set(DocumentoItem.fields) == _CAMPOS_ESPERADOS


def test_validacion_normaliza_esquema_y_calcula_hashes() -> None:
    fecha = datetime(2026, 1, 2, 3, 4, 5)
    tubería = TuberiaValidacion(caracteres_minimos=10)

    resultado = tubería.process_item(
        {
            "url": "HTTPS://EXAMPLE.COM:443/analisis#fragmento",
            "texto": "  Primer\r\n\r\n\r\nSegundo párrafo.  ",
            "categoria": "  análisis ",
            "codigo_http": 200,
            "profundidad": 1,
            "fecha_descarga": fecha,
        }
    )

    assert isinstance(resultado, DocumentoItem)
    assert resultado["url"] == "https://example.com/analisis"
    assert resultado["hash_url"] == hashlib.sha256(resultado["url"].encode()).hexdigest()
    assert (
        resultado["hash_contenido"]
        == hashlib.sha256(resultado["texto"].encode("utf-8")).hexdigest()
    )
    assert resultado["tamano_texto_bytes"] == len(resultado["texto"].encode("utf-8"))
    assert resultado["fecha_descarga"] == fecha.replace(tzinfo=UTC)
    assert resultado["fecha_revisitacion"] == fecha.replace(tzinfo=UTC) + timedelta(days=30)
    assert resultado["profundidad"] == 1
    assert resultado["codigo_http"] == 200


def test_validacion_rechaza_esquema_tipo_extension_y_densidad() -> None:
    tubería = TuberiaValidacion(caracteres_minimos=50, densidad_minima=0.5)

    casos = [
        {**_documento(url="ftp://example.com/texto"), "texto": "A" * 80},
        {**_documento(), "texto": 123},
        _documento(url="https://example.com/pelicula.jpg"),
        _documento(texto="!!!!!!!!!!!!"),
    ]
    for item in casos:
        try:
            tubería.process_item(item)
        except DropItem:
            pass
        else:  # pragma: no cover - indica una regresión concreta
            raise AssertionError(f"se esperaba DropItem para {item!r}")


def test_validacion_conserva_una_lista_corta_legitima() -> None:
    tubería = TuberiaValidacion(caracteres_minimos=300, densidad_minima=0.8)

    resultado = tubería.process_item(
        {
            "url": "https://example.com/movies",
            "titulo": "Movies",
            "categoria": "catalogo",
            "codigo_http": 200,
            "profundidad": 0,
            "texto": "Movie One\nMovie Two\nMovie Three",
        }
    )

    assert set(resultado) == _CAMPOS_ESPERADOS
    assert resultado["hash_contenido"]


def test_validacion_desde_crawler_usa_settings_reales_de_scrapy() -> None:
    ajustes = Settings(
        {
            "TUBERIA_LONGITUD_MINIMA": 5,
            "TUBERIA_DENSIDAD_MINIMA": 0.2,
            "TUBERIA_PERMITIR_HTTP": False,
            "TUBERIA_EXTENSIONS_PROHIBIDAS": ["bin", ".pdf"],
        }
    )
    crawler = SimpleNamespace(settings=ajustes)

    tubería = TuberiaValidacion.from_crawler(crawler)

    assert tubería.caracteres_minimos == 5
    assert tubería.densidad_minima == 0.2
    assert tubería.permitir_http is False
    assert tubería.extensiones_prohibidas == {"bin", "pdf"}


def test_almacenamiento_escribe_utf8_en_ruta_particionada(tmp_path: Path) -> None:
    repositorio = RepositorioFalso()
    tubería = TuberiaAlmacenamiento(
        ruta_repositorio=tmp_path / "repositorio",
        repositorio=repositorio,
    )
    item = DocumentoItem(**_documento(texto="Acentuación: acción, año y análisis."))

    resultado = tubería.process_item(item)

    hash_contenido = hashlib.sha256(item["texto"].encode("utf-8")).hexdigest()
    ruta_relativa = Path("example.com") / "analisis" / hash_contenido[:2] / f"{hash_contenido}.txt"
    assert resultado is item
    assert item["ruta_relativa"] == ruta_relativa.as_posix()
    assert (tmp_path / "repositorio" / ruta_relativa).read_text(encoding="utf-8") == item["texto"]
    assert repositorio.documentos[0].ruta_relativa == ruta_relativa.as_posix()
    assert not list((tmp_path / "repositorio").rglob("*.tmp"))


def test_almacenamiento_desde_crawler_inyecta_el_repositorio(tmp_path: Path) -> None:
    repositorio = RepositorioFalso()
    ajustes = Settings(
        {
            "TUBERIA_RUTA_REPOSITORIO": str(tmp_path / "repo"),
            "TUBERIA_RUTA_BASE_DATOS": str(tmp_path / "meta.db"),
            "TUBERIA_REPOSITORIO": repositorio,
            "TUBERIA_HASH_RUTA": "hash_url",
        }
    )

    tubería = TuberiaAlmacenamiento.from_crawler(SimpleNamespace(settings=ajustes))

    assert tubería.ruta_repositorio == tmp_path / "repo"
    assert tubería.repositorio is repositorio
    assert tubería.campo_hash_ruta == "hash_url"


def test_almacenamiento_deduplica_url_y_contenido(tmp_path: Path) -> None:
    repositorio = RepositorioFalso()
    tubería = TuberiaAlmacenamiento(tmp_path / "repo", repositorio=repositorio)
    primero = tubería.process_item(DocumentoItem(**_documento()))

    try:
        tubería.process_item(dict(primero))
    except DropItem as error:
        assert "url" in str(error)
    else:  # pragma: no cover - indica una regresión concreta
        raise AssertionError("se esperaba DropItem por URL duplicada")

    otro_url = dict(primero)
    otro_url["url"] = "https://example.com/otro-documento"
    otro_url["hash_url"] = hashlib.sha256(otro_url["url"].encode("utf-8")).hexdigest()
    try:
        tubería.process_item(otro_url)
    except DropItem as error:
        assert "contenido" in str(error)
    else:  # pragma: no cover - indica una regresión concreta
        raise AssertionError("se esperaba DropItem por contenido duplicado")

    archivos = list((tmp_path / "repo").rglob("*.txt"))
    assert len(archivos) == 1


def test_almacenamiento_usa_transaccion_atomica_del_repositorio(tmp_path: Path) -> None:
    tubería = TuberiaAlmacenamiento(
        tmp_path / "repo",
        ruta_base_datos=tmp_path / "meta.db",
    )
    primero = tubería.process_item(DocumentoItem(**_documento()))
    duplicado = dict(primero)
    duplicado["url"] = "https://example.com/mismo-texto"
    duplicado["hash_url"] = hashlib.sha256(duplicado["url"].encode("utf-8")).hexdigest()

    try:
        tubería.process_item(duplicado)
    except DropItem as error:
        assert "contenido" in str(error) or "duplicado_contenido" in str(error)
    else:  # pragma: no cover - indica una regresión concreta
        raise AssertionError("se esperaba DropItem por contenido duplicado")

    repositorio = tubería.repositorio
    assert repositorio is not None
    assert repositorio.contar_documentos() == 1
    assert len(list((tmp_path / "repo").rglob("*.txt"))) == 1
    conexion = tubería._conexion
    tubería.close_spider()
    assert conexion is not None and conexion.cerrada


def test_almacenamiento_renueva_revisita_identica_sin_duplicar_documento(tmp_path: Path) -> None:
    """La segunda entrega idéntica actualiza frescura y conserva una sola fila."""

    conexion = ConexionSQLite(tmp_path / "revisita.db")
    repositorio = RepositorioDocumentos(conexion)
    tubería = TuberiaAlmacenamiento(
        tmp_path / "repo",
        repositorio=repositorio,
    )
    primero = tubería.process_item(DocumentoItem(**_documento()))
    segundo = dict(primero)
    segundo["fecha_descarga"] = datetime(2026, 2, 1, tzinfo=UTC)
    segundo["fecha_revisitacion"] = datetime(2026, 3, 1, tzinfo=UTC)
    try:
        resultado = tubería.process_item(segundo)
        assert resultado["revisitado"] is True
        assert repositorio.contar_documentos() == 1
        actualizado = repositorio.obtener_por_url(primero["url"])
        assert actualizado is not None
        assert actualizado.fecha_descarga == datetime(2026, 2, 1, tzinfo=UTC)
    finally:
        tubería.close_spider()
        conexion.cerrar()


def test_almacenamiento_actualiza_contenido_cambiado_en_revisita(tmp_path: Path) -> None:
    """Una relectura con texto nuevo conserva la URL y reemplaza el artefacto."""

    conexion = ConexionSQLite(tmp_path / "cambio.db")
    repositorio = RepositorioDocumentos(conexion)
    tubería = TuberiaAlmacenamiento(tmp_path / "repo", repositorio=repositorio)
    primero = tubería.process_item(DocumentoItem(**_documento(texto="contenido original " * 30)))
    segundo = dict(primero)
    segundo["texto"] = "contenido actualizado " * 30
    segundo["hash_contenido"] = hashlib.sha256(segundo["texto"].encode()).hexdigest()
    segundo["tamano_texto_bytes"] = len(segundo["texto"].encode())
    try:
        resultado = tubería.process_item(segundo)
        assert resultado["revisitado"] is True
        assert repositorio.contar_documentos() == 1
        assert repositorio.obtener_por_hash_contenido(segundo["hash_contenido"]) is not None
        assert len(list((tmp_path / "repo").rglob("*.txt"))) == 1
    finally:
        tubería.close_spider()
        conexion.cerrar()


def test_filtro_persistente_omite_documento_no_vencido(tmp_path: Path) -> None:
    """La segunda capa de deduplicación consulta la fecha de relectura."""

    ruta_db = tmp_path / "dedupe.db"
    conexion = ConexionSQLite(ruta_db)
    repositorio = RepositorioDocumentos(conexion)
    url = "https://example.com/analisis/pendiente"
    metadato = MetadatoDocumento(
        url=url,
        hash_url=generar_hash_url(url),
        dominio="example.com",
        categoria="analisis",
        ruta_relativa="example.com/analisis/pendiente.txt",
        tamano_texto_bytes=10,
        hash_contenido="b" * 64,
        codigo_http=200,
        profundidad=1,
        fecha_descarga=datetime(2026, 1, 1, tzinfo=UTC),
        fecha_revisitacion=datetime(2099, 1, 1, tzinfo=UTC),
    )
    assert repositorio.guardar_documento(metadato)
    conexion.cerrar()

    filtro = FiltroDuplicadosPersistentes(ruta_db)
    try:
        filtro.open()
        assert filtro.request_seen(Request(url)) is True
        assert filtro.request_seen(Request("https://example.com/analisis/nuevo")) is False
    finally:
        filtro.close("finished")


def test_almacenamiento_deshace_archivo_nuevo_si_sqlite_falla(tmp_path: Path) -> None:
    repositorio = RepositorioFalso(fallar_al_guardar=True)
    tubería = TuberiaAlmacenamiento(tmp_path / "repo", repositorio=repositorio)

    try:
        tubería.process_item(DocumentoItem(**_documento()))
    except OSError:
        pass
    else:  # pragma: no cover - indica una regresión concreta
        raise AssertionError("se esperaba el fallo simulado")

    assert not list((tmp_path / "repo").rglob("*.txt"))
    assert not list((tmp_path / "repo").rglob("*.tmp"))


def test_almacenamiento_no_borra_un_archivo_preexistente(tmp_path: Path) -> None:
    repositorio = RepositorioFalso(fallar_al_guardar=True)
    tubería = TuberiaAlmacenamiento(tmp_path / "repo", repositorio=repositorio)
    item = DocumentoItem(**_documento())
    hash_contenido = hashlib.sha256(item["texto"].encode("utf-8")).hexdigest()
    ruta = (
        tmp_path
        / "repo"
        / "example.com"
        / "analisis"
        / hash_contenido[:2]
        / f"{hash_contenido}.txt"
    )
    ruta.parent.mkdir(parents=True)
    ruta.write_text("contenido válido previo", encoding="utf-8")

    with suppress(OSError):
        tubería.process_item(item)

    assert ruta.read_text(encoding="utf-8") == "contenido válido previo"


def test_metricas_son_seguras_entre_hilos() -> None:
    metricas = TuberiaMetricas()

    def registrar(_: int) -> None:
        for indice in range(100):
            metricas.registrar_documento(10, f"dominio{indice % 4}.example")
            if indice % 10 == 0:
                metricas.registrar_error()

    with ThreadPoolExecutor(max_workers=8) as ejecutor:
        list(ejecutor.map(registrar, range(8)))

    assert metricas.documentos == 800
    assert metricas.bytes == 8_000
    assert metricas.dominios == 4
    assert metricas.errores == 80


def test_metricas_calculan_velocidad_y_conectan_senales_actuales() -> None:
    reloj = [0.0]
    metricas = TuberiaMetricas(reloj=lambda: reloj[0])
    metricas.registrar_documento(100, "example.com")
    metricas.registrar_documento(300, "example.com")
    metricas.registrar_error()
    reloj[0] = 2.0

    assert metricas.documentos_por_segundo == 1.0
    assert metricas.bytes_por_segundo == 200.0
    assert metricas.resumen()["documentos_por_dominio"] == {"example.com": 2}

    manejador = SenalesFalsas()
    crawler = SimpleNamespace(signals=manejador, settings=Settings())
    desde_crawler = TuberiaMetricas.from_crawler(crawler)
    conectadas = {senal for _, senal in manejador.conexiones}
    assert {signals.spider_opened, signals.spider_closed, signals.item_scraped} <= conectadas
    if hasattr(signals, "item_error"):
        assert signals.item_error in conectadas
    assert desde_crawler.process_item({"cualquier": "dato"}) == {"cualquier": "dato"}

    directa = TuberiaMetricas()
    directa.process_item({"tamano_texto_bytes": 12, "dominio": "example.com"})
    directa.process_item({"tamano_texto_bytes": 12, "dominio": "example.com", "revisitado": True})
    assert directa.documentos == 1
    assert directa.revisitas == 1
    assert directa.bytes == 12


def test_reexports_de_conexion_y_repositorio_son_operativos(tmp_path: Path) -> None:
    conexion = ConexionSQLite(tmp_path / "meta.db")
    repositorio = RepositorioDocumentos(conexion)
    try:
        assert conexion.modo_journal == "wal"
        assert repositorio.contar_documentos() == 0
    finally:
        repositorio.cerrar()
        conexion.cerrar()
