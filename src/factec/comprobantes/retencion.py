"""Generación del XML de **Comprobante de Retención** (SRI, esquema 2.0.0).

El comprobante de retención (``codDoc`` 07) documenta los valores retenidos por
concepto de impuesto a la renta, IVA o ISD sobre uno o varios documentos
sustento. Es el esquema más anidado de los seis comprobantes: cada
``docSustento`` declara sus propios impuestos, retenciones, reembolsos y pagos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, List, Mapping, Optional, Tuple

from ..catalogos import (
    ETIQUETA_RAIZ,
    PORCENTAJE_RETENCION_IVA,
    CodigoRetencion,
    TipoComprobante,
    VERSIONES,
    codigo_documento,
)
from ..excepciones import ErrorValidacion
from ..modelos import (
    DetalleReembolso,
    DocSustento,
    ImpuestoRetencion,
    PagoRetencion,
    Receptor,
    a_decimal,
    cuantizar,
)
from .base import (
    Comprobante,
    Elemento,
    agregar,
    agregar_texto,
    crear,
    formatear_decimal,
    formatear_fecha,
)

__all__ = [
    "ComprobanteRetencion",
    "calcular_valor_retenido",
    "formatear_periodo_fiscal",
]

_CIEN = Decimal("100")

#: Campos de los sub-bloques opcionales de ``retencion``, en el orden del esquema.
_CAMPOS_DIVIDENDOS: Tuple[str, ...] = ("fechaPagoDiv", "imRentaSoc", "ejerFisUtDiv")
_CAMPOS_COMPRA_CAJ_BANANO: Tuple[str, ...] = ("numCajBan", "precCajBan")

#: Totales de reembolso a nivel de ``docSustento``: ``(etiqueta, atributo)``.
_TOTALES_REEMBOLSO: Tuple[Tuple[str, str], ...] = (
    ("totalComprobantesReembolso", "total_comprobantes_reembolso"),
    ("totalBaseImponibleReembolso", "total_base_imponible_reembolso"),
    ("totalImpuestoReembolso", "total_impuesto_reembolso"),
)

_PATRON_PERIODO = re.compile(r"(0[1-9]|1[012])/20\d{2}")
_PATRON_TIPO_IDENTIFICACION = re.compile(r"0[4-8]")


def formatear_periodo_fiscal(valor: Any) -> str:
    """Formatea el periodo fiscal como ``MM/AAAA`` (formato que exige el SRI)."""
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return valor.strftime("%m/%Y")
    return str(valor).strip()


def calcular_valor_retenido(base_imponible: Any, porcentaje_retener: Any) -> Decimal:
    """Calcula ``valorRetenido = baseImponible * porcentajeRetener / 100``.

    El resultado se redondea a dos decimales (``ROUND_HALF_UP``), igual que el
    resto de importes del paquete. Se usa cuando el usuario no informa el valor
    retenido de una :class:`~factec.modelos.ImpuestoRetencion`.
    """
    producto = a_decimal(base_imponible) * a_decimal(porcentaje_retener)
    return cuantizar(producto / _CIEN, 2)


def _porcentaje_retenido(impuesto: ImpuestoRetencion) -> Decimal:
    """Devuelve ``porcentajeRetener``: el informado o el del catálogo de IVA."""
    if impuesto.porcentaje_retener is not None:
        return a_decimal(impuesto.porcentaje_retener)
    codigo = codigo_documento(impuesto.codigo)
    if codigo == CodigoRetencion.IVA.value and impuesto.codigo_retencion is not None:
        porcentaje = PORCENTAJE_RETENCION_IVA.get(codigo_documento(impuesto.codigo_retencion))
        if porcentaje is not None:
            return porcentaje
    raise ErrorValidacion(
        "Indique porcentajeRetener o use un codigoRetencion de IVA del catálogo "
        f"(código de impuesto recibido: {codigo!r})."
    )


def _valor_retenido(impuesto: ImpuestoRetencion) -> Decimal:
    """Devuelve ``valorRetenido``: el informado o el calculado sobre la base."""
    if impuesto.valor_retenido is not None:
        return a_decimal(impuesto.valor_retenido)
    return calcular_valor_retenido(impuesto.base_imponible, _porcentaje_retenido(impuesto))


def _texto_afirmacion(valor: Any) -> Optional[str]:
    """Normaliza un valor a ``SI``/``NO``; ``None`` se propaga."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "SI" if valor else "NO"
    texto = str(valor).strip().upper()
    if texto not in ("SI", "NO"):
        raise ErrorValidacion(f"Se esperaba SI o NO y se recibió {valor!r}.")
    return texto


def _anexar_datos(padre: Elemento, etiqueta: str, campos: Tuple[str, ...], datos: Any) -> None:
    """Añade ``etiqueta`` leyendo ``campos`` de un mapeo o de un objeto."""
    if not datos:
        return
    nodo = crear(etiqueta)
    for campo in campos:
        valor = datos.get(campo) if isinstance(datos, Mapping) else getattr(datos, campo, None)
        agregar_texto(nodo, campo, valor)
    padre.append(nodo)


def _tipo_identificacion(receptor: Receptor) -> str:
    return codigo_documento(receptor.tipo_identificacion)


def _codigo_numerico(valor: Any) -> str:
    """Código del SRI a partir de un ``Enum``, un texto o un número.

    El esquema de retención declara ``codigo``, ``codigoPorcentaje`` y ``tarifa``
    del reembolso como códigos de hasta cuatro dígitos, no como importes.
    """
    if isinstance(valor, (int, float, Decimal)):
        numero = a_decimal(valor)
        if numero == numero.to_integral_value():
            return str(int(numero))
        return format(numero.normalize(), "f")
    return codigo_documento(valor)


@dataclass
class ComprobanteRetencion(Comprobante):
    """Comprobante de retención (``codDoc`` 07, esquema 2.0.0).

    Ejemplo::

        retencion = ComprobanteRetencion(
            emisor=Emisor(
                ruc="1790012345001",
                razon_social="ACME S.A.",
                dir_matriz="Av. Amazonas y Naciones Unidas",
                agente_retencion="1",
            ),
            sujeto_retenido=Receptor(
                razon_social="PROVEEDOR S.A.",
                identificacion="0703886697001",
                tipo_identificacion=TipoIdentificacion.RUC,
            ),
            periodo_fiscal=date(2026, 9, 1),
            docs_sustento=[
                DocSustento(
                    cod_sustento=CodSustento.CREDITO_TRIBUTARIO_IVA,
                    cod_doc_sustento=TipoComprobante.FACTURA,
                    num_doc_sustento="001001000000123",
                    fecha_emision=date(2026, 9, 12),
                    total_sin_impuestos=Decimal("100.00"),
                    importe_total=Decimal("115.00"),
                    impuestos=[
                        ImpuestoDocSustento(
                            codigo=CodigoImpuesto.IVA,
                            codigo_porcentaje=TarifaIva.IVA_15,
                            base_imponible=Decimal("100.00"),
                            tarifa=Decimal("15.00"),
                            valor=Decimal("15.00"),
                        )
                    ],
                    retenciones=[
                        ImpuestoRetencion(
                            codigo=CodigoRetencion.IVA,
                            codigo_retencion=TarifaRetencionIva.RETENCION_30,
                            base_imponible=Decimal("100.00"),
                            porcentaje_retener=Decimal("30.00"),
                            valor_retenido=Decimal("30.00"),
                        )
                    ],
                )
            ],
        )
        xml = retencion.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.COMPROBANTE_RETENCION.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.COMPROBANTE_RETENCION.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.COMPROBANTE_RETENCION.value]

    sujeto_retenido: Optional[Receptor] = None
    docs_sustento: List[DocSustento] = field(default_factory=list)
    periodo_fiscal: date = field(default_factory=date.today)
    parte_rel: str = "SI"
    tipo_sujeto_retenido: Optional[str] = None
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None

    @staticmethod
    def calcular_valor_retenido(base_imponible: Any, porcentaje_retener: Any) -> Decimal:
        """``baseImponible * porcentajeRetener / 100`` redondeado a 2 decimales."""
        return calcular_valor_retenido(base_imponible, porcentaje_retener)

    def _info_comp_retencion(self) -> Elemento:
        if self.sujeto_retenido is None:
            raise ErrorValidacion("El comprobante de retención requiere el sujeto retenido.")
        self.sujeto_retenido.validar()

        info = crear("infoCompRetencion")
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
        agregar_texto(
            info, "tipoIdentificacionSujetoRetenido", _tipo_identificacion(self.sujeto_retenido)
        )
        if self.tipo_sujeto_retenido is not None:
            agregar_texto(
                info, "tipoSujetoRetenido", codigo_documento(self.tipo_sujeto_retenido)
            )
        agregar_texto(info, "parteRel", str(self.parte_rel).strip().upper())
        agregar_texto(info, "razonSocialSujetoRetenido", self.sujeto_retenido.razon_social)
        agregar_texto(info, "identificacionSujetoRetenido", self.sujeto_retenido.identificacion)
        agregar_texto(info, "periodoFiscal", formatear_periodo_fiscal(self.periodo_fiscal))
        return info

    def _impuestos_doc_sustento(self, doc: DocSustento) -> Elemento:
        contenedor = crear("impuestosDocSustento")
        for impuesto in doc.impuestos:
            nodo = agregar(contenedor, "impuestoDocSustento")
            agregar_texto(nodo, "codImpuestoDocSustento", codigo_documento(impuesto.codigo))
            agregar_texto(nodo, "codigoPorcentaje", codigo_documento(impuesto.codigo_porcentaje))
            agregar_texto(nodo, "baseImponible", formatear_decimal(impuesto.base_imponible))
            agregar_texto(nodo, "tarifa", formatear_decimal(impuesto.tarifa))
            agregar_texto(nodo, "valorImpuesto", formatear_decimal(impuesto.valor))
        return contenedor

    def _retenciones(self, doc: DocSustento) -> Elemento:
        contenedor = crear("retenciones")
        for impuesto in doc.retenciones:
            nodo = agregar(contenedor, "retencion")
            agregar_texto(nodo, "codigo", codigo_documento(impuesto.codigo))
            agregar_texto(nodo, "codigoRetencion", codigo_documento(impuesto.codigo_retencion))
            agregar_texto(nodo, "baseImponible", formatear_decimal(impuesto.base_imponible))
            agregar_texto(
                nodo, "porcentajeRetener", formatear_decimal(_porcentaje_retenido(impuesto))
            )
            agregar_texto(nodo, "valorRetenido", formatear_decimal(_valor_retenido(impuesto)))
            _anexar_datos(
                nodo,
                "dividendos",
                _CAMPOS_DIVIDENDOS,
                getattr(impuesto, "dividendos", None),
            )
            _anexar_datos(
                nodo,
                "compraCajBanano",
                _CAMPOS_COMPRA_CAJ_BANANO,
                getattr(impuesto, "compra_caj_banano", None),
            )
        return contenedor

    def _impuestos_reembolso(self, detalle: DetalleReembolso) -> Elemento:
        contenedor = crear("detalleImpuestos")
        for impuesto in detalle.impuestos:
            nodo = agregar(contenedor, "detalleImpuesto")
            agregar_texto(nodo, "codigo", _codigo_numerico(impuesto.codigo))
            agregar_texto(nodo, "codigoPorcentaje", _codigo_numerico(impuesto.codigo_porcentaje))
            agregar_texto(nodo, "tarifa", _codigo_numerico(impuesto.tarifa))
            agregar_texto(
                nodo,
                "baseImponibleReembolso",
                formatear_decimal(impuesto.base_imponible_reembolso),
            )
            agregar_texto(
                nodo, "impuestoReembolso", formatear_decimal(impuesto.impuesto_reembolso)
            )
        return contenedor

    def _elemento_reembolsos(self, doc: DocSustento) -> Optional[Elemento]:
        """Construye ``<reembolsos>`` del ``docSustento``.

        No se reutiliza el helper de ``_comun`` porque el esquema 2.0.0 nombra el
        bloque como ``numeroAutorizacionDocReemb`` y trata ``tarifa`` como código,
        no como importe.
        """
        detalles: List[DetalleReembolso] = []
        for reembolso in getattr(doc, "reembolsos", None) or []:
            detalles.extend(reembolso.detalles)
        if not detalles:
            return None
        contenedor = crear("reembolsos")
        for detalle in detalles:
            nodo = agregar(contenedor, "reembolsoDetalle")
            agregar_texto(
                nodo,
                "tipoIdentificacionProveedorReembolso",
                codigo_documento(detalle.tipo_identificacion_proveedor),
            )
            agregar_texto(
                nodo, "identificacionProveedorReembolso", detalle.identificacion_proveedor
            )
            agregar_texto(
                nodo, "codPaisPagoProveedorReembolso", detalle.cod_pais_pago_proveedor
            )
            agregar_texto(nodo, "tipoProveedorReembolso", codigo_documento(detalle.tipo_proveedor))
            agregar_texto(nodo, "codDocReembolso", codigo_documento(detalle.cod_doc_reembolso))
            agregar_texto(nodo, "estabDocReembolso", detalle.estab_doc_reembolso)
            agregar_texto(nodo, "ptoEmiDocReembolso", detalle.pto_emi_doc_reembolso)
            agregar_texto(nodo, "secuencialDocReembolso", detalle.secuencial_doc_reembolso)
            agregar_texto(
                nodo,
                "fechaEmisionDocReembolso",
                formatear_fecha(detalle.fecha_emision_doc_reembolso),
            )
            agregar_texto(nodo, "numeroAutorizacionDocReemb", detalle.numero_autorizacion)
            nodo.append(self._impuestos_reembolso(detalle))
        return contenedor

    def _pagos_doc_sustento(self, doc: DocSustento) -> Elemento:
        contenedor = crear("pagos")
        pagos = list(doc.pagos) or [PagoRetencion(forma_pago="01", total=doc.importe_total)]
        for pago in pagos:
            nodo = agregar(contenedor, "pago")
            agregar_texto(nodo, "formaPago", codigo_documento(pago.forma_pago))
            agregar_texto(nodo, "total", formatear_decimal(pago.total))
        return contenedor

    def _doc_sustento(self, doc: DocSustento) -> Elemento:
        nodo = crear("docSustento")
        agregar_texto(nodo, "codSustento", codigo_documento(doc.cod_sustento))
        agregar_texto(nodo, "codDocSustento", codigo_documento(doc.cod_doc_sustento))
        agregar_texto(nodo, "numDocSustento", doc.num_doc_sustento)
        agregar_texto(nodo, "fechaEmisionDocSustento", formatear_fecha(doc.fecha_emision))
        if doc.fecha_registro_contable is not None:
            agregar_texto(
                nodo, "fechaRegistroContable", formatear_fecha(doc.fecha_registro_contable)
            )
        agregar_texto(nodo, "numAutDocSustento", doc.num_aut_doc_sustento)
        agregar_texto(nodo, "pagoLocExt", codigo_documento(doc.pago_loc_ext))
        if doc.tipo_regi:
            agregar_texto(nodo, "tipoRegi", codigo_documento(doc.tipo_regi))
        agregar_texto(nodo, "paisEfecPago", doc.pais_efec_pago)
        agregar_texto(nodo, "aplicConvDobTrib", _texto_afirmacion(doc.aplic_conv_dob_trib))
        agregar_texto(nodo, "pagExtSujRetNorLeg", _texto_afirmacion(doc.pag_ext_suj_ret_nor_leg))
        agregar_texto(nodo, "pagoRegFis", _texto_afirmacion(getattr(doc, "pago_reg_fis", None)))
        for etiqueta, atributo in _TOTALES_REEMBOLSO:
            total = getattr(doc, atributo, None)
            if total is not None:
                agregar_texto(nodo, etiqueta, formatear_decimal(total))
        agregar_texto(nodo, "totalSinImpuestos", formatear_decimal(doc.total_sin_impuestos))
        agregar_texto(nodo, "importeTotal", formatear_decimal(doc.importe_total))
        nodo.append(self._impuestos_doc_sustento(doc))
        nodo.append(self._retenciones(doc))
        reembolsos = self._elemento_reembolsos(doc)
        if reembolsos is not None:
            nodo.append(reembolsos)
        nodo.append(self._pagos_doc_sustento(doc))
        return nodo

    def _docs_sustento(self) -> Elemento:
        contenedor = crear("docsSustento")
        for doc in self.docs_sustento:
            contenedor.append(self._doc_sustento(doc))
        return contenedor

    def construir_cuerpo(self) -> List[Elemento]:
        return [self._info_comp_retencion(), self._docs_sustento()]

    def validar(self) -> None:
        super().validar()
        if self.sujeto_retenido is None:
            raise ErrorValidacion("El comprobante de retención requiere el sujeto retenido.")
        self.sujeto_retenido.validar()
        tipo = _tipo_identificacion(self.sujeto_retenido)
        if not _PATRON_TIPO_IDENTIFICACION.fullmatch(tipo):
            raise ErrorValidacion(
                f"El tipo de identificación del sujeto retenido no es válido: {tipo!r}."
            )
        if self.tipo_sujeto_retenido is not None:
            sujeto = codigo_documento(self.tipo_sujeto_retenido)
            if sujeto not in ("01", "02"):
                raise ErrorValidacion(f"El tipo de sujeto retenido no es válido: {sujeto!r}.")
        parte_rel = str(self.parte_rel).strip().upper()
        if parte_rel not in ("SI", "NO"):
            raise ErrorValidacion(f"parteRel debe ser 'SI' o 'NO': {self.parte_rel!r}.")
        periodo = formatear_periodo_fiscal(self.periodo_fiscal)
        if not _PATRON_PERIODO.fullmatch(periodo):
            raise ErrorValidacion(
                f"El periodo fiscal debe tener el formato MM/AAAA: {periodo!r}."
            )
        if not self.docs_sustento:
            raise ErrorValidacion(
                "El comprobante de retención debe tener al menos un documento sustento."
            )
        for indice, doc in enumerate(self.docs_sustento, start=1):
            if not doc.impuestos and not doc.retenciones:
                raise ErrorValidacion(
                    f"El documento sustento {indice} debe tener impuestos o retenciones."
                )
