"""Servicios: crear, firmar, enviar y autorizar comprobantes con persistencia.

Flujo típico desde una vista o un serializer::

    from factec.django import servicios

    registro = servicios.crear_factura(receptor=..., detalles=[...])
    servicios.encolar(registro)          # envía a Celery (segundo plano)

Y desde la tarea, :func:`procesar` hace firma + recepción + autorización.

Si se prefiere sin Celery (por ejemplo en pruebas o en un ``manage.py shell``)::

    servicios.procesar(registro)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from django.db import transaction

from ..catalogos import TipoComprobante
from ..comprobantes import (
    Comprobante,
    ComprobanteRetencion,
    Factura,
    GuiaRemision,
    LiquidacionCompra,
    NotaCredito,
    NotaDebito,
)
from ..modelos import Destinatario, Detalle, Motivo, Pago, Receptor, Reembolso
from ..emisor import EmisorElectronico
from ..excepciones import ErrorFacturacion, ErrorRecepcion
from ..firma import Certificado, firmar_xml
from ..sri.soap import (
    ESTADO_AUTORIZADO,
    ESTADO_EN_PROCESO,
    RespuestaAutorizacion,
    RespuestaRecepcion,
)
from . import conf, models

__all__ = [
    "crear_factura",
    "crear_liquidacion_compra",
    "crear_nota_credito",
    "crear_nota_debito",
    "crear_retencion",
    "crear_guia_remision",
    "registrar",
    "encolar",
    "firmar",
    "enviar",
    "autorizar",
    "procesar",
    "emitir_ahora",
    "siguiente_secuencial",
    "emisor_electronico",
    "mensajes_a_dict",
]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------- utilidades


def mensajes_a_dict(mensajes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Convierte los mensajes del SRI a diccionarios serializables en JSON."""
    resultado: List[Dict[str, Any]] = []
    for mensaje in mensajes or []:
        if is_dataclass(mensaje) and not isinstance(mensaje, type):
            resultado.append(asdict(mensaje))
        elif isinstance(mensaje, dict):
            resultado.append(dict(mensaje))
        else:
            resultado.append({"mensaje": str(mensaje)})
    return resultado


def siguiente_secuencial(
    tipo_comprobante: str = TipoComprobante.FACTURA.value,
    *,
    estab: Optional[str] = None,
    pto_emi: Optional[str] = None,
    ambiente: Optional[int] = None,
) -> str:
    """Reserva el siguiente secuencial persistente y lo devuelve formateado."""
    datos_emisor = conf.emisor()
    numero = models.Secuencial.siguiente(
        tipo_comprobante=tipo_comprobante,
        estab=estab or datos_emisor.estab,
        pto_emi=pto_emi or datos_emisor.pto_emi,
        ambiente=int(ambiente if ambiente is not None else conf.ambiente()),
    )
    return str(numero)


def emisor_electronico() -> EmisorElectronico:
    """Construye la fachada de emisión a partir de la configuración de Django."""
    return EmisorElectronico(
        emisor=conf.emisor(),
        certificado=conf.certificado(),
        ambiente=conf.ambiente(),
        timeout=conf.obtener("TIMEOUT"),
        validar_vigencia=conf.obtener("VALIDAR_VIGENCIA", True),
    )


def _metricas(comprobante: Comprobante) -> Dict[str, Any]:
    """Extrae el importe total y el receptor del comprobante, si los tiene."""
    total: Decimal = Decimal("0.00")
    receptor = getattr(comprobante, "receptor", None) or getattr(comprobante, "proveedor", None)
    receptor = receptor or getattr(comprobante, "sujeto_retenido", None)

    if isinstance(comprobante, (Factura, LiquidacionCompra, NotaCredito)):
        try:
            total = comprobante.calcular()["importe_total"]
        except ErrorFacturacion:  # pragma: no cover - solo si los datos son inválidos
            total = Decimal("0.00")
    elif isinstance(comprobante, NotaDebito):
        try:
            total = comprobante.calcular()["valor_total"]
        except ErrorFacturacion:  # pragma: no cover - solo si los datos son inválidos
            total = Decimal("0.00")
    elif isinstance(comprobante, ComprobanteRetencion):
        total = sum(
            (doc.importe_total or Decimal("0")) for doc in comprobante.docs_sustento
        ) or Decimal("0")

    return {
        "importe_total": total,
        "razon_social_receptor": getattr(receptor, "razon_social", "") or "",
        "identificacion_receptor": getattr(receptor, "identificacion", "") or "",
    }


# --------------------------------------------------------------- registro


def registrar(
    comprobante: Comprobante,
    *,
    guardar_xml: Optional[bool] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    """Guarda el comprobante (estado ``BORRADOR``) y devuelve el registro.

    El XML se persiste para que la tarea en segundo plano no tenga que
    reconstruir el objeto Python.
    """
    xml = comprobante.to_xml()
    if guardar_xml is None:
        guardar_xml = bool(conf.obtener("GUARDAR_XML", True))

    datos = {
        "clave_acceso": comprobante.clave,
        "tipo_comprobante": comprobante.TIPO,
        "ambiente": int(comprobante.ambiente),
        "tipo_emision": str(getattr(comprobante.tipo_emision, "value", comprobante.tipo_emision)),
        "estab": f"{comprobante.emisor.estab:0>3}",
        "pto_emi": f"{comprobante.emisor.pto_emi:0>3}",
        "secuencial": comprobante.secuencial_normalizado,
        "fecha_emision": comprobante.fecha_emision,
        "estado": models.EstadoComprobante.BORRADOR,
        "xml_sin_firma": xml if guardar_xml else "",
    }
    datos.update(_metricas(comprobante))
    datos.update(extra)
    if "configuracion" not in datos:
        datos["configuracion"] = conf.configuracion_activa()

    registro, creado = models.ComprobanteEmitido.objects.update_or_create(
        clave_acceso=datos["clave_acceso"], defaults=datos
    )
    logger.info(
        "%s comprobante %s (%s)", "Creado" if creado else "Actualizado",
        registro.clave_acceso, registro.estado,
    )
    return registro


# --------------------------------------------------- constructores tipados


def _comunes(
    fecha_emision: Optional[date],
    secuencial: Optional[str],
    tipo: str,
) -> Dict[str, Any]:
    return {
        "emisor": conf.emisor(),
        "ambiente": conf.ambiente(),
        "fecha_emision": fecha_emision or date.today(),
        "secuencial": secuencial or siguiente_secuencial(tipo),
    }


def crear_factura(
    *,
    receptor: Receptor,
    detalles: Sequence[Detalle],
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    pagos: Optional[Sequence[Pago]] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    """Crea una factura y la guarda como borrador."""
    comprobante = Factura(
        receptor=receptor,
        detalles=list(detalles),
        pagos=list(pagos or []),
        **_comunes(fecha_emision, secuencial, TipoComprobante.FACTURA.value),
        **extra,
    )
    return registrar(comprobante)


def crear_liquidacion_compra(
    *,
    proveedor: Receptor,
    detalles: Sequence[Detalle],
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    reembolsos: Optional[Sequence[Reembolso]] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    comprobante = LiquidacionCompra(
        proveedor=proveedor,
        detalles=list(detalles),
        reembolsos=list(reembolsos or []),
        **_comunes(fecha_emision, secuencial, TipoComprobante.LIQUIDACION_COMPRA.value),
        **extra,
    )
    return registrar(comprobante)


def crear_nota_credito(
    *,
    receptor: Receptor,
    detalles: Sequence[Detalle],
    motivo: str,
    cod_doc_modificado: str,
    num_doc_modificado: str,
    fecha_emision_doc_sustento: date,
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    comprobante = NotaCredito(
        receptor=receptor,
        detalles=list(detalles),
        motivo=motivo,
        cod_doc_modificado=cod_doc_modificado,
        num_doc_modificado=num_doc_modificado,
        fecha_emision_doc_sustento=fecha_emision_doc_sustento,
        **_comunes(fecha_emision, secuencial, TipoComprobante.NOTA_CREDITO.value),
        **extra,
    )
    return registrar(comprobante)


def crear_nota_debito(
    *,
    receptor: Receptor,
    motivos: Sequence[Motivo],
    cod_doc_modificado: str,
    num_doc_modificado: str,
    fecha_emision_doc_sustento: date,
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    comprobante = NotaDebito(
        receptor=receptor,
        motivos=list(motivos),
        cod_doc_modificado=cod_doc_modificado,
        num_doc_modificado=num_doc_modificado,
        fecha_emision_doc_sustento=fecha_emision_doc_sustento,
        **_comunes(fecha_emision, secuencial, TipoComprobante.NOTA_DEBITO.value),
        **extra,
    )
    return registrar(comprobante)


def crear_retencion(
    *,
    sujeto_retenido: Receptor,
    docs_sustento: Sequence[Any],
    periodo_fiscal: date,
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    comprobante = ComprobanteRetencion(
        sujeto_retenido=sujeto_retenido,
        docs_sustento=list(docs_sustento),
        periodo_fiscal=periodo_fiscal,
        **_comunes(fecha_emision, secuencial, TipoComprobante.COMPROBANTE_RETENCION.value),
        **extra,
    )
    return registrar(comprobante)


def crear_guia_remision(
    *,
    destinatarios: Sequence[Destinatario],
    dir_partida: str,
    razon_social_transportista: str,
    ruc_transportista: str,
    placa: str,
    fecha_ini_transporte: date,
    fecha_fin_transporte: date,
    fecha_emision: Optional[date] = None,
    secuencial: Optional[str] = None,
    **extra: Any,
) -> models.ComprobanteEmitido:
    comprobante = GuiaRemision(
        destinatarios=list(destinatarios),
        dir_partida=dir_partida,
        razon_social_transportista=razon_social_transportista,
        ruc_transportista=ruc_transportista,
        placa=placa,
        fecha_ini_transporte=fecha_ini_transporte,
        fecha_fin_transporte=fecha_fin_transporte,
        **_comunes(fecha_emision, secuencial, TipoComprobante.GUIA_REMISION.value),
        **extra,
    )
    return registrar(comprobante)


# ------------------------------------------------------------------- etapas


def _xml_del_registro(registro: models.ComprobanteEmitido) -> str:
    xml = registro.xml_sin_firma or registro.xml_firmado
    if not xml:
        raise ErrorFacturacion(
            "El registro no conserva el XML. Vuelva a crearlo o active "
            "FACTURACION_ELECTRONICA['GUARDAR_XML'] antes de emitirlo."
        )
    return xml


def firmar(
    registro: models.ComprobanteEmitido,
    *,
    certificado: Optional[Certificado] = None,
    guardar_xml: Optional[bool] = None,
) -> models.ComprobanteEmitido:
    """Firma el XML del borrador y guarda el resultado."""
    certificado = certificado or conf.certificado()
    xml_firmado = firmar_xml(
        _xml_del_registro(registro),
        certificado,
        algoritmo=conf.obtener("ALGORITMO_FIRMA", "sha1"),
    )
    if guardar_xml is None:
        guardar_xml = bool(conf.obtener("GUARDAR_XML", True))
    if guardar_xml:
        registro.xml_firmado = xml_firmado
    registro.estado = models.EstadoComprobante.FIRMADO
    registro.error = ""
    registro.save(update_fields=["xml_firmado", "estado", "error", "actualizado"])
    return registro


def enviar(registro: models.ComprobanteEmitido) -> RespuestaRecepcion:
    """Envía el comprobante a recepción y actualiza el estado."""
    xml = registro.xml_firmado or registro.xml_sin_firma
    if not xml:
        raise ErrorFacturacion("No hay XML firmado para enviar.")

    registro.intentos += 1
    try:
        recepcion = conf.cliente().validar_comprobante(xml)
    except ErrorFacturacion as exc:
        registro.estado = models.EstadoComprobante.ERROR
        registro.error = f"{type(exc).__name__}: {exc}"
        registro.mensajes = []
        registro.save(
            update_fields=["intentos", "estado", "error", "mensajes", "actualizado"]
        )
        raise

    mensajes = mensajes_a_dict(recepcion.mensajes)
    if recepcion.recibida:
        registro.estado = models.EstadoComprobante.RECIBIDO
        registro.error = ""
    else:
        registro.estado = models.EstadoComprobante.DEVUELTO
        registro.error = "; ".join(
            str(m.get("mensaje", "")) for m in mensajes
        ) or "El SRI devolvió el comprobante."
    registro.mensajes = mensajes
    registro.save(update_fields=["intentos", "estado", "error", "mensajes", "actualizado"])
    return recepcion


def autorizar(
    registro: models.ComprobanteEmitido,
    *,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
) -> RespuestaAutorizacion:
    """Consulta la autorización y actualiza el estado del registro."""
    intentos = int(intentos if intentos is not None else conf.obtener("REINTENTOS_AUTORIZACION"))
    espera = float(espera if espera is not None else conf.obtener("ESPERA_AUTORIZACION"))

    respuesta = conf.cliente().esperar_autorizacion(
        registro.clave_acceso, intentos=intentos, espera=espera
    )
    ultima = respuesta.ultima
    if ultima is None:
        registro.estado = models.EstadoComprobante.ERROR
        registro.error = "El SRI no devolvió ninguna autorización."
        registro.save(update_fields=["estado", "error", "actualizado"])
        return respuesta

    mensajes = mensajes_a_dict(ultima.mensajes)
    if ultima.estado.upper() == ESTADO_AUTORIZADO:
        registro.marcar_autorizado(
            numero_autorizacion=ultima.numero_autorizacion or registro.clave_acceso,
            fecha_autorizacion=ultima.fecha_autorizacion,
            xml_autorizado=ultima.comprobante or "",
            mensajes=mensajes,
        )
    elif ultima.estado.upper() == ESTADO_EN_PROCESO:
        registro.estado = models.EstadoComprobante.EN_PROCESO
        registro.mensajes = mensajes
        registro.save(update_fields=["estado", "mensajes", "actualizado"])
    else:
        registro.estado = models.EstadoComprobante.NO_AUTORIZADO
        registro.mensajes = mensajes
        registro.error = "; ".join(str(m.get("mensaje", "")) for m in mensajes)
        registro.save(update_fields=["estado", "mensajes", "error", "actualizado"])
    return respuesta


def procesar(
    registro: models.ComprobanteEmitido,
    *,
    firmar_si_falta: bool = True,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
    certificado: Optional[Certificado] = None,
) -> models.ComprobanteEmitido:
    """Ejecuta firma, recepción y autorización sobre un registro.

    Es la función que ejecutan las tareas de Celery. Los errores del SRI se
    registran en el propio registro (``estado`` y ``error``) y no se propagan,
    salvo los fallos inesperados de programación.
    """
    if registro.estado in ESTADOS_NO_PROCESABLES:
        logger.info("El comprobante %s ya está en estado %s", registro.pk, registro.estado)
        return registro

    try:
        if firmar_si_falta and not registro.xml_firmado:
            firmar(registro, certificado=certificado)
        recepcion = enviar(registro)
    except ErrorFacturacion as exc:
        logger.warning("Fallo al enviar el comprobante %s: %s", registro.pk, exc)
        return registro

    if not recepcion.recibida:
        return registro

    try:
        autorizar(registro, intentos=intentos, espera=espera)
    except ErrorFacturacion as exc:
        registro.estado = models.EstadoComprobante.ERROR
        registro.error = f"{type(exc).__name__}: {exc}"
        registro.save(update_fields=["estado", "error", "actualizado"])
        logger.warning("Fallo al autorizar el comprobante %s: %s", registro.pk, exc)
    return registro


#: Estados que ya no se vuelven a procesar desde cero.
ESTADOS_NO_PROCESABLES = frozenset(
    {
        models.EstadoComprobante.AUTORIZADO,
        models.EstadoComprobante.DEVUELTO,
        models.EstadoComprobante.NO_AUTORIZADO,
    }
)


def emitir_ahora(
    comprobante: Comprobante,
    *,
    intentos: Optional[int] = None,
    espera: Optional[float] = None,
) -> models.ComprobanteEmitido:
    """Registra y procesa un comprobante de forma síncrona (sin Celery)."""
    registro = registrar(comprobante)
    return procesar(registro, intentos=intentos, espera=espera)


# ------------------------------------------------------------------ Celery


def _app_celery() -> Optional[Any]:
    """Devuelve la aplicación de Celery actual, o ``None`` si no está disponible.

    Celery es **opcional**: si no está instalado o no hay broker configurado, se
    devuelve ``None`` para que quien llame emita de forma síncrona.
    """
    try:
        from celery import current_app
    except ImportError:
        logger.info("Celery no está instalado: se emite de forma síncrona.")
        return None

    if current_app.conf.broker_url is None and not getattr(
        current_app.conf, "task_always_eager", False
    ):
        logger.warning("No hay broker de Celery configurado: se emite de forma síncrona.")
        return None
    return current_app


def encolar(registro: models.ComprobanteEmitido, *, usar_cola: bool = True) -> Optional[str]:
    """Envía el registro a la tarea de Celery y devuelve el id de la tarea.

    La tarea se resuelve con ``send_task`` por nombre, de forma que el paquete
    no impone una instancia concreta de Celery ni obliga a importar la app del
    proyecto.

    Devuelve ``None`` si no hay ningún broker de Celery configurado: en ese caso
    conviene llamar a :func:`procesar` de forma síncrona.
    """
    actual = _app_celery()
    if actual is None:
        return None

    opciones: Dict[str, Any] = {}
    if usar_cola and conf.celery_queue():
        opciones["queue"] = conf.celery_queue()

    resultado = actual.send_task(
        conf.nombre_tarea("emitir_comprobante"),
        args=[registro.pk],
        **opciones,
    )
    logger.info("Comprobante %s encolado (tarea %s)", registro.clave_acceso, resultado.id)
    return resultado.id


def encolar_autorizacion(registro: models.ComprobanteEmitido, *, usar_cola: bool = True) -> Optional[str]:
    """Encola solo la consulta de autorización (para los ``EN_PROCESO``)."""
    actual = _app_celery()
    if actual is None:
        return None

    opciones: Dict[str, Any] = {}
    if usar_cola and conf.celery_queue():
        opciones["queue"] = conf.celery_queue()

    resultado = actual.send_task(
        conf.nombre_tarea("consultar_autorizacion"),
        args=[registro.pk],
        **opciones,
    )
    return resultado.id
