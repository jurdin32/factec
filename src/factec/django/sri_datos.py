"""Datos del contribuyente obtenidos del SRI y su mapeo a la configuración.

El SRI publica buena parte de lo que necesita la facturación electrónica, así que
la configuración del emisor se completa sola: basta con indicar el RUC.

Qué se obtiene del servicio y qué hay que aportar a mano:

===========================  ==================================================
Campo                        Origen
===========================  ==================================================
``razon_social``             SRI
``regimen``                  SRI (``RIMPE``, ``GENERAL``…)
``categoria``                SRI (``NEGOCIO POPULAR``…)
``obligado_contabilidad``    SRI
``contribuyente_especial``   El SRI dice *si lo es*, pero **no publica el número
                             de resolución**, que es lo que va en el XML
``agente_retencion``         Igual: el SRI dice si lo es, no el n.º
``nombre_comercial``         Manual (no consta en el catastro)
``dir_matriz``               Manual: el SRI no publica direcciones
``dir_establecimiento``      Manual
``estab`` / ``pto_emi``      Manual: dependen de los establecimientos registrados
``ambiente``                 Manual
``certificado`` / clave      Manual: la firma electrónica
===========================  ==================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..excepciones import ErrorFacturacion
from ..sri import consultar_ruc
from ..sri.consulta_ruc import DatosRuc

__all__ = [
    "CAMPOS_AUTOMATICOS",
    "CAMPOS_AUTOMATICOS_TEXTO",
    "CAMPOS_INFORMADOS",
    "CAMPOS_MANUALES",
    "ResultadoConsulta",
    "mapear_datos",
    "consultar_datos",
    "completar_configuracion",
    "avisos_de_datos",
    "faltantes_manuales",
]

#: Campos de texto que se rellenan solos con lo que publica el SRI.
CAMPOS_AUTOMATICOS_TEXTO: Tuple[str, ...] = (
    "razon_social",
    "regimen",
    "categoria",
)

#: Campos que se rellenan solos con lo que publica el SRI.
CAMPOS_AUTOMATICOS: Tuple[str, ...] = CAMPOS_AUTOMATICOS_TEXTO + (
    "obligado_contabilidad",
)

#: Campos de los que el SRI solo dice SI/NO: hay que aportar el número.
CAMPOS_INFORMADOS: Tuple[str, ...] = (
    "contribuyente_especial",
    "agente_retencion",
)

#: Campos que no constan en el catastro del SRI y debe indicar el usuario.
CAMPOS_MANUALES: Tuple[str, ...] = (
    "nombre_comercial",
    "dir_matriz",
    "dir_establecimiento",
    "estab",
    "pto_emi",
    "ambiente",
    "certificado",
    "clave_certificado",
)


@dataclass
class ResultadoConsulta:
    """Resultado de consultar el RUC y preparar los campos del emisor."""

    datos: DatosRuc
    campos: Dict[str, Any]
    avisos: List[str]

    @property
    def encontrado(self) -> bool:
        return self.datos.encontrado


def mapear_datos(datos: DatosRuc) -> Dict[str, Any]:
    """Convierte los datos del SRI en valores de la configuración del emisor."""
    return {
        "razon_social": datos.razon_social,
        "regimen": datos.regimen or "",
        "categoria": datos.categoria or "",
        "obligado_contabilidad": datos.obligado_contabilidad,
    }


def avisos_de_datos(datos: DatosRuc) -> List[str]:
    """Cosas que el SRI informa pero no se pueden deducir por completo."""
    avisos: List[str] = []
    if datos.contribuyente_especial:
        avisos.append(
            "El SRI lo registra como contribuyente especial: indique el número de "
            "resolución en «n.º de contribuyente especial»."
        )
    if datos.agente_retencion:
        avisos.append(
            "El SRI lo registra como agente de retención: indique el número de "
            "resolución en «n.º de resolución de agente de retención»."
        )
    if not datos.activo:
        avisos.append(
            f"El contribuyente no está ACTIVO (estado: {datos.estado}). "
            "El SRI rechazará los comprobantes."
        )
    if datos.es_rimpe:
        avisos.append(
            f"Régimen RIMPE. En el XML se enviará: {datos.regimen_rimpe_texto}"
        )
    return avisos


def consultar_datos(ruc: Any, *, timeout: Optional[float] = None, session: Any = None) -> ResultadoConsulta:
    """Consulta el RUC en el SRI y prepara los campos y los avisos.

    Lanza :class:`~factec.excepciones.ErrorFacturacion` si no se
    puede contactar con el servicio.
    """
    kwargs: Dict[str, Any] = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if session is not None:
        kwargs["session"] = session

    datos = consultar_ruc(ruc, **kwargs)
    return ResultadoConsulta(
        datos=datos,
        campos=mapear_datos(datos) if datos.encontrado else {},
        avisos=avisos_de_datos(datos) if datos.encontrado else [],
    )


def completar_configuracion(
    configuracion: Any,
    *,
    datos: Optional[DatosRuc] = None,
    forzar: bool = False,
    timeout: Optional[float] = None,
) -> List[str]:
    """Rellena en ``configuracion`` los campos que publica el SRI.

    Con ``forzar=False`` (lo habitual) solo se escriben los campos vacíos o que
    discrepen de lo que dice el SRI; así nunca se pisa lo que el usuario escribió
    a propósito... salvo que ``forzar=True``, que sobrescribe todo.

    Devuelve la lista de campos modificados. No guarda: el llamador decide.
    """
    if datos is None:
        resultado = consultar_datos(configuracion.ruc, timeout=timeout)
        datos = resultado.datos

    if not datos.encontrado:
        raise ErrorFacturacion(
            f"El SRI no devolvió datos para el RUC {configuracion.ruc}."
        )

    cambios: List[str] = []
    for campo, valor in mapear_datos(datos).items():
        actual = getattr(configuracion, campo, None)
        vacio = actual in (None, "", False) or (isinstance(actual, str) and not actual.strip())
        if forzar or vacio:
            if actual != valor:
                setattr(configuracion, campo, valor)
                cambios.append(campo)
    return cambios


def faltantes_manuales(configuracion: Any) -> List[str]:
    """Campos manuales que siguen sin informarse."""
    faltan: List[str] = []
    for campo in CAMPOS_MANUALES:
        valor = getattr(configuracion, campo, None)
        if valor in (None, "") or (isinstance(valor, str) and not valor.strip()):
            faltan.append(campo)
    return faltan
