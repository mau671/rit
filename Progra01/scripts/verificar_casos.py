"""Verifica un indexador contra casos_prueba/normalizacion.json.

Sin argumentos prueba el normalizador de Python. Para probar otra implementación
(Lucene) se le pasa --comando: el programa recibe la política donde aparezca
``{politica}``, lee por stdin un arreglo JSON de textos y escribe por stdout un
arreglo JSON con la lista de tokens de cada texto, en el mismo orden. Ejemplo:

    uv run python scripts/verificar_casos.py \
        --comando "java -jar ../lucene/target/indexador-lucene.jar tokenizar --politica {politica}"

Sale con código 1 si algún caso falla.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def tokenizar_python(politica: str, textos: list[str]) -> list[list[str]]:
    from indexador.configuracion import ConfiguracionIndexador
    from indexador.normalizador import Normalizador

    cfg = ConfiguracionIndexador.cargar_desde_archivo(RAIZ / "configuraciones" / f"{politica}.conf")
    norm = Normalizador(cfg.normalizacion)
    return [norm.normalizar_texto(t) for t in textos]


def tokenizar_externo(plantilla: str, politica: str, textos: list[str]) -> list[list[str]]:
    comando = shlex.split(plantilla.replace("{politica}", politica))
    res = subprocess.run(
        comando, input=json.dumps(textos), capture_output=True, text=True, encoding="utf-8", check=False
    )
    if res.returncode != 0:
        sys.exit(f"El comando falló ({res.returncode}) para {politica}:\n{res.stderr}")
    return json.loads(res.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comando", help="indexador externo (ver arriba); por defecto, el de Python")
    ap.add_argument("--casos", default=str(RAIZ / "casos_prueba" / "normalizacion.json"))
    args = ap.parse_args()

    datos = json.loads(Path(args.casos).read_text(encoding="utf-8"))
    textos = [c["texto"] for c in datos["casos"]]
    fallos = 0
    for politica in datos["politicas"]:
        obtenidos = (
            tokenizar_externo(args.comando, politica, textos)
            if args.comando
            else tokenizar_python(politica, textos)
        )
        for caso, tokens in zip(datos["casos"], obtenidos, strict=True):
            esperado = caso["tokens"][politica]
            if tokens != esperado:
                fallos += 1
                print(f"FALLA [{politica}] {caso['id']}\n  texto:     {caso['texto']!r}")
                print(f"  esperado:  {esperado}\n  obtenido:  {tokens}")
    total = len(datos["casos"]) * len(datos["politicas"])
    print(f"{total - fallos}/{total} casos correctos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
