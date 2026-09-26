"""Pruebas puras de utilidades y extracción, sin acceso a la red."""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from arañador.bibliotecas.extraccion import EstadoExtraccion, extraer_texto
from arañador.implementacion.utilidades import (
    crear_ruta_particionada,
    crear_slug,
    hash_contenido,
    hash_url,
    normalizar_url,
    obtener_dominio_registrable,
    obtener_host_origen,
    obtener_raiz_datos,
    resolver_ruta_datos,
)


def test_normaliza_url_quita_fragmento_y_tracking_conserva_query() -> None:
    normalizada = normalizar_url(
        "HTTPS://WWW.Example.COM:443/a/../b/á?utm_source=boletin&z=2&a=1#capitulo"
    )

    assert normalizada == "https://www.example.com/b/%C3%A1?a=1&z=2"
    assert hash_url(normalizada) == hash_url(
        "https://www.example.com/b/%C3%A1?a=1&z=2#otro-fragmento"
    )


def test_normaliza_idna_y_dominio_registrable_sin_red() -> None:
    normalizada = normalizar_url("https://WWW.例え.テスト/pelicula/á")
    assert "xn--" in normalizada
    assert normalizada.endswith("/%C3%A1")
    assert obtener_dominio_registrable("https://sub.example.co.uk/ruta") == "example.co.uk"
    assert obtener_dominio_registrable("https://192.0.2.10/pelicula") == "192.0.2.10"
    assert obtener_host_origen("https://www.example.org/pelicula") == "example.org"
    assert obtener_host_origen("https://es.wikipedia.org/wiki/Cine") == "es.wikipedia.org"


def test_dominio_registrable_no_consulta_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fallo_de_red(*args: object, **kwargs: object) -> None:
        raise AssertionError("la extracción de dominio no debe consultar DNS")

    monkeypatch.setattr(socket, "getaddrinfo", fallo_de_red)
    assert obtener_dominio_registrable("https://www.example.org/pelicula") == "example.org"


def test_raiz_datos_usa_entorno_y_no_ubicacion_del_paquete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las CLIs instaladas comparten datos con el checkout que las invoca."""

    raiz = tmp_path / "datos-compartidos"
    monkeypatch.setenv("RAIZ_DATOS", str(raiz))

    assert obtener_raiz_datos() == raiz.resolve()
    assert resolver_ruta_datos("almacenamiento", "repositorio") == (
        raiz.resolve() / "almacenamiento" / "repositorio"
    )


def test_slug_y_particion_no_admiten_travesia(tmp_path: Path) -> None:
    digest = "a" * 64
    assert crear_slug("  ¡Hola, señor audiovisual!  ") == "hola-senor-audiovisual"

    ruta = crear_ruta_particionada(
        tmp_path / "repositorio",
        "../dominio/secreto",
        "categoría/xxx",
        digest,
    )
    partes = ruta.parts

    assert ruta.name == f"{digest}.txt"
    assert ".." not in partes
    assert "secreto" not in partes
    assert partes[-2:] == ("aa", f"{digest}.txt")
    ruta_simple = crear_ruta_particionada(
        tmp_path / "repositorio-simple", "example.com", "guiones", digest
    )
    assert ruta_simple.parent.parent.parent.name == "example.com"


def test_hash_contenido_normaliza_nul_unicode_y_saltos() -> None:
    primero = hash_contenido("Cafe\u0301\x00\n  hola\r\nmundo")
    segundo = hash_contenido("Café\nhola\nmundo")

    assert primero == segundo
    assert len(primero) == 64
    assert hash_contenido(b"Cafe\xcc\x81\nhola") == hash_contenido("Café\nhola")


def test_extractor_sintetico_normaliza_y_mide_texto() -> None:
    parrafo = "Una historia audiovisual con diálogo y análisis. " * 20
    html = f"<!doctype html><html lang='es'><head><title>No debe primacy</title></head><body><article><h1>Película</h1><p>{parrafo}</p></article></body></html>"

    resultado = extraer_texto(
        html.encode("utf-8"), url="https://example.test/pelicula", titulo="Película"
    )

    assert resultado.estado is EstadoExtraccion.EXITO
    assert resultado.caracteres == len(resultado.texto)
    assert resultado.palabras > 0
    assert resultado.signos > 0
    assert resultado.densidad > 0
    assert "\x00" not in resultado.texto
    assert resultado.titulo == "Película"
    assert resultado.properties["estado"] == EstadoExtraccion.EXITO.value


def test_extractor_no_descarta_contenido_por_idioma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un ``lang`` incorrecto no debe convertir una página válida en fallo."""

    import arañador.bibliotecas.extraccion as modulo

    monkeypatch.setattr(
        modulo, "trafilatura", SimpleNamespace(extract=lambda *args, **kwargs: None)
    )
    html = (
        "<html lang='en'><body><article><p>Contenido válido en español.</p></article></body></html>"
    )

    resultado = extraer_texto(html, idioma="en", umbral_caracteres=1)

    assert resultado.estado is EstadoExtraccion.EXITO
    assert "Contenido válido" in resultado.texto


def test_extractor_distingue_html_vacio_y_texto_corto() -> None:
    vacio = extraer_texto(b"")
    assert vacio.estado is EstadoExtraccion.FALLO
    assert vacio.motivo == "html_vacio"

    sin_contenido = extraer_texto(
        "<html><head><title>Sólo metadatos</title></head><body></body></html>"
    )
    assert sin_contenido.estado is EstadoExtraccion.FALLO
    assert sin_contenido.motivo == "html_sin_texto"

    corto = extraer_texto(
        "<html><body><article><p>Texto breve.</p></article></body></html>",
        umbral_caracteres=100,
    )
    assert corto.estado is EstadoExtraccion.TEXTO_CORTO
    assert corto.texto == "Texto breve."


def test_extractor_pasa_opciones_de_precision_y_filtros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import arañador.bibliotecas.extraccion as modulo

    llamadas: list[dict[str, object]] = []

    def extracción_falsa(html: str, **opciones: object) -> str:
        llamadas.append(opciones)
        return "contenido principal"

    monkeypatch.setattr(
        modulo,
        "trafilatura",
        SimpleNamespace(extract=extracción_falsa),
    )
    resultado = extraer_texto(
        "<html><body><!-- comentario --><table><tr><td>tabla</td></tr></table><p>principal</p></body></html>",
        url="https://example.test",
        titulo="Título",
        umbral_caracteres=1,
    )

    assert resultado.estado is EstadoExtraccion.EXITO
    assert resultado.texto == "contenido principal"
    assert llamadas[0]["favor_precision"] is True
    assert llamadas[0]["include_comments"] is False
    assert llamadas[0]["include_tables"] is False
    assert llamadas[0]["url"] == "https://example.test"


def test_slug_rechaza_argumentos_invalidos() -> None:
    with pytest.raises(TypeError):
        crear_slug(cast(Any, 123))
    with pytest.raises(ValueError):
        crear_slug("texto", max_length=0)
