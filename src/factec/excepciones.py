"""Excepciones del paquete de facturación electrónica."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ErrorFacturacion",
    "ErrorValidacion",
    "ErrorFirma",
    "ErrorCertificado",
    "ErrorRevision",
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


class ErrorRevision(ErrorValidacion):
    """La revisión previa impide emitir.

    Se lanza cuando el certificado de firma no se puede usar (vencido, de otro
    contribuyente) o cuando los datos del comprobante harían que el SRI lo
    devolviera. Lleva el informe completo en :attr:`informe`::

        try:
            servicios.emitir(documento)
        except ErrorRevision as exc:
            exc.informe.problemas    # lo que hay que corregir
            exc.informe.avisos       # lo que conviene atender
    """

    def __init__(self, mensaje: str = "", informe: Any = None, problemas=()):
        super().__init__(mensaje or _texto_del_informe(informe, problemas))
        self.informe = informe
        self.problemas = list(problemas or getattr(informe, "problemas", []) or [])
        self.avisos = list(getattr(informe, "avisos", []) or [])


def _texto_del_informe(informe: Any, problemas) -> str:
    """Frase de la excepción a partir del informe de revisión."""
    partes = list(problemas or getattr(informe, "problemas", []) or [])
    if not partes:
        return "La revisión previa a la emisión no pasó."
    return "No se emitió: " + "; ".join(str(parte) for parte in partes)


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
