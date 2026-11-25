// consulta.js
// Interpreta la sintaxis de búsqueda avanzada.
//
//   palabras sueltas      se buscan con el modelo vectorial (similitud coseno)
//   "frase exacta"        las palabras deben aparecer juntas y en orden
//   +palabra              la palabra es obligatoria
//   -palabra / -"frase"   excluye los documentos que la contengan
//   dominio:imsdb.com     solo resultados de ese sitio (se puede repetir)
//   idioma:es | idioma:en solo documentos en ese idioma
//
// Las palabras de frases y obligatorias también suman al puntaje; las
// excluidas no.

const RE_PIEZA = /([+-]?)(?:(dominio|idioma):)?("([^"]*)"?|\S+)/giu;

export function interpretarConsulta(texto) {
  const resultado = {
    texto: (texto ?? "").trim(),
    libres: [],
    obligatorios: [],
    frases: [],
    excluidos: [],
    dominios: [],
    idiomas: [],
  };

  for (const m of resultado.texto.matchAll(RE_PIEZA)) {
    const [, signo, campo, crudo, entreComillas] = m;
    const esFrase = entreComillas !== undefined;
    const valor = (esFrase ? entreComillas : crudo).trim();
    if (!valor) continue;

    if (campo) {
      const lista = campo.toLowerCase() === "dominio" ? resultado.dominios : resultado.idiomas;
      lista.push(valor.toLowerCase());
    } else if (signo === "-") {
      resultado.excluidos.push(valor);
    } else if (esFrase) {
      resultado.frases.push(valor);
    } else if (signo === "+") {
      resultado.obligatorios.push(valor);
    } else {
      resultado.libres.push(valor);
    }
  }
  return resultado;
}

/** Texto que se usa para calcular el puntaje vectorial. */
export function textoParaPuntaje(c) {
  return [...c.libres, ...c.obligatorios, ...c.frases].join(" ");
}
