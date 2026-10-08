"""Excepciones del paquete de facturación electrónica."""

from __future__ import annotations

__all__ = [
    "ErrorFacturacion",
    "ErrorValidacion",
    "ErrorFirma",
    "ErrorCertificado",
    "ErrorSRI",
    "ErrorRecepcion",
    "ErrorAutorizacion",
]


class ErrorFacturacion(Exception):
    """Error base de la librería."""


class ErrorValidacion(ErrorFacturacion):
    """Los datos del comprobante no cumplen las reglas del SRI."""


class ErrorFirma(ErrorFacturacion):
    """No fue posible firmar el comprobante."""


class ErrorCertificado(ErrorFirma):
    """El certificado ``.p12`` no se pudo abrir o no es válido."""


class ErrorSRI(ErrorFacturacion):
    """Error de comunicación con los webservices del SRI."""


class ErrorRecepcion(ErrorSRI):
    """El comprobante fue rechazado o devuelto en recepción."""

    def __init__(self, mensaje: str, estado: str = "", clave_acceso: str = "", mensajes=()):
        super().__init__(mensaje)
        self.estado = estado
        self.clave_acceso = clave_acceso
        self.mensajes = list(mensajes)


class ErrorAutorizacion(ErrorSRI):
    """El comprobante no fue autorizado por el SRI."""

    def __init__(self, mensaje: str, estado: str = "", clave_acceso: str = "", mensajes=()):
        super().__init__(mensaje)
        self.estado = estado
        self.clave_acceso = clave_acceso
        self.mensajes = list(mensajes)
