"""Avisos de la revisión periódica: informe y correo a los responsables.

Se usa desde el comando ``python manage.py revisar_firma`` y desde la tarea de
Celery ``sri_fe.revisar_certificado``, que se programa una vez al día con
:func:`factec.django.conf.planificador`::

    # settings.py
    from factec.django.conf import planificador

    CELERY_BEAT_SCHEDULE = {**planificador()}

    FACTURACION_ELECTRONICA = {
        ...
        # A quién avisar cuando la firma esté vencida o a punto de vencer.
        "CORREOS_AVISO": ["administracion@mitienda.ec"],
    }

Así el aviso de que la firma electrónica vence llega **antes** de que una emisión
falle, en lugar de descubrirlo cuando el SRI devuelve el comprobante.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.mail import send_mail

from ..revision import DIAS_AVISO_CERTIFICADO
from . import conf, models

__all__ = [
    "avisar_por_correo",
    "certificados",
    "destinatarios",
    "pendientes",
    "revisar_firma",
    "texto",
]

logger = logging.getLogger(__name__)


def certificados(*, dias_aviso: Optional[int] = None) -> List[Dict[str, Any]]:
    """Revisa el certificado de cada configuración del emisor.

    Devuelve un informe por configuración con sus ``problemas`` (lo que impide
    emitir) y sus ``avisos`` (por ejemplo, que la firma vence en pocos días).
    """
    dias = (
        int(conf.obtener("DIAS_AVISO_CERTIFICADO", DIAS_AVISO_CERTIFICADO))
        if dias_aviso is None
        else int(dias_aviso)
    )
    informes: List[Dict[str, Any]] = []
    for configuracion in models.ConfiguracionEmisor.objects.all().order_by("-activo", "nombre"):
        revisado = configuracion.revisar_certificado(dias_aviso=dias)
        informes.append(
            {
                "id": configuracion.pk,
                "configuracion": str(configuracion),
                "activo": configuracion.activo,
                "ambiente": configuracion.get_ambiente_display(),
                "titular": revisado.titular,
                "ruc": revisado.ruc,
                "dias_restantes": revisado.dias_restantes,
                "valido_hasta": (
                    revisado.valido_hasta.strftime("%d/%m/%Y") if revisado.valido_hasta else ""
                ),
                "problemas": list(revisado.problemas),
                "avisos": list(revisado.avisos),
                **revisado.a_dict(),
            }
        )
    return informes


def pendientes(*, limite: int = 20) -> List[Dict[str, Any]]:
    """Comprobantes sin enviar que quedaron de días anteriores.

    Se listan para que se sepa cuáles van a firmarse con la fecha de hoy (y no con
    la fecha con la que se prepararon) y cuáles esperan a que se arregle el
    certificado.
    """
    from ..sri import fechas

    hoy = fechas.hoy_en_ecuador()
    sin_enviar = models.ComprobanteEmitido.objects.filter(intentos=0).exclude(
        fecha_emision=hoy
    ).order_by("fecha_emision")

    return [
        {
            "id": registro.pk,
            "clave_acceso": registro.clave_acceso,
            "numero": registro.numero_comprobante,
            "fecha_emision": registro.fecha_emision.isoformat(),
            "estado": registro.estado,
        }
        for registro in sin_enviar[:limite]
    ]


def revisar_firma(*, dias_aviso: Optional[int] = None, con_pendientes: bool = True) -> Dict[str, Any]:
    """Informe completo de la firma electrónica (y, si se pide, de los pendientes)."""
    informes = certificados(dias_aviso=dias_aviso)
    problemas = [
        f"{informe['configuracion']}: {texto}"
        for informe in informes
        for texto in informe["problemas"]
    ]
    avisos = [
        f"{informe['configuracion']}: {texto}"
        for informe in informes
        for texto in informe["avisos"]
    ]
    resultado: Dict[str, Any] = {
        "ok": not problemas,
        "revisados": len(informes),
        "certificados": informes,
        "problemas": problemas,
        "avisos": avisos,
    }
    if con_pendientes:
        resultado["pendientes"] = pendientes()
    return resultado


def destinatarios() -> List[str]:
    """Correos a los que avisar: ``CORREOS_AVISO`` y, si no hay, los ``ADMINS``."""
    configurados = conf.obtener("CORREOS_AVISO") or []
    if isinstance(configurados, str):
        configurados = [configurados]
    correos = [str(correo).strip() for correo in configurados if str(correo).strip()]
    if correos:
        return correos

    return [
        correo
        for _, correo in getattr(settings, "ADMINS", []) or []
        if correo
    ]


def texto(informe: Dict[str, Any]) -> str:
    """Informe en texto plano, para el correo o la consola."""
    lineas = ["Revisión de la firma electrónica del SRI", ""]
    for revisado in informe.get("certificados", []):
        estado = "correcto" if not revisado["problemas"] else "CON PROBLEMAS"
        lineas.append(f"— {revisado['configuracion']}: {estado}")
        if revisado.get("titular"):
            lineas.append(f"   Titular: {revisado['titular']}")
        if revisado.get("valido_hasta"):
            lineas.append(
                f"   Válido hasta: {revisado['valido_hasta']} "
                f"({revisado.get('dias_restantes')} día(s))"
            )
        for texto_problema in revisado["problemas"]:
            lineas.append(f"   ERROR: {texto_problema}")
        for texto_aviso in revisado["avisos"]:
            lineas.append(f"   AVISO: {texto_aviso}")
        lineas.append("")

    pendientes_ = informe.get("pendientes") or []
    if pendientes_:
        lineas.append(
            f"Comprobantes sin enviar de días anteriores: {len(pendientes_)} "
            "(se firmarán con la fecha de hoy cuando se emitan)"
        )
        for registro in pendientes_:
            lineas.append(
                f"   {registro['numero']} del {registro['fecha_emision']} "
                f"({registro['estado']})"
            )
        lineas.append("")
    return "\n".join(lineas).strip() + "\n"


def avisar_por_correo(
    informe: Dict[str, Any],
    *,
    destinatarios_: Optional[List[str]] = None,
) -> int:
    """Envía el informe por correo y devuelve a cuántos destinatarios se envió.

    Sin destinatarios configurados (``CORREOS_AVISO`` o ``ADMINS``) no se envía
    nada: se deja constancia en el log.
    """
    correos = destinatarios_ or destinatarios()
    if not correos:
        logger.warning(
            "No hay correos configurados para los avisos de facturación "
            "(FACTURACION_ELECTRONICA['CORREOS_AVISO']): no se envía ningún correo."
        )
        return 0

    asunto = "Firma electrónica con problemas" if informe.get("problemas") else (
        "Firma electrónica: aviso"
    )
    try:
        enviados = send_mail(
            subject=f"[SRI] {asunto}",
            message=texto(informe),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=list(correos),
        )
    except Exception as exc:  # noqa: BLE001 - un aviso no debe tumbar el comando
        logger.error("No se pudo enviar el aviso de la firma electrónica: %s", exc)
        return 0

    logger.info("Aviso de firma electrónica enviado a %s correo(s).", enviados)
    return enviados
