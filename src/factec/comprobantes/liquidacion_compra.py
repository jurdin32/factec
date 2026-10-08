"""Generación del XML de **Liquidación de Compra** (SRI, esquema 1.1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

from ..catalogos import ETIQUETA_RAIZ, TipoComprobante, VERSIONES
from ..excepciones import ErrorValidacion
from ..modelos import Detalle, Pago, Receptor, Reembolso
from ._comun import (
    anexar_pagos,
    calcular_totales,
    elemento_detalles,
    elemento_reembolsos,
    elemento_total_con_impuestos,
)
from .base import Comprobante, Elemento, agregar_texto, crear, formatear_decimal, formatear_fecha

__all__ = ["LiquidacionCompra"]


def _tipo_identificacion(proveedor: Receptor) -> str:
    return str(getattr(proveedor.tipo_identificacion, "value", proveedor.tipo_identificacion))


@dataclass
class LiquidacionCompra(Comprobante):
    """Liquidación de compra de bienes o servicios (``codDoc`` 03, esquema 1.1.0).

    El comprobante lo emite el comprador, por lo que el tercero de la
    operación es un **proveedor**.

    Ejemplo::

        liquidacion = LiquidacionCompra(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            proveedor=Receptor(
                razon_social="Proveedor",
                identificacion="0703886697001",
                tipo_identificacion=TipoIdentificacion.RUC,
            ),
            detalles=[Detalle(descripcion="Servicio", cantidad=1, precio_unitario=100)],
        )
        xml = liquidacion.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.LIQUIDACION_COMPRA.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.LIQUIDACION_COMPRA.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.LIQUIDACION_COMPRA.value]

    proveedor: Optional[Receptor] = None
    detalles: List[Detalle] = field(default_factory=list)
    pagos: List[Pago] = field(default_factory=list)
    reembolsos: List[Reembolso] = field(default_factory=list)
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None
    correo_tipo_negociable: Optional[str] = None

    def calcular(self) -> Dict[str, Any]:
        """Devuelve los totales calculados a partir de los detalles."""
        if not self.detalles:
            raise ErrorValidacion("La liquidación de compra debe tener al menos un detalle.")
        return calcular_totales(self.detalles)

    def _info_liquidacion(self, totales: Dict[str, Any]) -> Elemento:
        if self.proveedor is None:
            raise ErrorValidacion("La liquidación de compra requiere el proveedor.")
        self.proveedor.validar()

        info = crear("infoLiquidacionCompra")
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
        agregar_texto(info, "tipoIdentificacionProveedor", _tipo_identificacion(self.proveedor))
        agregar_texto(info, "razonSocialProveedor", self.proveedor.razon_social)
        agregar_texto(info, "identificacionProveedor", self.proveedor.identificacion)
        agregar_texto(info, "direccionProveedor", self.proveedor.direccion)
        agregar_texto(info, "totalSinImpuestos", formatear_decimal(totales["total_sin_impuestos"]))
        agregar_texto(info, "totalDescuento", formatear_decimal(totales["total_descuento"]))
        info.append(
            elemento_total_con_impuestos(
                totales["total_con_impuestos"], con_descuento_adicional=True
            )
        )
        agregar_texto(info, "importeTotal", formatear_decimal(totales["importe_total"]))
        agregar_texto(info, "moneda", self.moneda)
        if self.pagos:
            anexar_pagos(info, self.pagos)
        return info

    def _tipo_negociable(self) -> Optional[Elemento]:
        if not self.correo_tipo_negociable:
            return None
        nodo = crear("tipoNegociable")
        agregar_texto(nodo, "correo", self.correo_tipo_negociable)
        return nodo

    def construir_cuerpo(self) -> List[Elemento]:
        totales = self.calcular()
        cuerpo: List[Elemento] = [self._info_liquidacion(totales)]
        cuerpo.append(elemento_detalles(self.detalles))
        reembolsos = elemento_reembolsos(self.reembolsos)
        if reembolsos is not None:
            cuerpo.append(reembolsos)
        tipo_negociable = self._tipo_negociable()
        if tipo_negociable is not None:
            cuerpo.append(tipo_negociable)
        return cuerpo

    def validar(self) -> None:
        super().validar()
        if self.proveedor is None:
            raise ErrorValidacion("La liquidación de compra requiere el proveedor.")
        self.proveedor.validar()
        if not self.detalles:
            raise ErrorValidacion("La liquidación de compra debe tener al menos un detalle.")
        totales = calcular_totales(self.detalles)
        if totales["importe_total"] <= 0:
            raise ErrorValidacion(
                "El importe total de la liquidación de compra debe ser mayor que cero."
            )
