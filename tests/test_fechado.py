"""Pruebas de cambiar la fecha de emisión de un comprobante ya construido.

Se usa cuando un comprobante quedó preparado y se firma otro día: el SRI solo
admite la fecha del día de la firma (o de los 90 días anteriores).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from factec.catalogos import TarifaIva, TipoIdentificacion
from factec.clave_acceso import descomponer_clave_acceso, validar_clave_acceso
from factec.comprobantes import Factura
from factec.excepciones import ErrorValidacion
from factec.fechado import cambiar_fecha_de_emision
from factec.firma import Certificado, firmar_xml
from factec.modelos import Detalle, Emisor, Impuesto, Receptor

from conftest import CLAVE_CERTIFICADO, RUC, _crear_p12

FECHA = date(2026, 10, 1)
HOY = date(2026, 10, 8)
RUC_EMISOR = "1790012345001"


def _factura(fecha: date = FECHA, secuencial: str = "1") -> Factura:
    return Factura(
        emisor=Emisor(ruc=RUC_EMISOR, razon_social="ACME S.A.", dir_matriz="QUITO"),
        receptor=Receptor(
            razon_social="CLIENTE",
            identificacion="1712345678",
            tipo_identificacion=TipoIdentificacion.CEDULA,
            direccion="QUITO",
        ),
        detalles=[
            Detalle(
                descripcion="Servicio", cantidad=1, precio_unitario=Decimal("100.00"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
        fecha_emision=fecha,
        secuencial=secuencial,
    )


@pytest.fixture
def certificado() -> Certificado:
    return Certificado.desde_bytes(_crear_p12(RUC), CLAVE_CERTIFICADO)


def test_cambia_la_fecha_y_recalcula_la_clave():
    factura = _factura()
    clave_vieja = factura.clave

    cambiado = cambiar_fecha_de_emision(factura.to_xml(), HOY)

    assert cambiado.fecha_anterior == FECHA
    assert cambiado.fecha == HOY
    assert cambiado.clave_anterior == clave_vieja
    assert cambiado.clave_acceso != clave_vieja
    assert validar_clave_acceso(cambiado.clave_acceso)
    assert cambiado.clave_acceso[:8] == "08102026"
    assert "<fechaEmision>08/10/2026</fechaEmision>" in cambiado.xml
    assert "01/10/2026" not in cambiado.xml


def test_conserva_el_resto_del_comprobante():
    """Solo cambian la fecha y la clave: el número y el código son los mismos."""
    original = _factura(secuencial="7")
    cambiado = cambiar_fecha_de_emision(original.to_xml(), HOY)

    antes = descomponer_clave_acceso(original.clave)
    despues = descomponer_clave_acceso(cambiado.clave_acceso)

    assert antes["codigo_numerico"] == despues["codigo_numerico"]
    assert antes["secuencial"] == despues["secuencial"] == "000000007"
    assert antes["ruc"] == despues["ruc"] == RUC_EMISOR
    assert "<secuencial>000000007</secuencial>" in cambiado.xml
    assert "100.00" in cambiado.xml


def test_con_la_misma_fecha_no_cambia_nada():
    xml = _factura().to_xml()

    cambiado = cambiar_fecha_de_emision(xml, FECHA)

    assert cambiado.xml == xml
    assert cambiado.clave_acceso == cambiado.clave_anterior


@pytest.mark.parametrize("fecha", [HOY, "08/10/2026", "2026-10-08", datetime(2026, 10, 8, 15, 0)])
def test_admite_varios_formatos_de_fecha(fecha):
    cambiado = cambiar_fecha_de_emision(_factura().to_xml(), fecha)

    assert cambiado.fecha == HOY
    assert "<fechaEmision>08/10/2026</fechaEmision>" in cambiado.xml


def test_el_xml_resultante_sigue_siendo_firmable(certificado):
    cambiado = cambiar_fecha_de_emision(_factura().to_xml(), HOY)

    firmado = firmar_xml(cambiado.xml, certificado)

    assert "<ds:Signature" in firmado
    assert cambiado.clave_acceso in firmado


def test_se_puede_indicar_el_codigo_numerico():
    cambiado = cambiar_fecha_de_emision(
        _factura().to_xml(), HOY, codigo_numerico="99999999"
    )

    assert descomponer_clave_acceso(cambiado.clave_acceso)["codigo_numerico"] == "99999999"


def test_un_xml_firmado_no_se_puede_refechar(certificado):
    firmado = firmar_xml(_factura().to_xml(), certificado)

    with pytest.raises(ErrorValidacion, match="ya está firmado"):
        cambiar_fecha_de_emision(firmado, HOY)


def test_un_xml_que_no_es_un_comprobante():
    with pytest.raises(ErrorValidacion, match="claveAcceso"):
        cambiar_fecha_de_emision("<hola/>", HOY)


def test_sin_xml():
    with pytest.raises(ErrorValidacion, match="No hay XML"):
        cambiar_fecha_de_emision("", HOY)


def test_el_resultado_se_puede_pasar_a_diccionario():
    datos = cambiar_fecha_de_emision(_factura().to_xml(), HOY).a_dict()

    assert datos["fecha"] == HOY.isoformat()
    assert datos["fecha_anterior"] == FECHA.isoformat()
    assert len(datos["clave_acceso"]) == 49
