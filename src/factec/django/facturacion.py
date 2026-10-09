"""Fachada de facturación: del modelo del proyecto al comprobante autorizado.

Es la vía recomendada para emitir. Le pasas tu objeto y el paquete hace el resto:
armar el XML, firmarlo, enviarlo al SRI y guardar el resultado.

::

    from factec.django import facturacion

    # 1) En segundo plano (Celery): devuelve el registro al instante
    registro = facturacion.emitir(mi_factura)

    # 2) Síncrono, para pruebas o scripts
    registro = facturacion.emitir(mi_factura, encolar=False)
    print(registro.estado, registro.numero_autorizacion)

    # 3) Solo el XML, sin firmar ni enviar
    comprobante = facturacion.comprobante_de(mi_factura)
    print(comprobante.to_xml())

    # 4) Firmado, sin enviar
    xml = facturacion.firmar_modelo(mi_factura)

Todas las funciones aceptan un adaptador explícito (``adaptador=``) para cuando
los nombres de tu modelo no encajen con la convención.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from ..catalogos import TipoComprobante
from ..comprobantes import Comprobante
from ..excepciones import ErrorFacturacion, ErrorRevision
from ..firma import Certificado
from ..sri import fechas
from . import adaptadores as mod_adaptadores
from . import conf, models, services

__all__ = [
    "comprobante_de",
    "fijar_fecha_de_emision",
    "firmar_modelo",
    "emitir",
    "emitir_sincrono",
    "emitir_lote",
    "registro_de",
    "ya_emitido",
    "reintentar",
]

logger = logging.getLogger(__name__)


def _clase_adaptador(
    obj: Any,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
) -> mod_adaptadores.AdaptadorComprobante:
    """Resuelve el adaptador que corresponde al objeto."""
    if adaptador is None:
        clase = mod_adaptadores.obtener(type(obj), tipo)
        return clase()
    if isinstance(adaptador, mod_adaptadores.AdaptadorComprobante):
        return adaptador
    return adaptador()  # type: ignore[operator]


def comprobante_de(
    obj: Any,
    *,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
    emisor: Any = None,
    ambiente: Any = None,
    fecha_emision: Any = None,
) -> Comprobante:
    """Construye el comprobante (sin firmar) a partir del objeto.

    La fecha de emisión es la del documento (o la que se indique en
    ``fecha_emision``); la del día de la firma la pone :func:`fijar_fecha_de_emision`,
    que es lo que hace :func:`emitir` y :func:`firmar_modelo`.
    """
    manejador = _clase_adaptador(obj, adaptador, tipo)
    if emisor is not None:
        manejador._emisor = emisor  # noqa: SLF001 - ajuste deliberado
    if ambiente is not None:
        manejador._ambiente = ambiente  # noqa: SLF001
    if fecha_emision is not None:
        manejador._fecha_emision = fecha_emision  # noqa: SLF001
    return manejador.comprobante(obj)


def _fecha_desactualizada(registro: models.ComprobanteEmitido) -> bool:
    """¿El comprobante quedó sin enviar y ya no es del día de hoy?

    Un borrador (o un firmado que nunca llegó a enviarse) de un día anterior se
    rehace para firmarlo con la fecha de hoy: el SRI solo admite la fecha del día
    de la firma o una de los 90 días anteriores, y con la fecha vieja lo devolvería.
    """
    return registro.fecha_desactualizada


def fijar_fecha_de_emision(comprobante: Comprobante, *, fecha: Any = None) -> None:
    """Deja el comprobante con la fecha del día en que se firma.

    El SRI compara la fecha de emisión con su propio reloj, así que el comprobante
    se firma con la fecha del día en que realmente se emite, no con la fecha con la
    que se preparó el documento: así no salen comprobantes con fecha de otro día.
    Con ``FECHA_EMISION_AL_EMITIR = False`` se respeta la fecha del documento, y con
    ``fecha_emision=`` se indica una concreta (que sí se valida: nunca futura ni de
    hace más de 90 días).
    """
    if fecha is not None:
        nueva = fecha
    elif not bool(conf.obtener("FECHA_EMISION_AL_EMITIR", True)):
        return
    else:
        nueva = fechas.hoy_en_ecuador()

    if comprobante.fecha_emision == nueva:
        return
    comprobante.fecha_emision = nueva
    # La clave de acceso empieza por la fecha de emisión: se recalcula.
    comprobante.clave_acceso = None


def _revisar(comprobante: Comprobante, *, revisar: Optional[bool]) -> None:
    """Revisa el comprobante y lanza :class:`ErrorRevision` si no se puede emitir."""
    if revisar is None:
        revisar = bool(conf.obtener("REVISAR_ANTES_DE_EMITIR", True))
    if not revisar:
        return
    informe = services.revisar_comprobante(comprobante)
    if not informe.puede_emitir:
        logger.warning("No se emite: %s", informe.resumen())
        raise ErrorRevision(informe=informe)


def firmar_modelo(
    obj: Any,
    *,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
    certificado: Optional[Certificado] = None,
    algoritmo: Optional[str] = None,
    fecha_emision: Any = None,
) -> str:
    """Devuelve el XML firmado del comprobante que corresponde al objeto.

    Se firma con la fecha del día (``FECHA_EMISION_AL_EMITIR``), como al emitir.
    """
    comprobante = comprobante_de(
        obj, adaptador=adaptador, tipo=tipo, fecha_emision=fecha_emision
    )
    fijar_fecha_de_emision(comprobante, fecha=fecha_emision)
    return comprobante.firmar(
        certificado or conf.certificado(),
        algoritmo=algoritmo or conf.obtener("ALGORITMO_FIRMA", "sha1"),
    )


def emitir(
    obj: Any,
    *,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
    encolar: Optional[bool] = None,
    forzar: bool = False,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
    guardar_xml: Optional[bool] = None,
    vincular: bool = True,
    fecha_emision: Any = None,
    revisar: Optional[bool] = None,
) -> models.ComprobanteEmitido:
    """Emite el comprobante del objeto: XML, firma y envío al SRI.

    Es **idempotente por documento**: si ese objeto ya tiene un comprobante
    registrado, se reutiliza y solo se reintenta el envío; así no se duplican
    comprobantes ni se consumen secuenciales de más. Con ``forzar=True`` se crea
    uno nuevo desde cero (por ejemplo, tras corregir los datos).

    Antes de firmar se **revisa** el comprobante (certificado vigente y del mismo
    RUC, fecha de emisión dentro del rango del SRI, totales y clave de acceso): si
    algo fallaría, se lanza :class:`~factec.excepciones.ErrorRevision` y no se emite
    ni se consume secuencial. Con ``revisar=False`` se omite esa comprobación.

    La fecha de emisión es la del día de la firma (``fecha_emision=`` fuerza otra).
    Un comprobante que quedó sin enviar de un día anterior se rehace para no
    reenviarlo con fecha vieja.

    El vínculo con el documento requiere un **modelo de Django guardado**. Con un
    objeto cualquiera (un diccionario, un ``dataclass``) el comprobante se emite
    igual, pero sin enlace: sin enlace no hay idempotencia, así que cada llamada
    genera un comprobante nuevo.

    Parámetros:

    * ``encolar=True`` lo manda a Celery y devuelve el registro en ``BORRADOR``;
      ``encolar=False`` lo hace todo de forma síncrona. Por omisión se decide
      según ``EMITIR_CON_CELERY``.
    """
    manejador = _clase_adaptador(obj, adaptador, tipo)
    if fecha_emision is not None:
        manejador._fecha_emision = fecha_emision  # noqa: SLF001 - ajuste deliberado
    tipo_comprobante = tipo or manejador.tipo
    # (la fecha con la que se emite la fija fijar_fecha_de_emision)

    if not forzar:
        existente = models.ComprobanteEmitido.para_objeto(obj, tipo_comprobante)
        if existente is not None:
            if existente.autorizado:
                logger.info(
                    "El documento %s ya tiene el comprobante %s autorizado; no se reemite.",
                    obj, existente.clave_acceso,
                )
                _asociar_en_modelo(obj, existente)
                return existente
            if existente.estado in models.ESTADOS_RECHAZADOS:
                # El SRI lo devolvió (fecha fuera de rango, datos mal): el XML
                # guardado ya no sirve. Se rehace con el documento corregido,
                # reutilizando el secuencial ya reservado. El registro anterior
                # se conserva como historial del rechazo.
                logger.info(
                    "El comprobante %s del documento %s fue rechazado por el SRI (%s); "
                    "se genera uno nuevo con los datos actuales.",
                    existente.clave_acceso, obj, existente.estado,
                )
            elif _fecha_desactualizada(existente):
                logger.info(
                    "El comprobante %s del documento %s quedó sin enviar del %s; se "
                    "refecha a hoy (%s) antes de firmarlo.",
                    existente.clave_acceso, obj, existente.fecha_emision, fechas.hoy_en_ecuador(),
                )
                services.actualizar_fecha(existente)
                _asociar_en_modelo(obj, existente)
                return _enviar(existente, encolar=encolar, intentos=intentos, espera=espera)
            else:
                logger.info(
                    "El documento %s ya tiene el comprobante %s (%s); se reintenta.",
                    obj, existente.clave_acceso, existente.estado,
                )
                _asociar_en_modelo(obj, existente)
                return _enviar(existente, encolar=encolar, intentos=intentos, espera=espera)

    comprobante = manejador.comprobante(obj)
    fijar_fecha_de_emision(comprobante, fecha=fecha_emision)
    _revisar(comprobante, revisar=revisar)
    # Solo cuando ya se sabe que se puede emitir: si la revisión falla, el
    # documento se queda como estaba.
    _fijar_fecha_en_el_documento(obj, comprobante.fecha_emision)
    registro = services.registrar(comprobante, guardar_xml=guardar_xml)
    if vincular:
        registro.vincular(obj)
    _asociar_en_modelo(obj, registro)
    return _enviar(registro, encolar=encolar, intentos=intentos, espera=espera)


def _fijar_fecha_en_el_documento(obj: Any, fecha: Any) -> None:
    """Copia en el documento la fecha con la que se emite el comprobante.

    El comprobante se firma con el día de la firma y el documento puede traer la
    fecha con la que se preparó: sin copiarla, la lista del admin mostraría un día
    y el XML autorizado otro. Los modelos del paquete exponen
    ``fijar_fecha_de_emision()``; con modelos propios basta con definir ese método.
    """
    fijar = getattr(obj, "fijar_fecha_de_emision", None)
    if not callable(fijar):
        return
    try:
        fijar(fecha)
    except Exception:  # pragma: no cover - modelos propios sin el campo
        logger.warning("No se pudo actualizar la fecha de emisión del documento %s", obj)


def _asociar_en_modelo(obj: Any, registro: models.ComprobanteEmitido) -> None:
    """Deja el enlace en el propio documento, si el modelo lo ofrece.

    Los modelos del paquete exponen ``asociar_comprobante()``, que guarda el
    comprobante y el secuencial reservado. Con modelos propios basta con definir
    ese método para recibir el aviso.
    """
    asociar = getattr(obj, "asociar_comprobante", None)
    if callable(asociar):
        asociar(registro)


def _enviar(
    registro: models.ComprobanteEmitido,
    *,
    encolar: Optional[bool],
    intentos: Optional[int],
    espera: Optional[float],
) -> models.ComprobanteEmitido:
    """Envía (o encola) un registro ya guardado y devuelve su estado actualizado."""
    if encolar is None:
        encolar = bool(conf.obtener("EMITIR_CON_CELERY", True))

    if encolar:
        if services.encolar(registro) is None:
            # Sin broker configurado: se emite aquí para no dejar el trabajo a medias.
            services.procesar(registro, intentos=intentos, espera=espera)
    else:
        services.procesar(registro, intentos=intentos, espera=espera)

    registro.refresh_from_db()
    return registro


def emitir_sincrono(obj: Any, **kwargs: Any) -> models.ComprobanteEmitido:
    """Atajo de :func:`emitir` sin Celery (``encolar=False``)."""
    kwargs["encolar"] = False
    return emitir(obj, **kwargs)


def emitir_lote(
    objetos: Iterable[Any],
    *,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
    encolar: Optional[bool] = None,
    forzar: bool = False,
    detener_en_error: bool = False,
    revisar: Optional[bool] = None,
) -> List[Any]:
    """Emite varios comprobantes.

    Devuelve una lista con un registro por documento; si alguno falla y
    ``detener_en_error`` es ``False``, se devuelve la excepción en su lugar para
    poder revisarla sin perder el resto del lote.
    """
    resultados: List[Any] = []
    for objeto in objetos:
        try:
            resultados.append(
                emitir(
                    objeto, adaptador=adaptador, tipo=tipo, encolar=encolar,
                    forzar=forzar, revisar=revisar,
                )
            )
        except Exception as exc:  # noqa: BLE001 - se reporta por elemento
            if detener_en_error:
                raise
            logger.warning("No se pudo emitir el documento %s: %s", objeto, exc)
            resultados.append(exc)
    return resultados


def registro_de(
    obj: Any, tipo: Optional[str] = None
) -> Optional[models.ComprobanteEmitido]:
    """Comprobante emitido para el objeto, si lo hay."""
    return models.ComprobanteEmitido.para_objeto(obj, tipo)


def ya_emitido(obj: Any, tipo: Optional[str] = None) -> bool:
    """``True`` si el objeto ya tiene un comprobante autorizado."""
    return models.ComprobanteEmitido.ya_emitido(obj, tipo)


def reintentar(
    obj: Any,
    *,
    tipo: Optional[str] = None,
    encolar: Optional[bool] = None,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
) -> Optional[models.ComprobanteEmitido]:
    """Reintenta la emisión de un documento que quedó sin autorizar.

    Reutiliza el XML ya registrado (y firmado, si lo estaba), sin volver a
    construir el comprobante ni pedir un secuencial nuevo.

    Si el SRI ya lo había **rechazado** (``DEVUELTO`` o ``NO_AUTORIZADO``), se rehace
    el comprobante con los datos actuales del documento: reenviar el mismo XML
    devolvería el mismo error. Si simplemente quedó sin enviar de un día anterior,
    se le cambia la fecha de emisión a la de hoy (que es la que admite el SRI) y se
    firma: un comprobante se emite el día en que se firma.
    """
    registro = models.ComprobanteEmitido.para_objeto(obj, tipo)
    if registro is None:
        raise ErrorFacturacion(
            "No hay ningún comprobante registrado para ese documento. "
            "Use emitir() para crearlo."
        )
    if registro.autorizado:
        return registro
    if registro.estado in models.ESTADOS_RECHAZADOS:
        logger.info(
            "El comprobante %s fue rechazado (%s); se rehace con los datos actuales.",
            registro.clave_acceso, registro.estado,
        )
        return emitir(obj, tipo=tipo, encolar=encolar, intentos=intentos, espera=espera)
    if _fecha_desactualizada(registro):
        logger.info(
            "El comprobante %s quedó sin enviar del %s; se refecha a hoy (%s) antes de "
            "firmarlo.",
            registro.clave_acceso, registro.fecha_emision, fechas.hoy_en_ecuador(),
        )
        services.actualizar_fecha(registro)
        return _enviar(registro, encolar=encolar, intentos=intentos, espera=espera)
    return _enviar(registro, encolar=encolar, intentos=intentos, espera=espera)
