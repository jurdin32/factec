"""Generación del XML de **Factura** (SRI, esquema 1.1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional

from ..catalogos import ETIQUETA_RAIZ, TipoComprobante, VERSIONES
from ..excepciones import ErrorValidacion
from ..modelos import Compensacion, Detalle, Pago, Receptor, Reembolso, a_decimal
from ._comun import (
    anexar_pagos,
    calcular_totales,
    elemento_compensaciones,
    elemento_detalles,
    elemento_reembolsos,
    elemento_total_con_impuestos,
)
from .base import Comprobante, Elemento, agregar_texto, crear, formatear_decimal, formatear_fecha

__all__ = ["Factura", "DetalleFactura"]

#: Alias con el nombre habitual en aplicaciones de facturación.
DetalleFactura = Detalle


def _tipo_identificacion(receptor: Receptor) -> str:
    return str(getattr(receptor.tipo_identificacion, "value", receptor.tipo_identificacion))


@dataclass
class Factura(Comprobante):
    """Factura electrónica (``codDoc`` 01, esquema 1.1.0).

    Ejemplo::

        factura = Factura(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            receptor=Receptor(identificacion="0703886697001", razon_social="Cliente"),
            detalles=[Detalle(descripcion="Servicio", cantidad=1, precio_unitario=100)],
        )
        xml = factura.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.FACTURA.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.FACTURA.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.FACTURA.value]

    receptor: Optional[Receptor] = None
    detalles: List[Detalle] = field(default_factory=list)
    pagos: List[Pago] = field(default_factory=list)
    compensaciones: List[Compensacion] = field(default_factory=list)
    reembolsos: List[Reembolso] = field(default_factory=list)
    propina: Optional[Decimal] = None
    placa: Optional[str] = None
    guia_remision: Optional[str] = None
    valor_ret_iva: Optional[Decimal] = None
    valor_ret_renta: Optional[Decimal] = None
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None

    def calcular(self) -> Dict[str, Any]:
        """Devuelve los totales calculados a partir de los detalles."""
        if not self.detalles:
            raise ErrorValidacion("La factura debe tener al menos un detalle.")
        return calcular_totales(self.detalles)

    def _info_factura(self, totales: Dict[str, Any]) -> Elemento:
        if self.receptor is None:
            raise ErrorValidacion("La factura requiere el adquirente (receptor).")
        self.receptor.validar()

        info = crear("infoFactura")
        agregar_texto(info, "fechaEmision", formatear_fecha(self.fecha_emision))
        agregar_texto(
            info,
            "dirEstablecimiento",
            self.direccion_establecimiento or self.emisor.dir_establecimiento,
        )
        agregar_texto(
            info,
            "contribuyenteEspecial",
            self.contribuyente_especial or self.emisor.contribuyente_especial,
        )
        obligado = (
            self.emisor.obligado_contabilidad
            if self.obligado_contabilidad is None
            else self.obligado_contabilidad
        )
        agregar_texto(info, "obligadoContabilidad", "SI" if obligado else "NO")
        agregar_texto(info, "tipoIdentificacionComprador", _tipo_identificacion(self.receptor))
        agregar_texto(info, "guiaRemision", self.guia_remision)
        agregar_texto(info, "razonSocialComprador", self.receptor.razon_social)
        agregar_texto(info, "identificacionComprador", self.receptor.identificacion)
        agregar_texto(info, "direccionComprador", self.receptor.direccion)
        agregar_texto(info, "totalSinImpuestos", formatear_decimal(totales["total_sin_impuestos"]))
        agregar_texto(info, "totalDescuento", formatear_decimal(totales["total_descuento"]))
        info.append(
            elemento_total_con_impuestos(
                totales["total_con_impuestos"], con_descuento_adicional=True
            )
        )
        if self.compensaciones:
            info.append(elemento_compensaciones(self.compensaciones))
        if self.propina is not None:
            agregar_texto(info, "propina", formatear_decimal(self.propina))
        agregar_texto(info, "importeTotal", formatear_decimal(totales["importe_total"]))
        agregar_texto(info, "moneda", self.moneda)
        agregar_texto(info, "placa", self.placa)
        pagos = list(self.pagos) or [Pago(total=totales["importe_total"])]
        anexar_pagos(info, pagos)
        if self.valor_ret_iva is not None:
            agregar_texto(info, "valorRetIva", formatear_decimal(self.valor_ret_iva))
        if self.valor_ret_renta is not None:
            agregar_texto(info, "valorRetRenta", formatear_decimal(self.valor_ret_renta))
        return info

    def construir_cuerpo(self) -> List[Elemento]:
        totales = self.calcular()
        cuerpo: List[Elemento] = [self._info_factura(totales)]
        cuerpo.append(elemento_detalles(self.detalles))
        reembolsos = elemento_reembolsos(self.reembolsos)
        if reembolsos is not None:
            cuerpo.append(reembolsos)
        return cuerpo

    def validar(self) -> None:
        super().validar()
        if self.receptor is None:
            raise ErrorValidacion("La factura requiere el adquirente (receptor).")
        self.receptor.validar()
        if not self.detalles:
            raise ErrorValidacion("La factura debe tener al menos un detalle.")
        for indice, detalle in enumerate(self.detalles, start=1):
            if a_decimal(detalle.descuento) < 0:
                raise ErrorValidacion(f"El descuento del detalle {indice} no puede ser negativo.")
        totales = calcular_totales(self.detalles)
        if totales["importe_total"] <= 0:
            raise ErrorValidacion("El importe total de la factura debe ser mayor que cero.")
