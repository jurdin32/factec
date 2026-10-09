"""Catálogos oficiales del SRI para comprobantes electrónicos.

Los códigos provienen de las tablas publicadas en la *Ficha Técnica de
Comprobantes Electrónicos* del SRI. Se exponen como ``Enum`` de cadenas para que
se puedan usar directamente en los modelos, y como diccionarios
``{codigo: descripcion}`` cuando hace falta mostrar la descripción.
"""

from __future__ import annotations

from decimal import Decimal
from enum import IntEnum, Enum

from .excepciones import ErrorValidacion
from typing import Dict, Tuple


class _EnumCodigo(str, Enum):
    """Enum de cadenas cuyo ``str()`` devuelve el código y no ``Nombre.MIEMBRO``.

    Desde Python 3.11 ``str(Miembro)`` de un ``str, Enum`` devuelve
    ``"Clase.MIEMBRO"``, lo que rompería el XML. Estas bases lo evitan.
    """

    def __str__(self) -> str:
        return str(self.value)

    def __format__(self, formato: str) -> str:
        return format(self.value, formato)


class _EnumEntero(IntEnum):
    """``IntEnum`` cuyo ``str()`` devuelve el número."""

    def __str__(self) -> str:
        return str(int(self))

    def __format__(self, formato: str) -> str:
        return format(int(self), formato)


__all__ = [
    "Ambiente",
    "TipoEmision",
    "TipoComprobante",
    "TipoIdentificacion",
    "FormaPago",
    "CodigoImpuesto",
    "CodigoRetencion",
    "TarifaIva",
    "TarifaRetencionIva",
    "MotivoTraslado",
    "TipoSujetoRetenido",
    "PagoLocExt",
    "TipoProveedorReembolso",
    "CodSustento",
    "Moneda",
    "DESCRIPCION_TIPO_IDENTIFICACION",
    "DESCRIPCION_FORMA_PAGO",
    "DESCRIPCION_TARIFA_IVA",
    "DESCRIPCION_MOTIVO_TRASLADO",
    "DESCRIPCION_TIPO_COMPROBANTE",
    "NOMBRES_AMBIENTE",
    "DESCRIPCION_AMBIENTE",
    "leer_ambiente",
    "PORCENTAJE_IVA",
    "PORCENTAJE_RETENCION_IVA",
    "codigo_documento",
]


class Ambiente(_EnumEntero):
    """Ambiente de emisión (SRI, tabla 4).

    * ``1`` — **Pruebas**: comprobantes sin validez fiscal, para ensayar.
    * ``2`` — **Producción**: comprobantes con validez legal ante el SRI.
    """

    PRUEBAS = 1
    PRODUCCION = 2


class TipoEmision(_EnumCodigo):
    """Tipo de emisión (SRI, tabla 3)."""

    NORMAL = "1"
    CONTINGENCIA = "2"


class TipoComprobante(_EnumCodigo):
    """Tipo de comprobante (SRI, tabla 1)."""

    FACTURA = "01"
    LIQUIDACION_COMPRA = "03"
    NOTA_CREDITO = "04"
    NOTA_DEBITO = "05"
    GUIA_REMISION = "06"
    COMPROBANTE_RETENCION = "07"


class TipoIdentificacion(_EnumCodigo):
    """Tipo de identificación del adquirente (SRI, tabla 2)."""

    RUC = "04"
    CEDULA = "05"
    PASAPORTE = "06"
    CONSUMIDOR_FINAL = "07"
    IDENTIFICACION_EXTERIOR = "08"


class FormaPago(_EnumCodigo):
    """Forma de pago (SRI, tabla 24)."""

    SIN_SISTEMA_FINANCIERO = "01"
    COMPENSACION_DEUDAS = "15"
    TARJETA_DEBITO = "16"
    DINERO_ELECTRONICO = "17"
    TARJETA_PREPAGO = "18"
    TARJETA_CREDITO = "19"
    OTROS_SISTEMA_FINANCIERO = "20"
    ENDOSO_TITULOS = "21"


class CodigoImpuesto(_EnumCodigo):
    """Código de impuesto dentro de un detalle (SRI)."""

    IVA = "2"
    ICE = "3"
    IRBPNR = "5"


class CodigoRetencion(_EnumCodigo):
    """Código de impuesto en un comprobante de retención (SRI)."""

    RENTA = "1"
    IVA = "2"
    ISD = "6"


class TarifaIva(_EnumCodigo):
    """Código de porcentaje de IVA (SRI, tabla 16)."""

    IVA_0 = "0"
    IVA_12 = "2"
    IVA_14 = "3"
    IVA_15 = "4"
    IVA_5 = "5"
    NO_OBJETO = "6"
    EXENTO = "7"
    IVA_DIFERENCIADO = "8"


class TarifaRetencionIva(_EnumCodigo):
    """Código de porcentaje de retención de IVA (SRI, tabla 20)."""

    RETENCION_10 = "1"
    RETENCION_20 = "2"
    RETENCION_30 = "3"
    RETENCION_70 = "4"
    RETENCION_100 = "5"


class MotivoTraslado(_EnumCodigo):
    """Motivo de traslado en una guía de remisión (SRI, tabla 21)."""

    VENTA = "01"
    COMPRA = "02"
    TRANSFORMACION = "03"
    CONSIGNACION = "04"
    DEVOLUCION = "05"
    TRASLADO_ENTRE_ESTABLECIMIENTOS = "06"
    EMISOR_ITINERANTE = "07"
    EXPORTACION = "08"
    IMPORTACION = "09"
    OTROS = "10"


class TipoSujetoRetenido(_EnumCodigo):
    """Tipo de sujeto retenido (SRI, tabla 23)."""

    CONTRIBUYENTE = "01"
    NO_CONTRIBUYENTE = "02"


class PagoLocExt(_EnumCodigo):
    """Pago a residente o al exterior en un comprobante de retención."""

    LOCAL = "01"
    EXTERIOR = "02"


class TipoProveedorReembolso(_EnumCodigo):
    """Tipo de proveedor en el detalle de reembolsos."""

    CONTRIBUYENTE_RUC = "01"
    EXTERIOR = "02"


class CodSustento(_EnumCodigo):
    """Código de sustento tributario de un documento (SRI, tabla 5)."""

    CREDITO_TRIBUTARIO_IVA = "01"
    COSTO_GASTO_DEDUCIBLE = "02"
    ACTIVO_FIJO = "03"
    PROMOCIONES = "04"
    GASTOS_EDUCACION = "05"


class Moneda(_EnumCodigo):
    """Moneda del comprobante. El SRI usa ``DOLAR`` para USD."""

    DOLAR = "DOLAR"


DESCRIPCION_TIPO_IDENTIFICACION: Dict[str, str] = {
    TipoIdentificacion.RUC.value: "RUC",
    TipoIdentificacion.CEDULA.value: "Cédula",
    TipoIdentificacion.PASAPORTE.value: "Pasaporte",
    TipoIdentificacion.CONSUMIDOR_FINAL.value: "Consumidor final",
    TipoIdentificacion.IDENTIFICACION_EXTERIOR.value: "Identificación del exterior",
}

DESCRIPCION_FORMA_PAGO: Dict[str, str] = {
    FormaPago.SIN_SISTEMA_FINANCIERO.value: "Sin utilización del sistema financiero",
    FormaPago.COMPENSACION_DEUDAS.value: "Compensación de deudas",
    FormaPago.TARJETA_DEBITO.value: "Tarjeta de débito",
    FormaPago.DINERO_ELECTRONICO.value: "Dinero electrónico",
    FormaPago.TARJETA_PREPAGO.value: "Tarjeta prepago",
    FormaPago.TARJETA_CREDITO.value: "Tarjeta de crédito",
    FormaPago.OTROS_SISTEMA_FINANCIERO.value: "Otros con utilización del sistema financiero",
    FormaPago.ENDOSO_TITULOS.value: "Endoso de títulos",
}

DESCRIPCION_TARIFA_IVA: Dict[str, str] = {
    TarifaIva.IVA_0.value: "IVA 0%",
    TarifaIva.IVA_12.value: "IVA 12%",
    TarifaIva.IVA_14.value: "IVA 14%",
    TarifaIva.IVA_15.value: "IVA 15%",
    TarifaIva.IVA_5.value: "IVA 5%",
    TarifaIva.NO_OBJETO.value: "No objeto de impuesto",
    TarifaIva.EXENTO.value: "Exento de IVA",
    TarifaIva.IVA_DIFERENCIADO.value: "IVA diferenciado",
}

DESCRIPCION_MOTIVO_TRASLADO: Dict[str, str] = {
    MotivoTraslado.VENTA.value: "Venta",
    MotivoTraslado.COMPRA.value: "Compra",
    MotivoTraslado.TRANSFORMACION.value: "Transformación",
    MotivoTraslado.CONSIGNACION.value: "Consignación",
    MotivoTraslado.DEVOLUCION.value: "Devolución",
    MotivoTraslado.TRASLADO_ENTRE_ESTABLECIMIENTOS.value: "Traslado entre establecimientos",
    MotivoTraslado.EMISOR_ITINERANTE.value: "Traslado por emisor itinerante",
    MotivoTraslado.EXPORTACION.value: "Exportación",
    MotivoTraslado.IMPORTACION.value: "Importación",
    MotivoTraslado.OTROS.value: "Otros",
}

#: Nombres con los que se puede escribir el ambiente en lugar del número.
NOMBRES_AMBIENTE: Dict[str, int] = {
    "1": int(Ambiente.PRUEBAS),
    "pruebas": int(Ambiente.PRUEBAS),
    "prueba": int(Ambiente.PRUEBAS),
    "test": int(Ambiente.PRUEBAS),
    "testing": int(Ambiente.PRUEBAS),
    "sandbox": int(Ambiente.PRUEBAS),
    "certificacion": int(Ambiente.PRUEBAS),
    "certificación": int(Ambiente.PRUEBAS),
    "2": int(Ambiente.PRODUCCION),
    "produccion": int(Ambiente.PRODUCCION),
    "producción": int(Ambiente.PRODUCCION),
    "prod": int(Ambiente.PRODUCCION),
    "production": int(Ambiente.PRODUCCION),
    "real": int(Ambiente.PRODUCCION),
}

#: Cómo se muestra cada ambiente (para mensajes y ayuda del admin).
DESCRIPCION_AMBIENTE: Dict[int, str] = {
    int(Ambiente.PRUEBAS): "Pruebas (1) — sin validez fiscal, para ensayar",
    int(Ambiente.PRODUCCION): "Producción (2) — con validez legal ante el SRI",
}


def leer_ambiente(valor: Any) -> int:
    """Interpreta el ambiente: acepta el número o su nombre.

    Sirve para escribir ``AMBIENTE: "pruebas"`` (o ``"producción"``, ``"test"``,
    ``1``…) en lugar de tener que recordar el número. Lanza
    :class:`~factec.excepciones.ErrorValidacion` con la lista de nombres válidos si
    no se entiende.
    """
    if isinstance(valor, Ambiente) or isinstance(valor, bool):
        return int(valor)
    if valor is None:
        raise ErrorValidacion(
            "Falta el ambiente: use «pruebas» (1) o «producción» (2)."
        )
    if isinstance(valor, int):
        numero = int(valor)
    elif isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in NOMBRES_AMBIENTE:
            return NOMBRES_AMBIENTE[texto]
        if texto.isdigit():
            numero = int(texto)
        else:
            raise ErrorValidacion(
                f"Ambiente desconocido: {valor!r}. Use «pruebas» o «producción» "
                f"(o 1 y 2)."
            )
    else:
        numero = int(getattr(valor, "value", valor))

    if numero not in DESCRIPCION_AMBIENTE:
        raise ErrorValidacion(
            f"Ambiente inválido: {valor!r}. Use «pruebas» (1) o «producción» (2)."
        )
    return numero


DESCRIPCION_TIPO_COMPROBANTE: Dict[str, str] = {
    TipoComprobante.FACTURA.value: "Factura",
    TipoComprobante.LIQUIDACION_COMPRA.value: "Liquidación de compra",
    TipoComprobante.NOTA_CREDITO.value: "Nota de crédito",
    TipoComprobante.NOTA_DEBITO.value: "Nota de débito",
    TipoComprobante.GUIA_REMISION.value: "Guía de remisión",
    TipoComprobante.COMPROBANTE_RETENCION.value: "Comprobante de retención",
}

#: Tarifa numérica asociada a cada ``codigoPorcentaje`` de IVA.
PORCENTAJE_IVA: Dict[str, Decimal] = {
    TarifaIva.IVA_0.value: Decimal("0.00"),
    TarifaIva.IVA_12.value: Decimal("12.00"),
    TarifaIva.IVA_14.value: Decimal("14.00"),
    TarifaIva.IVA_15.value: Decimal("15.00"),
    TarifaIva.IVA_5.value: Decimal("5.00"),
    TarifaIva.NO_OBJETO.value: Decimal("0.00"),
    TarifaIva.EXENTO.value: Decimal("0.00"),
    TarifaIva.IVA_DIFERENCIADO.value: Decimal("0.00"),
}

#: Porcentaje retenido asociado a cada código de retención de IVA.
PORCENTAJE_RETENCION_IVA: Dict[str, Decimal] = {
    TarifaRetencionIva.RETENCION_10.value: Decimal("10.00"),
    TarifaRetencionIva.RETENCION_20.value: Decimal("20.00"),
    TarifaRetencionIva.RETENCION_30.value: Decimal("30.00"),
    TarifaRetencionIva.RETENCION_70.value: Decimal("70.00"),
    TarifaRetencionIva.RETENCION_100.value: Decimal("100.00"),
}

#: ``(codDoc, version)`` que corresponde a cada tipo de comprobante.
VERSIONES: Dict[str, str] = {
    TipoComprobante.FACTURA.value: "1.1.0",
    TipoComprobante.LIQUIDACION_COMPRA.value: "1.1.0",
    TipoComprobante.NOTA_CREDITO.value: "1.1.0",
    TipoComprobante.NOTA_DEBITO.value: "1.0.0",
    TipoComprobante.GUIA_REMISION.value: "1.1.0",
    TipoComprobante.COMPROBANTE_RETENCION.value: "2.0.0",
}

#: Etiqueta del elemento raíz XML de cada comprobante.
ETIQUETA_RAIZ: Dict[str, str] = {
    TipoComprobante.FACTURA.value: "factura",
    TipoComprobante.LIQUIDACION_COMPRA.value: "liquidacionCompra",
    TipoComprobante.NOTA_CREDITO.value: "notaCredito",
    TipoComprobante.NOTA_DEBITO.value: "notaDebito",
    TipoComprobante.GUIA_REMISION.value: "guiaRemision",
    TipoComprobante.COMPROBANTE_RETENCION.value: "comprobanteRetencion",
}

#: Impuestos que acepta la sección ``impuestos`` de cada comprobante.
IMPUESTOS_PERMITIDOS: Dict[str, Tuple[str, ...]] = {
    TipoComprobante.FACTURA.value: (
        CodigoImpuesto.IVA.value,
        CodigoImpuesto.ICE.value,
        CodigoImpuesto.IRBPNR.value,
    ),
    TipoComprobante.LIQUIDACION_COMPRA.value: (
        CodigoImpuesto.IVA.value,
        CodigoImpuesto.ICE.value,
        CodigoImpuesto.IRBPNR.value,
    ),
    TipoComprobante.NOTA_CREDITO.value: (
        CodigoImpuesto.IVA.value,
        CodigoImpuesto.ICE.value,
        CodigoImpuesto.IRBPNR.value,
    ),
    TipoComprobante.NOTA_DEBITO.value: (
        CodigoImpuesto.IVA.value,
        CodigoImpuesto.ICE.value,
        CodigoImpuesto.IRBPNR.value,
    ),
    TipoComprobante.COMPROBANTE_RETENCION.value: (
        CodigoRetencion.RENTA.value,
        CodigoRetencion.IVA.value,
        CodigoRetencion.ISD.value,
    ),
}


def codigo_documento(valor: object) -> str:
    """Normaliza un ``Enum`` o una cadena al código de dos dígitos del SRI."""
    codigo = getattr(valor, "value", valor)
    return str(codigo)
