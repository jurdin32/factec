"""Endpoints de los webservices del SRI.

El SRI publica dos ambientes con la misma estructura de servicios:

======== ==========================================
Ambiente Host
======== ==========================================
Pruebas  ``celcer.sri.gob.ec``
Producción ``cel.sri.gob.ec``
======== ==========================================

Cada ambiente expone *recepción* (``validarComprobante``) y *autorización*
(``autorizacionComprobante``).
"""

from __future__ import annotations

from typing import Dict, Optional

from ..catalogos import Ambiente

__all__ = [
    "HOSTS",
    "RUTA_RECEPCION",
    "RUTA_AUTORIZACION",
    "NS_RECEPCION",
    "NS_AUTORIZACION",
    "url_recepcion",
    "url_autorizacion",
    "host_ambiente",
    "AMBIENTES",
]

HOSTS: Dict[int, str] = {
    int(Ambiente.PRUEBAS): "https://celcer.sri.gob.ec",
    int(Ambiente.PRODUCCION): "https://cel.sri.gob.ec",
}

RUTA_RECEPCION = "/comprobantes-electronicos-ws/RecepcionComprobantesOffline"
RUTA_AUTORIZACION = "/comprobantes-electronicos-ws/AutorizacionComprobantesOffline"

NS_RECEPCION = "http://ec.gob.sri.ws.recepcion"
NS_AUTORIZACION = "http://ec.gob.sri.ws.autorizacion"

#: Nombres legibles de los ambientes.
AMBIENTES: Dict[int, str] = {1: "pruebas", 2: "producción"}


def host_ambiente(ambiente: object) -> str:
    """Devuelve el host del SRI para el ambiente indicado (1 o 2)."""
    numero = int(getattr(ambiente, "value", ambiente))
    if numero not in HOSTS:
        raise ValueError(f"Ambiente inválido: {ambiente!r}. Use 1 (pruebas) o 2 (producción).")
    return HOSTS[numero]


def url_recepcion(ambiente: object, host: Optional[str] = None) -> str:
    """URL completa del servicio de recepción."""
    return (host or host_ambiente(ambiente)) + RUTA_RECEPCION


def url_autorizacion(ambiente: object, host: Optional[str] = None) -> str:
    """URL completa del servicio de autorización."""
    return (host or host_ambiente(ambiente)) + RUTA_AUTORIZACION
