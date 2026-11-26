"""Pruebas del modelo de ponderación compartido por indexador y buscador."""

from __future__ import annotations

import math

import pytest

from indexador.modelo import (
    calcular_idf,
    calcular_peso_tf,
    similitud,
    validar_modelo,
)


class TestPesoTF:
    def test_logaritmico(self) -> None:
        assert calcular_peso_tf(1, "logaritmico") == 1.0
        assert calcular_peso_tf(10, "logaritmico") == pytest.approx(1.0 + math.log(10))
        assert calcular_peso_tf(4, "sublineal") == pytest.approx(1.0 + math.log(4))

    def test_binario_y_natural(self) -> None:
        assert calcular_peso_tf(7, "binario") == 1.0
        assert calcular_peso_tf(7, "natural") == 7.0

    def test_tf_cero_o_negativo(self) -> None:
        assert calcular_peso_tf(0) == 0.0
        assert calcular_peso_tf(-3) == 0.0

    def test_esquema_desconocido(self) -> None:
        with pytest.raises(ValueError, match="Esquema TF desconocido"):
            calcular_peso_tf(1, "cuadratico")


class TestIDF:
    def test_estandar_con_suavizado(self) -> None:
        # Fórmula del índice: ln(N/df + 1)
        assert calcular_idf(500, 1) == pytest.approx(math.log(501))
        assert calcular_idf(500, 500) == pytest.approx(math.log(2))

    def test_estandar_sin_suavizado(self) -> None:
        assert calcular_idf(500, 5, suavizado=False) == pytest.approx(math.log(100))

    def test_probabilistico_se_trunca_en_cero(self) -> None:
        assert calcular_idf(10, 12, "probabilistico") == 0.0
        assert calcular_idf(10, 5, "probabilistico") > 0.0

    def test_ninguno(self) -> None:
        assert calcular_idf(10, 3, "ninguno") == 1.0

    def test_esquema_desconocido(self) -> None:
        with pytest.raises(ValueError, match="Esquema IDF desconocido"):
            calcular_idf(10, 3, "bogus")


class TestSimilitud:
    def test_coseno_normaliza_por_normas(self) -> None:
        assert similitud(10.0, 2.0, 5.0) == pytest.approx(1.0)
        assert similitud(10.0, 2.0, 10.0) == pytest.approx(0.5)

    def test_norma_documento_cero_no_divide(self) -> None:
        # Un documento con norma 0 no puede producir una excepción.
        assert similitud(5.0, 2.0, 0.0) == pytest.approx(2.5)

    def test_sin_coseno_devuelve_producto_punto(self) -> None:
        assert similitud(5.0, 2.0, 3.0, usar_coseno=False) == 5.0


def test_validar_modelo() -> None:
    validar_modelo("logaritmico", "estandar")
    with pytest.raises(ValueError):
        validar_modelo("logaritmico", "inventado")
