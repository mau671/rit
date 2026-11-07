"""Pruebas exhaustivas para el módulo de configuración estilo Linux (.conf)."""

from __future__ import annotations

from pathlib import Path

import pytest

from indexador.configuracion import (
    ConfiguracionIndexador,
    ConfiguracionRutas,
    FlagsNormalizacion,
    ParserConfiguracionLinux,
    parsear_booleano,
    parsear_valor_primitivo,
)


class TestPrimitivosYBooleanos:
    """Verifica la conversión de cadenas a tipos primitivos y booleanos."""

    def test_parsear_booleano_exitoso(self) -> None:
        verdaderos = ["true", "TRUE", "True", "1", "yes", "YES", "si", "sí", "on", "ON", "verdadero"]
        for v in verdaderos:
            assert parsear_booleano(v) is True

        falsos = ["false", "FALSE", "False", "0", "no", "NO", "off", "OFF", "falso"]
        for f in falsos:
            assert parsear_booleano(f) is False

    def test_parsear_booleano_invalido(self) -> None:
        with pytest.raises(ValueError, match="Valor booleano no reconocido"):
            parsear_booleano("quizas")

    def test_parsear_valor_primitivo(self) -> None:
        assert parsear_valor_primitivo("123") == 123
        assert parsear_valor_primitivo("-45") == -45
        assert parsear_valor_primitivo("3.1416") == 3.1416
        assert parsear_valor_primitivo("true") is True
        assert parsear_valor_primitivo("false") is False
        assert parsear_valor_primitivo('"texto entre comillas"') == "texto entre comillas"
        assert parsear_valor_primitivo("'otro texto'") == "otro texto"
        assert parsear_valor_primitivo("es, en, fr") == ["es", "en", "fr"]
        assert parsear_valor_primitivo("10, 20, 30") == [10, 20, 30]
        assert parsear_valor_primitivo("simple_palabra") == "simple_palabra"


class TestParserConfiguracionLinux:
    """Pruebas del parser estilo Linux (.conf) con comentarios, secciones y tipos."""

    def test_parsear_comentarios_y_secciones(self) -> None:
        texto = """
        # Comentario de inicio con almohadilla
        ; Comentario con punto y coma
        [general]
        nombre = buscador_cine
        habilitado = yes ; comentario inline con punto y coma
        reintentos = 5 # comentario inline con almohadilla

        [rutas]
        base = "/ruta con espacios/db.sqlite"
        formatos = txt, html, json
        """
        resultado = ParserConfiguracionLinux.parsear_texto(texto)

        assert "general" in resultado
        assert resultado["general"]["nombre"] == "buscador_cine"
        assert resultado["general"]["habilitado"] is True
        assert resultado["general"]["reintentos"] == 5

        assert "rutas" in resultado
        assert resultado["rutas"]["base"] == "/ruta con espacios/db.sqlite"
        assert resultado["rutas"]["formatos"] == ["txt", "html", "json"]

    def test_parsear_sintaxis_dos_puntos(self) -> None:
        texto = """
        [servicio]
        puerto: 8080
        host: 127.0.0.1
        """
        resultado = ParserConfiguracionLinux.parsear_texto(texto)
        assert resultado["servicio"]["puerto"] == 8080
        assert resultado["servicio"]["host"] == "127.0.0.1"

    def test_error_sintaxis_sin_delimitador(self) -> None:
        texto = """
        [error]
        linea_invalida_sin_igual_ni_dos_puntos
        """
        with pytest.raises(ValueError, match="Error de sintaxis"):
            ParserConfiguracionLinux.parsear_texto(texto)

    def test_archivo_no_encontrado(self) -> None:
        with pytest.raises(FileNotFoundError):
            ParserConfiguracionLinux.parsear_archivo("ruta_inexistente_12345.conf")


class TestConfiguracionIndexador:
    """Pruebas de la dataclass fuertemente tipada y serialización a .conf."""

    def test_creacion_politicas_defecto(self) -> None:
        cfg_min = ConfiguracionIndexador.crear_defecto("minimo")
        assert cfg_min.nombre_politica == "minimo"
        assert cfg_min.normalizacion.stopwords is False
        assert cfg_min.normalizacion.stemming is False
        assert cfg_min.normalizacion.expandir_sinonimos is False

        cfg_std = ConfiguracionIndexador.crear_defecto("estandar")
        assert cfg_std.nombre_politica == "estandar"
        assert cfg_std.normalizacion.stopwords is True
        assert cfg_std.normalizacion.stemming is True
        assert cfg_std.normalizacion.expandir_sinonimos is False

        cfg_adv = ConfiguracionIndexador.crear_defecto("avanzado")
        assert cfg_adv.nombre_politica == "avanzado"
        assert cfg_adv.normalizacion.stopwords is True
        assert cfg_adv.normalizacion.stemming is True
        assert cfg_adv.normalizacion.normalizar_numeros is True
        assert cfg_adv.normalizacion.normalizar_fechas is True
        assert cfg_adv.normalizacion.expandir_sinonimos is True

    def test_politica_invalida(self) -> None:
        with pytest.raises(ValueError, match="Política desconocida"):
            ConfiguracionIndexador.crear_defecto("inexistente")

    def test_serializacion_y_deserializacion_roundtrip(self, tmp_path: Path) -> None:
        cfg_orig = ConfiguracionIndexador.crear_defecto("avanzado")
        cfg_orig.rutas.indice_salida = "pruebas/test.idx"
        cfg_orig.spimi.limite_memoria_mb = 512

        archivo_conf = tmp_path / "test_roundtrip.conf"
        cfg_orig.guardar_en_archivo(archivo_conf)

        assert archivo_conf.is_file()
        cfg_leida = ConfiguracionIndexador.cargar_desde_archivo(archivo_conf)

        assert cfg_leida.nombre_politica == cfg_orig.nombre_politica
        assert cfg_leida.rutas.indice_salida == "pruebas/test.idx"
        assert cfg_leida.spimi.limite_memoria_mb == 512
        assert cfg_leida.normalizacion.expandir_sinonimos is True
        assert cfg_leida.normalizacion.idiomas_stopwords == ["es", "en"]

    def test_calcular_flags_bitmask(self) -> None:
        cfg_min = ConfiguracionIndexador.crear_defecto("minimo")
        # minimo: minusculas=True (0x01) y eliminar_puntuacion=True (0x02) -> 0x03
        flags_min = cfg_min.normalizacion.calcular_flags_bitmask()
        assert flags_min == (FlagsNormalizacion.MINUSCULAS | FlagsNormalizacion.ELIMINAR_PUNTUACION)

        cfg_adv = ConfiguracionIndexador.crear_defecto("avanzado")
        flags_adv = cfg_adv.normalizacion.calcular_flags_bitmask()
        assert (flags_adv & FlagsNormalizacion.EXPANDIR_SINONIMOS) > 0
        assert (flags_adv & FlagsNormalizacion.NORMALIZAR_FECHAS) > 0
        assert (flags_adv & FlagsNormalizacion.NORMALIZAR_NUMEROS) > 0
        assert (flags_adv & FlagsNormalizacion.STEMMING) > 0
        assert (flags_adv & FlagsNormalizacion.STOPWORDS) > 0

    def test_resolucion_rutas(self, tmp_path: Path) -> None:
        rutas = ConfiguracionRutas(
            base_datos="sub/db.sqlite",
            repositorio="sub/repo",
            indice_salida="out/indice.idx",
            directorio_spimi="spimi_bloques",
        )
        res = rutas.resolver_rutas(base_dir=tmp_path)
        assert Path(res.base_datos).is_absolute()
        assert str(tmp_path / "sub/db.sqlite") == res.base_datos


class TestArchivosConfiguracionesEjemplo:
    """Verifica que los archivos .conf creados en configuraciones/ sean válidos y coherentes."""

    @pytest.mark.parametrize("nombre", ["minimo.conf", "estandar.conf", "avanzado.conf"])
    def test_cargar_archivos_de_ejemplo(self, nombre: str) -> None:
        ruta = Path("configuraciones") / nombre
        assert ruta.is_file(), f"Falta el archivo {ruta}"
        cfg = ConfiguracionIndexador.cargar_desde_archivo(ruta)

        assert cfg.nombre_politica == nombre.split(".")[0]
        assert cfg.rutas.base_datos.endswith(".db")
        assert cfg.spimi.limite_memoria_mb > 0
