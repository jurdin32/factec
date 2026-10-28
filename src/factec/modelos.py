"""Modelos de datos para los comprobantes electrónicos del SRI.

Son ``dataclass`` sencillos y agnósticos del XML: describen el emisor, el
adquirente, los detalles, los impuestos y los pagos. Los generadores de XML
(``factec.comprobantes``) los traducen al esquema del SRI.

Los valores monetarios se manejan con :class:`~decimal.Decimal` para evitar los
errores de redondeo de ``float``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Union

from .catalogos import (
    CodigoImpuesto,
    FormaPago,
    Moneda,
    PORCENTAJE_IVA,
    TarifaIva,
    TipoIdentificacion,
)
from .excepciones import ErrorValidacion

__all__ = [
    "Decimalizable",
    "a_decimal",
    "cuantizar",
    "RIMPE_GENERAL",
    "RIMPE_NEGOCIO_POPULAR",
    "Emisor",
    "Receptor",
    "Impuesto",
    "Detalle",
    "TotalImpuesto",
    "Pago",
    "Compensacion",
    "DetalleReembolso",
    "ImpuestoReembolso",
    "Reembolso",
    "Motivo",
    "ImpuestoRetencion",
    "Retencion",
    "ImpuestoDocSustento",
    "DocSustento",
    "PagoRetencion",
    "DetalleGuia",
    "Destinatario",
]

Decimalizable = Union[Decimal, int, float, str]

_CENTIMOS = Decimal("0.01")
_SEIS_DECIMALES = Decimal("0.000001")

#: Valores exactos que admite ``contribuyenteRimpe`` en el esquema del SRI.
RIMPE_GENERAL = "CONTRIBUYENTE RÉGIMEN RIMPE"
RIMPE_NEGOCIO_POPULAR = "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"


def a_decimal(valor: Optional[Decimalizable]) -> Decimal:
    """Convierte ``valor`` a ``Decimal`` sin arrastrar el error binario de float."""
    if valor is None:
        return Decimal("0")
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, float):
        return Decimal(repr(valor))
    return Decimal(str(valor))


def cuantizar(valor: Decimal, decimales: int = 2) -> Decimal:
    """Redondea ``valor`` a la cantidad de decimales indicada (half-up)."""
    exponente = Decimal(1).scaleb(-decimales)
    return valor.quantize(exponente, rounding=ROUND_HALF_UP)


def _dos_decimales(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTIMOS, rounding=ROUND_HALF_UP)


def _impuesto_con_base(impuesto: "Impuesto", base: Decimal) -> "Impuesto":
    """Una copia del impuesto con la base resuelta y la tarifa y el valor puestos."""
    propia = impuesto.base_imponible
    trae_base = propia is not None and a_decimal(propia) != 0
    copia = Impuesto(
        codigo=impuesto.codigo,
        codigo_porcentaje=impuesto.codigo_porcentaje,
        base_imponible=propia if trae_base else base,
        tarifa=impuesto.tarifa,
        valor=impuesto.valor,
        valor_devolucion_iva=impuesto.valor_devolucion_iva,
    )
    return copia.resolver()


@dataclass
class Emisor:
    """Datos del contribuyente que emite el comprobante."""

    ruc: str
    razon_social: str
    dir_matriz: str
    estab: str = "001"
    pto_emi: str = "001"
    nombre_comercial: Optional[str] = None
    dir_establecimiento: Optional[str] = None
    obligado_contabilidad: bool = True
    contribuyente_especial: Optional[str] = None
    agente_retencion: Optional[str] = None
    regimen: Optional[str] = None
    contribuyente_rimpe: bool = False
    logo: Optional[str] = None

    @property
    def serie(self) -> str:
        """``estab`` + ``ptoEmi`` (6 dígitos)."""
        return f"{self.estab:0>3}{self.pto_emi:0>3}"

    @property
    def obligado_contabilidad_texto(self) -> str:
        return "SI" if self.obligado_contabilidad else "NO"

    @property
    def rimpe_texto(self) -> Optional[str]:
        """Texto que espera ``contribuyenteRimpe`` en el XML.

        El esquema del SRI solo admite estos dos valores exactos
        (patrón ``CONTRIBUYENTE (NEGOCIO POPULAR - )?RÉGIMEN RIMPE``).
        """
        if not self.contribuyente_rimpe:
            return None
        if "NEGOCIO POPULAR" in (self.regimen or "").upper():
            return RIMPE_NEGOCIO_POPULAR
        return RIMPE_GENERAL

    def validar(self) -> None:
        if len(str(self.ruc)) != 13:
            raise ErrorValidacion(f"El RUC del emisor debe tener 13 dígitos: {self.ruc!r}")
        if not self.razon_social:
            raise ErrorValidacion("La razón social del emisor es obligatoria.")
        if not self.dir_matriz:
            raise ErrorValidacion("La dirección de la matriz es obligatoria.")


@dataclass
class Receptor:
    """Adquirente, proveedor, sujeto retenido o destinatario según el comprobante."""

    razon_social: str
    identificacion: str
    tipo_identificacion: str = TipoIdentificacion.CONSUMIDOR_FINAL
    direccion: Optional[str] = None
    email: Optional[str] = None

    def validar(self) -> None:
        tipo = getattr(self.tipo_identificacion, "value", self.tipo_identificacion)
        if tipo == TipoIdentificacion.CONSUMIDOR_FINAL.value:
            return
        if not self.identificacion:
            raise ErrorValidacion("La identificación del adquirente es obligatoria.")
        if not self.razon_social:
            raise ErrorValidacion("La razón social del adquirente es obligatoria.")


@dataclass
class Impuesto:
    """Impuesto aplicado a un detalle (IVA, ICE, IRBPNR)."""

    codigo: str = CodigoImpuesto.IVA
    codigo_porcentaje: str = TarifaIva.IVA_15
    base_imponible: Decimalizable = Decimal("0")
    tarifa: Optional[Decimalizable] = None
    valor: Optional[Decimalizable] = None
    valor_devolucion_iva: Optional[Decimalizable] = None

    def codigo_valor(self) -> str:
        return str(getattr(self.codigo, "value", self.codigo))

    def porcentaje_valor(self) -> str:
        return str(getattr(self.codigo_porcentaje, "value", self.codigo_porcentaje))

    def resolver(self) -> "Impuesto":
        """Completa ``tarifa`` (según el catálogo) y ``valor`` si faltan.

        Un código de IVA que no está en el catálogo **no** se sustituye por el 15 %:
        eso emitía el impuesto equivocado sin decir nada. Si el código no se conoce,
        hay que escribir la tarifa a mano (``tarifa=``).
        """
        base = a_decimal(self.base_imponible)
        if self.tarifa is None:
            es_iva = self.codigo_valor() == str(CodigoImpuesto.IVA.value)
            tarifa_del_catalogo = PORCENTAJE_IVA.get(self.porcentaje_valor())
            if tarifa_del_catalogo is None:
                raise ErrorValidacion(
                    f"Código de porcentaje «{self.porcentaje_valor()}» desconocido: "
                    "escriba la tarifa del impuesto o use uno del catálogo del SRI."
                    + ("" if es_iva else " Para ICE e IRBPNR la tarifa siempre va a mano.")
                )
            self.tarifa = tarifa_del_catalogo
        tarifa = a_decimal(self.tarifa)
        if self.valor is None:
            self.valor = _dos_decimales(base * tarifa / Decimal("100"))
        self.base_imponible = _dos_decimales(base)
        self.tarifa = cuantizar(a_decimal(self.tarifa), 2)
        self.valor = _dos_decimales(a_decimal(self.valor))
        return self


@dataclass
class Detalle:
    """Línea de un comprobante de venta (factura, nota de crédito, liquidación)."""

    descripcion: str
    cantidad: Decimalizable
    precio_unitario: Decimalizable
    impuestos: List[Impuesto] = field(default_factory=list)
    descuento: Decimalizable = Decimal("0")
    codigo_principal: Optional[str] = None
    codigo_auxiliar: Optional[str] = None
    unidad_medida: Optional[str] = None
    detalles_adicionales: Dict[str, str] = field(default_factory=dict)

    @property
    def subtotal(self) -> Decimal:
        """``cantidad * precioUnitario`` sin descuento."""
        return a_decimal(self.cantidad) * a_decimal(self.precio_unitario)

    @property
    def precio_total_sin_impuesto(self) -> Decimal:
        """``cantidad * precioUnitario - descuento`` redondeado a 2 decimales."""
        return _dos_decimales(self.subtotal - a_decimal(self.descuento))

    def impuestos_resueltos(self) -> List[Impuesto]:
        """Los impuestos de la línea, con la base y la tarifa ya resueltas.

        La base es la de la línea salvo que el impuesto traiga una propia: mirar si
        traía ``valor`` dejaba ``baseImponible 0.00`` en los impuestos escritos a
        mano, y el SRI devuelve el comprobante por no cuadrar la base.

        El IVA va sobre la base **más el ICE y el IRBPNR** de la misma línea, como
        manda la ficha técnica. Antes se calculaba sobre el total de la línea y el
        IVA salía corto en los bienes con ICE (alcohol, combustible, telecom).
        """
        base = self.precio_total_sin_impuesto
        iva = str(CodigoImpuesto.IVA.value)
        indices = list(range(len(self.impuestos)))
        resueltos: Dict[int, Impuesto] = {}

        # Primero los que no son IVA: su importe entra en la base del IVA.
        adicionales = Decimal("0")
        for indice in indices:
            impuesto = self.impuestos[indice]
            if impuesto.codigo_valor() == iva:
                continue
            resuelto = _impuesto_con_base(impuesto, base)
            resueltos[indice] = resuelto
            adicionales += a_decimal(resuelto.valor)

        for indice in indices:
            impuesto = self.impuestos[indice]
            if impuesto.codigo_valor() != iva:
                continue
            resueltos[indice] = _impuesto_con_base(impuesto, base + adicionales)

        return [resueltos[indice] for indice in indices]

    def validar(self) -> None:
        if not self.descripcion:
            raise ErrorValidacion("La descripción del detalle es obligatoria.")
        if a_decimal(self.cantidad) <= 0:
            raise ErrorValidacion(f"La cantidad debe ser mayor que cero: {self.cantidad!r}")
        if self.precio_total_sin_impuesto < 0:
            raise ErrorValidacion("El descuento no puede superar el subtotal del detalle.")
        if not self.impuestos:
            raise ErrorValidacion("Cada detalle debe tener al menos un impuesto.")


@dataclass
class TotalImpuesto:
    """Fila de ``totalConImpuestos``: impuesto agrupado por código y porcentaje."""

    codigo: str
    codigo_porcentaje: str
    base_imponible: Decimal
    valor: Decimal
    tarifa: Optional[Decimal] = None
    descuento_adicional: Decimal = Decimal("0")
    valor_devolucion_iva: Optional[Decimal] = None


@dataclass
class Pago:
    """Forma de pago declarada en el comprobante."""

    forma_pago: str = FormaPago.SIN_SISTEMA_FINANCIERO
    total: Decimalizable = Decimal("0")
    plazo: Optional[Decimalizable] = None
    unidad_tiempo: Optional[str] = None

    def forma_valor(self) -> str:
        return str(getattr(self.forma_pago, "value", self.forma_pago))


@dataclass
class Compensacion:
    """Compensación de valores dentro del comprobante."""

    codigo: str
    tarifa: Decimalizable
    valor: Decimalizable


@dataclass
class ImpuestoReembolso:
    codigo: str
    codigo_porcentaje: str
    tarifa: Decimalizable
    base_imponible_reembolso: Decimalizable
    impuesto_reembolso: Decimalizable


@dataclass
class DetalleReembolso:
    """Factura de reembolso referenciada dentro del comprobante."""

    tipo_identificacion_proveedor: str
    identificacion_proveedor: str
    tipo_proveedor: str
    cod_doc_reembolso: str
    estab_doc_reembolso: str
    pto_emi_doc_reembolso: str
    secuencial_doc_reembolso: str
    fecha_emision_doc_reembolso: date
    numero_autorizacion: str
    impuestos: List[ImpuestoReembolso] = field(default_factory=list)
    cod_pais_pago_proveedor: Optional[str] = None


@dataclass
class Reembolso:
    detalles: List[DetalleReembolso] = field(default_factory=list)


@dataclass
class Motivo:
    """Razón que sustenta una nota de débito."""

    razon: str
    valor: Decimalizable


@dataclass
class ImpuestoDocSustento:
    codigo: str
    codigo_porcentaje: str
    base_imponible: Decimalizable
    tarifa: Decimalizable
    valor: Decimalizable


@dataclass
class ImpuestoRetencion:
    """Retención aplicada sobre un documento sustento."""

    codigo: str
    codigo_retencion: str
    base_imponible: Decimalizable
    porcentaje_retener: Decimalizable
    valor_retenido: Decimalizable

    def codigo_valor(self) -> str:
        return str(getattr(self.codigo, "value", self.codigo))


@dataclass
class PagoRetencion:
    forma_pago: str
    total: Decimalizable


@dataclass
class DocSustento:
    """Documento que sustenta la retención."""

    cod_sustento: str
    cod_doc_sustento: str
    num_doc_sustento: str
    fecha_emision: date
    total_sin_impuestos: Decimalizable
    importe_total: Decimalizable
    impuestos: List[ImpuestoDocSustento] = field(default_factory=list)
    retenciones: List[ImpuestoRetencion] = field(default_factory=list)
    pagos: List[PagoRetencion] = field(default_factory=list)
    fecha_registro_contable: Optional[date] = None
    num_aut_doc_sustento: Optional[str] = None
    pago_loc_ext: str = "01"
    tipo_regi: Optional[str] = None
    pais_efec_pago: Optional[str] = None
    aplic_conv_dob_trib: Optional[str] = None
    pag_ext_suj_ret_nor_leg: Optional[str] = None


@dataclass
class Retencion:
    """Agrupa las retenciones aplicadas a un documento sustento."""

    impuestos: List[ImpuestoRetencion] = field(default_factory=list)


@dataclass
class DetalleGuia:
    """Bien o bienes transportados en una guía de remisión."""

    descripcion: str
    cantidad: Decimalizable
    codigo_principal: Optional[str] = None
    codigo_adicional: Optional[str] = None
    detalles_adicionales: Dict[str, str] = field(default_factory=dict)


@dataclass
class Destinatario:
    """Destinatario de una guía de remisión."""

    razon_social: str
    identificacion: str
    direccion: str
    motivo_traslado: str
    detalles: List[DetalleGuia] = field(default_factory=list)
    tipo_identificacion: str = TipoIdentificacion.RUC
    doc_aduanero_unico: Optional[str] = None
    cod_estab_destino: Optional[str] = None
    ruta: Optional[str] = None
    cod_doc_sustento: Optional[str] = None
    num_doc_sustento: Optional[str] = None
    num_aut_doc_sustento: Optional[str] = None
    fecha_emision_doc_sustento: Optional[date] = None


@dataclass
class InfoAdicional:
    """Pares ``nombre``/``valor`` de la sección ``infoAdicional`` (máximo 15)."""

    campos: Dict[str, Any] = field(default_factory=dict)

    MAXIMO = 15

    def a_lista(self) -> List[Dict[str, str]]:
        items = [(str(k), str(v)) for k, v in self.campos.items() if v not in (None, "")]
        if len(items) > self.MAXIMO:
            raise ErrorValidacion(
                f"infoAdicional admite máximo {self.MAXIMO} campos, se recibieron {len(items)}."
            )
        return [{"nombre": nombre, "valor": valor} for nombre, valor in items]


MONEDA_POR_DEFECTO = Moneda.DOLAR.value
