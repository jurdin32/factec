"""Consultar y verificar comprobantes desde cualquier sitio, también desde vistas.

Reúne lo que suele hacer falta en una vista: leer el XML guardado, verificarlo,
preguntar al SRI por su estado y devolver los datos ya listos para JSON::

    from factec.django import consulta

    # En una vista, con la clave de acceso que le pasen
    datos = consulta.datos_por_clave(clave)
    if datos is None:
        return JsonResponse({"error": "no encontrado"}, status=404)
    return JsonResponse(datos)

    # Verificar una factura de un proveedor que llega en XML
    informe = consulta.verificar_xml(xml_recibido)
    if not informe.ok:
        return JsonResponse({"problemas": informe.problemas}, status=400)

Nada de esto necesita el admin: son funciones normales, así que se pueden usar en
una vista, en una tarea de Celery, en un comando o en un test.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from ..lectura import ComprobanteLeido, es_comprobante, leer_comprobante
from ..verificacion import InformeVerificacion, verificar_comprobante
from ..verificacion import verificar_en_el_sri as verificar_en_el_sri_core
from . import archivos, conf, models

__all__ = [
    "archivos_de",
    "buscar_por_clave",
    "datos",
    "datos_por_clave",
    "leer",
    "revisar",
    "verificar",
    "verificar_en_el_sri",
    "verificar_xml",
]

logger = logging.getLogger(__name__)


def buscar_por_clave(clave_acceso: str) -> Optional[models.ComprobanteEmitido]:
    """Comprobante emitido con esa clave de acceso (o ``None``)."""
    clave = str(clave_acceso or "").strip()
    if not clave:
        return None
    return models.ComprobanteEmitido.objects.filter(clave_acceso=clave).first()


def _registro(objeto: Union[str, int, models.ComprobanteEmitido]) -> models.ComprobanteEmitido:
    """Acepta el comprobante, su ``pk`` o su clave de acceso."""
    if isinstance(objeto, models.ComprobanteEmitido):
        return objeto

    registro = None
    if isinstance(objeto, int) or (isinstance(objeto, str) and objeto.isdigit()):
        registro = models.ComprobanteEmitido.objects.filter(pk=int(objeto)).first()
    if registro is None and isinstance(objeto, str) and len(objeto.strip()) >= 20:
        registro = buscar_por_clave(objeto)
    if registro is None:
        raise models.ComprobanteEmitido.DoesNotExist(f"No hay comprobante para {objeto!r}.")
    return registro


def xml_de(registro: models.ComprobanteEmitido) -> str:
    """El mejor XML disponible: el autorizado, el firmado o el borrador.

    Se comprueba que lo guardado sea un comprobante completo: si el SRI devolvió
    una respuesta sin el documento, se usa el siguiente.
    """
    candidatos = (registro.xml_autorizado, registro.xml_firmado, registro.xml_sin_firma)
    for xml in candidatos:
        if xml and es_comprobante(xml):
            return xml
    return next((xml for xml in candidatos if xml), "")


def leer(
    objeto: Union[str, int, models.ComprobanteEmitido],
    *,
    xml: Optional[str] = None,
) -> ComprobanteLeido:
    """Lee el comprobante y devuelve sus datos ya separados."""
    registro = _registro(objeto)
    return leer_comprobante(xml or xml_de(registro))


def revisar(
    objeto: Union[str, int, models.ComprobanteEmitido],
    **opciones: Any,
) -> Any:
    """Revisa el comprobante **antes** de firmarlo y enviarlo.

    Devuelve un :class:`factec.revision.InformeRevision` con ``problemas`` (lo que
    impediría emitir: certificado vencido, fecha fuera del rango del SRI, totales
    que no cuadran…) y ``avisos`` (por ejemplo, que la firma vence en pocos días).
    No contacta con el SRI ni consume secuenciales::

        informe = consulta.revisar(registro)
        if not informe.puede_emitir:
            return JsonResponse({"problemas": informe.problemas}, status=400)
    """
    from . import services

    return services.revisar(_registro(objeto), **opciones)


def verificar_xml(xml: Union[str, bytes], *, exigir_firma: bool = True) -> InformeVerificacion:
    """Verifica un XML suelto (por ejemplo, el de una factura de un proveedor)."""
    return verificar_comprobante(xml, exigir_firma=exigir_firma)


def verificar(
    objeto: Union[str, int, models.ComprobanteEmitido],
    *,
    xml: Optional[str] = None,
    exigir_firma: bool = True,
) -> InformeVerificacion:
    """Verifica el comprobante guardado: firma, clave, fecha y totales."""
    registro = _registro(objeto)
    return verificar_comprobante(
        xml or xml_de(registro), exigir_firma=exigir_firma
    )


def verificar_en_el_sri(
    objeto: Union[str, models.ComprobanteEmitido],
    *,
    guardar: bool = True,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
) -> Any:
    """Pregunta al SRI por el estado de una clave de acceso.

    Con ``guardar=True`` (por omisión) y si el comprobante está en la base de
    datos, se actualiza su estado, sus mensajes, la respuesta del SRI y sus
    archivos, igual que hace la acción del admin.
    """
    from ..lectura import AutorizacionLeida
    from . import services

    registro = None
    if isinstance(objeto, models.ComprobanteEmitido):
        registro = objeto
    else:
        registro = buscar_por_clave(str(objeto))

    if registro is not None and guardar:
        respuesta = services.autorizar(registro, intentos=intentos, espera=espera)
        registro.refresh_from_db()
        if registro.respuesta_autorizacion:
            from ..excepciones import ErrorValidacion
            from ..lectura import leer_autorizacion

            try:
                autorizacion = leer_autorizacion(registro.respuesta_autorizacion)
            except ErrorValidacion:      # respuesta en otro formato
                autorizacion = None
            if autorizacion is not None:
                autorizacion.clave_acceso = autorizacion.clave_acceso or registro.clave_acceso
                return autorizacion
        return AutorizacionLeida(
            estado=registro.estado,
            numero_autorizacion=registro.numero_autorizacion,
            fecha_autorizacion=registro.fecha_autorizacion,
            ambiente=str(registro.ambiente),
            clave_acceso=registro.clave_acceso,
            mensajes=list(registro.mensajes or []),
        )

    # Sin registro (o sin guardar): se consulta directamente al SRI.
    clave = str(objeto if not isinstance(objeto, models.ComprobanteEmitido) else objeto.clave_acceso)
    return verificar_en_el_sri_core(clave, ambiente=conf.ambiente(), cliente=conf.cliente())


def archivos_de(
    objeto: Union[str, int, models.ComprobanteEmitido],
) -> List[Dict[str, Any]]:
    """Archivos guardados del comprobante (nombre, ruta, ruta relativa y tamaño)."""
    return archivos.archivos_del_registro(_registro(objeto))


def datos(
    objeto: Union[str, int, models.ComprobanteEmitido],
    *,
    verificar_comprobante_: bool = False,
) -> Dict[str, Any]:
    """Todo lo del comprobante en un diccionario listo para una vista.

    Incluye el estado ante el SRI, los datos leídos del XML, los archivos
    guardados y, si se pide, el informe de verificación.
    """
    registro = _registro(objeto)
    xml = xml_de(registro)

    datos: Dict[str, Any] = {
        "id": registro.pk,
        "clave_acceso": registro.clave_acceso,
        "estado": registro.estado,
        "numero": registro.numero_comprobante,
        "tipo": registro.tipo_comprobante,
        "ambiente": registro.ambiente,
        "fecha_emision": registro.fecha_emision.isoformat() if registro.fecha_emision else None,
        "numero_autorizacion": registro.numero_autorizacion,
        "fecha_autorizacion": (
            registro.fecha_autorizacion.isoformat() if registro.fecha_autorizacion else None
        ),
        "importe_total": str(registro.importe_total),
        "receptor": {
            "razon_social": registro.razon_social_receptor,
            "identificacion": registro.identificacion_receptor,
        },
        "mensajes": list(registro.mensajes or []),
        "error": registro.error,
        "carpeta": registro.carpeta,
        "fecha_desactualizada": registro.fecha_desactualizada,
        "archivos": [
            {"nombre": archivo["nombre"], "relativa": archivo["relativa"], "bytes": archivo["bytes"]}
            for archivo in archivos.archivos_del_registro(registro)
        ],
    }

    if xml:
        try:
            datos["leido"] = leer_comprobante(xml).a_dict()
        except Exception as error:  # noqa: BLE001 - se informa y se sigue
            logger.warning("No se pudo leer el XML del comprobante %s: %s", registro.pk, error)
            datos["leido"] = None

    if verificar_comprobante_:
        datos["verificacion"] = verificar_comprobante(xml).a_dict() if xml else None

    return datos


def datos_por_clave(clave_acceso: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """:func:`datos` buscando por clave de acceso; ``None`` si no existe."""
    registro = buscar_por_clave(clave_acceso)
    if registro is None:
        return None
    return datos(registro, **kwargs)
