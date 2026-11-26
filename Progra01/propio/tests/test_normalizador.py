"""Pruebas exhaustivas para el motor de normalización de texto y tesauro audiovisual."""

from __future__ import annotations

from pathlib import Path

import pytest

from indexador.configuracion import ConfiguracionIndexador, ConfiguracionNormalizacion
from indexador.normalizador import (
    STOPWORDS_ESPANOL,
    STOPWORDS_INGLES,
    Normalizador,
    limpiar_texto_crudo,
    remover_diacriticos,
)


class TestLimpiezaYCaracteresEspeciales:
    """Verifica el manejo de caracteres raros, ligaduras y comillas tipográficas."""

    def test_remover_diacriticos(self) -> None:
        texto = "película, acción, canción, detrás, lingüística"
        esperado = "pelicula, accion, cancion, detras, linguistica"
        assert remover_diacriticos(texto) == esperado

    def test_limpiar_texto_crudo_comillas_y_guiones(self) -> None:
        texto = "“Inception” ‘director’s cut’ — starring DiCaprio – rated 9/10"
        limpio = limpiar_texto_crudo(texto)
        assert '"Inception"' in limpio
        assert "'director's cut'" in limpio
        assert " - " in limpio

    def test_unicode_nfkc_ligaduras(self) -> None:
        # fi ligadura: \ufb01 ("ﬁ") -> "fi"
        texto = "ﬁlmography and ﬂow"
        limpio = limpiar_texto_crudo(texto)
        assert "filmography" in limpio
        assert "flow" in limpio


class TestTokenizador:
    """Pruebas de tokenización con guiones internos, apóstrofes y contracciones."""

    def test_palabras_compuestas_con_guion(self) -> None:
        cfg = ConfiguracionNormalizacion(
            minusculas=True,
            stopwords=False,
            stemming=False,
        )
        norm = Normalizador(cfg)
        tokens = norm.normalizar_texto("Spider-Man en una película sci-fi post-apocalíptica")
        assert "spider-man" in tokens
        assert "sci-fi" in tokens
        assert "post-apocalíptica" in tokens

    def test_contracciones_en_ingles(self) -> None:
        cfg = ConfiguracionNormalizacion(
            minusculas=True,
            stopwords=False,
            stemming=False,
        )
        norm = Normalizador(cfg)
        tokens = norm.normalizar_texto("It's Conan O'Brien's show, isn't it?")
        assert "it's" in tokens
        assert "o'brien's" in tokens
        assert "isn't" in tokens


class TestStopwords:
    """Pruebas de filtrado de palabras vacías completas en español e inglés."""

    def test_presencia_stopwords_espanol(self) -> None:
        assert "el" in STOPWORDS_ESPANOL
        assert "mediante" in STOPWORDS_ESPANOL
        assert "fueron" in STOPWORDS_ESPANOL
        assert "también" in STOPWORDS_ESPANOL

    def test_presencia_stopwords_ingles(self) -> None:
        assert "the" in STOPWORDS_INGLES
        assert "throughout" in STOPWORDS_INGLES
        assert "were" in STOPWORDS_INGLES
        assert "themselves" in STOPWORDS_INGLES

    def test_filtrado_activo_en_espanol(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=True,
            idiomas_stopwords=["es"],
            stemming=False,
        )
        norm = Normalizador(cfg)
        tokens = norm.normalizar_texto("La historia de los directores en el cine durante el rodaje")
        assert "la" not in tokens
        assert "de" not in tokens
        assert "los" not in tokens
        assert "en" not in tokens
        assert "el" not in tokens
        assert "durante" not in tokens
        assert "historia" in tokens
        assert "directores" in tokens
        assert "cine" in tokens
        assert "rodaje" in tokens


class TestDeteccionIdiomaYStemming:
    """Pruebas de detección heurística de idioma y reducción Snowball."""

    def test_deteccion_idioma_espanol(self) -> None:
        norm = Normalizador()
        texto_es = "Esta es una película maravillosa sobre la vida de un gran director español."
        assert norm.detectar_idioma(texto_es) == "es"

    def test_deteccion_idioma_ingles(self) -> None:
        norm = Normalizador()
        texto_en = "This is a wonderful movie about the life of a great American director."
        assert norm.detectar_idioma(texto_en) == "en"

    def test_stemming_espanol_convergencia(self) -> None:
        cfg = ConfiguracionNormalizacion(
            idioma="es",
            stopwords=False,
            stemming=True,
        )
        norm = Normalizador(cfg)
        # 'película' y 'películas' deben converger
        t1 = norm.normalizar_texto("película")[0]
        t2 = norm.normalizar_texto("películas")[0]
        assert t1 == t2 == "pelicul"

    def test_stemming_ingles_convergencia(self) -> None:
        cfg = ConfiguracionNormalizacion(
            idioma="en",
            stopwords=False,
            stemming=True,
        )
        norm = Normalizador(cfg)
        # 'movies' and 'movie' deben converger
        t1 = norm.normalizar_texto("movie")[0]
        t2 = norm.normalizar_texto("movies")[0]
        assert t1 == t2 == "movi"


class TestNormalizacionFechasYNumeros:
    """Verifica la detección y marcado semántico de años, fechas y números."""

    def test_fechas_completas_y_anios(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=False,
            stemming=False,
            normalizar_fechas=True,
        )
        norm = Normalizador(cfg)
        texto = "Estrenada el 25/12/1977 o 1977-10-15 durante el año 1994 y 2024."
        tokens = norm.normalizar_texto(texto)
        assert "<FECHA>" in tokens
        assert "<ANIO_1994>" in tokens
        assert "<ANIO_2024>" in tokens

    def test_modo_numeros_token(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=False,
            stemming=False,
            normalizar_numeros=True,
            modo_numeros="token",
        )
        norm = Normalizador(cfg)
        tokens = norm.normalizar_texto("Presupuesto de 150 millones y duración de 120.5 minutos")
        assert "<NUM>" in tokens
        assert "150" not in tokens
        assert "120.5" not in tokens

    def test_modo_numeros_descartar(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=False,
            stemming=False,
            normalizar_numeros=True,
            modo_numeros="descartar",
        )
        norm = Normalizador(cfg)
        tokens = norm.normalizar_texto("Sala 4 con 300 espectadores")
        assert "<NUM>" not in tokens
        assert "4" not in tokens
        assert "300" not in tokens
        assert "sala" in tokens
        assert "espectadores" in tokens


class TestTesauroYSinonimosAudiovisuales:
    """Pruebas de expansión y normalización semántica en el dominio cinematográfico."""

    def test_mapeo_sinonimos_audiovisuales(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=False,
            stemming=False,
            expandir_sinonimos=True,
        )
        norm = Normalizador(cfg)

        tokens = norm.normalizar_texto("película filme movie motion_picture cinema")
        # Todos deben mapear al término canónico 'film'
        assert set(tokens) == {"film"}

        tokens_dir = norm.normalizar_texto("director realizador cineasta filmmaker")
        assert set(tokens_dir) == {"realizador"}

        tokens_script = norm.normalizar_texto("guion libreto screenplay script")
        assert set(tokens_script) == {"screenplay"}

    def test_tesauro_combinado_con_stemming(self) -> None:
        cfg = ConfiguracionNormalizacion(
            stopwords=True,
            stemming=True,
            expandir_sinonimos=True,
        )
        norm = Normalizador(cfg)
        # 'El director de la película' vs 'The film director'
        tokens_es = norm.normalizar_texto("El director de la película")
        tokens_en = norm.normalizar_texto("The film director")
        # Ambos deben incluir la raíz de film y realizador
        assert "film" in tokens_es
        assert "film" in tokens_en
        assert "realiz" in tokens_es
        assert "realiz" in tokens_en


class TestColeccionReal:
    """Pruebas con fragmentos reales de archivos de Progra00/almacenamiento/repositorio/."""

    def test_fragmento_filmaffinity_real(self) -> None:
        ruta_filmaffinity = Path(
            "../Progra00/almacenamiento/repositorio/filmaffinity.com/documentos/76/761cc92d43c07f4bb06cd51bd4f607ed88cceb1fa30d33b22859d483e63d4808.txt"
        )
        if not ruta_filmaffinity.is_file():
            pytest.skip("Archivo de muestra de FilmAffinity no disponible")

        contenido = ruta_filmaffinity.read_text(encoding="utf-8", errors="replace")

        # Probar con las 3 políticas
        for pol in ["minimo", "estandar", "avanzado"]:
            cfg = ConfiguracionIndexador.crear_defecto(pol)
            norm = Normalizador(cfg.normalizacion)
            tokens = norm.normalizar_texto(contenido)
            assert len(tokens) > 50, f"Debe extraer tokens de FilmAffinity en política {pol}"
            # Asegurar que ningún token contenga caracteres de control o saltos de línea
            for t in tokens[:100]:
                assert "\n" not in t
                assert "\r" not in t
                assert "\t" not in t

    def test_fragmento_imsdb_guion_real(self) -> None:
        ruta_imsdb = Path(
            "../Progra00/almacenamiento/repositorio/imsdb.com/documentos/4f/4f33a1e015a5df181e5b9e2730d8730783e651cf4f7b8cad4aee8b5770c0f489.txt"
        )
        if not ruta_imsdb.is_file():
            pytest.skip("Archivo de muestra de IMSDb no disponible")

        contenido = ruta_imsdb.read_text(encoding="utf-8", errors="replace")

        for pol in ["minimo", "estandar", "avanzado"]:
            cfg = ConfiguracionIndexador.crear_defecto(pol)
            norm = Normalizador(cfg.normalizacion)
            tokens = norm.normalizar_texto(contenido[:10000])
            assert len(tokens) > 100, f"Debe extraer tokens de IMSDb en política {pol}"

    def test_generador_perezoso_iterar_tokens(self) -> None:
        norm = Normalizador()
        texto = "Una simple prueba de generador de tokens."
        gen = norm.iterar_tokens(texto)
        assert hasattr(gen, "__iter__")
        tokens = list(gen)
        assert len(tokens) > 0
        assert tokens == norm.normalizar_texto(texto)
