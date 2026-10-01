"""Paso 4: la araña. Frontera en amplitud + N hilos que descargan a la vez.

Ciclo de cada hilo trabajador:

    sacar URL de la frontera
      -> robots.txt la permite?           (cortesía)
      -> reservar turno en su host         (cortesía / concurrencia)
      -> descargar y anotar en bitácora    (auditoría)
      -> leer HTML: texto, enlaces, meta robots
      -> nofollow?  si no, encolar enlaces con profundidad + 1   (selección)
      -> noindex?   si no, validar longitud y densidad           (selección)
      -> hashes -> escribir .txt atómico -> insertar en SQLite    (revisitación)
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from . import bd as acciones
from .bd import BaseDatos, Documento
from .configuracion import (
    CATEGORIA_DOCUMENTOS,
    EXTENSIONES_EXCLUIDAS,
    HILOS,
    LONGITUD_MAXIMA_URL,
    OBJETIVO_BYTES,
    PROFUNDIDAD_MAXIMA,
    RUTA_REPOSITORIO,
)
from .descargador import Descargador, ErrorDescarga, HostOcupado
from .extraccion import analizar_html, directivas_de_cabecera, motivo_rechazo
from .generador_hash import hash_contenido, hash_url
from .normalizador_url import normalizar_url, obtener_host_origen
from .rutas import ruta_relativa_documento
from .semillas import derivar_hosts, host_permitido

log = logging.getLogger(__name__)

# Tupla que guarda la frontera. PriorityQueue ordena por el primer campo, así
# que las URL con menor profundidad salen primero: recorrido en AMPLITUD.
# ``orden`` desempata para que, a igual profundidad, se respete la llegada (FIFO).
type Entrada = tuple[int, int, str, str | None]  # (profundidad, orden, url, url_origen)


class Arana:
    def __init__(
        self,
        semillas: Iterable[str],
        base_datos: BaseDatos,
        *,
        descargador: Descargador | None = None,
        repositorio: Path = RUTA_REPOSITORIO,
        hilos: int = HILOS,
        objetivo_bytes: int = OBJETIVO_BYTES,
    ) -> None:
        self.semillas = tuple(semillas)
        self.hosts = derivar_hosts(self.semillas)
        self.bd = base_datos
        self.descargador = descargador or Descargador()
        self.repositorio = Path(repositorio)
        self.hilos = hilos
        self.objetivo_bytes = objetivo_bytes

        self.frontera: queue.PriorityQueue[Entrada] = queue.PriorityQueue()
        self._orden = itertools.count()
        self._vistas: set[str] = set()  # hash_url ya encolados en esta ejecución
        self._lock = threading.Lock()
        self._pendientes = 0  # URL encoladas que todavía no terminan de procesarse
        self.detener = threading.Event()

    # ------------------------------------------------------------------
    # Frontera (política de selección)
    # ------------------------------------------------------------------
    def url_aceptable(self, url: str) -> bool:
        """Filtro de selección: esquema, host permitido, largo y extensión."""

        if len(url) > LONGITUD_MAXIMA_URL:
            return False
        try:
            partes = urlsplit(url)
        except ValueError:
            return False
        if partes.scheme not in ("http", "https") or not partes.hostname:
            return False
        if partes.username or partes.password:
            return False
        nombre = unquote(partes.path).lower().rsplit("/", 1)[-1]
        if "." in nombre and "." + nombre.rsplit(".", 1)[-1] in EXTENSIONES_EXCLUIDAS:
            return False
        return host_permitido(partes.hostname, self.hosts)

    def encolar(
        self, url: str, profundidad: int, origen: str | None, *, forzar: bool = False
    ) -> bool:
        """Agrega una URL a la frontera si pasa todos los filtros.

        ``forzar`` se usa con las semillas: siempre se piden para redescubrir
        enlaces, aunque ya estén guardadas (igual que ``dont_filter`` en Scrapy).
        La URL se registra además en la tabla ``frontera`` compartida con la
        versión con Scrapy, de modo que cualquiera de las dos puede reanudar.
        """

        if profundidad > PROFUNDIDAD_MAXIMA or not self.url_aceptable(url):
            return False
        try:
            clave = hash_url(url)
        except ValueError:
            return False
        with self._lock:
            if clave in self._vistas:
                return False
            self._vistas.add(clave)
        # Política de frescura: si ya está guardada y no ha vencido, no se baja.
        if not forzar and not self.bd.necesita_revisita(clave):
            return False
        # Persistir: si ya estaba pendiente conserva su ``id`` (FIFO); si
        # estaba visitada o en error, se resucita.
        self.bd.frontera.registrar(url, profundidad, origen)
        with self._lock:
            self._pendientes += 1
        self.frontera.put((profundidad, next(self._orden), url, origen))
        return True

    def _terminar_entrada(self) -> None:
        with self._lock:
            self._pendientes -= 1

    def pendientes(self) -> int:
        with self._lock:
            return self._pendientes

    # --- Frontera persistente compartida (tabla SQLite) ----------------
    def _cargar_frontera(self) -> int:
        """Reanuda la frontera guardada en SQLite.

        Primero descarta las URL que ya no necesitan revisita (``reconciliar``)
        y luego encola las pendientes que siguen pasando los filtros de
        selección, profundidad y deduplicación en memoria.
        """

        descartadas = self.bd.frontera.reconciliar(
            lambda hash_: not self.bd.necesita_revisita(hash_)
        )
        if descartadas:
            log.info("Frontera: %d URL ya frescas descartadas", descartadas)
        cargadas = 0
        for entrada in self.bd.frontera.pendientes():
            if self.encolar(entrada.url, entrada.profundidad, entrada.url_origen):
                cargadas += 1
        return cargadas

    # ------------------------------------------------------------------
    # Hilos trabajadores
    # ------------------------------------------------------------------
    def _trabajador(self) -> None:
        while not self.detener.is_set():
            try:
                entrada = self.frontera.get(timeout=1.0)
            except queue.Empty:
                continue
            profundidad, _orden, url, origen = entrada
            try:
                self._procesar(url, profundidad, origen)
            except HostOcupado:
                # El host está saturado: devolvemos la URL al final de su nivel
                # y el hilo toma otra de un host libre. Evita que 16 hilos se
                # queden esperando al mismo sitio.
                self.frontera.put((profundidad, next(self._orden), url, origen))
                time.sleep(0.05)
                continue
            except Exception:
                log.exception("Error inesperado procesando %s", url)
            self._terminar_entrada()

    def _procesar(self, url: str, profundidad: int, origen: str | None) -> None:
        """Procesa una URL y refleja el resultado en la frontera compartida.

        * Termina normalmente -> ``visitada``.
        * ``ErrorDescarga`` -> ``marcar_error`` (con tope de intentos).
        * ``HostOcupado`` -> no toca la frontera: la URL sigue ``pendiente`` y
          el hilo la reencola para tomar otra de un host libre.
        """

        try:
            self._procesar_pagina(url, profundidad, origen)
        except ErrorDescarga as error:
            self.bd.frontera.marcar_error(hash_url(url))
            self.bd.registrar_bitacora(
                dominio=obtener_host_origen(url), url_origen=origen, url_destino=url,
                tiempo_respuesta_ms=error.tiempo_ms, codigo_http=error.codigo_http,
                accion=acciones.ACCION_ERROR,
            )  # fmt: skip
            log.info("ERROR   %s  (%s)", url, error)
        else:
            self.bd.frontera.marcar_visitada(hash_url(url))

    def _procesar_pagina(self, url: str, profundidad: int, origen: str | None) -> None:
        dominio = obtener_host_origen(url)

        # 1. Cortesía: robots.txt
        if not self.descargador.permitido_por_robots(url):
            self.bd.registrar_bitacora(
                dominio=dominio, url_origen=origen, url_destino=url,
                accion=acciones.ACCION_BLOQUEADO_ROBOTS,
            )  # fmt: skip
            log.info("ROBOTS  %s", url)
            return

        # 2. Descarga (lanza HostOcupado si el host está lleno); un ErrorDescarga
        #    se propaga para que ``_procesar`` marque la frontera con error.
        respuesta = self.descargador.descargar(url, espera_maxima=1.0)

        # 3. Auditoría: toda respuesta queda en la bitácora
        self.bd.registrar_bitacora(
            dominio=dominio, url_origen=origen, url_destino=url,
            tiempo_respuesta_ms=respuesta.tiempo_ms, codigo_http=respuesta.codigo_http,
            accion=acciones.ACCION_RESPUESTA,
        )  # fmt: skip
        log.info(
            "GET %d  %6.0f ms  prof=%d  %s", respuesta.codigo_http, respuesta.tiempo_ms,
            profundidad, url,
        )  # fmt: skip

        # Una redirección puede sacarnos de los hosts permitidos.
        url_final = respuesta.url_final
        if url_final != url and not self.url_aceptable(url_final):
            return
        if not 200 <= respuesta.codigo_http < 300:
            return
        if not respuesta.es_html:
            self.bd.registrar_bitacora(
                dominio=dominio, url_origen=origen, url_destino=url_final,
                codigo_http=respuesta.codigo_http, accion=acciones.ACCION_NO_HTML,
            )  # fmt: skip
            return

        # 4. Análisis del HTML
        html = analizar_html(respuesta.texto(), url_final)
        directivas = html.directivas_robots | directivas_de_cabecera(
            respuesta.cabeceras.get("x-robots-tag", "")
        )

        # 5. nofollow: no seguir enlaces de esta página
        sigue_enlaces = "nofollow" not in directivas and "none" not in directivas
        if sigue_enlaces and profundidad < PROFUNDIDAD_MAXIMA:
            for enlace in html.enlaces:
                self.encolar(enlace, profundidad + 1, url_final)

        # 6. noindex: no guardar esta página
        if "noindex" in directivas or "none" in directivas:
            self.bd.registrar_bitacora(
                dominio=dominio, url_origen=origen, url_destino=url_final,
                codigo_http=respuesta.codigo_http, accion=acciones.ACCION_NOINDEX,
            )  # fmt: skip
            return

        # 7. Calidad del texto
        motivo = motivo_rechazo(html.texto)
        if motivo is not None:
            self.bd.registrar_bitacora(
                dominio=dominio, url_origen=origen, url_destino=url_final,
                codigo_http=respuesta.codigo_http, accion=acciones.ACCION_BAJA_DENSIDAD,
            )  # fmt: skip
            log.debug("DESCARTE %s (%s)", url_final, motivo)
            return

        # 8. Guardar
        accion = self._guardar(url_final, obtener_host_origen(url_final), html.texto,
                               respuesta.codigo_http, profundidad)  # fmt: skip
        self.bd.registrar_bitacora(
            dominio=dominio, url_origen=origen, url_destino=url_final,
            codigo_http=respuesta.codigo_http, accion=accion,
        )  # fmt: skip
        if accion in (acciones.ACCION_ALMACENADO, acciones.ACCION_REVISITADO):
            log.info("%s %s", accion, url_final)

    # ------------------------------------------------------------------
    # Almacenamiento atómico (misma técnica que la tubería de Scrapy)
    # ------------------------------------------------------------------
    def _guardar(self, url: str, dominio: str, texto: str, codigo: int, profundidad: int) -> str:
        h_contenido = hash_contenido(texto)
        # El nombre del archivo ES el hash del contenido: si el mismo texto ya
        # está guardado, el archivo ya existe y no se vuelve a escribir.
        relativa = ruta_relativa_documento(dominio, CATEGORIA_DOCUMENTOS, h_contenido)
        ruta = self.repositorio.joinpath(*PurePosixPath(relativa).parts)
        datos = texto.encode("utf-8")
        existia = ruta.exists()
        if not existia:
            self._escribir_atomico(ruta, datos)

        documento = Documento(
            url=normalizar_url(url),
            hash_url=hash_url(url),
            dominio=dominio,
            categoria=CATEGORIA_DOCUMENTOS,
            ruta_relativa=relativa,
            tamano_texto_bytes=len(datos),
            hash_contenido=h_contenido,
            codigo_http=codigo,
            profundidad=profundidad,
        )
        try:
            accion, ruta_anterior = self.bd.guardar_documento(documento)
        except Exception:
            if not existia:
                ruta.unlink(missing_ok=True)  # sin fila en la BD no debe quedar archivo
            raise

        if accion not in (acciones.ACCION_ALMACENADO, acciones.ACCION_REVISITADO) and not existia:
            ruta.unlink(missing_ok=True)  # otro hilo ganó: se quita el archivo huérfano
        if ruta_anterior:
            (self.repositorio / ruta_anterior).unlink(missing_ok=True)
        return accion

    @staticmethod
    def _escribir_atomico(ruta: Path, datos: bytes) -> None:
        """Temporal en la misma carpeta + fsync + rename.

        ``os.replace`` es atómico: el .txt existe completo o no existe; nunca
        queda a medias si el programa se cae.
        """

        ruta.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.")
        try:
            with os.fdopen(descriptor, "wb") as archivo:
                archivo.write(datos)
                archivo.flush()
                os.fsync(archivo.fileno())
            os.replace(temporal, ruta)
        except BaseException:
            Path(temporal).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def ejecutar(self, intervalo_progreso: float = 15.0) -> dict[str, int]:
        """Lanza los hilos y espera hasta vaciar la frontera, llegar a la meta o Ctrl+C.

        Al reanudar, las URL que quedaron pendientes en la tabla ``frontera`` se
        recuperan automáticamente en ``_cargar_frontera``.
        """

        for semilla in self.semillas:
            self.encolar(semilla, 0, None, forzar=True)
        reanudadas = self._cargar_frontera()
        log.info(
            "Inicio: %d semillas, %d URL reanudadas, %d hosts, %d hilos",
            len(self.semillas), reanudadas, len(self.hosts), self.hilos,
        )  # fmt: skip

        trabajadores = [
            threading.Thread(target=self._trabajador, name=f"arana-{i:02d}", daemon=True)
            for i in range(self.hilos)
        ]
        for hilo in trabajadores:
            hilo.start()

        ultimo_reporte = time.monotonic()
        try:
            while not self.detener.is_set():
                time.sleep(1.0)
                ahora = time.monotonic()
                resumen = self.bd.resumen()
                if resumen["bytes"] >= self.objetivo_bytes:
                    log.info("Meta de tamaño alcanzada")
                    break
                if self.pendientes() == 0:
                    log.info("Frontera vacía")
                    break
                if ahora - ultimo_reporte >= intervalo_progreso:
                    ultimo_reporte = ahora
                    print(self.texto_progreso(resumen), flush=True)
        except KeyboardInterrupt:
            print("\nDeteniendo… (la frontera queda en la base de datos para reanudar)", flush=True)
        finally:
            self.detener.set()
            for hilo in trabajadores:
                hilo.join(timeout=TIEMPO_CIERRE)
        return self.bd.resumen()

    def texto_progreso(self, resumen: dict[str, int] | None = None) -> str:
        resumen = resumen or self.bd.resumen()
        gib = resumen["bytes"] / 1024**3
        meta = self.objetivo_bytes / 1024**3
        return (
            f"[progreso] documentos={resumen['documentos']:,}  texto={gib:.3f}/{meta:.0f} GiB "
            f"({gib / meta:.1%})  visitas={resumen['bitacora']:,}  en_cola={self.pendientes():,}"
        )


TIEMPO_CIERRE = 50  # s; un hilo puede estar a mitad de una descarga de 45 s
