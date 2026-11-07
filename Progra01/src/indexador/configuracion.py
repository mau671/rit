"""Módulo de configuración estilo Linux (.conf) para el indexador.

Proporciona soporte para:
- Archivos .conf con sintaxis estilo Linux:
    # y ; para comentarios (de línea completa o inline)
    [secciones]
    clave = valor  (o clave: valor)
- Tipos primitivos:
    - Booleanos: true/false, yes/no, 1/0, on/off, si/no, sí/no (insensible a mayúsculas)
    - Enteros
    - Flotantes
    - Listas separadas por coma
- Dataclass tipada `ConfiguracionIndexador` con subconfiguraciones:
    - `ConfiguracionRutas`: rutas a base de datos, repositorio y archivo de índice de salida.
    - `ConfiguracionNormalizacion`: flags y parámetros lingüísticos (idioma, stopwords, stemming, números, fechas, sinónimos).
    - `ConfiguracionModelo`: opciones TF/IDF (esquema tf, idf, normalización coseno).
    - `ConfiguracionSpimi`: límites de memoria en MB o documentos por bloque para SPIMI.
- Flags bitmask para el encabezado del índice binario `flags_normalizacion` (uint16).
- Carga desde archivo / cadena, generación de configuraciones por defecto y volcado a .conf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag
from pathlib import Path
from typing import Any


class FlagsNormalizacion(IntFlag):
    """Banderas binarias (uint16) de normalización para el encabezado .idx."""

    NINGUNA = 0
    MINUSCULAS = 1 << 0       # 0x0001: Conversión a minúsculas
    ELIMINAR_PUNTUACION = 1 << 1  # 0x0002: Eliminación de signos de puntuación
    STOPWORDS = 1 << 2        # 0x0004: Filtrado de palabras vacías
    STEMMING = 1 << 3         # 0x0008: Reducción morfológica (Snowball)
    NORMALIZAR_NUMEROS = 1 << 4  # 0x0010: Tratamiento semántico de números
    NORMALIZAR_FECHAS = 1 << 5   # 0x0020: Normalización de fechas/años
    EXPANDIR_SINONIMOS = 1 << 6  # 0x0040: Normalización/expansión con tesauro audiovisual
    REMOVER_ACENTOS = 1 << 7     # 0x0080: Eliminación de tildes/diacríticos


def parsear_booleano(valor: str | bool | int) -> bool:
    """Convierte representaciones comunes de booleanos en True o False."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)

    limpio = str(valor).strip().lower()
    verdaderos = {"true", "1", "yes", "si", "sí", "on", "y", "s", "verdadero"}
    falsos = {"false", "0", "no", "off", "n", "falso"}

    if limpio in verdaderos:
        return True
    if limpio in falsos:
        return False
    raise ValueError(f"Valor booleano no reconocido: {valor!r}")


def parsear_valor_primitivo(valor_raw: str) -> Any:
    """Detecta e infiere el tipo primitivo de una cadena: bool, int, float, list o str."""
    val = valor_raw.strip()

    # Quitar comillas si están presentes envolviendo el valor completo
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]

    # Intentar booleano
    val_lower = val.lower()
    if val_lower in {"true", "false", "yes", "no", "si", "sí", "on", "off"}:
        return parsear_booleano(val_lower)

    # Intentar entero
    try:
        return int(val)
    except ValueError:
        pass

    # Intentar flotante
    try:
        return float(val)
    except ValueError:
        pass

    # Si contiene comas y no es una simple frase, inferir lista
    if "," in val:
        elementos = [item.strip() for item in val.split(",") if item.strip()]
        return [parsear_valor_primitivo(elem) for elem in elementos]

    return val


class ParserConfiguracionLinux:
    """Analizador para archivos de configuración estilo Linux (.conf).
    
    Características:
    - Comentarios iniciados con '#' o ';' en líneas completas o inline.
    - Secciones delimitadas por [seccion].
    - Pares clave = valor o clave: valor.
    - Preserva espacios en cadenas entre comillas.
    - Agrupación por diccionarios anidados {seccion: {clave: valor}}.
    """

    @classmethod
    def _limpiar_linea(cls, linea: str) -> str:
        """Elimina comentarios '#' y ';' respetando comillas."""
        resultado = []
        en_comillas: str | None = None

        for ch in linea:
            if ch in "\"'":
                if en_comillas is None:
                    en_comillas = ch
                elif en_comillas == ch:
                    en_comillas = None
                resultado.append(ch)
            elif ch in "#;" and en_comillas is None:
                # Comentario encontrado
                break
            else:
                resultado.append(ch)

        return "".join(resultado).strip()

    @classmethod
    def parsear_texto(cls, texto: str) -> dict[str, dict[str, Any]]:
        """Parsea una cadena de texto .conf y retorna un diccionario anidado."""
        seccion_actual = "global"
        config: dict[str, dict[str, Any]] = {seccion_actual: {}}

        for num_linea, linea_bruta in enumerate(texto.splitlines(), start=1):
            linea = cls._limpiar_linea(linea_bruta)
            if not linea:
                continue

            # Encabezado de sección: [nombre_seccion]
            if linea.startswith("[") and linea.endswith("]"):
                seccion_actual = linea[1:-1].strip().lower()
                if seccion_actual not in config:
                    config[seccion_actual] = {}
                continue

            # Separador clave-valor (= o :)
            separador = "=" if "=" in linea else (":" if ":" in linea else None)
            if not separador:
                raise ValueError(
                    f"Error de sintaxis en línea {num_linea}: se esperaba '=' o ':' -> {linea_bruta!r}"
                )

            clave_raw, valor_raw = linea.split(separador, 1)
            clave = clave_raw.strip().lower()
            if not clave:
                raise ValueError(f"Clave vacía en línea {num_linea}: {linea_bruta!r}")

            valor = parsear_valor_primitivo(valor_raw)
            config[seccion_actual][clave] = valor

        return config

    @classmethod
    def parsear_archivo(cls, ruta: str | Path) -> dict[str, dict[str, Any]]:
        """Lee y parsea un archivo de configuración .conf."""
        ruta_p = Path(ruta)
        if not ruta_p.is_file():
            raise FileNotFoundError(f"Archivo de configuración no encontrado: {ruta}")
        contenido = ruta_p.read_text(encoding="utf-8")
        return cls.parsear_texto(contenido)


# ==============================================================================
# ESTRUCTURAS DE DATOS TIPADAS
# ==============================================================================


@dataclass(slots=True)
class ConfiguracionRutas:
    """Rutas de almacenamiento, base de datos y archivos de salida."""

    base_datos: str = "../Progra00/almacenamiento/metadatos_araña.db"
    repositorio: str = "../Progra00/almacenamiento/repositorio"
    indice_salida: str = "salida/indice.idx"
    directorio_spimi: str = "salida/spimi_bloques"

    def resolver_rutas(self, base_dir: Path | None = None) -> ConfiguracionRutas:
        """Resuelve rutas relativas contra el directorio base dado."""
        if base_dir is None:
            base_dir = Path.cwd()

        def _resolver(r: str) -> str:
            p = Path(r)
            return str((base_dir / p).resolve() if not p.is_absolute() else p)

        return ConfiguracionRutas(
            base_datos=_resolver(self.base_datos),
            repositorio=_resolver(self.repositorio),
            indice_salida=_resolver(self.indice_salida),
            directorio_spimi=_resolver(self.directorio_spimi),
        )


@dataclass(slots=True)
class ConfiguracionNormalizacion:
    """Parámetros de normalización lingüística para documentos y consultas."""

    idioma: str = "auto"  # 'es', 'en', 'auto' (detección heurística multilingüe)
    minusculas: bool = True
    eliminar_puntuacion: bool = True
    remover_acentos: bool = False
    stopwords: bool = True
    idiomas_stopwords: list[str] = field(default_factory=lambda: ["es", "en"])
    stemming: bool = True
    algoritmo_stemming: str = "snowball"  # 'snowball'
    normalizar_numeros: bool = False
    modo_numeros: str = "preservar"  # 'preservar', 'token', 'descartar'
    normalizar_fechas: bool = False
    expandir_sinonimos: bool = False
    longitud_minima_token: int = 2
    longitud_maxima_token: int = 50

    def calcular_flags_bitmask(self) -> int:
        """Calcula la máscara de bits uint16 para el encabezado del archivo binario."""
        flags = FlagsNormalizacion.NINGUNA
        if self.minusculas:
            flags |= FlagsNormalizacion.MINUSCULAS
        if self.eliminar_puntuacion:
            flags |= FlagsNormalizacion.ELIMINAR_PUNTUACION
        if self.stopwords:
            flags |= FlagsNormalizacion.STOPWORDS
        if self.stemming:
            flags |= FlagsNormalizacion.STEMMING
        if self.normalizar_numeros:
            flags |= FlagsNormalizacion.NORMALIZAR_NUMEROS
        if self.normalizar_fechas:
            flags |= FlagsNormalizacion.NORMALIZAR_FECHAS
        if self.expandir_sinonimos:
            flags |= FlagsNormalizacion.EXPANDIR_SINONIMOS
        if self.remover_acentos:
            flags |= FlagsNormalizacion.REMOVER_ACENTOS
        return int(flags)


@dataclass(slots=True)
class ConfiguracionModelo:
    """Configuración del modelo de recuperación y ponderación TF/IDF."""

    esquema_tf: str = "logaritmico"  # 'natural' (raw tf), 'logaritmico' (1 + log(tf)), 'binario', 'sublineal'
    esquema_idf: str = "estandar"    # 'estandar' (log(N/df)), 'probabilistico' (max(0, log((N-df)/df))), 'ninguno'
    normalizacion_coseno: bool = True
    suavizado_idf: bool = True


@dataclass(slots=True)
class ConfiguracionSpimi:
    """Parámetros de memoria y particionamiento para indexación SPIMI."""

    limite_memoria_mb: int = 256
    docs_por_bloque: int = 5000
    tamano_buffer_escritura_kb: int = 64


@dataclass(slots=True)
class ConfiguracionIndexador:
    """Configuración integral del indexador."""

    nombre_politica: str = "estandar"
    descripcion: str = "Política estándar con stopwords y stemming multilingüe"
    rutas: ConfiguracionRutas = field(default_factory=ConfiguracionRutas)
    normalizacion: ConfiguracionNormalizacion = field(default_factory=ConfiguracionNormalizacion)
    modelo: ConfiguracionModelo = field(default_factory=ConfiguracionModelo)
    spimi: ConfiguracionSpimi = field(default_factory=ConfiguracionSpimi)

    @classmethod
    def crear_defecto(cls, politica: str = "estandar") -> ConfiguracionIndexador:
        """Crea configuraciones predefinidas: 'minimo', 'estandar', 'avanzado'."""
        politica_norm = politica.strip().lower()

        if politica_norm == "minimo":
            return cls(
                nombre_politica="minimo",
                descripcion="Política 1: Tokenización básica y minúsculas sin stopwords ni stemming",
                normalizacion=ConfiguracionNormalizacion(
                    idioma="auto",
                    minusculas=True,
                    eliminar_puntuacion=True,
                    stopwords=False,
                    stemming=False,
                    normalizar_numeros=False,
                    modo_numeros="preservar",
                    normalizar_fechas=False,
                    expandir_sinonimos=False,
                    longitud_minima_token=2,
                ),
            )

        if politica_norm == "estandar":
            return cls(
                nombre_politica="estandar",
                descripcion="Política 2: Minúsculas, eliminación de puntuación, stopwords ES+EN y stemming Snowball",
                normalizacion=ConfiguracionNormalizacion(
                    idioma="auto",
                    minusculas=True,
                    eliminar_puntuacion=True,
                    stopwords=True,
                    idiomas_stopwords=["es", "en"],
                    stemming=True,
                    algoritmo_stemming="snowball",
                    normalizar_numeros=False,
                    modo_numeros="preservar",
                    normalizar_fechas=False,
                    expandir_sinonimos=False,
                    longitud_minima_token=2,
                ),
            )

        if politica_norm == "avanzado":
            return cls(
                nombre_politica="avanzado",
                descripcion="Política 3: Normalización completa con stopwords, stemming, números, fechas y tesauro de sinónimos",
                normalizacion=ConfiguracionNormalizacion(
                    idioma="auto",
                    minusculas=True,
                    eliminar_puntuacion=True,
                    stopwords=True,
                    idiomas_stopwords=["es", "en"],
                    stemming=True,
                    algoritmo_stemming="snowball",
                    normalizar_numeros=True,
                    modo_numeros="token",
                    normalizar_fechas=True,
                    expandir_sinonimos=True,
                    longitud_minima_token=2,
                ),
            )

        raise ValueError(f"Política desconocida: {politica!r}. Opciones: 'minimo', 'estandar', 'avanzado'")

    @classmethod
    def cargar_desde_archivo(cls, ruta: str | Path) -> ConfiguracionIndexador:
        """Carga y valida la configuración desde un archivo estilo Linux (.conf)."""
        dicc = ParserConfiguracionLinux.parsear_archivo(ruta)
        return cls.cargar_desde_dict(dicc)

    @classmethod
    def cargar_desde_texto(cls, texto: str) -> ConfiguracionIndexador:
        """Carga la configuración a partir de una cadena con contenido .conf."""
        dicc = ParserConfiguracionLinux.parsear_texto(texto)
        return cls.cargar_desde_dict(dicc)

    @classmethod
    def cargar_desde_dict(cls, d: dict[str, dict[str, Any]]) -> ConfiguracionIndexador:
        """Reconstruye una instancia fuertemente tipada desde el diccionario parseado."""
        cfg = cls()

        # Sección [global] o [general]
        sec_global = d.get("global", {}) | d.get("general", {})
        if "nombre_politica" in sec_global:
            cfg.nombre_politica = str(sec_global["nombre_politica"])
        if "descripcion" in sec_global:
            v_desc = sec_global["descripcion"]
            cfg.descripcion = ", ".join(v_desc) if isinstance(v_desc, list) else str(v_desc)

        # Sección [rutas]
        sec_rutas = d.get("rutas", {})
        if "base_datos" in sec_rutas:
            cfg.rutas.base_datos = str(sec_rutas["base_datos"])
        if "repositorio" in sec_rutas:
            cfg.rutas.repositorio = str(sec_rutas["repositorio"])
        if "indice_salida" in sec_rutas:
            cfg.rutas.indice_salida = str(sec_rutas["indice_salida"])
        if "directorio_spimi" in sec_rutas:
            cfg.rutas.directorio_spimi = str(sec_rutas["directorio_spimi"])

        # Sección [normalizacion]
        sec_norm = d.get("normalizacion", {})
        for campo, val in sec_norm.items():
            if hasattr(cfg.normalizacion, campo):
                tipo_orig = type(getattr(cfg.normalizacion, campo))
                if tipo_orig is bool:
                    setattr(cfg.normalizacion, campo, parsear_booleano(val))
                elif tipo_orig is int:
                    setattr(cfg.normalizacion, campo, int(val))
                elif tipo_orig is list:
                    if isinstance(val, list):
                        setattr(cfg.normalizacion, campo, [str(x) for x in val])
                    else:
                        setattr(cfg.normalizacion, campo, [str(val)])
                else:
                    setattr(cfg.normalizacion, campo, str(val))

        # Sección [modelo]
        sec_mod = d.get("modelo", {})
        for campo, val in sec_mod.items():
            if hasattr(cfg.modelo, campo):
                tipo_orig = type(getattr(cfg.modelo, campo))
                if tipo_orig is bool:
                    setattr(cfg.modelo, campo, parsear_booleano(val))
                else:
                    setattr(cfg.modelo, campo, str(val))

        # Sección [spimi]
        sec_spimi = d.get("spimi", {})
        for campo, val in sec_spimi.items():
            if hasattr(cfg.spimi, campo):
                setattr(cfg.spimi, campo, int(val))

        return cfg

    def a_texto_conf(self) -> str:
        """Serializa la configuración en formato .conf limpio y comentado."""
        lineas = [
            "# ==============================================================================",
            "# Archivo de Configuración del Indexador (Estilo Linux .conf)",
            f"# Política: {self.nombre_politica}",
            f"# Descripción: {self.descripcion}",
            "# ==============================================================================",
            "",
            "[general]",
            f"nombre_politica = {self.nombre_politica}",
            f"descripcion = {self.descripcion}",
            "",
            "[rutas]",
            "# Rutas a base de datos de metadatos, archivos brutos e índice generado",
            f"base_datos = {self.rutas.base_datos}",
            f"repositorio = {self.rutas.repositorio}",
            f"indice_salida = {self.rutas.indice_salida}",
            f"directorio_spimi = {self.rutas.directorio_spimi}",
            "",
            "[normalizacion]",
            "# Opciones lingüísticas de tokenización y filtrado",
            f"idioma = {self.normalizacion.idioma}",
            f"minusculas = {'true' if self.normalizacion.minusculas else 'false'}",
            f"eliminar_puntuacion = {'true' if self.normalizacion.eliminar_puntuacion else 'false'}",
            f"remover_acentos = {'true' if self.normalizacion.remover_acentos else 'false'}",
            f"stopwords = {'true' if self.normalizacion.stopwords else 'false'}",
            f"idiomas_stopwords = {', '.join(self.normalizacion.idiomas_stopwords)}",
            f"stemming = {'true' if self.normalizacion.stemming else 'false'}",
            f"algoritmo_stemming = {self.normalizacion.algoritmo_stemming}",
            f"normalizar_numeros = {'true' if self.normalizacion.normalizar_numeros else 'false'}",
            f"modo_numeros = {self.normalizacion.modo_numeros}",
            f"normalizar_fechas = {'true' if self.normalizacion.normalizar_fechas else 'false'}",
            f"expandir_sinonimos = {'true' if self.normalizacion.expandir_sinonimos else 'false'}",
            f"longitud_minima_token = {self.normalizacion.longitud_minima_token}",
            f"longitud_maxima_token = {self.normalizacion.longitud_maxima_token}",
            "",
            "[modelo]",
            "# Ponderación TF-IDF y similitud de coseno",
            f"esquema_tf = {self.modelo.esquema_tf}",
            f"esquema_idf = {self.modelo.esquema_idf}",
            f"normalizacion_coseno = {'true' if self.modelo.normalizacion_coseno else 'false'}",
            f"suavizado_idf = {'true' if self.modelo.suavizado_idf else 'false'}",
            "",
            "[spimi]",
            "# Parámetros de memoria del algoritmo SPIMI",
            f"limite_memoria_mb = {self.spimi.limite_memoria_mb}",
            f"docs_por_bloque = {self.spimi.docs_por_bloque}",
            f"tamano_buffer_escritura_kb = {self.spimi.tamano_buffer_escritura_kb}",
            "",
        ]
        return "\n".join(lineas)

    def guardar_en_archivo(self, ruta: str | Path) -> None:
        """Guarda la configuración en un archivo .conf en disco."""
        ruta_p = Path(ruta)
        ruta_p.parent.mkdir(parents=True, exist_ok=True)
        ruta_p.write_text(self.a_texto_conf(), encoding="utf-8")
