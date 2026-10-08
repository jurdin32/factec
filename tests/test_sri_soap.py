"""Pruebas del cliente SOAP del SRI (sin contactar la red)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from factec.catalogos import Ambiente
from factec.excepciones import ErrorRecepcion, ErrorSRI, ErrorAutorizacion
from factec.sri import (
    ClienteSRI,
    construir_sobre_autorizacion,
    construir_sobre_recepcion,
)
from factec.sri.endpoints import url_autorizacion, url_recepcion

CLAVE = "0810202601179001234500110010010000000011234567819"

RESPUESTA_RECIBIDA = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">
<RespuestaRecepcionComprobante><estado>RECIBIDA</estado><comprobantes><comprobante>
<claveAcceso>%s</claveAcceso><mensajes/></comprobante></comprobantes>
</RespuestaRecepcionComprobante></ns2:validarComprobanteResponse>
</soap:Body></soap:Envelope>""" % CLAVE

RESPUESTA_DEVUELTA = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">
<RespuestaRecepcionComprobante><estado>DEVUELTA</estado><comprobantes><comprobante>
<claveAcceso>N/A</claveAcceso><mensajes><mensaje>
<identificador>35</identificador><mensaje>ARCHIVO NO CUMPLE ESTRUCTURA XML</mensaje>
<informacionAdicional>RUC no registrado</informacionAdicional><tipo>ERROR</tipo>
</mensaje></mensajes></comprobante></comprobantes></RespuestaRecepcionComprobante>
</ns2:validarComprobanteResponse></soap:Body></soap:Envelope>"""

RESPUESTA_AUTORIZADO = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">
<RespuestaAutorizacionComprobante><claveAccesoConsultada>%s</claveAccesoConsultada>
<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>
<estado>AUTORIZADO</estado><numeroAutorizacion>%s</numeroAutorizacion>
<fechaAutorizacion>2026-10-08T10:00:00-05:00</fechaAutorizacion><ambiente>PRUEBAS</ambiente>
<comprobante>&lt;factura id="comprobante"&gt;&lt;infoTributaria/&gt;&lt;/factura&gt;</comprobante>
<mensajes/></autorizacion></autorizaciones></RespuestaAutorizacionComprobante>
</ns2:autorizacionComprobanteResponse></soap:Body></soap:Envelope>""" % (CLAVE, CLAVE)

RESPUESTA_EN_PROCESO = RESPUESTA_AUTORIZADO.replace("<estado>AUTORIZADO</estado>", "<estado>EN PROCESO</estado>")

RESPUESTA_NO_AUTORIZADO = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">
<RespuestaAutorizacionComprobante><claveAccesoConsultada>%s</claveAccesoConsultada>
<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>
<estado>NO AUTORIZADO</estado><mensajes><mensaje><identificador>70</identificador>
<mensaje>CLAVE DE ACCESO REGISTRADA</mensaje><tipo>ERROR</tipo></mensaje></mensajes>
</autorizacion></autorizaciones></RespuestaAutorizacionComprobante>
</ns2:autorizacionComprobanteResponse></soap:Body></soap:Envelope>""" % CLAVE

FAULT = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<soap:Fault><faultcode>soap:Server</faultcode>
<faultstring>The given SOAPAction does not match an operation.</faultstring>
</soap:Fault></soap:Body></soap:Envelope>"""


class RespuestaFalsa:
    def __init__(self, contenido: bytes, status_code: int = 200):
        self.content = contenido
        self.status_code = status_code
        self.text = contenido.decode("utf-8", errors="replace")


class SesionFalsa:
    """Sustituye a ``requests.Session`` y registra las peticiones."""

    def __init__(self, respuestas: List[RespuestaFalsa]):
        self.headers: Dict[str, str] = {}
        self.respuestas = list(respuestas)
        self.peticiones: List[Dict[str, Any]] = []

    def post(self, url, data=None, headers=None, timeout=None, verify=None):
        self.peticiones.append(
            {"url": url, "data": data, "headers": headers or {}, "timeout": timeout}
        )
        if not self.respuestas:
            raise AssertionError("Se agotaron las respuestas falsas")
        return self.respuestas.pop(0)


def _cliente(sesion: SesionFalsa, **kwargs) -> ClienteSRI:
    """Cliente con una sesión falsa ya preparada."""
    return ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion, **kwargs)


class TestSobres:
    def test_sobre_de_recepcion_lleva_el_xml_en_base64(self):
        import base64

        sobre = construir_sobre_recepcion("<factura/>")
        assert b"validarComprobante" in sobre
        assert b"http://ec.gob.sri.ws.recepcion" in sobre
        assert base64.b64encode(b"<factura/>") in sobre

    def test_sobre_de_autorizacion_lleva_la_clave(self):
        sobre = construir_sobre_autorizacion(CLAVE)
        assert clave_en(sobre, CLAVE)
        assert b"http://ec.gob.sri.ws.autorizacion" in sobre

    def test_sobre_de_autorizacion_escapa_la_clave(self):
        sobre = construir_sobre_autorizacion("<&>").decode()
        assert "&lt;&amp;&gt;" in sobre


def clave_en(sobre: bytes, clave: str) -> bool:
    return clave.encode() in sobre


class TestUrls:
    def test_pruebas_y_produccion(self):
        assert "celcer.sri.gob.ec" in url_recepcion(Ambiente.PRUEBAS)
        assert "cel.sri.gob.ec" in url_recepcion(Ambiente.PRODUCCION)
        assert "cel.sri.gob.ec" in url_autorizacion(2)
        assert not url_autorizacion(2).count("celcer")

    def test_ambiente_invalido(self):
        with pytest.raises(ValueError):
            url_recepcion(9)


class TestRecepcion:
    def test_recibida(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_RECIBIDA.encode())])
        respuesta = _cliente(sesion).validar_comprobante("<factura/>")
        assert respuesta.estado == "RECIBIDA"
        assert respuesta.recibida is True
        assert respuesta.clave_acceso == CLAVE
        respuesta.lanzar_si_devuelta()

    def test_soapaction_vacio(self):
        """El SRI exige ``SOAPAction`` vacío; cualquier otro valor da SOAP Fault."""
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_RECIBIDA.encode())])
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        cliente.validar_comprobante("<factura/>")
        assert sesion.peticiones[0]["headers"]["SOAPAction"] == ""
        assert sesion.peticiones[0]["url"] == url_recepcion(Ambiente.PRUEBAS)

    def test_devuelta_parsea_mensajes(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_DEVUELTA.encode())])
        respuesta = _cliente(sesion).validar_comprobante("<factura/>")
        assert respuesta.estado == "DEVUELTA"
        assert respuesta.recibida is False
        assert len(respuesta.mensajes) == 1
        mensaje = respuesta.mensajes[0]
        assert mensaje.identificador == "35"
        assert "NO CUMPLE ESTRUCTURA" in mensaje.mensaje
        assert mensaje.informacion_adicional == "RUC no registrado"
        assert mensaje.tipo == "ERROR"

    def test_devuelta_lanza_error(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_DEVUELTA.encode())])
        respuesta = _cliente(sesion).validar_comprobante("<factura/>")
        with pytest.raises(ErrorRecepcion) as excinfo:
            respuesta.lanzar_si_devuelta()
        assert excinfo.value.estado == "DEVUELTA"
        assert excinfo.value.mensajes[0].identificador == "35"


class TestAutorizacion:
    def test_autorizado(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_AUTORIZADO.encode())])
        respuesta = _cliente(sesion).autorizar(CLAVE)
        assert respuesta.numero_comprobantes == 1
        assert respuesta.autorizada is True
        autorizacion = respuesta.ultima
        assert autorizacion.estado == "AUTORIZADO"
        assert autorizacion.numero_autorizacion == CLAVE
        assert autorizacion.fecha_autorizacion is not None
        assert autorizacion.ambiente == "PRUEBAS"
        assert autorizacion.comprobante.startswith("<factura")

    def test_no_autorizado_lanza(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_NO_AUTORIZADO.encode())])
        respuesta = _cliente(sesion).autorizar(CLAVE)
        assert respuesta.autorizada is False
        with pytest.raises(ErrorAutorizacion):
            respuesta.lanzar_si_no_autorizada()

    def test_sin_autorizaciones_lanza(self):
        vacia = RESPUESTA_AUTORIZADO.replace(
            "<autorizaciones><autorizacion>", "<autorizaciones><!--"
        ).replace("</autorizacion></autorizaciones>", "--></autorizaciones>")
        sesion = SesionFalsa([RespuestaFalsa(vacia.encode())])
        respuesta = _cliente(sesion).autorizar(CLAVE)
        with pytest.raises(ErrorAutorizacion, match="ninguna autorización"):
            respuesta.lanzar_si_no_autorizada()

    def test_esperar_autorizacion_reintenta(self):
        sesion = SesionFalsa([
            RespuestaFalsa(RESPUESTA_EN_PROCESO.encode()),
            RespuestaFalsa(RESPUESTA_EN_PROCESO.encode()),
            RespuestaFalsa(RESPUESTA_AUTORIZADO.encode()),
        ])
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        respuesta = cliente.esperar_autorizacion(CLAVE, intentos=5, espera=0)
        assert respuesta.autorizada is True
        assert len(sesion.peticiones) == 3

    def test_esperar_autorizacion_agota_intentos(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_EN_PROCESO.encode())] * 3)
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        respuesta = cliente.esperar_autorizacion(CLAVE, intentos=3, espera=0)
        assert respuesta.autorizada is False
        assert len(sesion.peticiones) == 3


class TestErrores:
    def test_soap_fault(self):
        sesion = SesionFalsa([RespuestaFalsa(FAULT.encode())])
        with pytest.raises(ErrorSRI, match="SOAP Fault"):
            _cliente(sesion).autorizar(CLAVE)

    def test_error_500(self):
        sesion = SesionFalsa([RespuestaFalsa(b"boom", status_code=500)])
        with pytest.raises(ErrorSRI, match="500"):
            _cliente(sesion).autorizar(CLAVE)

    def test_respuesta_no_xml(self):
        sesion = SesionFalsa([RespuestaFalsa(b"<html>error</html", status_code=200)])
        with pytest.raises(ErrorSRI, match="no XML"):
            _cliente(sesion).autorizar(CLAVE)

    def test_error_de_red(self):
        class SesionQueFalla(SesionFalsa):
            def post(self, *args, **kwargs):
                import requests

                raise requests.ConnectionError("sin red")

        with pytest.raises(ErrorSRI, match="No se pudo contactar"):
            ClienteSRI(ambiente=Ambiente.PRUEBAS, session=SesionQueFalla([])).autorizar(CLAVE)


class TestEnviarYAutorizar:
    def test_flujo_completo(self):
        sesion = SesionFalsa([
            RespuestaFalsa(RESPUESTA_RECIBIDA.encode()),
            RespuestaFalsa(RESPUESTA_AUTORIZADO.encode()),
        ])
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        respuesta = cliente.enviar_y_autorizar("<factura/>", intentos=2, espera=0)
        assert respuesta.autorizada is True
        assert len(sesion.peticiones) == 2

    def test_devuelta_no_llega_a_autorizar(self):
        sesion = SesionFalsa([RespuestaFalsa(RESPUESTA_DEVUELTA.encode())])
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        with pytest.raises(ErrorRecepcion):
            cliente.enviar_y_autorizar("<factura/>", intentos=1, espera=0)
        assert len(sesion.peticiones) == 1

    def test_toma_la_clave_del_xml_si_no_se_indica(self):
        xml = '<?xml version="1.0"?><factura id="comprobante"><infoTributaria>' \
              f"<claveAcceso>{CLAVE}</claveAcceso></infoTributaria></factura>"
        sesion = SesionFalsa([
            RespuestaFalsa(RESPUESTA_RECIBIDA.replace(CLAVE, "N/A").encode()),
            RespuestaFalsa(RESPUESTA_AUTORIZADO.encode()),
        ])
        cliente = ClienteSRI(ambiente=Ambiente.PRUEBAS, session=sesion)
        respuesta = cliente.enviar_y_autorizar(xml, intentos=1, espera=0)
        assert respuesta.clave_acceso_consultada == CLAVE
