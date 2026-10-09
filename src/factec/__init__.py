"""Facturación electrónica de Ecuador (SRI).

Genera, firma y envía los seis comprobantes electrónicos que exige el SRI:
factura, liquidación de compra, nota de crédito, nota de débito, guía de
remisión y comprobante de retención.

Uso rápido::

    from factec import EmisorElectronico, Emisor, Receptor, Detalle

    emisor = EmisorElectronico(
        emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
        certificado="firmante.p12",
        clave_certificado="secreto",
    )
    factura = emisor.factura(
        receptor=Receptor(identificacion="1790012345001", razon_social="Cliente"),
        detalles=[Detalle(descripcion="Servicio", cantidad=1, precio_unitario=100)],
    )
    print(factura.clave)          # clave de acceso (49 dígitos)
    print(factura.to_xml())       # XML sin firmar
    print(emisor.firmar(factura)) # XML firmado con XAdES-BES
"""

from __future__ import annotations

from . import catalogos, clave_acceso, comprobantes, firma, modelos, sri
from .clave_acceso import (
    calcular_digito_verificador,
    descomponer_clave_acceso,
    generar_clave_acceso,
    validar_clave_acceso,
)
from .comprobantes import (
    Comprobante,
    ComprobanteRetencion,
    DetalleFactura,
    Factura,
    GuiaRemision,
    LiquidacionCompra,
    NotaCredito,
    NotaDebito,
    calcular_totales,
    calcular_valor_retenido,
)
from .emisor import EmisorElectronico, ResultadoEmision
from .excepciones import (
    ErrorAutorizacion,
    ErrorCertificado,
    ErrorFacturacion,
    ErrorFirma,
    ErrorRecepcion,
    ErrorSRI,
    ErrorValidacion,
)
from .firma import Certificado, firmar_xml, verificar_firma
from .modelos import (
    Compensacion,
    Destinatario,
    Detalle,
    DetalleGuia,
    DetalleReembolso,
    DocSustento,
    Emisor,
    Impuesto,
    ImpuestoDocSustento,
    ImpuestoReembolso,
    ImpuestoRetencion,
    Motivo,
    Pago,
    PagoRetencion,
    Receptor,
    Reembolso,
    TotalImpuesto,
)
from .sri import (
    ClienteSRI,
    DatosRuc,
    RespuestaAutorizacion,
    RespuestaRecepcion,
    consultar_ruc,
    existe_ruc,
)

__version__ = "1.3.1"

__all__ = [
    "__version__",
    # fachada
    "EmisorElectronico",
    "ResultadoEmision",
    # comprobantes
    "Comprobante",
    "Factura",
    "DetalleFactura",
    "NotaCredito",
    "NotaDebito",
    "ComprobanteRetencion",
    "GuiaRemision",
    "LiquidacionCompra",
    # modelos
    "Emisor",
    "Receptor",
    "Detalle",
    "DetalleGuia",
    "Destinatario",
    "Impuesto",
    "TotalImpuesto",
    "Pago",
    "Compensacion",
    "Motivo",
    "Reembolso",
    "DetalleReembolso",
    "ImpuestoReembolso",
    "DocSustento",
    "ImpuestoDocSustento",
    "ImpuestoRetencion",
    "PagoRetencion",
    # clave de acceso
    "generar_clave_acceso",
    "validar_clave_acceso",
    "descomponer_clave_acceso",
    "calcular_digito_verificador",
    # firma
    "Certificado",
    "firmar_xml",
    "verificar_firma",
    # SRI
    "ClienteSRI",
    "RespuestaRecepcion",
    "RespuestaAutorizacion",
    "DatosRuc",
    "consultar_ruc",
    "existe_ruc",
    # utilidades
    "calcular_totales",
    "calcular_valor_retenido",
    # errores
    "ErrorFacturacion",
    "ErrorValidacion",
    "ErrorFirma",
    "ErrorCertificado",
    "ErrorSRI",
    "ErrorRecepcion",
    "ErrorAutorizacion",
    # submódulos
    "catalogos",
    "modelos",
    "comprobantes",
    "firma",
    "sri",
    "clave_acceso",
]
