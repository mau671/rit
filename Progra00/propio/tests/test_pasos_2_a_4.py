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

from propio.arana import Arana, es_pagina_hub
from propio.bd import BaseDatos
from propio.extraccion import (
    analizar_html,
    es_desambiguacion,
    es_lista_probable,
    limpiar_texto,
    motivo_rechazo,
)

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
        <a href="/inestable">i</a> <a href="/a#seccion">a otra vez</a>
        <a href="/wiki/Portal:Cine">hub</a>"""
        ),
    ),
    "/wiki/Portal:Cine": (
        200,
        "text/html",
        pagina(
            PARRAFO.replace("detalle", "detalle Hub")
            + '<a href="/desde-hub">dh</a>'
        ),
    ),
    "/desde-hub": (200, "text/html", pagina(PARRAFO.replace("detalle", "detalle DH"))),
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
    assert "/desde-hub" in guardadas  # páginas hub sí dejan seguir enlaces...
    assert "/wiki/Portal:Cine" not in guardadas  # ...pero no guardan documento
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


def test_motivo_rechazo_palabras_desambiguacion_y_listas() -> None:
    # 1. Desambiguación
    assert es_desambiguacion("texto", url="https://es.wikipedia.org/wiki/X_(desambiguación)")
    assert motivo_rechazo("Página normal con suficiente texto", url="https://es.wikipedia.org/wiki/X_(desambiguación)") == "desambiguacion"
    assert motivo_rechazo("Página normal con suficiente texto", titulo="X (disambiguation)") == "desambiguacion"
    assert motivo_rechazo("X puede referirse a:\n1. Primera acepción\n2. Segunda acepción") == "desambiguacion"

    # 2. Conteo de palabras en texto estándar
    texto_largo_pocas_palabras = "palabra " * 40 + "x" * 500  # > 500 caracteres pero < 100 palabras
    motivo = motivo_rechazo(texto_largo_pocas_palabras)
    assert motivo is not None and "palabras" in motivo

    # 3. Listas
    lista_corta = "1. Uno\n2. Dos\n3. Tres"
    assert motivo_rechazo(lista_corta, es_lista=True) is not None  # < 300 caracteres

    lista_valida = (
        "1. Casablanca - Drama clásico dirigido por Michael Curtiz con Humphrey Bogart y Bergman.\n"
        "2. Citizen Kane - Obra maestra de Orson Welles sobre el magnate de medios Charles Foster Kane.\n"
        "3. Vertigo - Suspense psicológico de Alfred Hitchcock ambientado en San Francisco California.\n"
        "4. Metropolis - Película de ciencia ficción expresionista alemana dirigida por Fritz Lang en 1927.\n"
        "5. Seven Samurai - Épico drama japonés de época histórica dirigido por Akira Kurosawa en Japón."
    )
    assert es_lista_probable(lista_valida, titulo="Movies", url="https://example.com/movies")
    assert motivo_rechazo(lista_valida, es_lista=True) is None


def test_limpiar_texto_quita_artefacto_apariencia() -> None:
    sucio = "Apariencia\n\nApariencia\n\nTexto principal de la película con contenido útil."
    limpio = limpiar_texto(sucio)
    assert not limpio.startswith("Apariencia")
    assert limpio.startswith("Texto principal")


def test_url_aceptable_excluye_urls_administrativas_y_query_actions(tmp_path: Path) -> None:
    bd = BaseDatos(tmp_path / "test.db")
    arana = Arana(
        semillas=["https://es.wikipedia.org/wiki/Portal:Cine", "https://tvtropes.org/pmwiki/pmwiki.php/Main/Film"],
        base_datos=bd,
    )

    rechazadas = [
        "https://es.wikipedia.org/wiki/Especial:CambiosRecientes",
        "https://es.wikipedia.org/wiki/Special:WhatLinksHere",
        "https://es.wikipedia.org/wiki/Discusi%C3%B3n:Anime",
        "https://es.wikipedia.org/wiki/Talk:Film",
        "https://es.wikipedia.org/wiki/Usuario:Mau",
        "https://es.wikipedia.org/wiki/User:Mau",
        "https://es.wikipedia.org/wiki/Plantilla:Infobox",
        "https://es.wikipedia.org/wiki/Template:Infobox",
        "https://es.wikipedia.org/wiki/Ayuda:Ayuda",
        "https://es.wikipedia.org/wiki/Wikiproyecto:Cine",
        "https://es.wikipedia.org/wiki/Archivo:Foto.jpg",
        "https://es.wikipedia.org/wiki/MediaWiki:Common.css",
        "https://es.wikipedia.org/w/index.php?title=Anime&action=edit",
        "https://es.wikipedia.org/w/index.php?title=Anime&diff=1&oldid=2",
        "https://tvtropes.org/pmwiki/pmwiki.php/Discussion/Film",
        "https://tvtropes.org/pmwiki/pmwiki.php/Administrivia/Film",
        "https://tvtropes.org/pmwiki/pmwiki.php/SugarWiki/Film",
        "https://tvtropes.org/pmwiki/pmwiki.php/YMMV/Film",
    ]
    for url in rechazadas:
        assert not arana.url_aceptable(url), f"Se debió rechazar {url}"

    permitidas = [
        "https://es.wikipedia.org/wiki/Portal:Cine",
        "https://es.wikipedia.org/wiki/Categor%C3%ADa:Series_de_anime",
        "https://es.wikipedia.org/wiki/Neon_Genesis_Evangelion",
        "https://tvtropes.org/pmwiki/pmwiki.php/Main/Film",
    ]
    for url in permitidas:
        assert arana.url_aceptable(url), f"Se debió permitir {url}"
    bd.cerrar()


def test_es_pagina_hub_y_comportamiento_arana(tmp_path: Path) -> None:
    assert es_pagina_hub("https://es.wikipedia.org/wiki/Categor%C3%ADa:Series_de_anime")
    assert es_pagina_hub("https://es.wikipedia.org/wiki/Category:Anime")
    assert es_pagina_hub("https://es.wikipedia.org/wiki/Portal:Cine")
    assert es_pagina_hub("https://es.wikipedia.org/wiki/Matrix_(desambiguaci%C3%B3n)")
    assert es_pagina_hub("https://es.wikipedia.org/wiki/Alien", titulo="Alien (desambiguación)")
    assert not es_pagina_hub("https://es.wikipedia.org/wiki/Neon_Genesis_Evangelion", titulo="Evangelion")
