"""Validación de los seis comprobantes contra los XSD oficiales del SRI.

Estas pruebas se saltan automáticamente si no hay XSD disponibles; indique el
directorio con la variable de entorno ``SRI_XSD_DIR`` (por omisión ``/tmp/sri_xsd``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict

import pytest
from lxml import etree

from factec.comprobantes.factura import Factura
from factec.comprobantes.guia_remision import GuiaRemision
from factec.comprobantes.liquidacion_compra import LiquidacionCompra
from factec.comprobantes.nota_credito import NotaCredito
from factec.comprobantes.nota_debito import NotaDebito
from factec.comprobantes.retencion import ComprobanteRetencion
from factec.catalogos import MotivoTraslado, TarifaIva, TipoComprobante
from factec.modelos import (
    Destinatario,
    Detalle,
    DetalleGuia,
    Impuesto,
    Motivo,
)

FECHA = date(2026, 10, 8)
XSD = {
    "factura": "factura_V1.1.0.xsd",
    "nota_credito": "NotaCredito_V1.1.0.xsd",
    "nota_debito": "NotaDebito_V1.0.0.xsd",
    "retencion": "ComprobanteRetencion_V2.0.0.xsd",
    "guia_remision": "GuiaRemision_V1.1.0.xsd",
    "liquidacion_compra": "LiquidacionCompra_V1.1.0.xsd",
}


@pytest.fixture
def comprobantes(emisor, receptor, detalle, doc_sustento) -> Dict[str, Any]:
    comunes = dict(emisor=emisor, fecha_emision=FECHA)
    return {
        "factura": Factura(receptor=receptor, detalles=[detalle], secuencial="1", **comunes),
        "liquidacion_compra": LiquidacionCompra(
            proveedor=receptor, detalles=[detalle], secuencial="2", **comunes
        ),
        "nota_credito": NotaCredito(
            receptor=receptor, detalles=[detalle], motivo="Devolución",
            cod_doc_modificado="01", num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), secuencial="3", **comunes,
        ),
        "nota_debito": NotaDebito(
            receptor=receptor, motivos=[Motivo(razon="Intereses", valor=Decimal("50.00"))],
            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15,
                                base_imponible=Decimal("50.00"))],
            total_sin_impuestos=Decimal("50.00"), cod_doc_modificado="01",
            num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), secuencial="4", **comunes,
        ),
        "retencion": ComprobanteRetencion(
            sujeto_retenido=receptor, docs_sustento=[doc_sustento],
            periodo_fiscal=date(2026, 9, 1), secuencial="5", **comunes,
        ),
        "guia_remision": GuiaRemision(
            dir_partida="Quito", razon_social_transportista="TRANSPORTES DE PRUEBAS",
            ruc_transportista="1790012345001", placa="ABC1234",
            fecha_ini_transporte=FECHA, fecha_fin_transporte=date(2026, 10, 9),
            destinatarios=[Destinatario(
                razon_social="DESTINO", identificacion="0703886697001", direccion="Guayaquil",
                motivo_traslado=MotivoTraslado.VENTA,
                detalles=[DetalleGuia(descripcion="Caja", cantidad=1)])],
            secuencial="6", **comunes,
        ),
    }


@pytest.mark.parametrize("nombre", sorted(XSD))
def test_comprobante_valida_contra_xsd(nombre, comprobantes, esquemas):
    comprobante = comprobantes[nombre]
    esquema = esquemas(XSD[nombre])
    documento = etree.fromstring(comprobante.to_xml().encode("utf-8"))
    valido = esquema.validate(documento)
    assert valido, "\n".join(str(e) for e in esquema.error_log)


@pytest.mark.parametrize("nombre", sorted(XSD))
def test_raiz_y_version_correctas(nombre, comprobantes):
    comprobante = comprobantes[nombre]
    raiz = etree.fromstring(comprobante.to_xml().encode("utf-8"))
    assert raiz.tag == comprobante.ETIQUETA
    assert raiz.get("id") == "comprobante"
    assert raiz.get("version") == comprobante.VERSION
    assert raiz.find("infoTributaria") is not None
    assert raiz.findtext("infoTributaria/codDoc") == comprobante.TIPO


@pytest.mark.parametrize("nombre", sorted(XSD))
def test_clave_de_acceso_en_info_tributaria(nombre, comprobantes):
    from factec.clave_acceso import validar_clave_acceso

    comprobante = comprobantes[nombre]
    raiz = etree.fromstring(comprobante.to_xml().encode("utf-8"))
    clave = raiz.findtext("infoTributaria/claveAcceso")
    assert clave == comprobante.clave
    assert validar_clave_acceso(clave)


def test_orden_de_secciones_factura(comprobantes):
    raiz = etree.fromstring(comprobantes["factura"].to_xml().encode("utf-8"))
    assert [h.tag for h in raiz] == ["infoTributaria", "infoFactura", "detalles"]


def test_orden_de_secciones_retencion(comprobantes):
    raiz = etree.fromstring(comprobantes["retencion"].to_xml().encode("utf-8"))
    assert [h.tag for h in raiz] == ["infoTributaria", "infoCompRetencion", "docsSustento"]


def test_orden_de_secciones_guia(comprobantes):
    raiz = etree.fromstring(comprobantes["guia_remision"].to_xml().encode("utf-8"))
    assert [h.tag for h in raiz] == ["infoTributaria", "infoGuiaRemision", "destinatarios"]


def test_info_adicional_se_agrega_al_final(emisor, receptor, detalle):
    factura = Factura(
        emisor=emisor, receptor=receptor, detalles=[detalle], fecha_emision=FECHA,
        secuencial="9", info_adicional={"Email": "a@b.com"},
    )
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert [h.tag for h in raiz][-1] == "infoAdicional"
    assert raiz.find("infoAdicional/campoAdicional").get("nombre") == "Email"


def test_totales_de_la_factura_en_el_xml(emisor, receptor):
    factura = Factura(
        emisor=emisor, receptor=receptor, fecha_emision=FECHA, secuencial="1",
        detalles=[
            Detalle(descripcion="A", cantidad=2, precio_unitario=Decimal("100.00"),
                    descuento=Decimal("5.00"),
                    impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
        ],
    )
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert raiz.findtext("infoFactura/totalSinImpuestos") == "195.00"
    assert raiz.findtext("infoFactura/totalDescuento") == "5.00"
    assert raiz.findtext("infoFactura/importeTotal") == "224.25"
    total = raiz.find("infoFactura/totalConImpuestos/totalImpuesto")
    assert total.findtext("codigo") == "2"
    assert total.findtext("codigoPorcentaje") == "4"
    assert total.findtext("baseImponible") == "195.00"
    assert total.findtext("valor") == "29.25"


def test_cantidad_y_precio_con_seis_decimales(emisor, receptor):
    factura = Factura(
        emisor=emisor, receptor=receptor, fecha_emision=FECHA, secuencial="1",
        detalles=[Detalle(descripcion="A", cantidad=Decimal("1.5"),
                          precio_unitario=Decimal("0.333333"),
                          impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])],
    )
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert raiz.findtext("detalles/detalle/cantidad") == "1.500000"
    assert raiz.findtext("detalles/detalle/precioUnitario") == "0.333333"


def test_pago_por_defecto_igual_al_importe(emisor, receptor, detalle):
    factura = Factura(emisor=emisor, receptor=receptor, detalles=[detalle],
                      fecha_emision=FECHA, secuencial="1")
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert raiz.findtext("infoFactura/pagos/pago/total") == raiz.findtext("infoFactura/importeTotal")
    assert raiz.findtext("infoFactura/pagos/pago/formaPago") == "01"


def test_rimpe_negocio_popular_valida_contra_xsd(emisor, receptor, detalle, esquemas):
    """El valor de ``contribuyenteRimpe`` debe cumplir el patrón exacto del XSD."""
    from dataclasses import replace

    emisor_rimpe = replace(
        emisor,
        contribuyente_rimpe=True,
        regimen="NEGOCIO POPULAR",
        contribuyente_especial="12345",
        agente_retencion="123",
    )
    factura = Factura(emisor=emisor_rimpe, receptor=receptor, detalles=[detalle],
                      fecha_emision=FECHA, secuencial="1")
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert raiz.findtext("infoTributaria/contribuyenteRimpe") == (
        "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"
    )
    esquema = esquemas("factura_V1.1.0.xsd")
    assert esquema.validate(raiz), "\n".join(str(e) for e in esquema.error_log)


def test_rimpe_general_valida_contra_xsd(emisor, receptor, detalle, esquemas):
    from dataclasses import replace

    factura = Factura(
        emisor=replace(emisor, contribuyente_rimpe=True), receptor=receptor,
        detalles=[detalle], fecha_emision=FECHA, secuencial="1",
    )
    raiz = etree.fromstring(factura.to_xml().encode("utf-8"))
    assert raiz.findtext("infoTributaria/contribuyenteRimpe") == "CONTRIBUYENTE RÉGIMEN RIMPE"
    esquema = esquemas("factura_V1.1.0.xsd")
    assert esquema.validate(raiz), "\n".join(str(e) for e in esquema.error_log)


def _normalizar(xml: bytes) -> bytes:
    """Serializa ignorando el sangrado (que la impresión bonita sí añade)."""
    raiz = etree.fromstring(xml)
    for elemento in raiz.iter():
        if elemento.text is not None and not elemento.text.strip():
            elemento.text = None
        if elemento.tail is not None and not elemento.tail.strip():
            elemento.tail = None
    return etree.tostring(raiz)


def test_pretty_print_no_cambia_el_contenido(comprobantes):
    compacto = comprobantes["factura"].to_xml()
    bonito = comprobantes["factura"].to_xml(pretty=True)
    assert _normalizar(compacto.encode("utf-8")) == _normalizar(bonito.encode("utf-8"))
