"""Pruebas de la app de Django: modelos, admin, servicios y emisión."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, List

import pytest

# Las fixtures (entorno_django, configuracion, cliente_falso, clave_cifrado…) y las
# ayudas (_crear_p12, _datos_sri, _formulario…) están en conftest.py, para poder
# compartirlas con el resto de la batería.
from factec.django import sri_datos
from factec.excepciones import ErrorFacturacion

from conftest import (
    CLAVE_CERTIFICADO,
    RUC,
    ClienteFalso,
    _configuracion_emisor,
    _crear_p12,
    _datos_formulario,
    _datos_sri,
    _detalle,
    _formulario,
    _receptor,
    _superusuario,
)


# --------------------------------------------------------------- estructura


def test_tablas_creadas(entorno_django):
    from django.db import connection

    tablas = set(connection.introspection.table_names())
    assert "sri_fe_configuracionemisor" in tablas
    assert "sri_fe_comprobanteemitido" in tablas
    assert "sri_fe_secuencial" in tablas


def test_admin_registra_los_modelos(entorno_django):
    from django.contrib import admin as admin_django

    from factec.django import models

    for modelo in (models.ConfiguracionEmisor, models.ComprobanteEmitido, models.Secuencial):
        assert modelo in admin_django.site._registry


def test_checks_avisan_cuando_no_hay_configuracion(limpiar_tablas, clave_cifrado):
    from factec.django.checks import comprobar_configuracion

    ids = {p.id for p in comprobar_configuracion()}
    assert {"sri_fe.W001", "sri_fe.W002", "sri_fe.W003"} <= ids


def test_checks_no_dan_error_con_configuracion_correcta(configuracion):
    from factec.django.checks import comprobar_configuracion

    assert [p for p in comprobar_configuracion() if p.id.startswith("sri_fe.E")] == []


def test_check_detecta_certificado_de_otro_ruc(configuracion):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from factec.django import conf
    from factec.django.checks import comprobar_configuracion

    configuracion.certificado = SimpleUploadedFile("otro.p12", _crear_p12("1790012345001"))
    configuracion.establecer_clave(CLAVE_CERTIFICADO)
    configuracion.save()
    conf.limpiar_cache()

    ids = {p.id for p in comprobar_configuracion()}
    assert "sri_fe.E007" in ids


# ------------------------------------------------------- tabla de config


def test_carga_del_certificado_y_contraseña_cifrada(configuracion):
    from factec.django import models

    guardada = models.ConfiguracionEmisor.objects.get(pk=configuracion.pk)
    assert guardada.tiene_certificado
    assert guardada.clave_certificado_cifrada
    assert CLAVE_CERTIFICADO not in guardada.clave_certificado_cifrada
    assert guardada.obtener_clave() == CLAVE_CERTIFICADO


def test_certificado_se_puede_usar(configuracion):
    certificado = configuracion.certificado_obj()
    assert not certificado.vencido()
    assert configuracion.ruc_del_certificado() == RUC


def test_formulario_rechaza_contraseña_incorrecta(limpiar_tablas, clave_cifrado, certificado_p12):
    formulario = _formulario(certificado_p12, clave_certificado="incorrecta")
    assert not formulario.is_valid()
    assert "clave_certificado" in formulario.errors


def test_formulario_rechaza_certificado_de_otro_ruc(limpiar_tablas, clave_cifrado):
    formulario = _formulario(_crear_p12("1790012345001"))
    assert not formulario.is_valid()
    assert "certificado" in formulario.errors


def test_formulario_exige_certificado(limpiar_tablas, clave_cifrado):
    formulario = _formulario(False)
    assert not formulario.is_valid()
    assert "certificado" in formulario.errors


def test_formulario_exige_contraseña_al_subir_el_certificado(
    limpiar_tablas, clave_cifrado, certificado_p12
):
    """Caso real que rompía el admin: se sube el .p12 y se deja la contraseña vacía."""
    formulario = _formulario(certificado_p12, clave_certificado="")
    assert not formulario.is_valid()
    assert "clave_certificado" in formulario.errors


# ------------------------------------- consulta automática al SRI (en el admin)


def _parchear_sri(monkeypatch, respuesta: Any = None, error: Exception | None = None):
    """Sustituye la consulta al SRI por una respuesta prefabricada."""
    from factec.django import sri_datos
    from factec.sri.consulta_ruc import DatosRuc

    llamadas: list = []

    def falso(ruc, **kwargs):
        llamadas.append(ruc)
        if error is not None:
            raise error
        if respuesta is not None:
            return respuesta
        return DatosRuc(
            ruc=ruc, razon_social="URDIN GONZALEZ JOHNNY EDGAR", estado="ACTIVO",
            tipo_contribuyente="PERSONA NATURAL", regimen="RIMPE",
            categoria="NEGOCIO POPULAR", obligado_contabilidad=False,
            agente_retencion=False, contribuyente_especial=False, encontrado=True,
        )

    monkeypatch.setattr(sri_datos, "consultar_ruc", falso)
    return llamadas


def test_formulario_rellena_los_datos_del_sri(limpiar_tablas, clave_cifrado,
                                              certificado_p12, monkeypatch):
    """Solo se escriben RUC y datos manuales: lo demás llega del SRI."""
    llamadas = _parchear_sri(monkeypatch)

    formulario = _formulario(
        certificado_p12,
        ruc=RUC,
        razon_social="",          # no se escribe: lo trae el SRI
        regimen="",
        categoria="",
        obligado_contabilidad=False,
        dir_matriz="PANAMERICANA Y CARCHI",
        consultar_sri=True,
    )
    assert formulario.is_valid(), dict(formulario.errors)
    instancia = formulario.save()

    assert llamadas == [RUC]
    assert instancia.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"
    assert instancia.regimen == "RIMPE"
    assert instancia.categoria == "NEGOCIO POPULAR"
    assert instancia.rimpe_texto == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"
    assert instancia.obligado_contabilidad is False


def test_formulario_respeta_lo_escrito_al_consultar(limpiar_tablas, clave_cifrado,
                                                    certificado_p12, monkeypatch):
    _parchear_sri(monkeypatch)
    formulario = _formulario(
        certificado_p12, ruc=RUC, razon_social="MI NOMBRE PROPIO",
        dir_matriz="QUITO", consultar_sri=True,
    )
    assert formulario.is_valid(), dict(formulario.errors)
    assert formulario.save().razon_social == "MI NOMBRE PROPIO"


def test_formulario_avisa_si_el_sri_falla_pero_hay_datos(
    limpiar_tablas, clave_cifrado, certificado_p12, monkeypatch
):
    from factec.excepciones import ErrorSRI

    _parchear_sri(monkeypatch, error=ErrorSRI("sin conexión"))
    formulario = _formulario(
        certificado_p12, ruc=RUC, razon_social="ESCRITO A MANO",
        regimen="RIMPE", categoria="NEGOCIO POPULAR", dir_matriz="QUITO",
        consultar_sri=True,
    )
    assert formulario.is_valid(), dict(formulario.errors)
    assert any("sin conexión" in aviso for aviso in formulario.avisos_sri)


def test_formulario_exige_los_datos_si_el_sri_falla_y_faltan(
    limpiar_tablas, clave_cifrado, certificado_p12, monkeypatch
):
    from factec.excepciones import ErrorSRI

    _parchear_sri(monkeypatch, error=ErrorSRI("sin conexión"))
    formulario = _formulario(
        certificado_p12, ruc=RUC, razon_social="", dir_matriz="QUITO",
        consultar_sri=True,
    )
    assert not formulario.is_valid()
    assert "razon_social" in str(formulario.errors.get("ruc", ""))


def test_formulario_avisa_de_contribuyente_especial(
    limpiar_tablas, clave_cifrado, certificado_p12, monkeypatch
):
    from factec.sri.consulta_ruc import DatosRuc

    _parchear_sri(
        monkeypatch,
        respuesta=DatosRuc(
            ruc=RUC, razon_social="EMPRESA", estado="ACTIVO", regimen="RIMPE",
            obligado_contabilidad=True, contribuyente_especial=True,
            agente_retencion=True, encontrado=True,
        ),
    )
    formulario = _formulario(certificado_p12, ruc=RUC, dir_matriz="QUITO",
                             consultar_sri=True)
    assert formulario.is_valid(), dict(formulario.errors)
    assert any("contribuyente especial" in a for a in formulario.avisos_sri)
    assert any("agente de retención" in a for a in formulario.avisos_sri)


def test_formulario_sin_consulta_exige_los_datos_a_mano(limpiar_tablas, clave_cifrado,
                                                        certificado_p12, monkeypatch):
    llamadas = _parchear_sri(monkeypatch)
    formulario = _formulario(
        certificado_p12, ruc=RUC, razon_social="", dir_matriz="QUITO",
        consultar_sri=False,
    )
    assert not formulario.is_valid()
    assert "razon_social" in formulario.errors
    assert llamadas == []      # no se consultó el SRI


def test_admin_pagina_de_alta_responde(configuracion):
    from django.test import Client

    cliente = Client()
    cliente.force_login(_superusuario())
    respuesta = cliente.get("/admin/sri_fe/configuracionemisor/add/")
    assert respuesta.status_code == 200
    assert b"consultar_sri" in respuesta.content


def test_admin_accion_actualizar_desde_el_sri(configuracion, monkeypatch):
    from django.contrib import admin as admin_django
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from factec.django import models, sri_datos

    _parchear_sri(monkeypatch)
    configuracion.razon_social = "DESACTUALIZADA"
    configuracion.save()

    peticion = RequestFactory().get("/")
    peticion.session = {}
    peticion._messages = FallbackStorage(peticion)

    modelo_admin = admin_django.site._registry[models.ConfiguracionEmisor]
    modelo_admin.accion_actualizar_desde_sri(peticion, models.ConfiguracionEmisor.objects.all())

    configuracion.refresh_from_db()
    assert configuracion.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"


def test_formulario_valida_formato_de_ruc(limpiar_tablas, clave_cifrado, certificado_p12):
    formulario = _formulario(certificado_p12, ruc="123")
    assert not formulario.is_valid()
    assert "ruc" in formulario.errors


def test_formulario_conserva_contraseña_al_editar(configuracion, clave_cifrado):
    from factec.django import conf
    from factec.django.forms import ConfiguracionEmisorForm

    cifrada_antes = configuracion.clave_certificado_cifrada

    formulario = ConfiguracionEmisorForm(
        data=_datos_formulario(nombre="Renombrada", clave_certificado=""),
        instance=configuracion,
    )
    assert formulario.is_valid(), dict(formulario.errors)
    guardada = formulario.save()
    guardada.refresh_from_db()
    conf.limpiar_cache()

    assert guardada.nombre == "Renombrada"
    assert guardada.clave_certificado_cifrada == cifrada_antes
    assert guardada.obtener_clave() == CLAVE_CERTIFICADO


def test_solo_una_configuracion_activa_por_ambiente(configuracion, clave_cifrado):
    from factec.django import models

    formulario = _formulario(_crear_p12(RUC), nombre="Otra", pto_emi="002")
    assert formulario.is_valid(), dict(formulario.errors)
    formulario.save()

    activas = models.ConfiguracionEmisor.objects.filter(activo=True, ambiente=1)
    assert activas.count() == 1
    assert activas.first().nombre == "Otra"


def test_rimpe_segun_categoria(configuracion):
    assert configuracion.es_rimpe
    assert configuracion.es_negocio_popular
    assert configuracion.rimpe_texto == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"


def test_regimen_general_no_emite_rimpe(configuracion, clave_cifrado):
    from factec.django import conf

    configuracion.regimen = ""
    configuracion.categoria = ""
    configuracion.save()
    conf.limpiar_cache()
    assert not configuracion.es_rimpe
    assert configuracion.rimpe_texto is None


# ------------------------------------------------------------------- conf


def test_conf_lee_de_la_base_de_datos(configuracion):
    from factec.django import conf

    emisor = conf.emisor()
    assert emisor.ruc == RUC
    assert emisor.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"
    assert emisor.nombre_comercial == "ORVIQUE"
    assert emisor.contribuyente_rimpe is True
    assert conf.ambiente() == 1
    assert conf.clave_certificado() == CLAVE_CERTIFICADO
    assert conf.certificado().titular
    assert conf.cliente().ambiente == 1


# -------------------------------------------------------------- emisión


def test_crear_factura_guarda_borrador(configuracion):
    from factec.django import models, services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])

    assert registro.estado == models.EstadoComprobante.BORRADOR
    assert len(registro.clave_acceso) == 49
    assert registro.tipo_comprobante == "01"
    assert registro.importe_total == Decimal("11.50")
    assert registro.razon_social_receptor == "CONSUMIDOR FINAL"
    assert registro.identificacion_receptor == "9999999999999"
    assert registro.secuencial == "000000001"
    assert registro.configuracion == configuracion
    assert "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE" in registro.xml_sin_firma
    assert "ORVIQUE" in registro.xml_sin_firma


def test_firmar_cambia_el_estado(configuracion):
    from factec.django import models, services
    from factec.firma import verificar_firma

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.firmar(registro)
    registro.refresh_from_db()

    assert registro.estado == models.EstadoComprobante.FIRMADO
    assert verificar_firma(registro.xml_firmado, configuracion.certificado_obj())["valido"]


def test_secuencial_persistente(configuracion):
    from factec.django import services

    primero = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    segundo = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    assert (primero.secuencial, segundo.secuencial) == ("000000001", "000000002")
    assert primero.clave_acceso != segundo.clave_acceso


def test_enviar_recibido(configuracion, cliente_falso):
    from factec.django import models, services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.firmar(registro)
    recepcion = services.enviar(registro)
    registro.refresh_from_db()

    assert recepcion.recibida
    assert registro.estado == models.EstadoComprobante.RECIBIDO
    assert registro.intentos == 1
    assert cliente_falso.llamadas == ["recepcion"]


def test_enviar_devuelto_guarda_mensajes(configuracion, monkeypatch):
    from factec.django import conf, models, services

    monkeypatch.setattr(conf, "cliente", lambda: ClienteFalso(estado_recepcion="DEVUELTA"))
    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.firmar(registro)
    services.enviar(registro)
    registro.refresh_from_db()

    assert registro.estado == models.EstadoComprobante.DEVUELTO
    assert registro.mensajes[0]["identificador"] == "35"
    assert "ESTRUCTURA" in registro.error


def test_autorizar_marca_autorizado(configuracion, cliente_falso):
    from factec.django import models, services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.firmar(registro)
    services.enviar(registro)
    services.autorizar(registro, intentos=1, espera=0)
    registro.refresh_from_db()

    assert registro.estado == models.EstadoComprobante.AUTORIZADO
    assert registro.autorizado is True
    assert registro.es_final
    assert registro.numero_autorizacion == registro.clave_acceso
    assert registro.fecha_autorizacion is not None
    assert "factura" in registro.xml_autorizado
    assert registro.xml_para_archivar == registro.xml_autorizado


def test_autorizar_no_autorizado(configuracion, monkeypatch):
    from factec.django import conf, models, services

    monkeypatch.setattr(
        conf, "cliente", lambda: ClienteFalso(estado_autorizacion="NO AUTORIZADO")
    )
    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.firmar(registro)
    services.enviar(registro)
    services.autorizar(registro, intentos=1, espera=0)
    registro.refresh_from_db()

    assert registro.estado == models.EstadoComprobante.NO_AUTORIZADO
    assert registro.autorizado is False
    assert registro.puede_reintentarse is False


def test_procesar_flujo_completo(configuracion, cliente_falso):
    from factec.django import models, services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.procesar(registro, intentos=1, espera=0)
    registro.refresh_from_db()

    assert registro.estado == models.EstadoComprobante.AUTORIZADO
    assert cliente_falso.llamadas == ["recepcion", "autorizacion"]


def test_procesar_no_repite_un_autorizado(configuracion, cliente_falso):
    from factec.django import services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    services.procesar(registro, intentos=1, espera=0)
    cliente_falso.llamadas.clear()

    services.procesar(registro, intentos=1, espera=0)
    assert cliente_falso.llamadas == []


def test_emitir_ahora(configuracion, cliente_falso):
    from datetime import date

    from factec.comprobantes import Factura
    from factec.django import conf, services

    comprobante = Factura(
        emisor=conf.emisor(),
        ambiente=conf.ambiente(),
        fecha_emision=date(2026, 10, 8),
        secuencial=services.siguiente_secuencial("01"),
        receptor=_receptor(),
        detalles=[_detalle()],
    )
    registro = services.emitir_ahora(comprobante, intentos=1, espera=0)
    assert registro.autorizado


# ------------------------------------------------------------- Celery


def test_tareas_con_nombre_fijo(entorno_django):
    from factec.django import tasks

    assert tasks.NOMBRE_EMITIR_COMPROBANTE == "sri_fe.emitir_comprobante"
    assert tasks.NOMBRE_CONSULTAR_AUTORIZACION == "sri_fe.consultar_autorizacion"
    assert tasks.NOMBRE_REINTENTAR_PENDIENTES == "sri_fe.reintentar_pendientes"
    assert tasks.emitir_comprobante.name == "sri_fe.emitir_comprobante"


def test_emitir_comprobante_por_tarea(configuracion, cliente_falso):
    from factec.django import services, tasks

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    resultado = tasks.emitir_comprobante.apply(args=[registro.pk]).get()

    assert resultado["estado"] == "AUTORIZADO"
    assert resultado["numero_autorizacion"] == registro.clave_acceso


def test_tarea_ignora_comprobante_inexistente(configuracion):
    from factec.django import tasks

    assert tasks.emitir_comprobante.apply(args=[123456]).get() is None
    assert tasks.consultar_autorizacion.apply(args=[999]).get() is None


def test_reintentar_pendientes_recupera(configuracion, cliente_falso):
    from factec.django import services, tasks

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    resultados = tasks.reintentar_pendientes.apply(args=[10]).get()

    assert registro.clave_acceso in {r["clave_acceso"] for r in resultados}


def test_encolar_sin_broker_devuelve_none(configuracion):
    """Sin broker configurado no se encola: se avisa y se devuelve ``None``."""
    from factec.django import services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    assert services.encolar(registro) is None


# ---------------------------------- completar la configuración desde el SRI


class TestCompletar:
    """Estas pruebas usan el modelo, así que viven junto a Django configurado."""

    def test_rellena_los_campos_vacios(self):
        configuracion = _configuracion_emisor(ruc=RUC, dir_matriz="QUITO")
        cambios = sri_datos.completar_configuracion(configuracion, datos=_datos_sri())

        assert set(cambios) == set(sri_datos.CAMPOS_AUTOMATICOS_TEXTO)
        assert configuracion.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"
        assert configuracion.regimen == "RIMPE"
        assert configuracion.obligado_contabilidad is False
        assert configuracion.rimpe_texto == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"

    def test_rellena_tambien_obligado_contabilidad(self):
        configuracion = _configuracion_emisor(ruc=RUC, dir_matriz="QUITO")
        cambios = sri_datos.completar_configuracion(
            configuracion, datos=_datos_sri(obligado_contabilidad=True)
        )
        assert "obligado_contabilidad" in cambios
        assert configuracion.obligado_contabilidad is True

    def test_no_pisa_lo_que_el_usuario_escribio(self):
        configuracion = _configuracion_emisor(
            ruc=RUC, dir_matriz="QUITO", razon_social="MI PROPIA RAZON SOCIAL"
        )
        cambios = sri_datos.completar_configuracion(configuracion, datos=_datos_sri())

        assert "razon_social" not in cambios
        assert configuracion.razon_social == "MI PROPIA RAZON SOCIAL"
        assert "regimen" in cambios

    def test_forzar_sobrescribe(self):
        configuracion = _configuracion_emisor(
            ruc=RUC, dir_matriz="QUITO", razon_social="OTRA"
        )
        cambios = sri_datos.completar_configuracion(configuracion, datos=_datos_sri(), forzar=True)
        assert "razon_social" in cambios
        assert configuracion.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"

    def test_ruc_sin_datos_en_el_sri(self):
        with pytest.raises(ErrorFacturacion, match="no devolvió datos"):
            sri_datos.completar_configuracion(
                _configuracion_emisor(ruc=RUC, dir_matriz="QUITO"),
                datos=_datos_sri(encontrado=False, razon_social=""),
            )


class TestFaltantes:
    def test_lista_los_campos_manuales_pendientes(self):
        configuracion = _configuracion_emisor(
            ruc=RUC, razon_social="X", dir_matriz="QUITO", estab="001", pto_emi="001"
        )
        faltan = sri_datos.faltantes_manuales(configuracion)
        assert "dir_matriz" not in faltan
        assert "nombre_comercial" in faltan
        assert "certificado" in faltan

# --------------------------------------------------------------- admin


def test_admin_configuracion_responde_y_no_muestra_la_contraseña(configuracion):
    from django.test import Client

    cliente = Client()
    cliente.force_login(_superusuario())

    assert cliente.get("/admin/sri_fe/configuracionemisor/").status_code == 200
    respuesta = cliente.get(f"/admin/sri_fe/configuracionemisor/{configuracion.pk}/change/")
    assert respuesta.status_code == 200
    assert CLAVE_CERTIFICADO.encode() not in respuesta.content


def test_certificado_obj_lanza_error_del_paquete_sin_contraseña(configuracion):
    """Sin contraseña debe lanzar ErrorCertificado, no un ValidationError de Django."""
    from django.core.exceptions import ValidationError

    from factec.excepciones import ErrorCertificado

    configuracion.clave_certificado_cifrada = ""
    try:
        configuracion.certificado_obj()
    except ErrorCertificado as exc:
        assert "contraseña" in str(exc)
    except ValidationError as exc:  # pragma: no cover - sería el bug antiguo
        pytest.fail(f"Debería lanzar ErrorCertificado, no ValidationError: {exc}")
    else:  # pragma: no cover
        pytest.fail("Debería haber lanzado ErrorCertificado")


def test_certificado_obj_lanza_error_del_paquete_sin_archivo(limpiar_tablas, clave_cifrado):
    from factec.django import models
    from factec.excepciones import ErrorCertificado

    vacia = models.ConfiguracionEmisor(ruc=RUC, razon_social="X", dir_matriz="Y")
    with pytest.raises(ErrorCertificado, match="archivo de firma"):
        vacia.certificado_obj()


def test_admin_no_falla_sin_certificado(limpiar_tablas, clave_cifrado):
    """La página debe responder 200 aunque la configuración esté incompleta."""
    from django.test import Client

    from factec.django import models

    configuracion = models.ConfiguracionEmisor.objects.create(
        ruc=RUC, razon_social="SIN FIRMA", dir_matriz="QUITO",
    )
    cliente = Client()
    cliente.force_login(_superusuario())

    assert cliente.get("/admin/sri_fe/configuracionemisor/").status_code == 200
    assert cliente.get(f"/admin/sri_fe/configuracionemisor/{configuracion.pk}/change/").status_code == 200


def test_admin_no_falla_con_certificado_sin_contraseña(configuracion, clave_cifrado):
    """Caso real: se sube el .p12 pero todavía no se escribe la contraseña."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    configuracion.certificado = SimpleUploadedFile("firma.p12", _crear_p12(RUC))
    configuracion.clave_certificado_cifrada = ""
    configuracion.save()

    cliente = Client()
    cliente.force_login(_superusuario())

    respuesta = cliente.get("/admin/sri_fe/configuracionemisor/")
    assert respuesta.status_code == 200
    assert b"sin contrase" in respuesta.content

    respuesta = cliente.get(f"/admin/sri_fe/configuracionemisor/{configuracion.pk}/change/")
    assert respuesta.status_code == 200
    assert "contraseña del certificado".encode() in respuesta.content


def test_admin_comprobantes_responde(configuracion, cliente_falso):
    from django.test import Client

    from factec.django import services

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    cliente = Client()
    cliente.force_login(_superusuario())

    assert cliente.get("/admin/sri_fe/comprobanteemitido/").status_code == 200
    assert cliente.get(f"/admin/sri_fe/comprobanteemitido/{registro.pk}/change/").status_code == 200
    assert cliente.get("/admin/sri_fe/secuencial/").status_code == 200


# ---------------------------------------- emitir a partir del modelo propio

_MODELO_DOCUMENTO: Any = None


def _modelo_documento():
    """Modelo Django mínimo que imita un documento del proyecto.

    Se declara perezosamente porque ``app_label`` exige que las apps ya estén
    cargadas. Se crea con ``pk`` puesto a mano, así que no necesita tabla.
    """
    global _MODELO_DOCUMENTO
    if _MODELO_DOCUMENTO is not None:
        return _MODELO_DOCUMENTO

    from datetime import date

    from django.contrib.contenttypes.fields import GenericRelation
    from django.db import models as dj

    class DocumentoPrueba(dj.Model):
        referencia = dj.CharField(max_length=20)
        comprobantes_sri = GenericRelation("sri_fe.ComprobanteEmitido")

        class Meta:
            app_label = "sri_fe"
            # No gestionado: es un modelo solo para las pruebas, sin tabla, así que
            # no genera migraciones (las del paquete están al día).
            managed = False

        # Nombres convencionales que el adaptador reconoce sin configuración.
        @property
        def emisor(self):
            from factec.django import conf

            return conf.emisor()

        @property
        def ambiente(self):
            return 1

        @property
        def fecha(self):
            return date(2026, 10, 8)

        @property
        def secuencial(self):
            return "1"

        def cliente(self):
            return {"razon_social": "CONSUMIDOR FINAL",
                    "identificacion": "9999999999999",
                    "direccion": "QUITO"}

        def detalles(self):
            return [
                {
                    "descripcion": "Servicio de prueba",
                    "cantidad": 1,
                    "precio_unitario": "10.00",
                    "codigo_porcentaje_iva": "4",
                }
            ]

    _MODELO_DOCUMENTO = DocumentoPrueba
    return DocumentoPrueba


@pytest.fixture
def documento(configuracion):
    """Documento ya "guardado" (con pk) para emitir desde él."""
    return _modelo_documento()(pk=1, referencia="DOC-1")


@pytest.fixture
def documento_sin_emisor(documento):
    """Igual, pero sin emisor propio: debe caer en la configuración activa."""

    class _SinEmisor(type(documento)):
        pass

    documento.emisor_simulado = None
    return documento


def test_comprobante_de_usa_la_convencion(documento):
    from factec.comprobantes import Factura
    from factec.django import facturacion

    comprobante = facturacion.comprobante_de(documento)

    assert isinstance(comprobante, Factura)
    assert comprobante.receptor.razon_social == "CONSUMIDOR FINAL"
    assert comprobante.detalles[0].descripcion == "Servicio de prueba"
    assert comprobante.secuencial_normalizado == "000000001"
    assert comprobante.ambiente == 1
    assert comprobante.emisor.ruc == RUC


def test_firmar_modelo_firma_el_xml(documento):
    from factec.django import facturacion

    xml = facturacion.firmar_modelo(documento)

    assert "ds:Signature" in xml
    assert "<factura" in xml


def test_emitir_firma_envia_autoriza_y_vincula(configuracion, cliente_falso, documento):
    from factec.django import facturacion, models

    registro = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)

    assert registro.estado == models.EstadoComprobante.AUTORIZADO
    assert cliente_falso.llamadas == ["recepcion", "autorizacion"]
    assert registro.xml_firmado and "ds:Signature" in registro.xml_firmado
    # Queda enlazado con el documento del proyecto.
    assert registro.object_id == documento.pk
    assert registro.content_type.model == "documentoprueba"
    assert facturacion.registro_de(documento).pk == registro.pk
    assert facturacion.ya_emitido(documento) is True
    # Y desde el documento se llega al comprobante (GenericRelation).
    assert documento.comprobantes_sri.get().pk == registro.pk


def test_emitir_es_idempotente_por_documento(configuracion, cliente_falso, documento):
    from factec.django import facturacion, models

    primero = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)
    cliente_falso.llamadas.clear()

    segundo = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)

    assert segundo.pk == primero.pk
    assert cliente_falso.llamadas == []  # ya estaba autorizado: no se reenvía
    assert models.ComprobanteEmitido.objects.count() == 1


def test_emitir_con_forzar_crea_otro_comprobante(configuracion, cliente_falso, documento):
    from factec.django import facturacion, models

    primero = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)
    segundo = facturacion.emitir(documento, encolar=False, forzar=True, intentos=1, espera=0)

    assert segundo.pk != primero.pk
    assert segundo.clave_acceso != primero.clave_acceso
    assert models.ComprobanteEmitido.objects.count() == 2


def test_emitir_sin_vincular_no_guarda_el_enlace(configuracion, cliente_falso, documento):
    from factec.django import facturacion

    registro = facturacion.emitir(
        documento, encolar=False, vincular=False, intentos=1, espera=0
    )

    assert registro.object_id is None
    assert facturacion.registro_de(documento) is None


def test_emitir_reutiliza_un_borrador_pendiente(configuracion, cliente_falso, documento):
    """Un intento fallido previo no debe quemar otro secuencial."""
    from factec.django import facturacion, models, services

    cliente_falso.estado_recepcion = "DEVUELTA"
    primero = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)
    assert primero.estado == models.EstadoComprobante.DEVUELTO
    secuenciales = models.Secuencial.objects.count()

    cliente_falso.estado_recepcion = "RECIBIDA"
    cliente_falso.llamadas.clear()
    segundo = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)

    assert segundo.pk == primero.pk
    assert models.Secuencial.objects.count() == secuenciales
    assert cliente_falso.llamadas == []  # DEVUELTO ya no se reprocesa


def test_reintentar_sin_registro_avisa(configuracion, cliente_falso, documento):
    from factec.django import facturacion
    from factec.excepciones import ErrorFacturacion

    with pytest.raises(ErrorFacturacion):
        facturacion.reintentar(documento)


def test_reintentar_retoma_uno_en_proceso(configuracion, cliente_falso, documento):
    from factec.django import facturacion, models

    cliente_falso.estado_autorizacion = "EN PROCESO"
    registro = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)
    assert registro.estado == models.EstadoComprobante.EN_PROCESO

    cliente_falso.estado_autorizacion = "AUTORIZADO"
    recuperado = facturacion.reintentar(documento, encolar=False, intentos=1, espera=0)

    assert recuperado.pk == registro.pk
    assert recuperado.estado == models.EstadoComprobante.AUTORIZADO
    assert recuperado.numero_autorizacion == registro.clave_acceso


def test_reintentar_no_toca_un_autorizado(configuracion, cliente_falso, documento):
    from factec.django import facturacion

    registro = facturacion.emitir(documento, encolar=False, intentos=1, espera=0)
    cliente_falso.llamadas.clear()

    assert facturacion.reintentar(documento).pk == registro.pk
    assert cliente_falso.llamadas == []


def test_emitir_lote_procesa_todos(configuracion, cliente_falso):
    from factec.django import facturacion, models

    modelo = _modelo_documento()
    documentos = [modelo(pk=n, referencia=f"DOC-{n}") for n in (10, 11)]

    resultados = facturacion.emitir_lote(documentos, encolar=False)

    assert [r.estado for r in resultados] == [models.EstadoComprobante.AUTORIZADO] * 2
    assert len({r.clave_acceso for r in resultados}) == 2


def test_emitir_lote_sigue_tras_un_error(configuracion, cliente_falso):
    from factec.django import facturacion, models

    modelo = _modelo_documento()
    roto = modelo(pk=12, referencia="ROTO")
    roto.detalles = lambda: []  # sin líneas: el comprobante no se puede armar

    resultados = facturacion.emitir_lote([roto, modelo(pk=13, referencia="OK")],
                                         encolar=False)

    assert isinstance(resultados[0], Exception)
    assert resultados[1].estado == models.EstadoComprobante.AUTORIZADO


def test_emitir_lote_se_detiene_si_se_pide(configuracion, cliente_falso):
    from factec.django import facturacion

    roto = _modelo_documento()(pk=14, referencia="ROTO")
    roto.detalles = lambda: []

    with pytest.raises(Exception):
        facturacion.emitir_lote([roto], encolar=False, detener_en_error=True)


def test_emitir_usa_el_adaptador_indicado(configuracion, cliente_falso, documento):
    from factec.django import adaptadores, facturacion

    registro = facturacion.emitir(
        documento,
        adaptador=adaptadores.AdaptadorFactura,
        encolar=False,
        intentos=1,
        espera=0,
    )

    assert registro.tipo_comprobante == "01"


def test_check_avisa_si_ninguna_configuracion_esta_activa(configuracion):
    """E009: hay configuraciones, pero ninguna activa."""
    from django.core.checks import Error

    from factec.django import checks

    configuracion.activo = False
    configuracion.save()

    problemas = checks.comprobar_configuracion()
    assert any(
        p.id == checks.ID_ACTIVA and isinstance(p, Error) for p in problemas
    )


def test_emitir_con_objeto_sin_meta_no_falla(configuracion, cliente_falso):
    """Un objeto cualquiera (sin ``_meta``) se emite igual, pero sin vínculo."""
    from datetime import date

    from factec.django import facturacion, models

    class VentaSuelta:
        pk = 1
        fecha = date(2026, 10, 8)
        numero = "2"
        cliente = {"razon_social": "CONSUMIDOR FINAL", "identificacion": "9999999999999"}
        detalles = [{"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 10,
                     "codigo_porcentaje_iva": "4"}]

    registro = facturacion.emitir(VentaSuelta(), encolar=False, intentos=1, espera=0)

    assert registro.estado == models.EstadoComprobante.AUTORIZADO
    assert registro.object_id is None
    assert facturacion.registro_de(VentaSuelta()) is None
    assert facturacion.ya_emitido(VentaSuelta()) is False
    # Sin vínculo no hay idempotencia: cada llamada emite un comprobante nuevo.
    segundo = facturacion.emitir(VentaSuelta(), encolar=False, intentos=1, espera=0)
    assert segundo.pk != registro.pk


def test_sin_celery_instalado_se_emite_igual(configuracion, cliente_falso, monkeypatch):
    """``factec`` sin el extra ``[django]`` no trae Celery: se emite en el acto.

    Antes fallaba con ``ModuleNotFoundError``; el paquete lo trata como «no hay
    cola» y sigue de forma síncrona.
    """
    import sys

    from factec.django import services

    # Simula que Celery no está instalado.
    monkeypatch.setitem(sys.modules, "celery", None)

    registro = services.crear_factura(receptor=_receptor(), detalles=[_detalle()])
    assert services.encolar(registro) is None
    assert services.encolar_autorizacion(registro) is None

    # El camino síncrono (el que usa ``emitir`` cuando no hay cola) funciona igual.
    emitido = services.procesar(registro, intentos=1, espera=0)
    assert emitido.autorizado
