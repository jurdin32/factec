"""Consulta de datos de un RUC en el SRI (catastro de contribuyentes).

Complementa a los webservices de comprobantes electrónicos: sirve para obtener
los datos del emisor (razón social, régimen, obligaciones) que deben coincidir
con los que se declaran en el XML.

El SRI **bloquea** a los clientes que no envían un ``User-Agent`` de navegador:
con ``curl`` la petición se queda colgada sin responder, por eso se envía uno.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

from ..excepciones import ErrorSRI

__all__ = ["DatosRuc", "consultar_ruc", "existe_ruc", "URL_CATASTRO", "URL_EXISTE"]

URL_CATASTRO = (
    "https://srienlinea.sri.gob.ec/sri-catastro-sujeto-servicio-internet/rest/"
    "ConsultaRuc/obtenerPorNumerosRuc"
)
URL_EXISTE = (
    "https://srienlinea.sri.gob.ec/sri-catastro-sujeto-servicio-internet/rest/"
    "ConsolidadoContribuyente/existePorNumeroRuc"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CABECERAS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-EC,es;q=0.9",
    "Referer": (
        "https://srienlinea.sri.gob.ec/sri-en-linea/SriRucWeb/ConsultaRuc/"
        "Consultas/consultaRuc"
    ),
}


def _si_no(valor: Any) -> bool:
    return str(valor or "").strip().upper() == "SI"


def _fecha(valor: Any) -> str:
    """``'2015-04-15 00:00:00.0'`` -> ``'2015-04-15'``."""
    texto = str(valor or "").strip()
    return texto.split(" ")[0] if " " in texto else texto


@dataclass
class DatosRuc:
    """Datos del contribuyente tal como los publica el SRI."""

    ruc: str = ""
    razon_social: str = ""
    estado: str = ""
    tipo_contribuyente: str = ""
    regimen: str = ""
    categoria: str = ""
    actividad_economica: str = ""
    obligado_contabilidad: bool = False
    agente_retencion: bool = False
    contribuyente_especial: bool = False
    contribuyente_fantasma: bool = False
    transacciones_inexistentes: bool = False
    motivo_cancelacion: str = ""
    fecha_inicio_actividades: str = ""
    fecha_cese: str = ""
    fecha_reinicio_actividades: str = ""
    fecha_actualizacion: str = ""
    encontrado: bool = False
    crudo: Dict[str, Any] = field(default_factory=dict)

    @property
    def activo(self) -> bool:
        return self.estado.strip().upper() == "ACTIVO"

    @property
    def es_rimpe(self) -> bool:
        return "RIMPE" in (self.regimen or "").upper()

    @property
    def es_negocio_popular(self) -> bool:
        return "NEGOCIO POPULAR" in (self.categoria or "").upper()

    @property
    def regimen_rimpe_texto(self) -> Optional[str]:
        """Valor exacto que admite ``contribuyenteRimpe`` en el XML."""
        if not self.es_rimpe:
            return None
        from ..modelos import RIMPE_GENERAL, RIMPE_NEGOCIO_POPULAR

        return RIMPE_NEGOCIO_POPULAR if self.es_negocio_popular else RIMPE_GENERAL

    def como_config(self, **extra: Any) -> Dict[str, Any]:
        """Diccionario con las claves que espera ``prueba_config.json``."""
        config: Dict[str, Any] = {
            "ruc": self.ruc,
            "razon_social": self.razon_social,
            "obligado_contabilidad": self.obligado_contabilidad,
            "contribuyente_especial": None,
            "agente_retencion": None,
            "contribuyente_rimpe": self.es_rimpe,
            "regimen": self.categoria or self.regimen or None,
        }
        config.update(extra)
        return config


def consultar_ruc(
    ruc: Any,
    *,
    timeout: float = 20.0,
    session: Optional[requests.Session] = None,
) -> DatosRuc:
    """Consulta el catastro del SRI y devuelve los datos del contribuyente."""
    numero = re.sub(r"\D", "", str(ruc or ""))
    if len(numero) != 13:
        raise ErrorSRI(f"El RUC debe tener 13 dígitos: {ruc!r}")

    sesion = session or requests.Session()
    sesion.headers.update(_CABECERAS)
    try:
        respuesta = sesion.get(URL_CATASTRO, params={"ruc": numero}, timeout=timeout)
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise ErrorSRI(f"No se pudo consultar el RUC en el SRI: {exc}") from exc

    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise ErrorSRI("El SRI devolvió una respuesta que no es JSON.") from exc

    contribuyentes = datos.get("contribuyentes") if isinstance(datos, dict) else None
    if not contribuyentes:
        return DatosRuc(ruc=numero, encontrado=False)

    crudo = contribuyentes[0]
    fechas = crudo.get("informacionFechasContribuyente") or {}
    return DatosRuc(
        ruc=str(crudo.get("numeroRuc") or numero),
        razon_social=str(crudo.get("razonSocial") or ""),
        estado=str(crudo.get("estadoContribuyenteRuc") or ""),
        tipo_contribuyente=str(crudo.get("tipoContribuyente") or ""),
        regimen=str(crudo.get("regimen") or ""),
        categoria=str(crudo.get("categoria") or ""),
        actividad_economica=str(crudo.get("actividadEconomicaPrincipal") or ""),
        obligado_contabilidad=_si_no(crudo.get("obligadoLlevarContabilidad")),
        agente_retencion=_si_no(crudo.get("agenteRetencion")),
        contribuyente_especial=_si_no(crudo.get("contribuyenteEspecial")),
        contribuyente_fantasma=_si_no(crudo.get("contribuyenteFantasma")),
        transacciones_inexistentes=_si_no(crudo.get("transaccionesInexistente")),
        motivo_cancelacion=str(crudo.get("motivoCancelacionSuspension") or ""),
        fecha_inicio_actividades=_fecha(fechas.get("fechaInicioActividades")),
        fecha_cese=_fecha(fechas.get("fechaCese")),
        fecha_reinicio_actividades=_fecha(fechas.get("fechaReinicioActividades")),
        fecha_actualizacion=_fecha(fechas.get("fechaActualizacion")),
        encontrado=True,
        crudo=crudo,
    )


def existe_ruc(ruc: Any, *, timeout: float = 20.0,
               session: Optional[requests.Session] = None) -> bool:
    """Comprobación rápida (``True``/``False``) mediante el SRI."""
    numero = re.sub(r"\D", "", str(ruc or ""))
    if len(numero) != 13:
        return False
    sesion = session or requests.Session()
    sesion.headers.update(_CABECERAS)
    try:
        respuesta = sesion.get(URL_EXISTE, params={"numeroRuc": numero}, timeout=timeout)
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise ErrorSRI(f"No se pudo consultar el RUC en el SRI: {exc}") from exc
    return respuesta.text.strip().lower() == "true"
