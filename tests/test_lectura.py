"""Lectura de comprobantes: los propios y los de terceros."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from factec.catalogos import TarifaIva, TipoIdentificacion
from factec.comprobantes import Factura
from factec.excepciones import ErrorValidacion
from factec.lectura import (
    es_comprobante,
    leer_autorizacion,
    leer_comprobante,
    tipo_del_xml,
)
from factec.modelos import Detalle, Emisor, Impuesto, Receptor

FECHA = date(2026, 9, 15)


def _factura(**cambios):
    datos = dict(
        emisor=Emisor(
            ruc="1790012345001",
            razon_social="ACME S.A.",
            nombre_comercial="ACME",
            dir_matriz="Av. Amazonas 123, Quito",
            dir_establecimiento="Av. Amazonas 123, Quito",
            obligado_contabilidad=True,
        ),
        ambiente=1,
        fecha_emision=FECHA,
        secuencial="42",
        info_adicional={"VENDEDOR": "JOHNNY"},
        receptor=Receptor(
            identificacion="0703886697001",
            razon_social="CLIENTE EJEMPLO",
            tipo_identificacion=TipoIdentificacion.RUC,
            direccion="Guayaquil",
        ),
        detalles=[
            Detalle(
                descripcion="Servicio de desarrollo",
                cantidad=Decimal("2"),
                precio_unitario=Decimal("100.00"),
                codigo_principal="SRV001",
                codigo_auxiliar="001",
                unidad_medida="HORA",
                detalles_adicionales={"MARCA": "ACME"},
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
    )
    datos.update(cambios)
    return Factura(**datos)


class TestLeerComprobante:
    def test_reconoce_el_comprobante(self):
        xml = _factura().to_xml()

        assert es_comprobante(xml)
        assert tipo_del_xml(xml) == "01"

    def test_extrae_los_datos_de_la_factura(self):
        leido = leer_comprobante(_factura().to_xml())

        assert leido.tipo == "01"
        assert leido.descripcion_tipo == "Factura"
        assert leido.numero == "001-001-000000042"
        assert leido.fecha_emision == FECHA
        assert leido.ambiente == "1"
        assert leido.moneda == "DOLAR"

        assert leido.emisor.ruc == "1790012345001"
        assert leido.emisor.razon_social == "ACME S.A."
        assert leido.emisor.obligado_contabilidad is True
        assert leido.emisor.estab == "001"

        assert leido.receptor.identificacion == "0703886697001"
        assert leido.receptor.razon_social == "CLIENTE EJEMPLO"
        assert leido.receptor.tipo_identificacion == "04"
        assert leido.receptor.direccion == "Guayaquil"
        assert leido.receptor.es_consumidor_final is False

        assert leido.totales.subtotal == Decimal("200.00")
        assert leido.totales.valor_impuestos == Decimal("30.00")
        assert leido.totales.importe_total == Decimal("230.00")
        assert leido.totales.impuestos[0].codigo_porcentaje == "4"
        assert leido.totales.impuestos[0].tarifa == Decimal("15.00")

        assert leido.info_adicional == {"VENDEDOR": "JOHNNY"}

    def test_extrae_las_lineas(self):
        leido = leer_comprobante(_factura().to_xml())

        assert len(leido.detalles) == 1
        detalle = leido.detalles[0]
        assert detalle.descripcion == "Servicio de desarrollo"
        assert detalle.cantidad == Decimal("2.000000")
        assert detalle.precio_unitario == Decimal("100.000000")
        assert detalle.precio_total_sin_impuesto == Decimal("200.00")
        assert detalle.codigo_principal == "SRV001"
        assert detalle.codigo_auxiliar == "001"
        assert detalle.unidad_medida == "HORA"
        assert detalle.datos_adicionales == {"MARCA": "ACME"}
        assert detalle.impuestos[0].valor == Decimal("30.00")

    def test_el_consumidor_final_se_reconoce(self):
        factura = _factura(
            receptor=Receptor(
                identificacion="9999999999999",
                razon_social="CONSUMIDOR FINAL",
                tipo_identificacion=TipoIdentificacion.CONSUMIDOR_FINAL,
            )
        )
        assert leer_comprobante(factura.to_xml()).receptor.es_consumidor_final is True

    def test_lee_los_pagos(self):
        leido = leer_comprobante(_factura().to_xml())

        assert leido.pagos == [
            {"forma_pago": "01", "total": "230.00", "plazo": "", "unidad_tiempo": ""}
        ]

    def test_a_dict_es_serializable(self):
        import json

        datos = leer_comprobante(_factura().to_xml()).a_dict()

        texto = json.dumps(datos)          # no debe fallar con Decimal ni date
        assert '"importe_total": "230.00"' in texto
        assert '"fecha_emision": "2026-09-15"' in texto
        assert datos["detalles"][0]["codigo_auxiliar"] == "001"

    def test_admite_bytes_y_el_espacio_de_nombres_del_sri(self):
        xml = _factura().to_xml()
        assert leer_comprobante(xml.encode("utf-8")).numero.endswith("042")

        con_prefijos = xml.replace("<factura ", '<ns2:factura xmlns:ns2="http://x" ')
        con_prefijos = con_prefijos.replace("</factura>", "</ns2:factura>")
        assert leer_comprobante(con_prefijos).tipo == "01"

    def test_un_xml_ajeno_no_es_comprobante(self):
        assert es_comprobante("<html><body>hola</body></html>") is False
        with pytest.raises(ErrorValidacion, match="infoTributaria"):
            leer_comprobante("<html><body>hola</body></html>")

    def test_sin_xml_avisa(self):
        with pytest.raises(ErrorValidacion, match="No hay XML"):
            leer_comprobante("")


class TestLeerAutorizacion:
    def _respuesta(self, firmado: str, estado: str = "AUTORIZADO") -> str:
        from xml.sax.saxutils import escape

        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            '<ns2:RespuestaAutorizacionComprobante xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
            "<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>"
            f"<estado>{estado}</estado>"
            "<numeroAutorizacion>0810202601179001234500110010010000000421234567819"
            "</numeroAutorizacion>"
            "<fechaAutorizacion>2026-09-15T10:30:00-05:00</fechaAutorizacion>"
            "<ambiente>PRUEBAS</ambiente>"
            f"<comprobante>{escape(firmado)}</comprobante>"
            "<mensajes><mensaje><identificador>35</identificador>"
            "<mensaje>OK</mensaje><tipo>INFORMATIVO</tipo></mensaje></mensajes>"
            "</autorizacion></autorizaciones></ns2:RespuestaAutorizacionComprobante>"
            "</soap:Body></soap:Envelope>"
        )

    def test_extrae_el_estado_y_el_comprobante(self):
        firmado = _factura().firmar_para_prueba() if hasattr(Factura, "firmar_para_prueba") else _factura().to_xml()

        autorizacion = leer_autorizacion(self._respuesta(firmado))

        assert autorizacion.autorizada is True
        assert autorizacion.estado == "AUTORIZADO"
        assert autorizacion.fecha_autorizacion is not None
        assert autorizacion.mensajes[0]["mensaje"] == "OK"
        assert autorizacion.comprobante is not None
        assert autorizacion.comprobante.numero == "001-001-000000042"
        assert autorizacion.clave_acceso == autorizacion.comprobante.clave_acceso

    def test_una_respuesta_sin_autorizaciones(self):
        xml = (
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            "<RespuestaAutorizacionComprobante><numeroComprobantes>0</numeroComprobantes>"
            "</RespuestaAutorizacionComprobante></soap:Body></soap:Envelope>"
        )
        with pytest.raises(ErrorValidacion, match="autorización"):
            leer_autorizacion(xml)

    def test_a_dict(self):
        autorizacion = leer_autorizacion(self._respuesta(_factura().to_xml()))
        datos = autorizacion.a_dict()

        assert datos["autorizada"] is True
        assert datos["comprobante"]["tipo"] == "01"
