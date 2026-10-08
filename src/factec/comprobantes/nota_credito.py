"""Generación del XML de **Nota de Crédito** (SRI, esquema 1.1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Dict, List, Optional

from ..catalogos import ETIQUETA_RAIZ, TipoComprobante, VERSIONES
from ..excepciones import ErrorValidacion
from ..modelos import Compensacion, Detalle, Receptor
from ._comun import (
    CODIGOS_DETALLE_NOTA_CREDITO,
    calcular_totales,
    elemento_compensaciones,
    elemento_detalles,
    elemento_total_con_impuestos,
)
from .base import Comprobante, Elemento, agregar_texto, crear, formatear_decimal, formatear_fecha

__all__ = ["NotaCredito"]


def _tipo_identificacion(receptor: Receptor) -> str:
    return str(getattr(receptor.tipo_identificacion, "value", receptor.tipo_identificacion))


@dataclass
class NotaCredito(Comprobante):
    """Nota de crédito electrónica (``codDoc`` 04, esquema 1.1.0).

    Documenta descuentos, devoluciones o anulaciones totales o parciales de un
    comprobante de venta anterior, que se identifica con ``codDocModificado`` y
    ``numDocModificado``.

    Ejemplo::

        nota = NotaCredito(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            receptor=Receptor(identificacion="0703886697001", razon_social="Cliente"),
            detalles=[Detalle(descripcion="Devolución", cantidad=1, precio_unitario=100)],
            cod_doc_modificado=TipoComprobante.FACTURA.value,
            num_doc_modificado="001-001-000000123",
            fecha_emision_doc_sustento=date(2026, 1, 15),
            motivo="Devolución parcial de la mercadería",
        )
        xml = nota.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.NOTA_CREDITO.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.NOTA_CREDITO.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.NOTA_CREDITO.value]

    receptor: Optional[Receptor] = None
    detalles: List[Detalle] = field(default_factory=list)
    compensaciones: List[Compensacion] = field(default_factory=list)
    cod_doc_modificado: str = TipoComprobante.FACTURA.value
    num_doc_modificado: str = ""
    fecha_emision_doc_sustento: Optional[date] = None
    motivo: str = ""
    rise: Optional[str] = None
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None

    def calcular(self) -> Dict[str, Any]:
        """Devuelve los totales calculados a partir de los detalles."""
        if not self.detalles:
            raise ErrorValidacion("La nota de crédito debe tener al menos un detalle.")
        return calcular_totales(self.detalles)

    def _info_nota_credito(self, totales: Dict[str, Any]) -> Elemento:
        if self.receptor is None:
            raise ErrorValidacion("La nota de crédito requiere el adquirente (receptor).")
        self.receptor.validar()

        info = crear("infoNotaCredito")
        agregar_texto(info, "fechaEmision", formatear_fecha(self.fecha_emision))
        agregar_texto(
            info,
            "dirEstablecimiento",
            self.direccion_establecimiento or self.emisor.dir_establecimiento,
        )
        agregar_texto(info, "tipoIdentificacionComprador", _tipo_identificacion(self.receptor))
        agregar_texto(info, "razonSocialComprador", self.receptor.razon_social)
        agregar_texto(info, "identificacionComprador", self.receptor.identificacion)
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
        agregar_texto(info, "rise", self.rise)
        agregar_texto(info, "codDocModificado", self.cod_doc_modificado)
        agregar_texto(info, "numDocModificado", self.num_doc_modificado)
        agregar_texto(info, "fechaEmisionDocSustento", self._fecha_doc_sustento())
        agregar_texto(info, "totalSinImpuestos", formatear_decimal(totales["total_sin_impuestos"]))
        if self.compensaciones:
            info.append(elemento_compensaciones(self.compensaciones))
        agregar_texto(info, "valorModificacion", formatear_decimal(totales["importe_total"]))
        agregar_texto(info, "moneda", self.moneda)
        info.append(elemento_total_con_impuestos(totales["total_con_impuestos"], con_tarifa=False))
        agregar_texto(info, "motivo", self.motivo)
        return info

    def _fecha_doc_sustento(self) -> str:
        if self.fecha_emision_doc_sustento is None:
            raise ErrorValidacion(
                "La nota de crédito requiere la fecha de emisión del documento sustento."
            )
        return formatear_fecha(self.fecha_emision_doc_sustento)

    def construir_cuerpo(self) -> List[Elemento]:
        totales = self.calcular()
        return [
            self._info_nota_credito(totales),
            elemento_detalles(
                self.detalles,
                codigos=CODIGOS_DETALLE_NOTA_CREDITO,
                con_unidad_medida=False,
                descuento_opcional=True,
            ),
        ]

    def validar(self) -> None:
        super().validar()
        if self.receptor is None:
            raise ErrorValidacion("La nota de crédito requiere el adquirente (receptor).")
        self.receptor.validar()
        if not self.detalles:
            raise ErrorValidacion("La nota de crédito debe tener al menos un detalle.")
        if not str(self.motivo or "").strip():
            raise ErrorValidacion("La nota de crédito requiere el motivo o razón.")
        if not str(self.cod_doc_modificado or "").strip():
            raise ErrorValidacion("La nota de crédito requiere el código del documento modificado.")
        if not str(self.num_doc_modificado or "").strip():
            raise ErrorValidacion("La nota de crédito requiere el número del documento modificado.")
        if self.fecha_emision_doc_sustento is None:
            raise ErrorValidacion(
                "La nota de crédito requiere la fecha de emisión del documento sustento."
            )
