#!/usr/bin/env node
// cli.js
// Punto de entrada del indexador con Elasticsearch.
//
//   node src/cli.js indexar     --config configuraciones/estandar.conf [--limite 10000]
//   node src/cli.js generar     --config configuraciones/estandar.conf
//   node src/cli.js verificar   --config configuraciones/estandar.conf [--casos ruta.json]
//   node src/cli.js buscar      --config configuraciones/estandar.conf "film director" [--top 10]
//   node src/cli.js benchmark   [--limite 10000] [--propio ../propio/salida/benchmark_indices.json]

import { parseArgs } from "node:util";

// node:sqlite todavía emite un aviso "experimental"; se oculta solo ese aviso.
// Los módulos se importan después (import dinámico) para que el filtro ya esté activo.
const emitirAviso = process.emitWarning;
process.emitWarning = (aviso, ...resto) => {
  if (String(aviso?.message ?? aviso).includes("SQLite")) return;
  return emitirAviso.call(process, aviso, ...resto);
};

const { cargarConfiguracion } = await import("./configuracion.js");
const { indexar, generarConfiguracionIndice } = await import("./indexador.js");
const { verificar } = await import("./verificador.js");
const { buscar } = await import("./buscador.js");
const { benchmark } = await import("./benchmark.js");

const AYUDA = `
Indexador TF/IDF con Elasticsearch (Proyecto 2 - RIT)

Comandos:
  indexar     Construye el índice de una política y guarda sus estadísticas
  generar     Solo genera la configuración del índice (settings/mappings) sin conectarse
  verificar   Compara la normalización con los tokens esperados del propio
  buscar      Consulta de prueba con similitud coseno
  benchmark   Indexa las tres políticas y compara con el propio

Opciones:
  --config, -c   Archivo .conf de la política
  --limite, -l   Cantidad máxima de documentos (0 = todos)
  --top, -k      Resultados a mostrar en buscar (por defecto 10)
  --casos        Archivo de casos para verificar
  --propio       Benchmark del propio (por defecto ../propio/salida/benchmark_indices.json)
`;

const POLITICAS = ["configuraciones/minimo.conf", "configuraciones/estandar.conf", "configuraciones/avanzado.conf"];

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      config: { type: "string", short: "c" },
      limite: { type: "string", short: "l", default: "0" },
      top: { type: "string", short: "k", default: "10" },
      casos: { type: "string" },
      propio: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  const [comando, ...resto] = positionals;
  if (values.help || !comando) {
    console.log(AYUDA);
    return;
  }

  const limite = Number.parseInt(values.limite, 10) || 0;
  const necesitaConfig = () => {
    if (!values.config) throw new Error("Falta --config <archivo .conf>");
    return cargarConfiguracion(values.config);
  };

  switch (comando) {
    case "indexar":
      await indexar(necesitaConfig(), { limite });
      break;
    case "generar": {
      const { ruta } = generarConfiguracionIndice(necesitaConfig());
      console.log(`Configuración del índice guardada en ${ruta}`);
      break;
    }
    case "verificar":
      await verificar(necesitaConfig(), values.casos);
      break;
    case "buscar": {
      const consulta = resto.join(" ");
      if (!consulta) throw new Error('Falta la consulta, por ejemplo: buscar -c ... "film director"');
      await buscar(necesitaConfig(), consulta, { top: Number.parseInt(values.top, 10) || 10 });
      break;
    }
    case "benchmark":
      await benchmark(values.config ? [values.config] : POLITICAS, { limite, benchmarkPropio: values.propio });
      break;
    default:
      throw new Error(`Comando desconocido: ${comando}\n${AYUDA}`);
  }
}

main().catch((err) => {
  console.error(`\nError: ${err.message}`);
  process.exit(1);
});
