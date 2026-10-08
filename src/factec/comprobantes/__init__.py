"""Generadores de XML de los seis comprobantes electrónicos del SRI."""

from __future__ import annotations

from ._comun import (
    CODIGOS_DETALLE_FACTURA,
    CODIGOS_DETALLE_NOTA_CREDITO,
    anexar_pagos,
    calcular_totales,
    elemento_compensaciones,
    elemento_detalles,
    elemento_impuestos,
    elemento_motivos,
    elemento_reembolsos,
    elemento_total_con_impuestos,
)
from .base import (
    DECLARACION_XML,
    Comprobante,
    agregar,
    agregar_texto,
    crear,
    formatear_decimal,
    formatear_fecha,
    formatear_fecha_hora,
)
from .factura import DetalleFactura, Factura
from .guia_remision import GuiaRemision
from .liquidacion_compra import LiquidacionCompra
from .nota_credito import NotaCredito
from .nota_debito import NotaDebito
from .retencion import ComprobanteRetencion, calcular_valor_retenido

__all__ = [
    "Comprobante",
    "Factura",
    "DetalleFactura",
    "NotaCredito",
    "NotaDebito",
    "ComprobanteRetencion",
    "GuiaRemision",
    "LiquidacionCompra",
    "calcular_totales",
    "calcular_valor_retenido",
    "DECLARACION_XML",
    "crear",
    "agregar",
    "agregar_texto",
    "formatear_decimal",
    "formatear_fecha",
    "formatear_fecha_hora",
    "anexar_pagos",
    "elemento_compensaciones",
    "elemento_detalles",
    "elemento_impuestos",
    "elemento_motivos",
    "elemento_reembolsos",
    "elemento_total_con_impuestos",
    "CODIGOS_DETALLE_FACTURA",
    "CODIGOS_DETALLE_NOTA_CREDITO",
]
