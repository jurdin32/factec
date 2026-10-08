"""Generación del XML de **Nota de Débito** (SRI, esquema 1.0.0)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional

from ..catalogos import ETIQUETA_RAIZ, TipoComprobante, VERSIONES
from ..excepciones import ErrorValidacion
from ..modelos import Compensacion, Impuesto, Motivo, Pago, Receptor, a_decimal, cuantizar
from ._comun import (
    anexar_pagos,
    elemento_compensaciones,
    elemento_impuestos_simples,
    elemento_motivos,
)
from .base import Comprobante, Elemento, agregar_texto, crear, formatear_decimal, formatear_fecha

__all__ = ["NotaDebito"]


def _tipo_identificacion(receptor: Receptor) -> str:
    return str(getattr(receptor.tipo_identificacion, "value", receptor.tipo_identificacion))


@dataclass
class NotaDebito(Comprobante):
    """Nota de débito electrónica (``codDoc`` 05, esquema 1.0.0).

    Ejemplo::

        nota = NotaDebito(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            receptor=Receptor(identificacion="0703886697001", razon_social="Cliente"),
            cod_doc_modificado="01",
            num_doc_modificado="001001000000123",
            fecha_emision_doc_sustento=date(2026, 1, 15),
            total_sin_impuestos=Decimal("100"),
            impuestos=[Impuesto(base_imponible=100)],
            motivos=[Motivo(razon="Intereses por mora", valor=15)],
        )
        xml = nota.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.NOTA_DEBITO.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.NOTA_DEBITO.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.NOTA_DEBITO.value]

    receptor: Optional[Receptor] = None
    impuestos: List[Impuesto] = field(default_factory=list)
    motivos: List[Motivo] = field(default_factory=list)
    pagos: List[Pago] = field(default_factory=list)
    compensaciones: List[Compensacion] = field(default_factory=list)
    cod_doc_modificado: str = ""
    num_doc_modificado: str = ""
    fecha_emision_doc_sustento: date = field(default_factory=date.today)
    total_sin_impuestos: Decimal = Decimal("0")
    rise: Optional[str] = None
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None

    def _impuestos_resueltos(self) -> List[Impuesto]:
        """Copia los impuestos completando ``tarifa`` y ``valor`` si faltan."""
        resueltos: List[Impuesto] = []
        for impuesto in self.impuestos:
            resueltos.append(copy.copy(impuesto).resolver())
        return resueltos

    def calcular(self) -> Dict[str, Any]:
        """Devuelve ``total_sin_impuestos``, ``total_impuestos`` y ``valor_total``."""
        total_sin_impuestos = cuantizar(a_decimal(self.total_sin_impuestos), 2)
        total_impuestos = Decimal("0")
        for impuesto in self._impuestos_resueltos():
            total_impuestos += a_decimal(impuesto.valor)
        total_impuestos = cuantizar(total_impuestos, 2)
        return {
            "total_sin_impuestos": total_sin_impuestos,
            "total_impuestos": total_impuestos,
            "valor_total": cuantizar(total_sin_impuestos + total_impuestos, 2),
        }

    def _info_nota_debito(self, totales: Dict[str, Any]) -> Elemento:
        if self.receptor is None:
            raise ErrorValidacion("La nota de débito requiere el adquirente (receptor).")
        self.receptor.validar()

        info = crear("infoNotaDebito")
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
        agregar_texto(
            info,
            "fechaEmisionDocSustento",
            formatear_fecha(self.fecha_emision_doc_sustento),
        )
        agregar_texto(info, "totalSinImpuestos", formatear_decimal(totales["total_sin_impuestos"]))
        info.append(
            elemento_impuestos_simples(self._impuestos_resueltos(), con_valor_devolucion_iva=True)
        )
        if self.compensaciones:
            info.append(elemento_compensaciones(self.compensaciones))
        agregar_texto(info, "valorTotal", formatear_decimal(totales["valor_total"]))
        if self.pagos:
            anexar_pagos(info, self.pagos)
        return info

    def construir_cuerpo(self) -> List[Elemento]:
        totales = self.calcular()
        return [self._info_nota_debito(totales), elemento_motivos(self.motivos)]

    def validar(self) -> None:
        super().validar()
        if self.receptor is None:
            raise ErrorValidacion("La nota de débito requiere el adquirente (receptor).")
        self.receptor.validar()
        if not self.motivos:
            raise ErrorValidacion("La nota de débito debe tener al menos un motivo.")
        for indice, motivo in enumerate(self.motivos, start=1):
            if not str(motivo.razon or "").strip():
                raise ErrorValidacion(f"La razón del motivo {indice} es obligatoria.")
        if not self.impuestos:
            raise ErrorValidacion("La nota de débito debe tener al menos un impuesto.")
        if not str(self.cod_doc_modificado or "").strip():
            raise ErrorValidacion("El código del documento modificado es obligatorio.")
        if not str(self.num_doc_modificado or "").strip():
            raise ErrorValidacion("El número del documento modificado es obligatorio.")
        totales = self.calcular()
        esperado = totales["total_sin_impuestos"] + totales["total_impuestos"]
        if abs(a_decimal(totales["valor_total"]) - esperado) > Decimal("0.01"):
            raise ErrorValidacion(
                "El valor total de la nota de débito no cuadra con el total sin impuestos "
                "más los impuestos."
            )
