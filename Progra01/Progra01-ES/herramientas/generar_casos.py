"""Genera los casos de prueba "texto -> tokens esperados" con el indexador propio.

Ejecuta el Normalizador del propio sobre textos de prueba y sobre una muestra
de documentos reales de la colección, y guarda los tokens que produce. Luego
`node src/cli.js verificar` envía los mismos textos a Elasticsearch y compara.

Se corre desde la carpeta del propio (Progra01), con su entorno de uv:

    cd ../  # carpeta Progra01
    uv run python Progra01-ES/herramientas/generar_casos.py --politica estandar --muestras 30

Genera Progra01-ES/casos/esperados_<politica>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ_ES = Path(__file__).resolve().parent.parent  # Progra01-ES
RAIZ_PROPIO = RAIZ_ES.parent  # Progra01
sys.path.insert(0, str(RAIZ_PROPIO / "src"))

from indexador.configuracion import ConfiguracionIndexador  # noqa: E402
from indexador.lector_coleccion import LectorColeccion  # noqa: E402
from indexador.normalizador import Normalizador  # noqa: E402


def normalizar_con_idioma(normalizador: Normalizador, texto: str) -> tuple[list[str], str]:
    """Devuelve los tokens y el idioma que el propio detectó para el texto."""
    detectado: list[str] = []
    original = normalizador.detectar_idioma

    def espia(tokens_o_texto):
        resultado = original(tokens_o_texto)
        detectado.append(resultado)
        return resultado

    normalizador.detectar_idioma = espia  # type: ignore[method-assign]
    try:
        tokens = normalizador.normalizar_texto(texto)
    finally:
        normalizador.detectar_idioma = original  # type: ignore[method-assign]
    return tokens, (detectado[0] if detectado else "es")


def leer_textos_prueba(ruta: Path) -> list[str]:
    """Un caso por bloque; los bloques se separan con una línea '---'."""
    bloques: list[str] = []
    actual: list[str] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.strip() == "---":
            if actual:
                bloques.append("\n".join(actual).strip())
            actual = []
        elif not linea.startswith("#"):
            actual.append(linea)
    if actual:
        bloques.append("\n".join(actual).strip())
    return [b for b in bloques if b]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--politica", required=True, choices=["minimo", "estandar", "avanzado"])
    parser.add_argument("--textos", default=str(RAIZ_ES / "casos" / "textos_prueba.txt"))
    parser.add_argument("--muestras", type=int, default=20, help="Documentos reales de la colección a incluir")
    args = parser.parse_args()

    ruta_conf = RAIZ_PROPIO / "configuraciones" / f"{args.politica}.conf"
    config = ConfiguracionIndexador.cargar_desde_archivo(ruta_conf)
    normalizador = Normalizador(config.normalizacion)

    casos = []
    for i, texto in enumerate(leer_textos_prueba(Path(args.textos)), start=1):
        tokens, idioma = normalizar_con_idioma(normalizador, texto)
        casos.append({"id": f"prueba-{i}", "origen": "textos_prueba", "idioma": idioma, "texto": texto, "tokens": tokens})

    if args.muestras > 0:
        rutas = config.rutas.resolver_rutas(RAIZ_PROPIO)
        lector = LectorColeccion(rutas.base_datos, rutas.repositorio)
        for doc in lector.iterar_documentos(limite=args.muestras):
            tokens, idioma = normalizar_con_idioma(normalizador, doc.texto)
            casos.append({
                "id": f"doc-{doc.db_id}",
                "origen": doc.url,
                "idioma": idioma,
                "texto": doc.texto,
                "tokens": tokens,
            })

    salida = RAIZ_ES / "casos" / f"esperados_{args.politica}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(casos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(casos)} casos guardados en {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
