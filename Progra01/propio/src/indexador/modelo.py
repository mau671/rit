"""Ponderación TF/IDF y similitud, compartidas por el indexador y el buscador.

El indexador almacena en cada posting el peso ``tf * idf`` y en cada documento la
norma euclidiana ``‖d‖``. El buscador debe reproducir exactamente el mismo
esquema de ponderación, de modo que estas funciones son el único lugar donde se
definen las fórmulas y ambos componentes las reutilizan.
"""

from __future__ import annotations

import math
from typing import Final

ESQUEMA_TF_DEFECTO: Final[str] = "logaritmico"
ESQUEMA_IDF_DEFECTO: Final[str] = "estandar"

ESQUEMAS_TF: Final[frozenset[str]] = frozenset(
    {"logaritmico", "sublineal", "binario", "natural"}
)
ESQUEMAS_IDF: Final[frozenset[str]] = frozenset(
    {"estandar", "probabilistico", "ninguno"}
)


def calcular_peso_tf(tf: int, esquema_tf: str = ESQUEMA_TF_DEFECTO) -> float:
    """Calcula el peso por frecuencia de término.

    - ``logaritmico`` (o ``sublineal``): ``1 + ln(tf)``.
    - ``binario``: ``1`` si el término aparece.
    - ``natural``: ``tf`` crudo.
    """
    if tf <= 0:
        return 0.0
    if esquema_tf in {"logaritmico", "sublineal"}:
        return 1.0 + math.log(tf)
    if esquema_tf == "binario":
        return 1.0
    if esquema_tf == "natural":
        return float(tf)
    raise ValueError(
        f"Esquema TF desconocido: {esquema_tf!r}. Opciones: {sorted(ESQUEMAS_TF)}"
    )


def calcular_idf(
    n_docs: int,
    df: int,
    esquema_idf: str = ESQUEMA_IDF_DEFECTO,
    suavizado: bool = True,
) -> float:
    """Calcula el peso inverso de frecuencia de documento.

    - ``estandar``: ``ln(N/df)``, con ``+1`` dentro del logaritmo si hay suavizado.
    - ``probabilistico``: ``ln((N - df)/df)``, truncado en cero.
    - ``ninguno``: ``1``.
    """
    if df <= 0 or n_docs <= 0:
        return 0.0

    if esquema_idf == "estandar":
        return math.log(n_docs / df + 1.0) if suavizado else math.log(n_docs / df)

    if esquema_idf == "probabilistico":
        ratio = (n_docs - df) / df
        if suavizado:
            return float(max(0.0, math.log(ratio + 1.0)))
        if ratio <= 0.0:
            return 0.0
        return float(max(0.0, math.log(ratio)))

    if esquema_idf == "ninguno":
        return 1.0

    raise ValueError(
        f"Esquema IDF desconocido: {esquema_idf!r}. Opciones: {sorted(ESQUEMAS_IDF)}"
    )


def similitud(
    producto_punto: float,
    norma_consulta: float,
    norma_documento: float,
    usar_coseno: bool = True,
) -> float:
    """Convierte el producto punto en similitud coseno o lo deja crudo."""
    if not usar_coseno:
        return producto_punto
    denominador = norma_consulta * (norma_documento or 1.0)
    if denominador <= 0.0:
        return 0.0
    return producto_punto / denominador


def validar_modelo(esquema_tf: str, esquema_idf: str) -> None:
    """Valida los nombres de esquema antes de indexar, para fallar temprano."""
    if esquema_tf not in ESQUEMAS_TF:
        raise ValueError(
            f"Esquema TF desconocido: {esquema_tf!r}. Opciones: {sorted(ESQUEMAS_TF)}"
        )
    if esquema_idf not in ESQUEMAS_IDF:
        raise ValueError(
            f"Esquema IDF desconocido: {esquema_idf!r}. Opciones: {sorted(ESQUEMAS_IDF)}"
        )
