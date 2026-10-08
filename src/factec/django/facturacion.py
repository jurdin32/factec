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
from ..excepciones import ErrorFacturacion
from ..firma import Certificado
from . import adaptadores as mod_adaptadores
from . import conf, models, services

__all__ = [
    "comprobante_de",
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
) -> Comprobante:
    """Construye el comprobante (sin firmar) a partir del objeto."""
    manejador = _clase_adaptador(obj, adaptador, tipo)
    if emisor is not None:
        manejador._emisor = emisor  # noqa: SLF001 - ajuste deliberado
    if ambiente is not None:
        manejador._ambiente = ambiente  # noqa: SLF001
    return manejador.comprobante(obj)


def firmar_modelo(
    obj: Any,
    *,
    adaptador: Optional[Union[type, mod_adaptadores.AdaptadorComprobante]] = None,
    tipo: Optional[str] = None,
    certificado: Optional[Certificado] = None,
    algoritmo: Optional[str] = None,
) -> str:
    """Devuelve el XML firmado del comprobante que corresponde al objeto."""
    comprobante = comprobante_de(obj, adaptador=adaptador, tipo=tipo)
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
) -> models.ComprobanteEmitido:
    """Emite el comprobante del objeto: XML, firma y envío al SRI.

    Es **idempotente por documento**: si ese objeto ya tiene un comprobante
    registrado, se reutiliza y solo se reintenta el envío; así no se duplican
    comprobantes ni se consumen secuenciales de más. Con ``forzar=True`` se crea
    uno nuevo desde cero (por ejemplo, tras corregir los datos).

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
    tipo_comprobante = tipo or manejador.tipo

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
            logger.info(
                "El documento %s ya tiene el comprobante %s (%s); se reintenta.",
                obj, existente.clave_acceso, existente.estado,
            )
            _asociar_en_modelo(obj, existente)
            return _enviar(existente, encolar=encolar, intentos=intentos, espera=espera)

    comprobante = manejador.comprobante(obj)
    registro = services.registrar(comprobante, guardar_xml=guardar_xml)
    if vincular:
        registro.vincular(obj)
    _asociar_en_modelo(obj, registro)
    return _enviar(registro, encolar=encolar, intentos=intentos, espera=espera)


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
                emitir(objeto, adaptador=adaptador, tipo=tipo, encolar=encolar, forzar=forzar)
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
    """
    registro = models.ComprobanteEmitido.para_objeto(obj, tipo)
    if registro is None:
        raise ErrorFacturacion(
            "No hay ningún comprobante registrado para ese documento. "
            "Use emitir() para crearlo."
        )
    if registro.autorizado:
        return registro
    return _enviar(registro, encolar=encolar, intentos=intentos, espera=espera)
