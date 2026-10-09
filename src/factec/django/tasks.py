"""Tareas de Celery para emitir y consultar comprobantes.

Las tareas se registran con nombre fijo (``sri_fe.emitir_comprobante`` y
``sri_fe.consultar_autorizacion``) para que ``services.encolar()`` pueda
enviarlas con ``send_task`` sin importar la instancia de Celery del proyecto.

Las dos tareas periódicas del paquete se programan de una vez con
:func:`factec.django.conf.planificador`:

* ``revisar_certificado`` — revisa la firma electrónica una vez al día y avisa por
  correo (y en el log) cuando está vencida o a punto de vencer, **antes** de que
  falle una emisión.
* ``reintentar_pendientes`` — recupera los comprobantes que quedaron en proceso o
  con error.

::

    # settings.py
    from factec.django.conf import planificador

    CELERY_BEAT_SCHEDULE = {**planificador()}

También se puede llamar a mano:``python manage.py revisar_firma``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from celery import shared_task

from . import avisos, conf, models, services

logger = logging.getLogger(__name__)

__all__ = [
    "NOMBRE_EMITIR_COMPROBANTE",
    "NOMBRE_CONSULTAR_AUTORIZACION",
    "NOMBRE_REINTENTAR_PENDIENTES",
    "NOMBRE_COMPROBAR_ACTUALIZACION",
    "NOMBRE_REVISAR_CERTIFICADO",
    "emitir_comprobante",
    "consultar_autorizacion",
    "reintentar_pendientes",
    "revisar_certificado",
]

NOMBRE_EMITIR_COMPROBANTE = conf.nombre_tarea("emitir_comprobante")
NOMBRE_CONSULTAR_AUTORIZACION = conf.nombre_tarea("consultar_autorizacion")
NOMBRE_REINTENTAR_PENDIENTES = conf.nombre_tarea("reintentar_pendientes")
NOMBRE_REVISAR_CERTIFICADO = conf.nombre_tarea("revisar_certificado")
NOMBRE_COMPROBAR_ACTUALIZACION = conf.nombre_tarea("comprobar_actualizacion")

#: Estados que se recuperan con la tarea periódica.
ESTADOS_A_RECUPERAR = (
    models.EstadoComprobante.BORRADOR,
    models.EstadoComprobante.FIRMADO,
    models.EstadoComprobante.EN_PROCESO,
    models.EstadoComprobante.ERROR,
)


def _resumen(registro: models.ComprobanteEmitido) -> Dict[str, Any]:
    return {
        "id": registro.pk,
        "clave_acceso": registro.clave_acceso,
        "estado": registro.estado,
        "numero_autorizacion": registro.numero_autorizacion,
        "intentos": registro.intentos,
        "error": registro.error,
    }


@shared_task(name=NOMBRE_EMITIR_COMPROBANTE, bind=True)
def emitir_comprobante(self, comprobante_id: int) -> Optional[Dict[str, Any]]:
    """Firma, envía y espera la autorización de un comprobante registrado.

    Si el SRI responde ``EN PROCESO`` la tarea se reprograma sola, para no
    mantener ocupado un worker consultando en bucle.
    """
    registro = models.ComprobanteEmitido.objects.filter(pk=comprobante_id).first()
    if registro is None:
        logger.warning("No existe el comprobante %s", comprobante_id)
        return None

    if registro.estado == models.EstadoComprobante.AUTORIZADO:
        logger.info("El comprobante %s ya estaba autorizado", registro.clave_acceso)
        return _resumen(registro)

    services.procesar(registro)
    registro.refresh_from_db()

    if registro.estado == models.EstadoComprobante.EN_PROCESO:
        max_reintentos = int(conf.obtener("REINTENTOS_AUTORIZACION", 6))
        if self.request.retries < max_reintentos:
            raise self.retry(
                countdown=float(conf.obtener("ESPERA_AUTORIZACION", 4.0)),
                max_retries=max_reintentos,
            )

    return _resumen(registro)


@shared_task(name=NOMBRE_CONSULTAR_AUTORIZACION)
def consultar_autorizacion(comprobante_id: int) -> Optional[Dict[str, Any]]:
    """Consulta el estado de autorización de un comprobante ya recibido."""
    registro = models.ComprobanteEmitido.objects.filter(pk=comprobante_id).first()
    if registro is None:
        logger.warning("No existe el comprobante %s", comprobante_id)
        return None

    services.autorizar(registro)
    registro.refresh_from_db()
    return _resumen(registro)


@shared_task(name=NOMBRE_REVISAR_CERTIFICADO)
def revisar_certificado(avisar_por_correo: bool = True) -> Dict[str, Any]:
    """Revisa la firma electrónica y avisa si hay problemas (tarea periódica).

    Se programa con ``conf.planificador()`` (una vez al día) para que el aviso
    llegue **antes** de que una emisión falle: si el certificado está vencido o a
    punto de vencer, se envía un correo y se deja constancia en el log.
    """
    informe = avisos.revisar_firma()
    if avisar_por_correo and informe["problemas"]:
        avisos.avisar_por_correo(informe)
    return informe


@shared_task(name=NOMBRE_REINTENTAR_PENDIENTES)
def reintentar_pendientes(limite: int = 50) -> List[Dict[str, Any]]:
    """Reencola los comprobantes que quedaron sin estado final.

    Pensada para ejecutarse periódicamente con Celery Beat.
    """
    pendientes = list(
        models.ComprobanteEmitido.objects.filter(estado__in=ESTADOS_A_RECUPERAR)
        .order_by("creado")[:limite]
    )
    resultados = []
    for registro in pendientes:
        try:
            if registro.estado == models.EstadoComprobante.EN_PROCESO:
                consultar_autorizacion(registro.pk)
            else:
                emitir_comprobante(registro.pk)
            registro.refresh_from_db()
        except Exception as exc:  # noqa: BLE001 - una tarea no debe tumbar el lote
            logger.warning("No se pudo reintentar el comprobante %s: %s", registro.pk, exc)
        resultados.append(_resumen(registro))
    return resultados


@shared_task(name=NOMBRE_COMPROBAR_ACTUALIZACION, ignore_result=True)
def comprobar_actualizacion() -> str:
    """Mira si hay una versión nueva del paquete y la deja anotada.

    La programación semanal la pone ``planificador()``. El resultado se guarda
    (``~/.cache/factec/actualizacion.json``) y es lo que usan ``manage.py check``
    (``sri_fe.W011``) y el aviso de la consola. Si no hay internet, **no** falla la
    cola: se anota y se sigue.
    """
    from .. import actualizacion

    try:
        informe = actualizacion.comprobar(forzar=True)
    except Exception as exc:  # noqa: BLE001 - un aviso no puede tumbar el worker
        logger.warning("No se pudo comprobar si hay versiones nuevas: %s", exc)
        return ""
    logger.info("Comprobación de versión: %s", informe)
    return str(informe)
