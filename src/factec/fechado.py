"""Cambiar la fecha de emisión de un comprobante ya construido.

El SRI solo admite comprobantes con la fecha del día de la firma (o, como mucho,
de los 90 días anteriores) y devuelve los demás con «FECHA EMISIÓN EXTEMPORANEA»
(mensaje 65). Por eso un comprobante que quedó preparado y se firma días después
debe llevar la fecha de la firma: al cambiar la fecha hay que **recalcular la
clave de acceso**, porque la clave empieza por la fecha de emisión::

    from datetime import date

    from factec.fechado import cambiar_fecha_de_emision

    cambiado = cambiar_fecha_de_emision(xml_sin_firmar, date(2026, 10, 8))
    cambiado.fecha_anterior        # 2026-10-01
    cambiado.fecha                 # 2026-10-08
    cambiado.clave_acceso          # la nueva clave, ya con la fecha nueva
    cambiado.xml                   # el XML listo para firmar

Solo tiene sentido con el XML **sin firmar**: al cambiar el contenido, la firma
anterior deja de ser válida.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional, Union
from xml.etree import ElementTree as ET

from .clave_acceso import descomponer_clave_acceso, generar_clave_acceso
from .comprobantes.base import DECLARACION_XML
from .excepciones import ErrorValidacion

__all__ = ["FechaCambiada", "cambiar_fecha_de_emision"]


@dataclass
class FechaCambiada:
    """Resultado de cambiar la fecha de emisión de un comprobante."""

    xml: str
    clave_acceso: str
    clave_anterior: str
    fecha: date
    fecha_anterior: Optional[date] = None

    def a_dict(self) -> dict:
        """Diccionario listo para JSON."""
        return {
            "clave_acceso": self.clave_acceso,
            "clave_anterior": self.clave_anterior,
            "fecha": self.fecha.isoformat(),
            "fecha_anterior": self.fecha_anterior.isoformat() if self.fecha_anterior else None,
        }


def _local(etiqueta: str) -> str:
    """Nombre de la etiqueta sin el espacio de nombres."""
    return etiqueta.rsplit("}", 1)[-1]


def _texto(raiz: Any, etiqueta: str) -> str:
    """Texto del primer nodo con esa etiqueta (sin espacios de nombres)."""
    for nodo in raiz.iter():
        if _local(nodo.tag) == etiqueta:
            return (nodo.text or "").strip()
    return ""


def _nodos(raiz: Any, etiqueta: str) -> list:
    return [nodo for nodo in raiz.iter() if _local(nodo.tag) == etiqueta]


def _fecha(valor: str) -> Optional[date]:
    try:
        return datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def _como_fecha(valor: Any) -> Optional[date]:
    """Convierte a ``date`` lo que sea: ``date``, ``datetime``, ``"08/10/2026"`` o ISO."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        directa = _fecha(valor)
        if directa is not None:
            return directa
        try:
            return date.fromisoformat(valor.strip())
        except ValueError:
            return None
    return None


def cambiar_fecha_de_emision(
    xml: Union[str, bytes],
    fecha: Union[date, datetime, str],
    *,
    codigo_numerico: Optional[str] = None,
) -> FechaCambiada:
    """Devuelve el comprobante con ``fechaEmision`` cambiada y su clave recalculada.

    Se conservan el número de comprobante, el código numérico y todo lo demás: solo
    cambian la fecha de emisión y la clave de acceso. Lanza
    :class:`~factec.excepciones.ErrorValidacion` si el XML no es un comprobante, si
    lleva firma (hay que firmarlo de nuevo) o si no se puede leer su clave.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8")
    if not (xml or "").strip():
        raise ErrorValidacion("No hay XML para cambiar de fecha.")

    try:
        raiz = ET.fromstring(xml.strip().encode("utf-8"))
    except ET.ParseError as exc:
        raise ErrorValidacion(f"El XML no se pudo interpretar: {exc}") from exc

    if any(_local(nodo.tag) in ("Signature", "firma") for nodo in raiz.iter()):
        raise ErrorValidacion(
            "El XML ya está firmado: al cambiar la fecha la firma dejaría de ser "
            "válida. Use el XML sin firmar (o vuelva a construir el comprobante)."
        )

    if not _nodos(raiz, "claveAcceso"):
        raise ErrorValidacion("El XML no parece un comprobante del SRI: no lleva «claveAcceso».")

    clave_anterior = _texto(raiz, "claveAcceso")
    datos_clave = descomponer_clave_acceso(clave_anterior)

    nodos_fecha = _nodos(raiz, "fechaEmision")
    if not nodos_fecha:
        raise ErrorValidacion("El XML no lleva «fechaEmision».")
    fecha_anterior = _fecha(nodos_fecha[0].text or "")

    nueva = _como_fecha(fecha)
    if nueva is None:
        raise ErrorValidacion(f"La fecha de emisión {fecha!r} no es válida.")

    if fecha_anterior == nueva:
        return FechaCambiada(
            xml=xml, clave_acceso=clave_anterior, clave_anterior=clave_anterior,
            fecha=nueva, fecha_anterior=fecha_anterior,
        )

    clave_nueva = generar_clave_acceso(
        fecha_emision=nueva,
        tipo_comprobante=datos_clave["tipo_comprobante"],
        ruc=datos_clave["ruc"],
        ambiente=datos_clave["ambiente"],
        serie=datos_clave["serie"],
        secuencial=datos_clave["secuencial"],
        codigo_numerico=codigo_numerico or datos_clave["codigo_numerico"],
        tipo_emision=datos_clave["tipo_emision"],
    )

    nodos_fecha[0].text = nueva.strftime("%d/%m/%Y")
    for nodo in _nodos(raiz, "claveAcceso"):
        nodo.text = clave_nueva

    return FechaCambiada(
        xml=DECLARACION_XML + ET.tostring(raiz, encoding="unicode"),
        clave_acceso=clave_nueva,
        clave_anterior=clave_anterior,
        fecha=nueva,
        fecha_anterior=fecha_anterior,
    )
