"""Tubería de persistencia de texto y metadatos de documentos."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from contextlib import suppress
from inspect import Parameter, signature
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Any

from scrapy.exceptions import DropItem

from ..configuracion import obtener_ruta_base_datos, obtener_ruta_repositorio
from ..utilidades.generador_hash import generar_hash_url
from ..utilidades.normalizador_url import obtener_host_origen
from ..utilidades.rutas import crear_slug
from .conexion import ConexionSQLite
from .modelos import DocumentoItem, MetadatoDocumento, utc_ahora
from .repositorio import DocumentoDuplicadoError, RepositorioDocumentos

_LOGGER = logging.getLogger(__name__)
_HASH = re.compile(r"[0-9a-f]{64}")


class TuberiaAlmacenamiento:
    """Escribe documentos UTF-8 de forma atómica y registra sus metadatos.

    La ruta canónica es
    ``{dominio}/{categoria}/{hash[:2]}/{hash}.txt``.  Por defecto se usa
    ``hash_contenido`` como nombre direccionable; ``campo_hash_ruta`` permite
    cambiarlo a ``hash_url`` desde el constructor o los settings.

    La conexión se crea de forma perezosa al recibir el primer documento y se
    cierra siempre en ``close_spider``.  El repositorio propio coordina el
    callback de escritura y el INSERT bajo ``BEGIN IMMEDIATE``.  Un repositorio
    externo que solo cumpla la API esperada se usa bajo un lock de proceso y
    mantiene la protección UNIQUE de SQLite.

    Args:
        ruta_repositorio: Raíz del árbol de texto plano.
        repositorio: DAO inyectable.  Si se omite se crea un
            :class:`RepositorioDocumentos` con una :class:`ConexionSQLite`.
        ruta_base_datos: Archivo SQLite, usado únicamente con el repositorio
            propio.
        metricas: Tubería opcional que se actualiza al confirmar cada guardado.
        campo_hash_ruta: ``hash_contenido`` o ``hash_url``.
        timeout: Espera máxima de SQLite al crear el repositorio propio.
    """

    def __init__(
        self,
        ruta_repositorio: str | PathLike[str] | None = None,
        repositorio: Any | None = None,
        ruta_base_datos: str | PathLike[str] | None = None,
        metricas: Any | None = None,
        *,
        campo_hash_ruta: str = "hash_contenido",
        timeout: float = 30.0,
    ) -> None:
        if campo_hash_ruta not in {"hash_contenido", "hash_url"}:
            raise ValueError("campo_hash_ruta debe ser 'hash_contenido' o 'hash_url'")
        if repositorio is not None and not callable(
            getattr(repositorio, "guardar_documento", None)
        ):
            raise TypeError("repositorio debe implementar guardar_documento")

        self.ruta_repositorio = (
            Path(ruta_repositorio).expanduser()
            if ruta_repositorio is not None
            else Path.cwd() / "almacenamiento" / "repositorio"
        )
        self.campo_hash_ruta = campo_hash_ruta
        self.metricas = metricas
        self._timeout = timeout
        self._lock = threading.RLock()
        self._cerrado = False
        self._posee_repositorio = repositorio is None
        self._conexion: ConexionSQLite | None = None
        self.repositorio: Any | None = repositorio
        self._ruta_base_datos: Path | None = None

        if repositorio is None:
            if ruta_base_datos is None:
                ruta_base_datos = self.ruta_repositorio.parent / "metadatos_araña.db"
            self._ruta_base_datos = Path(ruta_base_datos)
        else:
            self.repositorio = repositorio

    @classmethod
    def from_crawler(cls, crawler: Any) -> TuberiaAlmacenamiento:
        """Construye la tubería usando settings y APIs reales de Scrapy.

        Settings reconocidos (en orden de precedencia):

        * ``TUBERIA_RUTA_REPOSITORIO`` y alias ``RUTA_REPOSITORIO_TEXTO``.
        * ``TUBERIA_RUTA_BASE_DATOS`` y alias ``RUTA_BASE_DATOS``.
        * ``TUBERIA_REPOSITORIO`` como instancia, fábrica o clase de repositorio.
        * ``TUBERIA_METRICAS`` como instancia de métricas.
        * ``TUBERIA_HASH_RUTA`` con ``hash_contenido`` o ``hash_url``.

        Args:
            crawler: Instancia de :class:`scrapy.crawler.Crawler`.

        Returns:
            Tubería lista para el ciclo de vida de Scrapy, sin conexiones abiertas.
        """

        settings = crawler.settings
        ruta_repositorio = obtener_ruta_repositorio(settings)
        ruta_base_datos = obtener_ruta_base_datos(settings)
        repositorio = settings.get("TUBERIA_REPOSITORIO", None)
        if (
            repositorio is not None
            and not callable(getattr(repositorio, "guardar_documento", None))
            and callable(repositorio)
        ):
            try:
                repositorio = repositorio(crawler=crawler)
            except TypeError:
                repositorio = repositorio()
        metricas = settings.get("TUBERIA_METRICAS", None)
        return cls(
            ruta_repositorio=ruta_repositorio,
            repositorio=repositorio,
            ruta_base_datos=ruta_base_datos,
            metricas=metricas,
            campo_hash_ruta=settings.get("TUBERIA_HASH_RUTA", "hash_contenido"),
            timeout=settings.getfloat("TUBERIA_SQLITE_TIMEOUT", 30.0),
        )

    def open_spider(self, spider: Any = None) -> None:
        """Prepara directorios sin abrir ni retener una conexión SQLite."""

        del spider
        with self._lock:
            if self._cerrado:
                raise RuntimeError("La tubería de almacenamiento ya fue cerrada")
            self.ruta_repositorio.mkdir(parents=True, exist_ok=True)

    def process_item(self, item: Any, spider: Any = None) -> Any:
        """Valida lo indispensable, escribe el texto y conserva metadatos.

        Args:
            item: ``DocumentoItem`` o diccionario con los campos del documento.
            spider: Araña actual; no se utiliza directamente.

        Returns:
            El mismo objeto de entrada con ruta, tamaño y hashes normalizados.

        Raises:
            DropItem: Si faltan datos o el documento es duplicado por URL,
                contenido o ruta.
        """

        del spider
        with self._lock:
            if self._cerrado:
                raise RuntimeError("La tubería de almacenamiento ya fue cerrada")
            datos = self._extraer_datos(item)
            metadato, ruta_relativa = self._preparar_metadato(datos)
            estado_archivo: dict[str, Any] = {
                "ruta": None,
                "existencia_previa": False,
                "escrito": False,
            }

            def escribir(relative: str) -> None:
                if relative != ruta_relativa:
                    raise RuntimeError("El repositorio invocó una ruta relativa inesperada")
                self._escribir_atomica(
                    metadato, self._texto_requerido(datos, "texto"), estado_archivo
                )

            # Se verifica la mutabilidad del item antes de publicar archivo o
            # metadatos para que una excepción de asignación no quede después
            # de una transacción ya confirmada.
            self._asignar_metadatos(
                item,
                datos,
                metadato,
                ruta_relativa=ruta_relativa,
            )
            repositorio = self._obtener_repositorio()
            try:
                actualizar_url = getattr(repositorio, "actualizar_documento_por_url", None)
                if callable(actualizar_url) and self._acepta_callback(actualizar_url):
                    resultado_revisita = actualizar_url(metadato, escribir)
                    if resultado_revisita is not None:
                        ruta_anterior, _contenido_cambiado = resultado_revisita
                        self._eliminar_ruta_anterior(ruta_anterior, ruta_relativa)
                        self._marcar_revisitado(item)
                        return item
                else:
                    registrar_revisita = getattr(
                        repositorio,
                        "registrar_revisita_exitosa",
                        None,
                    )
                    if (
                        callable(registrar_revisita)
                        and self._acepta_callback(registrar_revisita)
                        and registrar_revisita(metadato, escribir)
                    ):
                        self._marcar_revisitado(item)
                        return item

                guardar_atomico = getattr(repositorio, "guardar_documento_atomico", None)
                if callable(guardar_atomico) and self._acepta_callback(guardar_atomico):
                    guardar_atomico(metadato, escribir)
                else:
                    self._guardar_con_api_plan(repositorio, metadato, escribir)
            except DocumentoDuplicadoError as error:
                self._deshacer_archivo(estado_archivo)
                raise DropItem(str(error)) from error
            except DropItem:
                raise
            except BaseException:
                self._deshacer_archivo(estado_archivo)
                raise

            if self.metricas is not None:
                registrar = getattr(self.metricas, "registrar_documento", None)
                if callable(registrar):
                    registrar(metadato.tamano_texto_bytes, metadato.dominio)
            return item

    def _obtener_repositorio(self) -> Any:
        """Crea el DAO y su conexión de forma perezosa y segura ante fallos."""

        if self.repositorio is not None:
            return self.repositorio
        if self._ruta_base_datos is None:  # pragma: no cover - invariante del constructor
            raise RuntimeError("No hay una configuración de repositorio disponible")
        conexion = ConexionSQLite(self._ruta_base_datos, timeout=self._timeout)
        try:
            repositorio = RepositorioDocumentos(conexion)
        except BaseException:
            conexion.cerrar()
            raise
        self._conexion = conexion
        self.repositorio = repositorio
        return repositorio

    def close_spider(self, spider: Any = None) -> None:
        """Cierra idempotentemente el repositorio y cualquier conexión abierta.

        Este hook de ciclo de vida es obligatorio para que SQLite no conserve un
        recurso entre ejecuciones de Scrapy.
        """

        del spider
        with self._lock:
            if self._cerrado:
                return
            try:
                if self._posee_repositorio and self.repositorio is not None:
                    cerrar = getattr(self.repositorio, "cerrar", None) or getattr(
                        self.repositorio, "close", None
                    )
                    if callable(cerrar):
                        cerrar()
                if self._conexion is not None:
                    self._conexion.cerrar()
            finally:
                self._cerrado = True

    @staticmethod
    def _acepta_callback(metodo: Any) -> bool:
        """Indica si ``guardar_documento_atomico`` admite el callback de archivo."""

        try:
            parametros = signature(metodo).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parametro.name == "escribir_archivo" or parametro.kind is Parameter.VAR_POSITIONAL
            for parametro in parametros
        )

    def _guardar_con_api_plan(
        self,
        repositorio: Any,
        metadato: MetadatoDocumento,
        escribir: Any,
    ) -> None:
        """Usa exclusivamente los métodos mínimos descritos en el plan."""

        existe_url = getattr(repositorio, "existe_hash_url", None)
        if callable(existe_url) and existe_url(metadato.hash_url):
            raise DocumentoDuplicadoError("url", metadato.hash_url)
        existe_contenido = getattr(repositorio, "existe_hash_contenido", None)
        if callable(existe_contenido) and existe_contenido(metadato.hash_contenido):
            raise DocumentoDuplicadoError("contenido", metadato.hash_contenido)
        escribir(metadato.ruta_relativa)
        guardar_si_existe = getattr(
            repositorio,
            "guardar_documento_si_archivo_existe",
            None,
        )
        if callable(guardar_si_existe):
            resultado = guardar_si_existe(metadato, self.ruta_repositorio)
        else:
            resultado = repositorio.guardar_documento(metadato)

        # La API mínima devuelve None y None significa éxito. El DAO canónico
        # devuelve bool y False permite distinguir atómicamente un duplicado.
        if resultado is False:
            if callable(existe_url) and existe_url(metadato.hash_url):
                raise DocumentoDuplicadoError("url", metadato.hash_url)
            if callable(existe_contenido) and existe_contenido(metadato.hash_contenido):
                raise DocumentoDuplicadoError("contenido", metadato.hash_contenido)
            raise RuntimeError("El repositorio rechazó un documento que ya fue escrito")

    def _escribir_atomica(
        self,
        metadato: MetadatoDocumento,
        texto: str,
        estado: dict[str, Any],
    ) -> None:
        ruta = self.ruta_repositorio.joinpath(*PurePosixPath(metadato.ruta_relativa).parts)
        try:
            ruta.resolve(strict=False).relative_to(self.ruta_repositorio.resolve())
        except ValueError as error:
            raise DropItem("La ruta de almacenamiento sale de la raíz configurada") from error
        estado["ruta"] = ruta
        estado["existencia_previa"] = ruta.exists()
        if estado["existencia_previa"]:
            if not ruta.is_file() or ruta.read_bytes() != texto.encode("utf-8"):
                raise FileExistsError(f"La ruta de contenido ya existe y difiere: {ruta}")
            # El artefacto ya era correcto.  No se vuelve a publicar para que un
            # fallo SQLite posterior no pueda sobrescribir un archivo válido.
            return
        ruta.parent.mkdir(parents=True, exist_ok=True)

        descriptor, nombre_temporal = tempfile.mkstemp(
            dir=ruta.parent,
            prefix=f".{ruta.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
                archivo.write(texto)
                archivo.flush()
                os.fsync(archivo.fileno())
            os.replace(nombre_temporal, ruta)
            estado["escrito"] = True
            self._sincronizar_directorio(ruta.parent)
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            try:
                Path(nombre_temporal).unlink(missing_ok=True)
            finally:
                raise

    def _eliminar_ruta_anterior(self, ruta_anterior: str, ruta_actual: str) -> None:
        """Retira el artefacto viejo sólo después de confirmar el UPDATE."""

        if ruta_anterior == ruta_actual:
            return
        try:
            raiz = self.ruta_repositorio.resolve()
            ruta = (raiz / Path(ruta_anterior)).resolve()
            ruta.relative_to(raiz)
            if ruta.is_file():
                ruta.unlink()
                padre = ruta.parent
                while padre != raiz and raiz in padre.parents:
                    try:
                        padre.rmdir()
                    except OSError:
                        break
                    padre = padre.parent
        except (OSError, RuntimeError, ValueError):
            _LOGGER.warning(
                "No se pudo retirar el archivo anterior %s", ruta_anterior, exc_info=True
            )

    @staticmethod
    def _sincronizar_directorio(directorio: Path) -> None:
        """Persiste la entrada del directorio tras publicar un archivo."""

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directorio, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _deshacer_archivo(self, estado: dict[str, Any]) -> None:
        ruta = estado.get("ruta")
        if not isinstance(ruta, Path) or not estado.get("escrito"):
            return
        # Nunca se borra un archivo preexistente: podría pertenecer a un
        # documento válido cuyo hash tiene el mismo contenido.
        if estado.get("existencia_previa"):
            return
        try:
            ruta.unlink(missing_ok=True)
        except OSError:
            _LOGGER.exception("No se pudo deshacer el archivo parcial %s", ruta)
            return
        padre = ruta.parent.resolve()
        raiz = self.ruta_repositorio.resolve()
        while padre != raiz and raiz in padre.parents:
            try:
                padre.rmdir()
            except OSError:
                break
            padre = padre.parent

    def _preparar_metadato(self, datos: dict[str, Any]) -> tuple[MetadatoDocumento, str]:
        url = self._texto_requerido(datos, "url")
        texto = self._texto_requerido(datos, "texto")
        if "codigo_http" not in datos or datos["codigo_http"] is None:
            raise DropItem("El campo 'codigo_http' es obligatorio")
        if "profundidad" not in datos or datos["profundidad"] is None:
            raise DropItem("El campo 'profundidad' es obligatorio")
        dominio = self._dominio(datos.get("dominio"), url)
        categoria = self._categoria(datos.get("categoria"))
        bytes_texto = len(texto.encode("utf-8"))

        hash_url = self._hash(datos.get("hash_url"), url, "hash_url")
        hash_contenido = self._hash(datos.get("hash_contenido"), texto, "hash_contenido")
        ruta_hash = hash_contenido if self.campo_hash_ruta == "hash_contenido" else hash_url
        ruta_relativa = PurePosixPath(
            self._segmento_dominio(dominio),
            categoria,
            ruta_hash[:2],
            f"{ruta_hash}.txt",
        ).as_posix()

        fecha_descarga = datos.get("fecha_descarga") or utc_ahora()
        fecha_revisitacion = datos.get("fecha_revisitacion")
        metadato = MetadatoDocumento(
            url=url,
            hash_url=hash_url,
            dominio=dominio,
            categoria=categoria,
            ruta_relativa=ruta_relativa,
            tamano_texto_bytes=bytes_texto,
            hash_contenido=hash_contenido,
            codigo_http=datos["codigo_http"],
            profundidad=datos["profundidad"],
            fecha_descarga=fecha_descarga,
            fecha_revisitacion=fecha_revisitacion,
        )
        return metadato, ruta_relativa

    @staticmethod
    def _extraer_datos(item: Any) -> dict[str, Any]:
        if isinstance(item, Mapping):
            return dict(item)
        if hasattr(item, "items") and callable(item.items):
            return dict(item.items())
        raise DropItem("El objeto recibido no representa un documento")

    @staticmethod
    def _texto_requerido(datos: Mapping[str, Any], campo: str) -> str:
        valor = datos.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            raise DropItem(f"El campo {campo!r} es obligatorio")
        return valor

    @staticmethod
    def _dominio(valor: Any, url: str) -> str:
        dominio_esperado = obtener_host_origen(url)
        if valor is None or valor == "":
            return dominio_esperado
        if not isinstance(valor, str):
            raise DropItem("El dominio debe ser una cadena")
        dominio = valor.strip().lower()
        if dominio.startswith("www."):
            dominio = dominio[4:]
        if dominio != dominio_esperado:
            raise DropItem("El dominio no coincide con el de la URL")
        return dominio_esperado

    @staticmethod
    def _categoria(valor: Any) -> str:
        if valor is None or valor == "":
            return "sin_categoria"
        if not isinstance(valor, str):
            raise DropItem("La categoría debe ser una cadena")
        return crear_slug(valor, max_length=80) or "sin_categoria"

    @staticmethod
    def _segmento_dominio(dominio: str) -> str:
        dominio = re.sub(r"[^a-z0-9.-]+", "-", dominio.lower()).strip(".-")
        if not dominio or ".." in dominio:
            raise DropItem("El dominio no puede convertirse en un segmento seguro")
        return dominio

    @staticmethod
    def _hash(valor: Any, fuente: str, campo: str) -> str:
        calculado = (
            generar_hash_url(fuente)
            if campo == "hash_url"
            else hashlib.sha256(fuente.encode("utf-8")).hexdigest()
        )
        if valor is None or valor == "":
            return calculado
        if not isinstance(valor, str) or _HASH.fullmatch(valor) is None:
            raise DropItem(f"El campo {campo!r} no es un SHA-256 válido")
        if valor != calculado:
            raise DropItem(f"El campo {campo!r} no coincide con su fuente")
        return valor

    @staticmethod
    def _marcar_revisitado(item: Any) -> None:
        """Marca una relectura idéntica sin alterar el Item base compartido."""

        try:
            if isinstance(item, (DocumentoItem, Mapping)):
                item["revisitado"] = True
            else:
                item.revisitado = True
        except (AttributeError, KeyError, TypeError) as error:
            raise RuntimeError("No se pudo marcar la relectura del documento") from error

    @staticmethod
    def _asignar_metadatos(
        item: Any,
        datos: dict[str, Any],
        metadato: MetadatoDocumento,
        *,
        ruta_relativa: str,
    ) -> None:
        valores = {
            **datos,
            "hash_url": metadato.hash_url,
            "dominio": metadato.dominio,
            "categoria": metadato.categoria,
            "ruta_relativa": ruta_relativa,
            "tamano_texto_bytes": metadato.tamano_texto_bytes,
            "hash_contenido": metadato.hash_contenido,
            "fecha_descarga": metadato.fecha_descarga,
        }
        try:
            if isinstance(item, (DocumentoItem, Mapping)):
                item.update(valores)
            else:
                for campo, valor in valores.items():
                    setattr(item, campo, valor)
        except (AttributeError, KeyError, TypeError) as error:
            raise RuntimeError("No se pudieron asignar los metadatos al item") from error


__all__ = ["TuberiaAlmacenamiento"]
