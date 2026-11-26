"""Pruebas de compatibilidad del formato, los flags de normalización y la integridad."""

from __future__ import annotations

from pathlib import Path

import pytest

from indexador.configuracion import (
    ConfiguracionIndexador,
    ConfiguracionNormalizacion,
    FlagsNormalizacion,
)
from indexador.escritor_binario import EscritorIndiceBinario
from indexador.formato_binario import (
    EncabezadoIndice,
    EntradaDocumento,
    EntradaTermino,
    Posting,
    hash_url_a_bytes,
)
from indexador.lector_binario import LectorIndiceBinario
from indexador.normalizador import Normalizador


class TestHashUrl:
    def test_hexadecimal_de_64_caracteres(self) -> None:
        hex_url = "df3c0abbabe9ad1e5aa20beb3ef15f6359074816c47175146dfaa820acce46b6"
        assert hash_url_a_bytes(hex_url) == bytes.fromhex(hex_url)

    def test_idempotente_sobre_bytes_crudos(self) -> None:
        crudo = bytes(range(32))
        assert hash_url_a_bytes(crudo) == crudo
        assert hash_url_a_bytes(hash_url_a_bytes(crudo)) == crudo

    def test_cadena_corta_se_rellena(self) -> None:
        resultado = hash_url_a_bytes("corto")
        assert len(resultado) == 32
        assert resultado.startswith(b"corto")
        assert resultado.endswith(b"\x00" * 27)

    def test_cadena_larga_se_trunca(self) -> None:
        assert len(hash_url_a_bytes("x" * 100)) == 32


class TestEncabezadoModelo:
    def test_modelo_por_defecto_en_bytes_reservados(self) -> None:
        enc = EncabezadoIndice()
        assert enc.reservado == b"\x00" * 12
        assert enc.esquema_tf == "logaritmico"
        assert enc.esquema_idf == "estandar"
        assert enc.normalizacion_coseno is True
        assert enc.suavizado_idf is True

    def test_ida_y_vuelta_del_modelo(self) -> None:
        enc = EncabezadoIndice()
        enc.configurar_modelo(
            "natural", "probabilistico", normalizacion_coseno=False, suavizado_idf=False
        )
        recuperado = EncabezadoIndice.desempaquetar(enc.empaquetar())
        assert recuperado.esquema_tf == "natural"
        assert recuperado.esquema_idf == "probabilistico"
        assert recuperado.normalizacion_coseno is False
        assert recuperado.suavizado_idf is False
        assert len(recuperado.empaquetar()) == 64

    def test_esquema_invalido_no_se_guarda(self) -> None:
        with pytest.raises(ValueError):
            EncabezadoIndice().configurar_modelo("cubico", "estandar")


class TestFlagsANormalizacion:
    @pytest.mark.parametrize("politica", ["minimo", "estandar", "avanzado"])
    def test_ida_y_vuelta_de_cada_politica(self, politica: str) -> None:
        config = ConfiguracionIndexador.crear_defecto(politica).normalizacion
        recuperada = ConfiguracionNormalizacion.desde_flags(
            config.calcular_flags_bitmask()
        )

        assert recuperada.minusculas == config.minusculas
        assert recuperada.eliminar_puntuacion == config.eliminar_puntuacion
        assert recuperada.remover_acentos == config.remover_acentos
        assert recuperada.stopwords == config.stopwords
        assert recuperada.stemming == config.stemming
        assert recuperada.normalizar_numeros == config.normalizar_numeros
        assert recuperada.normalizar_fechas == config.normalizar_fechas
        assert recuperada.expandir_sinonimos == config.expandir_sinonimos

    def test_consulta_sin_stemming_no_reduce_la_palabra(self) -> None:
        """La consulta de la política mínima no debe lematizar como la estándar."""
        normalizacion = ConfiguracionNormalizacion.desde_flags(
            ConfiguracionIndexador.crear_defecto(
                "minimo"
            ).normalizacion.calcular_flags_bitmask()
        )
        tokens = Normalizador(normalizacion).normalizar_texto("peliculas")
        assert tokens == ["peliculas"]


class TestIntegridad:
    def _escribir(self, ruta: Path) -> None:
        with EscritorIndiceBinario(ruta) as escritor:
            escritor.flags_normalizacion = FlagsNormalizacion.MINUSCULAS
            escritor.modelo = ("logaritmico", "estandar", True, True)
            escritor.escribir_documentos(
                [
                    EntradaDocumento(0, "a" * 64, 3, 5.0),
                    EntradaDocumento(1, "b" * 64, 2, 4.0),
                ]
            )
            terminos = [
                ("alfa", 1, 1.0, [Posting(0, 2, 3.0)]),
                ("beta", 2, 1.0, [Posting(0, 1, 3.0), Posting(1, 2, 4.0)]),
            ]
            escritor.escribir_diccionario_y_postings(terminos)
            escritor.finalizar(flags_normalizacion=FlagsNormalizacion.MINUSCULAS)

    def test_indice_consistente_no_reporta_problemas(self, tmp_path: Path) -> None:
        ruta = tmp_path / "ok.idx"
        self._escribir(ruta)
        with LectorIndiceBinario(ruta) as lector:
            assert lector.verificar_estructura() == []

    def test_indice_corrupto_reporta_el_desfase(self, tmp_path: Path) -> None:
        ruta = tmp_path / "malo.idx"
        self._escribir(ruta)

        # Se altera el offset de postings del encabezado para simular un archivo
        # dañado: el lector debe detectar que los bloques ya no encajan.
        datos = bytearray(ruta.read_bytes())
        datos[44:52] = (1234567).to_bytes(8, "little")
        ruta.write_bytes(datos)

        with LectorIndiceBinario(ruta) as lector:
            problemas = lector.verificar_estructura()
        assert problemas
        assert any("postings" in problema for problema in problemas)

    def test_normalizacion_se_reconstruye_del_archivo(self, tmp_path: Path) -> None:
        ruta = tmp_path / "flags.idx"
        self._escribir(ruta)
        with LectorIndiceBinario(ruta) as lector:
            normalizacion = lector.normalizacion
        assert normalizacion.minusculas is True
        assert normalizacion.stopwords is False
        assert normalizacion.stemming is False

    def test_diccionario_duplicado_se_rechaza(self, tmp_path: Path) -> None:
        ruta = tmp_path / "dup.idx"
        with (
            pytest.raises(ValueError, match="duplicados"),
            EscritorIndiceBinario(ruta) as escritor,
        ):
            escritor.escribir_documentos([EntradaDocumento(0, "a" * 64, 1, 1.0)])
            escritor.escribir_diccionario_y_postings(
                [
                    ("alfa", 1, 1.0, [Posting(0, 1, 1.0)]),
                    ("alfa", 1, 1.0, [Posting(0, 1, 1.0)]),
                ]
            )

    def test_terminos_con_prefijo(self, tmp_path: Path) -> None:
        ruta = tmp_path / "pref.idx"
        with EscritorIndiceBinario(ruta) as escritor:
            escritor.escribir_documentos([EntradaDocumento(0, "a" * 64, 3, 3.0)])
            escritor.escribir_diccionario_y_postings(
                [
                    ("guion", 1, 1.0, [Posting(0, 1, 1.0)]),
                    ("guionista", 1, 1.0, [Posting(0, 1, 1.0)]),
                    ("pelicula", 1, 1.0, [Posting(0, 1, 1.0)]),
                ]
            )
            escritor.finalizar()

        with LectorIndiceBinario(ruta) as lector:
            encontrados = [t.termino for t in lector.terminos_con_prefijo("guion")]
            assert encontrados == ["guion", "guionista"]
            assert lector.terminos_con_prefijo("") == []
            assert [
                t.termino for t in lector.terminos_con_prefijo("guion", limite=1)
            ] == ["guion"]


def test_entrada_termino_ida_y_vuelta() -> None:
    entrada = EntradaTermino("pelicula", 12, 3.5, 4096, 144)
    recuperada, consumidos = EntradaTermino.desempaquetar_desde_bytes(
        entrada.empaquetar()
    )
    assert recuperada == entrada
    assert consumidos == 2 + len("pelicula") + 20
