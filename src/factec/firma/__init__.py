"""Firma electrónica XAdES-BES para comprobantes electrónicos del SRI."""

from __future__ import annotations

from .xades import (
    ALGORITMOS,
    C14N_INCLUSIVO,
    NS_DS,
    NS_XADES,
    Certificado,
    firmar_xml,
    verificar_firma,
)

__all__ = [
    "Certificado",
    "firmar_xml",
    "verificar_firma",
    "ALGORITMOS",
    "NS_DS",
    "NS_XADES",
    "C14N_INCLUSIVO",
]
