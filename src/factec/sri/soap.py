"""Cliente de los webservices SOAP del SRI (recepción y autorización).

El SRI ofrece dos operaciones *offline* sobre SOAP 1.1:

* ``validarComprobante`` recibe el XML firmado en base64 y responde
  ``RECIBIDA`` o ``DEVUELTA`` junto con los mensajes de error.
* ``autorizacionComprobante`` recibe la clave de acceso y devuelve el estado
  ``AUTORIZADO``/``NO AUTORIZADO`` y, si procede, el comprobante autorizado.

Ejemplo::

    cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS)
    recepcion = cliente.validar_comprobante(xml_firmado)
    autorizacion = cliente.esperar_autorizacion(clave, intentos=5, espera=3)
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import requests
from lxml import etree

from ..catalogos import Ambiente
from ..excepciones import ErrorAutorizacion, ErrorRecepcion, ErrorSRI
from .endpoints import (
    NS_AUTORIZACION,
    NS_RECEPCION,
    host_ambiente,
    url_autorizacion,
    url_recepcion,
)

__all__ = [
    "Mensaje",
    "RespuestaRecepcion",
    "Autorizacion",
    "RespuestaAutorizacion",
    "ClienteSRI",
    "ESTADO_RECIBIDA",
    "ESTADO_DEVUELTA",
    "ESTADO_AUTORIZADO",
    "ESTADO_NO_AUTORIZADO",
    "ESTADO_EN_PROCESO",
    "SOAP_ENV",
    "construir_sobre_recepcion",
    "construir_sobre_autorizacion",
]

SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"

ESTADO_RECIBIDA = "RECIBIDA"
ESTADO_DEVUELTA = "DEVUELTA"
ESTADO_AUTORIZADO = "AUTORIZADO"
ESTADO_NO_AUTORIZADO = "NO AUTORIZADO"
ESTADO_EN_PROCESO = "EN PROCESO"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ------------------------------------------------------------------ respuestas


@dataclass
class Mensaje:
    """Mensaje de error o informativo devuelto por el SRI."""

    identificador: str = ""
    mensaje: str = ""
    informacion_adicional: str = ""
    tipo: str = ""

    def __str__(self) -> str:
        return f"[{self.identificador}] {self.mensaje}" + (
            f" ({self.informacion_adicional})" if self.informacion_adicional else ""
        )


@dataclass
class RespuestaRecepcion:
    """Resultado de ``validarComprobante``."""

    estado: str = ""
    clave_acceso: str = ""
    mensajes: List[Mensaje] = field(default_factory=list)
    #: Respuesta tal cual la devolvió el SRI (para conservarla como evidencia).
    crudo: str = ""

    @property
    def recibida(self) -> bool:
        return self.estado.upper() == ESTADO_RECIBIDA

    def lanzar_si_devuelta(self) -> "RespuestaRecepcion":
        """Lanza :class:`ErrorRecepcion` si el SRI devolvió el comprobante."""
        if not self.recibida:
            detalle = "; ".join(str(m) for m in self.mensajes) or "sin detalle"
            raise ErrorRecepcion(
                f"El SRI devolvió el comprobante ({self.estado or 'sin estado'}): {detalle}",
                estado=self.estado,
                clave_acceso=self.clave_acceso,
                mensajes=self.mensajes,
            )
        return self


@dataclass
class Autorizacion:
    """Autorización individual devuelta por el SRI."""

    estado: str = ""
    numero_autorizacion: str = ""
    fecha_autorizacion: Optional[datetime] = None
    ambiente: str = ""
    comprobante: str = ""
    mensajes: List[Mensaje] = field(default_factory=list)

    @property
    def autorizada(self) -> bool:
        return self.estado.upper() == ESTADO_AUTORIZADO

    @property
    def en_proceso(self) -> bool:
        return self.estado.upper() == ESTADO_EN_PROCESO

    def a_xml(self) -> str:
        """Devuelve el elemento ``<autorizacion>`` del SRI como cadena XML.

        Incluye estado, número y fecha de autorización junto con el comprobante
        autorizado: es el documento que conviene archivar y la base del RIDE.
        """
        declaracion = '<?xml version="1.0" encoding="UTF-8"?>'
        raiz = etree.Element("autorizacion")
        for etiqueta, valor in (
            ("estado", self.estado),
            ("numeroAutorizacion", self.numero_autorizacion),
            ("fechaAutorizacion", self.fecha_autorizacion.isoformat()
             if self.fecha_autorizacion else ""),
            ("ambiente", self.ambiente),
        ):
            if valor:
                etree.SubElement(raiz, etiqueta).text = str(valor)
        comprobante = etree.SubElement(raiz, "comprobante")
        comprobante.text = self.comprobante
        if self.mensajes:
            mensajes = etree.SubElement(raiz, "mensajes")
            for mensaje in self.mensajes:
                nodo = etree.SubElement(mensajes, "mensaje")
                for etiqueta, valor in (
                    ("identificador", mensaje.identificador),
                    ("mensaje", mensaje.mensaje),
                    ("informacionAdicional", mensaje.informacion_adicional),
                    ("tipo", mensaje.tipo),
                ):
                    if valor:
                        etree.SubElement(nodo, etiqueta).text = valor
        return declaracion + etree.tostring(raiz, encoding="unicode")

    def lanzar_si_no_autorizada(self) -> "Autorizacion":
        """Lanza :class:`ErrorAutorizacion` si no está autorizada."""
        if not self.autorizada:
            detalle = "; ".join(str(m) for m in self.mensajes) or "sin detalle"
            raise ErrorAutorizacion(
                f"El comprobante no fue autorizado ({self.estado or 'sin estado'}): {detalle}",
                estado=self.estado,
                clave_acceso=self.numero_autorizacion,
                mensajes=self.mensajes,
            )
        return self


@dataclass
class RespuestaAutorizacion:
    """Resultado de ``autorizacionComprobante``."""

    clave_acceso_consultada: str = ""
    numero_comprobantes: int = 0
    autorizaciones: List[Autorizacion] = field(default_factory=list)
    #: Respuesta tal cual la devolvió el SRI (para conservarla como evidencia).
    crudo: str = ""

    @property
    def ultima(self) -> Optional[Autorizacion]:
        return self.autorizaciones[-1] if self.autorizaciones else None

    @property
    def autorizada(self) -> bool:
        autorizacion = self.ultima
        return bool(autorizacion and autorizacion.autorizada)

    def lanzar_si_no_autorizada(self) -> RespuestaAutorizacion:
        """Lanza :class:`ErrorAutorizacion` si no hay una autorización válida."""
        if not self.autorizaciones:
            raise ErrorAutorizacion(
                "El SRI no devolvió ninguna autorización para la clave consultada.",
                clave_acceso=self.clave_acceso_consultada,
            )
        self.autorizaciones[-1].lanzar_si_no_autorizada()
        return self


# ---------------------------------------------------------- construcción SOAP


def construir_sobre_recepcion(xml_firmado: Union[str, bytes]) -> bytes:
    """Envuelve el comprobante firmado en el sobre SOAP de recepción."""
    if isinstance(xml_firmado, str):
        xml_firmado = xml_firmado.encode("utf-8")
    contenido = base64.b64encode(xml_firmado).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV}" xmlns:ec="{NS_RECEPCION}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        f"<ec:validarComprobante><xml>{contenido}</xml></ec:validarComprobante>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    ).encode("utf-8")


def construir_sobre_autorizacion(clave_acceso: str) -> bytes:
    """Envuelve la clave de acceso en el sobre SOAP de autorización."""
    from xml.sax.saxutils import escape

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV}" xmlns:ec="{NS_AUTORIZACION}">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<ec:autorizacionComprobante>"
        f"<claveAccesoComprobante>{escape(str(clave_acceso))}</claveAccesoComprobante>"
        "</ec:autorizacionComprobante>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    ).encode("utf-8")


def _texto(nodo: Optional[etree._Element]) -> str:
    if nodo is None or nodo.text is None:
        return ""
    return nodo.text.strip()


def _hijo(padre: Optional[etree._Element], nombre: str) -> Optional[etree._Element]:
    if padre is None:
        return None
    encontrado = padre.find(nombre)
    if encontrado is not None:
        return encontrado
    return padre.find(f".//{nombre}")


def _mensajes(nodo: Optional[etree._Element]) -> List[Mensaje]:
    """Lee los ``mensaje`` hijos de un contenedor ``mensajes``.

    Se usan solo los hijos directos: el nodo ``mensaje`` contiene a su vez un
    ``mensaje`` con el texto, y un recorrido recursivo devolvería los dos.
    """
    if nodo is None:
        return []
    resultado = []
    for item in nodo.findall("mensaje"):
        resultado.append(
            Mensaje(
                identificador=_texto(_hijo(item, "identificador")),
                mensaje=_texto(_hijo(item, "mensaje")),
                informacion_adicional=_texto(_hijo(item, "informacionAdicional")),
                tipo=_texto(_hijo(item, "tipo")),
            )
        )
    return resultado


def _fecha(texto: str) -> Optional[datetime]:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


# -------------------------------------------------------------------- cliente


class ClienteSRI:
    """Cliente de los webservices de comprobantes electrónicos del SRI."""

    def __init__(
        self,
        ambiente: Union[int, Ambiente] = Ambiente.PRUEBAS,
        *,
        host: Optional[str] = None,
        timeout: float = 30.0,
        session: Optional[requests.Session] = None,
        verificar_ssl: bool = True,
    ) -> None:
        self.ambiente = int(getattr(ambiente, "value", ambiente))
        self.host = host or host_ambiente(self.ambiente)
        self.timeout = timeout
        self.verificar_ssl = verificar_ssl
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _USER_AGENT)
        self.session.headers.setdefault("Content-Type", "text/xml; charset=utf-8")
        #: Texto de la última respuesta del SRI, tal cual llegó.
        self.ultima_respuesta = ""

    # ------------------------------------------------------------- transporte

    def _llamar(self, url: str, sobre: bytes, accion: str = "") -> etree._Element:
        # El WSDL del SRI declara style="document" y soapAction="" para todas
        # las operaciones: enviar un SOAPAction distinto provoca un SOAP Fault.
        headers = {"SOAPAction": accion}
        try:
            respuesta = self.session.post(
                url, data=sobre, headers=headers, timeout=self.timeout, verify=self.verificar_ssl
            )
        except requests.RequestException as exc:
            raise ErrorSRI(f"No se pudo contactar al SRI en {url}: {exc}") from exc

        if respuesta.status_code >= 500:
            raise ErrorSRI(
                f"El SRI respondió {respuesta.status_code} en {url}. "
                "Suele indicar indisponibilidad temporal; reintente más tarde."
            )
        try:
            raiz = etree.fromstring(respuesta.content)
        except etree.XMLSyntaxError as exc:
            raise ErrorSRI(
                f"El SRI devolvió una respuesta no XML ({respuesta.status_code}): "
                f"{respuesta.content[:200]!r}"
            ) from exc

        self.ultima_respuesta = respuesta.content.decode("utf-8", "replace")

        fallo = raiz.find(f".//{{{SOAP_ENV}}}Fault")
        if fallo is not None:
            raise ErrorSRI(
                "El SRI devolvió un SOAP Fault: "
                f"{_texto(_hijo(fallo, 'faultstring')) or etree.tostring(fallo)[:200]!r}"
            )
        return raiz

    # -------------------------------------------------------------- servicios

    def validar_comprobante(self, xml_firmado: Union[str, bytes]) -> RespuestaRecepcion:
        """Envía el comprobante firmado y devuelve la respuesta de recepción."""
        raiz = self._llamar(
            url_recepcion(self.ambiente, self.host),
            construir_sobre_recepcion(xml_firmado),
        )
        solicitud = _hijo(raiz, "RespuestaRecepcionComprobante")
        if solicitud is None:
            solicitud = _hijo(raiz, "RespuestaSolicitud")
        estado = _texto(_hijo(solicitud, "estado"))
        comprobante = _hijo(solicitud, "comprobante")
        return RespuestaRecepcion(
            estado=estado,
            clave_acceso=_texto(_hijo(comprobante, "claveAcceso")),
            mensajes=_mensajes(_hijo(comprobante, "mensajes")),
            crudo=self.ultima_respuesta,
        )

    def autorizar(self, clave_acceso: str) -> RespuestaAutorizacion:
        """Consulta la autorización de una clave de acceso."""
        raiz = self._llamar(
            url_autorizacion(self.ambiente, self.host),
            construir_sobre_autorizacion(clave_acceso),
        )
        respuesta = _hijo(raiz, "RespuestaAutorizacionComprobante")
        if respuesta is None:
            return RespuestaAutorizacion(
                clave_acceso_consultada=clave_acceso, crudo=self.ultima_respuesta
            )

        autorizaciones = []
        for nodo in respuesta.iter("autorizacion"):
            autorizaciones.append(
                Autorizacion(
                    estado=_texto(_hijo(nodo, "estado")),
                    numero_autorizacion=_texto(_hijo(nodo, "numeroAutorizacion")),
                    fecha_autorizacion=_fecha(_texto(_hijo(nodo, "fechaAutorizacion"))),
                    ambiente=_texto(_hijo(nodo, "ambiente")),
                    comprobante=_texto(_hijo(nodo, "comprobante")),
                    mensajes=_mensajes(_hijo(nodo, "mensajes")),
                )
            )
        try:
            numero = int(_texto(_hijo(respuesta, "numeroComprobantes")) or 0)
        except ValueError:
            numero = len(autorizaciones)

        return RespuestaAutorizacion(
            clave_acceso_consultada=_texto(_hijo(respuesta, "claveAccesoConsultada")) or clave_acceso,
            numero_comprobantes=numero,
            autorizaciones=autorizaciones,
            crudo=self.ultima_respuesta,
        )

    def esperar_autorizacion(
        self,
        clave_acceso: str,
        *,
        intentos: int = 5,
        espera: float = 3.0,
    ) -> RespuestaAutorizacion:
        """Sondea la autorización hasta obtener un estado definitivo.

        El SRI procesa la autorización de forma asíncrona, por lo que tras
        recibir el comprobante puede devolver ``EN PROCESO``; aquí se reintenta.
        """
        ultima = RespuestaAutorizacion(clave_acceso_consultada=clave_acceso)
        for numero in range(1, max(1, intentos) + 1):
            ultima = self.autorizar(clave_acceso)
            autorizacion = ultima.ultima
            if autorizacion and not autorizacion.en_proceso and autorizacion.estado:
                return ultima
            if numero < intentos:
                time.sleep(espera)
        return ultima

    def enviar_y_autorizar(
        self,
        xml_firmado: Union[str, bytes],
        *,
        clave_acceso: Optional[str] = None,
        intentos: int = 5,
        espera: float = 3.0,
    ) -> RespuestaAutorizacion:
        """Recepciona el comprobante y espera su autorización.

        Si no se indica ``clave_acceso`` se toma del propio XML (``claveAcceso``).
        """
        recepcion = self.validar_comprobante(xml_firmado)
        recepcion.lanzar_si_devuelta()

        clave = clave_acceso or recepcion.clave_acceso
        if not clave and isinstance(xml_firmado, bytes):
            xml_firmado = xml_firmado.decode("utf-8", errors="replace")
        if not clave and isinstance(xml_firmado, str):
            nodo = _hijo(etree.fromstring(xml_firmado.encode("utf-8")), "claveAcceso")
            clave = _texto(nodo)
        if not clave:
            raise ErrorSRI("No se pudo determinar la clave de acceso del comprobante enviado.")

        return self.esperar_autorizacion(clave, intentos=intentos, espera=espera)
