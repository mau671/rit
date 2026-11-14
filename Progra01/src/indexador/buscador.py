"""Consulta del índice invertido con el modelo vectorial y similitud coseno.

Reúne en un solo lugar la lógica que antes vivía dentro de la línea de comandos:
tokeniza la consulta con **la misma normalización con la que se construyó el
índice**, pondera los términos, acumula productos punto por documento y devuelve
los resultados ordenados. La CLI, las pruebas y cualquier componente posterior
(por ejemplo una interfaz web) reutilizan esta clase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from .formato_binario import EntradaTermino
from .lector_binario import LectorIndiceBinario
from .modelo import calcular_peso_tf, similitud
from .normalizador import Normalizador


@dataclass(slots=True)
class ResultadoBusqueda:
    """Un documento recuperado para una consulta."""

    doc_id: int
    similitud: float
    hash_url: str
    num_tokens: int


@dataclass(slots=True)
class RespuestaBusqueda:
    """Resultado completo de una consulta, con trazabilidad de lo que ocurrió."""

    consulta: str
    tokens: list[str]
    resultados: list[ResultadoBusqueda] = field(default_factory=list)
    terminos_en_indice: list[str] = field(default_factory=list)
    terminos_ausentes: list[str] = field(default_factory=list)
    sugerencias: list[str] = field(default_factory=list)


class BuscadorVectorial:
    """Ejecuta consultas vectoriales sobre un archivo ``.idx`` abierto."""

    def __init__(
        self,
        lector: LectorIndiceBinario,
        normalizador: Normalizador | None = None,
    ) -> None:
        self.lector = lector
        self.normalizador = normalizador or Normalizador(lector.normalizacion)

    @classmethod
    def desde_archivo(cls, ruta: str | Path) -> BuscadorVectorial:
        """Abre el índice y prepara el normalizador que dictan sus flags."""
        lector = LectorIndiceBinario(ruta)
        lector.abrir()
        return cls(lector)

    def cerrar(self) -> None:
        """Cierra el descriptor del índice."""
        self.lector.cerrar()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.cerrar()

    def buscar(
        self, consulta: str, top: int = 10, sugerir: bool = True
    ) -> RespuestaBusqueda:
        """Recupera los ``top`` documentos más similares a ``consulta``.

        Si ningún término de la consulta existe en el vocabulario, la respuesta
        queda vacía pero incluye sugerencias por prefijo, de modo que el usuario
        pueda corregir la consulta en lugar de recibir un "sin resultados" seco.
        """
        tokens = self.normalizador.normalizar_texto(consulta)
        respuesta = RespuestaBusqueda(consulta=consulta, tokens=tokens)
        if not tokens:
            return respuesta

        conteo: dict[str, int] = {}
        for token in tokens:
            conteo[token] = conteo.get(token, 0) + 1

        pesos_consulta: dict[str, float] = {}
        entradas: list[EntradaTermino] = []
        acumulado_norma = 0.0
        for termino, tf in conteo.items():
            variantes = self._resolver_variantes(termino)
            if not variantes:
                respuesta.terminos_ausentes.append(termino)
                continue
            for entrada in variantes:
                if entrada.termino in pesos_consulta:
                    continue
                peso = calcular_peso_tf(tf, self._esquema_tf()) * entrada.idf
                pesos_consulta[entrada.termino] = peso
                acumulado_norma += peso * peso
                entradas.append(entrada)
                respuesta.terminos_en_indice.append(entrada.termino)

        if not entradas:
            if sugerir:
                respuesta.sugerencias = self._sugerencias(tokens)
            return respuesta

        norma_consulta = acumulado_norma**0.5
        puntajes: dict[int, float] = {}
        for entrada in entradas:
            peso_q = pesos_consulta[entrada.termino]
            for posting in self.lector.leer_postings(entrada):
                puntajes[posting.doc_id] = (
                    puntajes.get(posting.doc_id, 0.0) + posting.peso_tfidf * peso_q
                )

        resultados: list[ResultadoBusqueda] = []
        for doc_id, producto_punto in puntajes.items():
            documento = self.lector.leer_documento(doc_id)
            resultados.append(
                ResultadoBusqueda(
                    doc_id=doc_id,
                    similitud=similitud(
                        producto_punto,
                        norma_consulta,
                        documento.norma_vectorial,
                        self._usar_coseno(),
                    ),
                    hash_url=documento.hash_url.hex(),
                    num_tokens=documento.num_tokens,
                )
            )

        resultados.sort(key=lambda r: (-r.similitud, r.doc_id))
        respuesta.resultados = resultados[:top] if top > 0 else resultados
        return respuesta

    def _resolver_variantes(self, token: str) -> list[EntradaTermino]:
        """Devuelve todas las variantes del token presentes en el vocabulario.

        Se ordenan por frecuencia documental descendente. Incluir todas las
        variantes evita perder documentos que el índice redujo con el stemmer
        del otro idioma, algo habitual en un corpus bilingüe español-inglés.
        """
        candidatas: list[EntradaTermino] = []
        vistos: set[str] = set()
        for variante in self.normalizador.variantes_de_busqueda(token):
            entrada = self.lector.buscar_termino(variante)
            if entrada is not None and entrada.termino not in vistos:
                vistos.add(entrada.termino)
                candidatas.append(entrada)
        candidatas.sort(key=lambda entrada: (-entrada.df, entrada.termino))
        return candidatas

    def _sugerencias(self, tokens: list[str], limite: int = 5) -> list[str]:
        """Propone términos del vocabulario que comparten prefijo con la consulta."""
        sugerencias: list[str] = []
        for token in tokens:
            for entrada in self.lector.terminos_con_prefijo(token[:4], limite=limite):
                if entrada.termino not in sugerencias:
                    sugerencias.append(entrada.termino)
                if len(sugerencias) >= limite:
                    return sugerencias
        return sugerencias

    def _esquema_tf(self) -> str:
        """Esquema TF declarado por el índice."""
        return self.lector.encabezado.esquema_tf

    def _usar_coseno(self) -> bool:
        """Indica si el índice se normalizó por la norma del documento."""
        return self.lector.encabezado.normalizacion_coseno
