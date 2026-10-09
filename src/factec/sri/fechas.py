"""Fechas y plazos que el SRI valida en los comprobantes.

El SRI rechaza un comprobante cuando su ``fechaEmision`` está fuera del rango de
tolerancia o es posterior a la fecha del servidor. El mensaje que devuelve es::

    identificador: 65
    mensaje: FECHA EMISIÓN EXTEMPORANEA
    información adicional: La fecha de emisión está fuera del rango de
        tolerancia [129600 minutos], o es mayor a la fecha del servidor

129600 minutos son **90 días**. Como el servidor del SRI está en Ecuador (UTC-5,
sin horario de verano desde 1993), comparar contra la fecha local del servidor que
ejecuta la aplicación puede dar un día de diferencia: a las 20:00 en Quito ya es
el día siguiente en UTC.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..excepciones import ErrorValidacion

__all__ = [
    "DESFASE_ECUADOR",
    "DIAS_TOLERANCIA",
    "MENSAJE_EXTEMPORANEA",
    "MINUTOS_TOLERANCIA",
    "hoy_en_ecuador",
    "fecha_para_firmar",
    "validar_fecha_emision",
]

#: Ecuador continental no aplica horario de verano desde 1993: UTC-5 todo el año.
DESFASE_ECUADOR = timedelta(hours=-5)

#: Días de tolerancia que publica el SRI (``129600 minutos``).
DIAS_TOLERANCIA = 90

#: Tolerancia en minutos, tal como la escribe el SRI en el mensaje.
MINUTOS_TOLERANCIA = DIAS_TOLERANCIA * 24 * 60

#: Mensaje con el que el SRI rechaza la fecha de emisión.
MENSAJE_EXTEMPORANEA = "FECHA EMISIÓN EXTEMPORANEA"


def hoy_en_ecuador(momento: Optional[datetime] = None) -> date:
    """Fecha de hoy según el reloj del SRI (Ecuador, UTC-5).

    Se calcula con el desfase fijo en lugar de la zona horaria del servidor: un
    servidor en UTC (el valor por omisión de Django) va un día por delante a
    partir de las 19:00 de Ecuador, y eso hace que se emitan comprobantes con la
    fecha de mañana.
    """
    ahora = momento or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    return (ahora.astimezone(timezone.utc) + DESFASE_ECUADOR).date()


def fecha_para_firmar(
    explicita: Optional[date] = None,
    *,
    momento: Optional[datetime] = None,
) -> date:
    """Fecha de emisión que se usa al firmar: la de hoy en Ecuador.

    El SRI exige que el comprobante se firme el día de su emisión (o, como muy
    tarde, dentro del rango de tolerancia). Por eso un comprobante que quedó
    preparado y se emite días después se firma con la fecha del día de la firma, y
    no con la fecha en la que se preparó. Una fecha indicada a propósito
    (``explicita``) se respeta tal cual.
    """
    return explicita or hoy_en_ecuador(momento)


def validar_fecha_emision(
    fecha: date,
    *,
    hoy: Optional[date] = None,
    dias: Optional[int] = None,
) -> None:
    """Comprueba que el SRI vaya a aceptar ``fecha`` como fecha de emisión.

    Lanza :class:`~factec.excepciones.ErrorValidacion` si es posterior a hoy en
    Ecuador (la emisión no puede ser futura) o si es anterior al límite de
    tolerancia (90 días). Con ``hoy`` se puede fijar la fecha de referencia, útil
    en las pruebas.
    """
    referencia = hoy or hoy_en_ecuador()
    dias = DIAS_TOLERANCIA if dias is None else dias
    if fecha > referencia:
        raise ErrorValidacion(
            f"La fecha de emisión {fecha:%d/%m/%Y} es posterior a hoy "
            f"({referencia:%d/%m/%Y}) en Ecuador: el SRI la devuelve con "
            f"«{MENSAJE_EXTEMPORANEA}» (mensaje 65). Un comprobante no se puede "
            "emitir con fecha futura. Revise la fecha del comprobante y, si el "
            "servidor está en UTC, configure TIME_ZONE = «America/Guayaquil»."
        )

    limite = referencia - timedelta(days=dias)
    if fecha < limite:
        raise ErrorValidacion(
            f"La fecha de emisión {fecha:%d/%m/%Y} está fuera del rango de "
            f"tolerancia del SRI ({dias} días): como muy antiguo, {limite:%d/%m/%Y}. "
            f"El SRI la devuelve con «{MENSAJE_EXTEMPORANEA}» (mensaje 65)."
        )
