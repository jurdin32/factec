"""Integración con los webservices del SRI (recepción y autorización)."""

from __future__ import annotations

from .consulta_ruc import URL_CATASTRO, URL_EXISTE, DatosRuc, consultar_ruc, existe_ruc
from .fechas import (
    DESFASE_ECUADOR,
    DIAS_TOLERANCIA,
    MENSAJE_EXTEMPORANEA,
    hoy_en_ecuador,
    validar_fecha_emision,
)
from .endpoints import (
    AMBIENTES,
    HOSTS,
    NS_AUTORIZACION,
    NS_RECEPCION,
    host_ambiente,
    url_autorizacion,
    url_recepcion,
)
from .soap import (
    ESTADO_AUTORIZADO,
    ESTADO_DEVUELTA,
    ESTADO_EN_PROCESO,
    ESTADO_NO_AUTORIZADO,
    ESTADO_RECIBIDA,
    Autorizacion,
    ClienteSRI,
    Mensaje,
    RespuestaAutorizacion,
    RespuestaRecepcion,
    construir_sobre_autorizacion,
    construir_sobre_recepcion,
)

__all__ = [
    "ClienteSRI",
    "DIAS_TOLERANCIA",
    "DESFASE_ECUADOR",
    "MENSAJE_EXTEMPORANEA",
    "hoy_en_ecuador",
    "validar_fecha_emision",
    "Mensaje",
    "RespuestaRecepcion",
    "RespuestaAutorizacion",
    "Autorizacion",
    "DatosRuc",
    "consultar_ruc",
    "existe_ruc",
    "URL_CATASTRO",
    "URL_EXISTE",
    "ESTADO_RECIBIDA",
    "ESTADO_DEVUELTA",
    "ESTADO_AUTORIZADO",
    "ESTADO_NO_AUTORIZADO",
    "ESTADO_EN_PROCESO",
    "HOSTS",
    "AMBIENTES",
    "NS_RECEPCION",
    "NS_AUTORIZACION",
    "host_ambiente",
    "url_recepcion",
    "url_autorizacion",
    "construir_sobre_recepcion",
    "construir_sobre_autorizacion",
]
