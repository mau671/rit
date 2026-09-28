"""Prueba de integración: un sitio web falso local recorrido por la araña real.

El sitio está diseñado para ejercitar cada política:
robots.txt, noindex, nofollow, calidad textual, duplicados, extensiones,
hosts externos, reintentos ante 503 y profundidad.
"""

from __future__ import annotations

import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from propio.arana import Arana
from propio.bd import BaseDatos
from propio.extraccion import analizar_html, motivo_rechazo

PARRAFO = "<p>" + "El guion describe la escena del agujero negro con mucho detalle. " * 10 + "</p>"
DOBLE = "<p>" + "Este texto aparece en dos direcciones distintas del mismo sitio. " * 10 + "</p>"


def pagina(cuerpo: str, meta: str = "") -> str:
    return f"<html><head><title>T</title>{meta}<script>var x=1;</script></head><body><nav>MENU</nav>{cuerpo}</body></html>"


SITIO: dict[str, tuple[int, str, str]] = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /privado/\n"),
    "/": (
        200,
        "text/html",
        pagina(
            PARRAFO
            + """
        <a href="/a">a</a> <a href="/privado/x">p</a> <a href="/noindex">n</a>
        <a href="/nofollow">nf</a> <a href="/corta">c</a> <a href="/archivo.pdf">pdf</a>
        <a href="https://externo.com/x">ext</a> <a href="/dup1">d1</a> <a href="/dup2">d2</a>
        <a href="/inestable">i</a> <a href="/a#seccion">a otra vez</a>"""
        ),
    ),
    "/a": (
        200,
        "text/html",
        pagina(PARRAFO.replace("detalle", "detalle A") + '<a href="/a/b">b</a>'),
    ),
    "/a/b": (
        200,
        "text/html",
        pagina(PARRAFO.replace("detalle", "detalle B") + '<a href="/a/b/c">c</a>'),
    ),
    "/a/b/c": (200, "text/html", pagina(PARRAFO.replace("detalle", "detalle C"))),
    "/privado/x": (200, "text/html", pagina(PARRAFO + "PRIVADO")),
    "/noindex": (
        200,
        "text/html",
        pagina(
            PARRAFO + '<a href="/desde-noindex">x</a>', '<meta name="robots" content="noindex">'
        ),
    ),
    "/desde-noindex": (200, "text/html", pagina(PARRAFO.replace("detalle", "detalle N"))),
    "/nofollow": (
        200,
        "text/html",
        pagina(
            PARRAFO.replace("detalle", "detalle F") + '<a href="/oculta">x</a>',
            '<meta name="robots" content="nofollow">',
        ),
    ),
    "/oculta": (200, "text/html", pagina(PARRAFO + "OCULTA")),
    "/corta": (200, "text/html", pagina("<p>Muy corto.</p>")),
    "/dup1": (200, "text/html", pagina(DOBLE)),
    "/dup2": (200, "text/html", pagina(DOBLE)),
    "/inestable": (200, "text/html", pagina(PARRAFO.replace("detalle", "detalle I"))),
}
FALLOS_RESTANTES = {"/inestable": 1}  # responde 503 una vez y luego 200
VISITAS: list[str] = []


class Manejador(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        VISITAS.append(self.path)
        if FALLOS_RESTANTES.get(self.path, 0) > 0:
            FALLOS_RESTANTES[self.path] -= 1
            self.send_response(503)
            self.end_headers()
            return
        codigo, tipo, cuerpo = SITIO.get(self.path, (404, "text/html", "no existe"))
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", f"{tipo}; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def servidor():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_extraccion_quita_scripts_y_menus() -> None:
    r = analizar_html(pagina(PARRAFO + '<a href="/x#y">x</a>'), "http://s.com/dir/")
    assert "var x" not in r.texto and "MENU" not in r.texto
    assert "agujero negro" in r.texto
    assert r.titulo == "T"
    assert r.enlaces == ["http://s.com/x"]
    assert motivo_rechazo(r.texto) is None
    assert motivo_rechazo("corto") is not None


def test_recorrido_completo_respeta_politicas(servidor: str, tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / "metadatos_araña.db")
    arana = Arana([servidor + "/"], bd, repositorio=tmp_path / "repositorio", hilos=4)
    resumen = arana.ejecutar(intervalo_progreso=999)

    con = sqlite3.connect(tmp_path / "metadatos_araña.db")
    guardadas = {u.removeprefix(servidor) for (u,) in con.execute("SELECT url FROM documentos")}
    acciones = dict(con.execute("SELECT accion, COUNT(*) FROM bitacora_recorrido GROUP BY accion"))

    assert "/privado/x" not in VISITAS  # robots.txt
    assert "/archivo.pdf" not in VISITAS  # extensión excluida
    assert "/oculta" not in VISITAS  # nofollow
    assert "/desde-noindex" in guardadas  # noindex sí deja seguir enlaces...
    assert "/noindex" not in guardadas  # ...pero no guarda la página
    assert "/corta" not in guardadas  # calidad textual
    assert "/inestable" in guardadas  # reintento tras 503
    assert len({"/dup1", "/dup2"} & guardadas) == 1  # duplicado por contenido
    assert {"/", "/a", "/a/b", "/a/b/c", "/nofollow"} <= guardadas
    assert VISITAS.count("/a") == 1  # /a#seccion es la misma URL
    assert acciones["BLOQUEADO_ROBOTS"] == 1
    assert acciones["NOINDEX"] == 1 and acciones["BAJA_DENSIDAD"] == 1
    assert acciones["DUPLICADO_CONTENIDO"] == 1

    # Cada fila tiene su .txt en disco, y no hay archivos de más.
    rutas = {r for (r,) in con.execute("SELECT ruta_relativa FROM documentos")}
    en_disco = {
        p.relative_to(tmp_path / "repositorio").as_posix()
        for p in (tmp_path / "repositorio").rglob("*.txt")
    }
    assert rutas == en_disco
    assert resumen["documentos"] == len(guardadas)
    texto = (tmp_path / "repositorio" / next(iter(rutas))).read_text()
    assert "<" not in texto and "var x" not in texto
    bd.cerrar()
