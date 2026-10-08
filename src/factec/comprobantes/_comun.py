"""Secciones XML compartidas por varios comprobantes.

Factura, nota de crédito, nota de débito y liquidación de compra repiten buena
parte de su estructura (``detalles``, ``totalConImpuestos``, ``pagos``,
``compensaciones``). Aquí se implementan una sola vez, parametrizando las
diferencias de nombres y de obligatoriedad que fijan los esquemas del SRI.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple
from decimal import Decimal

from ..excepciones import ErrorValidacion
from ..modelos import (
    Compensacion,
    Detalle,
    Motivo,
    Pago,
    Reembolso,
    TotalImpuesto,
    a_decimal,
    cuantizar,
)
from .base import Elemento, agregar, agregar_texto, crear, formatear_decimal

__all__ = [
    "calcular_totales",
    "elemento_total_con_impuestos",
    "elemento_detalles",
    "elemento_impuestos",
    "anexar_pagos",
    "elemento_compensaciones",
    "elemento_motivos",
    "elemento_reembolsos",
    "elemento_impuestos_simples",
    "CODIGOS_DETALLE_FACTURA",
    "CODIGOS_DETALLE_NOTA_CREDITO",
]

#: Nombres de los elementos de código en el detalle, según el comprobante.
CODIGOS_DETALLE_FACTURA: Tuple[str, str] = ("codigoPrincipal", "codigoAuxiliar")
CODIGOS_DETALLE_NOTA_CREDITO: Tuple[str, str] = ("codigoInterno", "codigoAdicional")


def calcular_totales(detalles: Sequence[Detalle]) -> dict:
    """Calcula los totales de un comprobante de venta a partir de sus detalles.

    Devuelve ``total_sin_impuestos``, ``total_descuento``, ``total_impuestos``,
    ``importe_total`` y ``total_con_impuestos`` (los impuestos agrupados por
    ``(codigo, codigoPorcentaje)``, que es lo que exige el esquema del SRI).
    """
    total_sin_impuestos = Decimal("0")
    total_descuento = Decimal("0")
    total_impuestos = Decimal("0")
    grupos: dict = {}

    for indice, detalle in enumerate(detalles, start=1):
        try:
            detalle.validar()
        except ErrorValidacion as exc:
            raise ErrorValidacion(f"Detalle {indice}: {exc}") from exc
        total_sin_impuestos += detalle.precio_total_sin_impuesto
        total_descuento += cuantizar(a_decimal(detalle.descuento), 2)
        for impuesto in detalle.impuestos_resueltos():
            clave = (impuesto.codigo_valor(), impuesto.porcentaje_valor())
            grupo = grupos.setdefault(
                clave,
                {
                    "codigo": clave[0],
                    "codigo_porcentaje": clave[1],
                    "base": Decimal("0"),
                    "valor": Decimal("0"),
                    "tarifa": impuesto.tarifa,
                    "devolucion": Decimal("0"),
                },
            )
            grupo["base"] += a_decimal(impuesto.base_imponible)
            grupo["valor"] += a_decimal(impuesto.valor)
            if impuesto.valor_devolucion_iva is not None:
                grupo["devolucion"] += a_decimal(impuesto.valor_devolucion_iva)
            total_impuestos += a_decimal(impuesto.valor)

    totales_impuestos = [
        TotalImpuesto(
            codigo=grupo["codigo"],
            codigo_porcentaje=grupo["codigo_porcentaje"],
            base_imponible=cuantizar(grupo["base"], 2),
            valor=cuantizar(grupo["valor"], 2),
            tarifa=grupo["tarifa"],
            valor_devolucion_iva=cuantizar(grupo["devolucion"], 2)
            if grupo["devolucion"]
            else None,
        )
        for grupo in grupos.values()
    ]
    totales_impuestos.sort(key=lambda t: (t.codigo, t.codigo_porcentaje))

    total_sin_impuestos = cuantizar(total_sin_impuestos, 2)
    total_descuento = cuantizar(total_descuento, 2)
    total_impuestos = cuantizar(total_impuestos, 2)
    return {
        "total_sin_impuestos": total_sin_impuestos,
        "total_descuento": total_descuento,
        "total_con_impuestos": totales_impuestos,
        "total_impuestos": total_impuestos,
        "importe_total": cuantizar(total_sin_impuestos + total_impuestos, 2),
    }


def elemento_total_con_impuestos(
    totales: Sequence[TotalImpuesto],
    *,
    con_tarifa: bool = True,
    con_descuento_adicional: bool = False,
    con_valor_devolucion_iva: bool = False,
) -> Elemento:
    """Construye ``<totalConImpuestos>``.

    El orden de los hijos cambia entre esquemas: la factura admite
    ``tarifa``/``descuentoAdicional`` y la nota de crédito coloca ``valor``
    antes de ``valorDevolucionIva``.
    """
    contenedor = crear("totalConImpuestos")
    for total in totales:
        nodo = agregar(contenedor, "totalImpuesto")
        agregar_texto(nodo, "codigo", total.codigo)
        agregar_texto(nodo, "codigoPorcentaje", total.codigo_porcentaje)
        if con_descuento_adicional and total.descuento_adicional:
            agregar_texto(nodo, "descuentoAdicional", formatear_decimal(total.descuento_adicional))
        agregar_texto(nodo, "baseImponible", formatear_decimal(total.base_imponible))
        if con_tarifa and total.tarifa is not None:
            agregar_texto(nodo, "tarifa", formatear_decimal(total.tarifa))
        agregar_texto(nodo, "valor", formatear_decimal(total.valor))
        if con_valor_devolucion_iva and total.valor_devolucion_iva is not None:
            agregar_texto(
                nodo, "valorDevolucionIva", formatear_decimal(total.valor_devolucion_iva)
            )
    return contenedor


def elemento_impuestos(
    impuestos: Iterable[Any],
    *,
    con_tarifa: bool = True,
    con_valor_devolucion_iva: bool = False,
) -> Elemento:
    """Construye ``<impuestos>`` de un detalle."""
    contenedor = crear("impuestos")
    for impuesto in impuestos:
        nodo = agregar(contenedor, "impuesto")
        agregar_texto(nodo, "codigo", impuesto.codigo_valor())
        agregar_texto(nodo, "codigoPorcentaje", impuesto.porcentaje_valor())
        if con_tarifa and impuesto.tarifa is not None:
            agregar_texto(nodo, "tarifa", formatear_decimal(impuesto.tarifa))
        agregar_texto(nodo, "baseImponible", formatear_decimal(impuesto.base_imponible))
        agregar_texto(nodo, "valor", formatear_decimal(impuesto.valor))
    return contenedor


def elemento_impuestos_simples(
    impuestos: Iterable[Any],
    *,
    con_valor_devolucion_iva: bool = False,
) -> Elemento:
    """Construye ``<impuestos>`` cuando los impuestos van directos (nota de débito)."""
    contenedor = crear("impuestos")
    for impuesto in impuestos:
        nodo = agregar(contenedor, "impuesto")
        agregar_texto(nodo, "codigo", impuesto.codigo_valor())
        agregar_texto(nodo, "codigoPorcentaje", impuesto.porcentaje_valor())
        if impuesto.tarifa is not None:
            agregar_texto(nodo, "tarifa", formatear_decimal(impuesto.tarifa))
        agregar_texto(nodo, "baseImponible", formatear_decimal(impuesto.base_imponible))
        agregar_texto(nodo, "valor", formatear_decimal(impuesto.valor))
        if con_valor_devolucion_iva and getattr(impuesto, "valor_devolucion_iva", None):
            agregar_texto(
                nodo, "valorDevolucionIva", formatear_decimal(impuesto.valor_devolucion_iva)
            )
    return contenedor


def elemento_detalles(
    detalles: Sequence[Detalle],
    *,
    codigos: Tuple[str, str] = CODIGOS_DETALLE_FACTURA,
    con_unidad_medida: bool = True,
    con_tarifa: bool = True,
    descuento_opcional: bool = False,
) -> Elemento:
    """Construye la sección ``<detalles>`` de factura, liquidación o nota de crédito."""
    contenedor = crear("detalles")
    codigo_a, codigo_b = codigos
    for detalle in detalles:
        nodo = agregar(contenedor, "detalle")
        agregar_texto(nodo, codigo_a, detalle.codigo_principal)
        agregar_texto(nodo, codigo_b, detalle.codigo_auxiliar)
        agregar_texto(nodo, "descripcion", detalle.descripcion)
        if con_unidad_medida:
            agregar_texto(nodo, "unidadMedida", detalle.unidad_medida)
        agregar_texto(nodo, "cantidad", formatear_decimal(detalle.cantidad, 6))
        agregar_texto(nodo, "precioUnitario", formatear_decimal(detalle.precio_unitario, 6))
        descuento = a_decimal(detalle.descuento)
        if descuento or not descuento_opcional:
            agregar_texto(nodo, "descuento", formatear_decimal(descuento))
        agregar_texto(
            nodo, "precioTotalSinImpuesto", formatear_decimal(detalle.precio_total_sin_impuesto)
        )
        if detalle.detalles_adicionales:
            adicionales = agregar(nodo, "detallesAdicionales")
            for nombre, valor in detalle.detalles_adicionales.items():
                agregar(adicionales, "detAdicional", nombre=nombre, valor=valor)
        if detalle.impuestos:
            nodo.append(elemento_impuestos(detalle.impuestos_resueltos(), con_tarifa=con_tarifa))
    return contenedor


def anexar_pagos(
    padre: Elemento,
    pagos: Sequence[Pago],
    *,
    con_plazo: bool = True,
) -> Elemento:
    """Añade ``<pagos>`` con sus ``<pago>`` a ``padre``."""
    nodo_pagos = crear("pagos")
    for pago in pagos:
        nodo = agregar(nodo_pagos, "pago")
        agregar_texto(nodo, "formaPago", pago.forma_valor())
        agregar_texto(nodo, "total", formatear_decimal(pago.total))
        if con_plazo and pago.plazo is not None:
            agregar_texto(nodo, "plazo", formatear_decimal(pago.plazo))
        if con_plazo:
            agregar_texto(nodo, "unidadTiempo", pago.unidad_tiempo)
    padre.append(nodo_pagos)
    return nodo_pagos


def elemento_compensaciones(compensaciones: Sequence[Compensacion]) -> Elemento:
    """Construye ``<compensaciones>``."""
    contenedor = crear("compensaciones")
    for compensacion in compensaciones:
        nodo = agregar(contenedor, "compensacion")
        agregar_texto(nodo, "codigo", compensacion.codigo)
        agregar_texto(nodo, "tarifa", formatear_decimal(compensacion.tarifa))
        agregar_texto(nodo, "valor", formatear_decimal(compensacion.valor))
    return contenedor


def elemento_motivos(motivos: Sequence[Motivo]) -> Elemento:
    """Construye ``<motivos>`` de una nota de débito."""
    contenedor = crear("motivos")
    for motivo in motivos:
        nodo = agregar(contenedor, "motivo")
        agregar_texto(nodo, "razon", motivo.razon)
        agregar_texto(nodo, "valor", formatear_decimal(motivo.valor))
    return contenedor


def elemento_reembolsos(reembolsos: Sequence[Reembolso]) -> Optional[Elemento]:
    """Construye ``<reembolsos>`` si hay detalles de reembolso."""
    detalles: List[Any] = []
    for reembolso in reembolsos:
        detalles.extend(reembolso.detalles)
    if not detalles:
        return None
    contenedor = crear("reembolsos")
    for detalle in detalles:
        nodo = agregar(contenedor, "reembolsoDetalle")
        agregar_texto(
            nodo, "tipoIdentificacionProveedorReembolso", detalle.tipo_identificacion_proveedor
        )
        agregar_texto(nodo, "identificacionProveedorReembolso", detalle.identificacion_proveedor)
        if detalle.cod_pais_pago_proveedor:
            agregar_texto(nodo, "codPaisPagoProveedorReembolso", detalle.cod_pais_pago_proveedor)
        agregar_texto(nodo, "tipoProveedorReembolso", detalle.tipo_proveedor)
        agregar_texto(nodo, "codDocReembolso", detalle.cod_doc_reembolso)
        agregar_texto(nodo, "estabDocReembolso", detalle.estab_doc_reembolso)
        agregar_texto(nodo, "ptoEmiDocReembolso", detalle.pto_emi_doc_reembolso)
        agregar_texto(nodo, "secuencialDocReembolso", detalle.secuencial_doc_reembolso)
        agregar_texto(
            nodo,
            "fechaEmisionDocReembolso",
            _fecha(detalle.fecha_emision_doc_reembolso),
        )
        agregar_texto(nodo, "numeroautorizacionDocReemb", detalle.numero_autorizacion)
        impuestos = agregar(nodo, "detalleImpuestos")
        for impuesto in detalle.impuestos:
            nodo_impuesto = agregar(impuestos, "detalleImpuesto")
            agregar_texto(nodo_impuesto, "codigo", impuesto.codigo)
            agregar_texto(nodo_impuesto, "codigoPorcentaje", impuesto.codigo_porcentaje)
            agregar_texto(nodo_impuesto, "tarifa", formatear_decimal(impuesto.tarifa))
            agregar_texto(
                nodo_impuesto,
                "baseImponibleReembolso",
                formatear_decimal(impuesto.base_imponible_reembolso),
            )
            agregar_texto(
                nodo_impuesto, "impuestoReembolso", formatear_decimal(impuesto.impuesto_reembolso)
            )
        compensaciones = getattr(detalle, "compensaciones", None)
        if compensaciones:
            nodo_compensaciones = agregar(nodo, "compensacionesReembolso")
            for compensacion in compensaciones:
                sub = agregar(nodo_compensaciones, "compensacionReembolso")
                agregar_texto(sub, "codigo", compensacion.codigo)
                agregar_texto(sub, "tarifa", formatear_decimal(compensacion.tarifa))
                agregar_texto(sub, "valor", formatear_decimal(compensacion.valor))
    return contenedor


def _fecha(valor: Any) -> str:
    from .base import formatear_fecha

    return formatear_fecha(valor)
