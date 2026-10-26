"""Las retenciones de IVA e ISD, con los códigos de la tabla del SRI.

Los códigos no siguen el orden del porcentaje —el 9 es el 10 %, el 10 el 20 %, el 1
el 30 %…—, así que un mapeo al revés retiene de menos o de más sin que el XML se
queje. Aquí se prueba lo que responde el SRI y lo que se avisa antes de firmar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

import pytest
from lxml import etree

from factec.catalogos import TarifaRetencionIva
from factec.comprobantes.retencion import ComprobanteRetencion
from factec.excepciones import ErrorValidacion
from factec.modelos import (
    DocSustento,
    ImpuestoDocSustento,
    ImpuestoRetencion,
    PagoRetencion,
)

FECHA = date(2026, 10, 26)


def _comprobante(
    emisor: Any,
    receptor: Any,
    codigo: Any,
    *,
    impuesto: str = "2",
    porcentaje: Optional[Decimal] = None,
) -> ComprobanteRetencion:
    """Una retención con el código puesto y, si se quiere, el porcentaje dicho."""
    sustento = DocSustento(
        cod_sustento="01",
        cod_doc_sustento="01",
        num_doc_sustento="001001000000039",
        fecha_emision=FECHA,
        total_sin_impuestos=Decimal("100.00"),
        importe_total=Decimal("115.00"),
        impuestos=[
            ImpuestoDocSustento(
                codigo="2",
                codigo_porcentaje="4",
                base_imponible=Decimal("100.00"),
                tarifa=Decimal("15.00"),
                valor=Decimal("15.00"),
            )
        ],
        retenciones=[
            ImpuestoRetencion(
                codigo=impuesto,
                codigo_retencion=codigo,
                base_imponible=Decimal("100.00"),
                porcentaje_retener=porcentaje,
                valor_retenido=None,
            )
        ],
        pagos=[PagoRetencion(forma_pago="01", total=Decimal("115.00"))],
    )
    return ComprobanteRetencion(
        emisor=emisor,
        sujeto_retenido=receptor,
        docs_sustento=[sustento],
        periodo_fiscal=FECHA,
        secuencial="1",
        fecha_emision=FECHA,
    )


@pytest.mark.parametrize(
    "codigo, porcentaje, valor",
    [
        (TarifaRetencionIva.RETENCION_10, "10.00", "10.00"),
        (TarifaRetencionIva.RETENCION_20, "20.00", "20.00"),
        (TarifaRetencionIva.RETENCION_30, "30.00", "30.00"),
        (TarifaRetencionIva.RETENCION_50, "50.00", "50.00"),
        (TarifaRetencionIva.RETENCION_70, "70.00", "70.00"),
        (TarifaRetencionIva.RETENCION_100, "100.00", "100.00"),
        (TarifaRetencionIva.RETENCION_CERO, "0.00", "0.00"),
        (TarifaRetencionIva.NO_PROCEDE, "0.00", "0.00"),
    ],
)
def test_el_porcentaje_de_iva_sale_del_codigo(
    codigo: TarifaRetencionIva, porcentaje: str, valor: str, emisor: Any, receptor: Any
) -> None:
    raiz = etree.fromstring(_comprobante(emisor, receptor, codigo).to_xml().encode("utf-8"))
    retencion = raiz.find("docsSustento/docSustento/retenciones/retencion")

    assert retencion is not None
    assert retencion.findtext("codigoRetencion") == codigo.value
    assert retencion.findtext("porcentajeRetener") == porcentaje
    assert retencion.findtext("valorRetenido") == valor


def test_el_isd_va_con_su_codigo_y_su_cinco_por_ciento(emisor: Any, receptor: Any) -> None:
    raiz = etree.fromstring(
        _comprobante(emisor, receptor, "4580", impuesto="6").to_xml().encode("utf-8")
    )
    retencion = raiz.find("docsSustento/docSustento/retenciones/retencion")

    assert retencion is not None
    assert retencion.findtext("codigo") == "6"
    assert retencion.findtext("codigoRetencion") == "4580"
    assert retencion.findtext("porcentajeRetener") == "5.00"


def test_un_codigo_que_no_existe_se_avisa_antes_de_firmar(emisor: Any, receptor: Any) -> None:
    """El SRI devuelve el comprobante entero; mejor decirlo aquí y con la lista."""
    with pytest.raises(ErrorValidacion) as error:
        _comprobante(emisor, receptor, "725").to_xml()

    mensaje = str(error.value)
    assert "725" in mensaje
    assert "9 (10 %)" in mensaje
    assert "11 (50 %)" in mensaje


def test_el_porcentaje_tiene_que_ser_el_del_codigo(emisor: Any, receptor: Any) -> None:
    """El 9 es el 10 %: pedirle 30 % es retener de más."""
    with pytest.raises(ErrorValidacion) as error:
        _comprobante(
            emisor, receptor, TarifaRetencionIva.RETENCION_10, porcentaje=Decimal("30")
        ).to_xml()

    mensaje = str(error.value)
    assert "10 %" in mensaje
    assert "30" in mensaje


def test_el_isd_no_admite_otro_codigo(emisor: Any, receptor: Any) -> None:
    with pytest.raises(ErrorValidacion) as error:
        _comprobante(emisor, receptor, "725", impuesto="6").to_xml()

    assert "4580 (5 %)" in str(error.value)
