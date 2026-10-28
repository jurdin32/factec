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

from . import catalogos, clave_acceso, comprobantes, firma, modelos, revision, sri
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
    ErrorRevision,
    ErrorSRI,
    ErrorValidacion,
)
from .firma import (
    Certificado,
    CertificadoPublico,
    certificado_del_xml,
    firmar_xml,
    ruc_del_certificado,
    verificar_firma,
)
from .lectura import (
    AutorizacionLeida,
    ComprobanteLeido,
    DetalleLeido,
    leer_autorizacion,
    leer_comprobante,
)
from .revision import (
    DIAS_AVISO_CERTIFICADO,
    InformeRevision,
    RevisionCertificado,
    revisar_certificado,
    revisar_emision,
    revisar_xml,
)
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
from .verificacion import (
    InformeVerificacion,
    verificar_comprobante,
    verificar_en_el_sri,
)
from .sri import (
    ClienteSRI,
    DatosRuc,
    RespuestaAutorizacion,
    RespuestaRecepcion,
    consultar_ruc,
    existe_ruc,
)

def _version_instalada() -> str:
    """La versión que está instalada, tomada de los metadatos del paquete.

    Escribirla a mano en este archivo la dejaba desfasada con ``pyproject.toml``
    (pasó con la 1.12.2, que se anunciaba como 1.12.0 y por eso el aviso de
    versión nueva salía siempre). Si el paquete se usa desde el código fuente sin
    instalar, no hay metadatos y se dice lo que diga ``pyproject.toml``.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("factec")
    except (PackageNotFoundError, ValueError):  # pragma: no cover - sin instalar
        return "1.12.3"


__version__ = _version_instalada()

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
    "CertificadoPublico",
    "certificado_del_xml",
    "firmar_xml",
    "verificar_firma",
    "ruc_del_certificado",
    # leer y verificar comprobantes
    "ComprobanteLeido",
    "DetalleLeido",
    "AutorizacionLeida",
    "leer_comprobante",
    "leer_autorizacion",
    "verificar_comprobante",
    "verificar_en_el_sri",
    "InformeVerificacion",
    # revisar antes de emitir
    "revisar_certificado",
    "revisar_emision",
    "revisar_xml",
    "InformeRevision",
    "RevisionCertificado",
    "DIAS_AVISO_CERTIFICADO",
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
    "ErrorRevision",
    "ErrorSRI",
    "ErrorRecepcion",
    "ErrorAutorizacion",
    # submódulos
    "catalogos",
    "lectura",
    "verificacion",
    "revision",
    "modelos",
    "comprobantes",
    "firma",
    "sri",
    "clave_acceso",
]
