"""El porcentaje de las retenciones de IVA sale del código de la tabla 20 del SRI.

Los códigos no siguen el orden del porcentaje —el 9 es el 10 %, el 10 el 20 %, el 1
el 30 %…—, así que un mapeo al revés retiene de menos o de más sin que el XML se
queje. Aquí se prueba contra lo que responde el SRI.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from lxml import etree

from factec.catalogos import TarifaRetencionIva
from factec.comprobantes.retencion import ComprobanteRetencion
from factec.modelos import (
    DocSustento,
    ImpuestoDocSustento,
    ImpuestoRetencion,
    PagoRetencion,
)

FECHA = date(2026, 10, 26)


def _comprobante(emisor: Any, receptor: Any, codigo: TarifaRetencionIva) -> ComprobanteRetencion:
    """Una retención de IVA con el código puesto y el porcentaje sin decir."""
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
                codigo="2",
                codigo_retencion=codigo,
                base_imponible=Decimal("100.00"),
                porcentaje_retener=None,
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
