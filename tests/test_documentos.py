"""Pruebas de los modelos del SRI: catálogos, comprobantes y su emisión.

Comprueban que basta con añadir la app a ``INSTALLED_APPS`` y migrar: las tablas
existen, cada comprobante se emite desde su modelo y el XML que se genera es válido
según el esquema oficial.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from lxml import etree

from django.contrib import admin as _admin

from conftest import CLAVE_CERTIFICADO, RUC, _crear_p12, _receptor, _superusuario

HOY = date(2026, 10, 8)

#: Tablas que deben existir tras migrar.
TABLAS = (
    "sri_fe_cliente",
    "sri_fe_producto",
    "sri_fe_factura",
    "sri_fe_facturadetalle",
    "sri_fe_liquidacioncompra",
    "sri_fe_liquidacioncompradetalle",
    "sri_fe_notacredito",
    "sri_fe_notacreditodetalle",
    "sri_fe_notadebito",
    "sri_fe_notadebitomotivo",
    "sri_fe_guiaremision",
    "sri_fe_guiadestinatario",
    "sri_fe_guiadetalle",
    "sri_fe_retencion",
    "sri_fe_retenciondocsustento",
    "sri_fe_retenciondocsustentoimpuesto",
    "sri_fe_retencionimpuesto",
)


# ------------------------------------------------------------------- fixtures


@pytest.fixture
def documentos(entorno_django):
    """Deja los comprobantes vacíos antes y después de cada prueba."""
    from factec.django import documentos as mod

    def vaciar() -> None:
        mod.RetencionDocSustento.objects.all().delete()
        mod.Retencion.objects.all().delete()
        mod.GuiaDestinatario.objects.all().delete()
        mod.GuiaRemision.objects.all().delete()
        mod.NotaDebito.objects.all().delete()
        mod.NotaCredito.objects.all().delete()
        mod.LiquidacionCompra.objects.all().delete()
        mod.Factura.objects.all().delete()
        mod.Producto.objects.all().delete()
        mod.Cliente.objects.all().delete()

    vaciar()
    yield mod
    vaciar()


@pytest.fixture
def cliente(documentos):
    return documentos.Cliente.objects.create(
        razon_social="DISTRIBUIDORA ANDINA CÍA. LTDA.",
        identificacion="1790012345001",
        tipo_identificacion="04",
        direccion="AV. AMAZONAS 123, QUITO",
    )


@pytest.fixture
def proveedor(documentos):
    return documentos.Cliente.objects.create(
        razon_social="MARÍA PÉREZ LOOR",
        identificacion="1712345678",
        tipo_identificacion="05",
        direccion="CALLE 10 DE AGOSTO, GUAYAQUIL",
    )


@pytest.fixture
def producto(documentos):
    return documentos.Producto.objects.create(
        codigo_principal="SRV001",
        descripcion="Servicio de desarrollo",
        unidad_medida="hora",
        precio_unitario=Decimal("100"),
        codigo_porcentaje_iva="4",
    )


@pytest.fixture
def factura(documentos, configuracion, cliente, producto):
    """Factura con dos líneas: una al 15 % y otra al 0 %."""
    documento = documentos.Factura.objects.create(
        receptor=cliente, fecha_emision=HOY, forma_pago="19"
    )
    documento.detalles.create(producto=producto, cantidad=Decimal("2"))
    documento.detalles.create(
        descripcion="Soporte mensual", cantidad=1, precio_unitario=Decimal("50"),
        codigo_porcentaje_iva="0",
    )
    return documento


def _valida(esquemas: Any, nombre_xsd: str, xml: str) -> None:
    esquema = esquemas(nombre_xsd)
    documento = etree.fromstring(xml.encode("utf-8"))
    assert esquema.validate(documento), "\n".join(str(e) for e in esquema.error_log)


# --------------------------------------------------------------- migraciones


def test_las_tablas_se_crean_al_migrar(entorno_django):
    """Añadir la app a ``INSTALLED_APPS`` y migrar crea todas las tablas."""
    from django.db import connection

    existentes = set(connection.introspection.table_names())
    faltan = [tabla for tabla in TABLAS if tabla not in existentes]
    assert not faltan, f"faltan tablas: {faltan}"


def test_no_quedan_migraciones_pendientes(entorno_django):
    """El paquete trae sus migraciones: el proyecto no tiene que generarlas."""
    import django
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    cargador = MigrationLoader(connection)
    detector = MigrationAutodetector(
        cargador.project_state(), ProjectState.from_apps(django.apps.apps)
    )
    cambios = detector.changes(graph=cargador.graph, trim_to_apps={"sri_fe"})

    # Los modelos no gestionados (los que declaran las propias pruebas para imitar
    # un proyecto) no crean tablas, así que no cuentan como migración pendiente.
    pendientes = [
        f"{operacion.__class__.__name__}: {getattr(operacion, 'name', '')}"
        for migracion in cambios.get("sri_fe", [])
        for operacion in migracion.operations
        if (getattr(operacion, "options", None) or {}).get("managed") is not False
    ]
    assert not pendientes, f"hay migraciones sin generar: {pendientes}"


def test_manage_py_check_no_reporta_errores(entorno_django):
    """El admin y los modelos del paquete pasan la comprobación de Django."""
    import io

    from django.core.management import call_command

    salida = io.StringIO()
    call_command("check", stdout=salida, stderr=salida)

    assert "ERRORS" not in salida.getvalue(), salida.getvalue()


def test_se_registran_los_adaptadores(documentos):
    """Cada comprobante sabe con qué adaptador emitirse."""
    from factec.django import adaptadores

    assert adaptadores.obtener(documentos.Factura) is adaptadores.AdaptadorFactura
    assert adaptadores.obtener(documentos.NotaCredito) is adaptadores.AdaptadorNotaCredito
    assert adaptadores.obtener(documentos.NotaDebito) is adaptadores.AdaptadorNotaDebito
    assert (
        adaptadores.obtener(documentos.LiquidacionCompra)
        is adaptadores.AdaptadorLiquidacionCompra
    )
    assert adaptadores.obtener(documentos.GuiaRemision) is adaptadores.AdaptadorGuiaRemision
    assert adaptadores.obtener(documentos.Retencion) is adaptadores.AdaptadorRetencion


# ----------------------------------------------------------------- catálogos


def test_el_cliente_valida_la_identificacion(documentos):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        documentos.Cliente(
            razon_social="X", identificacion="1790012345", tipo_identificacion="04"
        ).full_clean()

    with pytest.raises(ValidationError):
        documentos.Cliente(
            razon_social="X", identificacion="1790012345001", tipo_identificacion="05"
        ).full_clean()

    valido = documentos.Cliente(
        razon_social="X", identificacion="1790012345001", tipo_identificacion="04"
    )
    valido.full_clean()
    assert valido.identificacion == "1790012345001"


def test_el_consumidor_final_usa_su_identificacion(documentos):
    cliente = documentos.Cliente(
        razon_social="CONSUMIDOR FINAL", tipo_identificacion="07", identificacion=""
    )
    cliente.full_clean()
    assert cliente.identificacion == "9999999999999"

    guardado = documentos.Cliente.consumidor_final()
    assert guardado.identificacion == "9999999999999"
    assert documentos.Cliente.consumidor_final().pk == guardado.pk


def test_la_linea_se_completa_con_el_producto(factura, producto):
    """Al elegir producto no hay que repetir código, precio ni IVA."""
    linea = factura.detalles.create(producto=producto, cantidad=2)

    assert linea.codigo_principal == "SRV001"
    assert linea.descripcion == "Servicio de desarrollo"
    assert linea.unidad_medida == "hora"
    assert linea.precio_unitario == Decimal("100.000000")
    assert linea.codigo_porcentaje_iva == "4"
    assert linea.precio_total_sin_impuesto == Decimal("200.00")
    assert linea.valor_iva == Decimal("30.00")       # 200 × 15 %
    assert linea.total == Decimal("230.00")


def test_los_totales_del_modelo_son_los_del_xml(factura):
    assert factura.subtotal == Decimal("250.00")     # 200 + 50
    assert factura.valor_iva == Decimal("30.00")     # solo la línea al 15 %
    assert factura.total == Decimal("280.00")


# ------------------------------------------------------------------- factura


def test_factura_emite_y_el_xml_es_valido(factura, cliente_falso, esquemas):
    registro = factura.emitir(encolar=False)
    factura.refresh_from_db()

    assert registro.estado == "AUTORIZADO"
    assert factura.estado == "AUTORIZADO"
    assert factura.autorizado is True
    assert factura.clave_acceso == registro.clave_acceso
    assert factura.numero_autorizacion == registro.clave_acceso
    assert factura.secuencial == "1"
    assert factura.comprobante_id == registro.pk
    assert factura.total == registro.importe_total == Decimal("280.00")
    _valida(esquemas, "factura_V1.1.0.xsd", registro.xml_sin_firma)
    assert "ds:Signature" in registro.xml_firmado


def test_factura_incluye_el_pago_con_el_total(factura, cliente_falso):
    """La forma de pago del modelo viaja al XML con el importe total."""
    registro = factura.emitir(encolar=False)

    assert "<formaPago>19</formaPago>" in registro.xml_sin_firma
    assert "<total>280.00</total>" in registro.xml_sin_firma


def test_emitir_dos_veces_no_duplica(factura, cliente_falso):
    from factec.django import models

    primero = factura.emitir(encolar=False)
    cliente_falso.llamadas.clear()
    segundo = factura.emitir(encolar=False)

    assert segundo.pk == primero.pk
    assert models.ComprobanteEmitido.objects.count() == 1
    assert cliente_falso.llamadas == []


# ------------------------------------------------------ liquidación de compra


def test_liquidacion_de_compra(documentos, configuracion, proveedor, cliente_falso, esquemas):
    liquidacion = documentos.LiquidacionCompra.objects.create(
        proveedor=proveedor, fecha_emision=HOY, correo="maria@example.com"
    )
    liquidacion.detalles.create(
        descripcion="Compra de suministros", cantidad=1,
        precio_unitario=Decimal("80"), codigo_porcentaje_iva="4",
    )
    registro = liquidacion.emitir(encolar=False)

    assert registro.estado == "AUTORIZADO"
    assert liquidacion.total == registro.importe_total == Decimal("92.00")
    _valida(esquemas, "LiquidacionCompra_V1.1.0.xsd", registro.xml_sin_firma)
    assert "<tipoNegociable>" in registro.xml_sin_firma
    assert "<correo>maria@example.com</correo>" in registro.xml_sin_firma


# ---------------------------------------------------------- nota de crédito


def test_nota_de_credito(documentos, configuracion, cliente, cliente_falso, esquemas):
    nota = documentos.NotaCredito.objects.create(
        receptor=cliente, fecha_emision=HOY, motivo="Devolución de mercadería",
        num_doc_modificado="001001000000012",          # sin guiones: se normaliza
        fecha_emision_doc_sustento=HOY,
    )
    nota.detalles.create(
        descripcion="Devolución", cantidad=1, precio_unitario=Decimal("30"),
        codigo_porcentaje_iva="4",
    )
    registro = nota.emitir(encolar=False)
    nota.refresh_from_db()

    assert nota.num_doc_modificado == "001-001-000000012"
    assert registro.estado == "AUTORIZADO"
    _valida(esquemas, "NotaCredito_V1.1.0.xsd", registro.xml_sin_firma)
    assert "<numDocModificado>001-001-000000012</numDocModificado>" in registro.xml_sin_firma


def test_nota_de_credito_exige_motivo(documentos, configuracion, cliente, cliente_falso):
    from factec.excepciones import ErrorValidacion

    nota = documentos.NotaCredito.objects.create(
        receptor=cliente, fecha_emision=HOY, motivo="",
        num_doc_modificado="001-001-000000012", fecha_emision_doc_sustento=HOY,
    )
    nota.detalles.create(descripcion="X", cantidad=1, precio_unitario=10)

    with pytest.raises(ErrorValidacion):
        nota.emitir(encolar=False)


# ----------------------------------------------------------- nota de débito


def test_nota_de_debito(documentos, configuracion, cliente, cliente_falso, esquemas):
    nota = documentos.NotaDebito.objects.create(
        receptor=cliente, fecha_emision=HOY, codigo_porcentaje_iva="4",
        num_doc_modificado="001-001-000000014", fecha_emision_doc_sustento=HOY,
        forma_pago="01",
    )
    nota.motivos.create(razon="Intereses por mora", valor=Decimal("25"))

    assert nota.total_sin_impuestos == Decimal("25.00")
    assert nota.valor_iva == Decimal("3.75")
    assert nota.total == Decimal("28.75")

    registro = nota.emitir(encolar=False)
    assert registro.estado == "AUTORIZADO"
    assert registro.importe_total == Decimal("28.75")
    _valida(esquemas, "NotaDebito_V1.0.0.xsd", registro.xml_sin_firma)


# -------------------------------------------------------- guía de remisión


def test_guia_de_remision(documentos, configuracion, cliente, cliente_falso, esquemas):
    guia = documentos.GuiaRemision.objects.create(
        fecha_emision=HOY, dir_partida="PANAMERICANA Y CARCHI",
        razon_social_transportista="TRANSPORTES DEL NORTE CÍA. LTDA.",
        ruc_transportista="1790012345001", placa="PBX-1234",
        fecha_ini_transporte=HOY, fecha_fin_transporte=HOY,
    )
    destinatario = guia.destinatarios.create(
        razon_social=cliente.razon_social, identificacion=cliente.identificacion,
        tipo_identificacion="04", direccion="AV. AMAZONAS 123, QUITO", motivo_traslado="01",
    )
    destinatario.detalles.create(descripcion="Servicio de desarrollo", cantidad=2,
                                  codigo_principal="SRV001")

    registro = guia.emitir(encolar=False)
    assert registro.estado == "AUTORIZADO"
    _valida(esquemas, "GuiaRemision_V1.1.0.xsd", registro.xml_sin_firma)


def test_guia_valida_las_fechas(documentos):
    from django.core.exceptions import ValidationError

    guia = documentos.GuiaRemision(
        fecha_emision=HOY, dir_partida="X", razon_social_transportista="Y",
        ruc_transportista="1790012345001", placa="PBX-1234",
        fecha_ini_transporte=HOY, fecha_fin_transporte=date(2026, 10, 1),
    )
    with pytest.raises(ValidationError):
        guia.full_clean()


# ----------------------------------------------------- comprobante de retención


@pytest.fixture
def retencion(documentos, configuracion, proveedor):
    retencion = documentos.Retencion.objects.create(
        sujeto_retenido=proveedor, fecha_emision=HOY, periodo_fiscal=HOY, parte_rel="NO"
    )
    sustento = retencion.docs_sustento.create(
        cod_sustento="01", cod_doc_sustento="01",
        num_doc_sustento="001-001-000000012",           # con guiones: se limpian
        fecha_emision=HOY,
        num_aut_doc_sustento="0810202601179001234500110010010000000121234567818",
        total_sin_impuestos=Decimal("100"), importe_total=Decimal("115"),
    )
    sustento.impuestos.create(codigo_porcentaje="4")
    sustento.retenciones.create(codigo="1", codigo_retencion="312",
                                 porcentaje_retener=Decimal("1.75"))
    return retencion


def test_retencion(documentos, retencion, cliente_falso, esquemas):
    registro = retencion.emitir(encolar=False)
    retencion.refresh_from_db()
    sustento = retencion.docs_sustento.get()

    assert sustento.num_doc_sustento == "001001000000012"
    assert registro.estado == "AUTORIZADO"
    assert retencion.total_retenido == Decimal("1.75")
    _valida(esquemas, "ComprobanteRetencion_V2.0.0.xsd", registro.xml_sin_firma)


def test_impuestos_y_retenciones_se_calculan(retencion):
    """Base, tarifa y valor se completan desde el documento y el porcentaje."""
    impuesto = retencion.docs_sustento.get().impuestos.get()
    assert impuesto.tarifa == Decimal("15.00")
    assert impuesto.base_imponible == Decimal("100.00")
    assert impuesto.valor == Decimal("15.00")

    aplicada = retencion.docs_sustento.get().retenciones.get()
    assert aplicada.base_imponible == Decimal("100.00")
    assert aplicada.valor_retenido == Decimal("1.75")      # 100 × 1,75 %


# -------------------------------------------------------------------- admin


@pytest.fixture
def admin_cliente(entorno_django):
    from django.test import Client

    cliente = Client(SERVER_NAME="localhost")
    cliente.force_login(_superusuario())
    return cliente


@pytest.mark.parametrize(
    "ruta",
    [
        "/admin/sri_fe/cliente/",
        "/admin/sri_fe/producto/",
        "/admin/sri_fe/factura/",
        "/admin/sri_fe/factura/add/",
        "/admin/sri_fe/liquidacioncompra/",
        "/admin/sri_fe/liquidacioncompra/add/",
        "/admin/sri_fe/notacredito/",
        "/admin/sri_fe/notacredito/add/",
        "/admin/sri_fe/notadebito/",
        "/admin/sri_fe/notadebito/add/",
        "/admin/sri_fe/guiaremision/",
        "/admin/sri_fe/guiaremision/add/",
        "/admin/sri_fe/retencion/",
        "/admin/sri_fe/retencion/add/",
        "/admin/sri_fe/retenciondocsustento/",
        "/admin/sri_fe/guiadestinatario/",
    ],
)
def test_el_admin_responde(admin_cliente, ruta):
    """Las páginas de listado y de alta de los comprobantes responden."""
    assert admin_cliente.get(ruta).status_code == 200


def test_el_admin_muestra_la_factura_con_sus_lineas(admin_cliente, factura):
    respuesta = admin_cliente.get(f"/admin/sri_fe/factura/{factura.pk}/change/")

    assert respuesta.status_code == 200
    contenido = respuesta.content.decode()
    assert "Servicio de desarrollo" in contenido
    assert "280.00" in contenido       # el total calculado


def test_el_admin_emite_la_factura(admin_cliente, factura, cliente_falso):
    """La acción del admin emite, firma y envía: la factura queda autorizada."""
    from factec.django import models

    respuesta = admin_cliente.post("/admin/sri_fe/factura/", {
        "action": "accion_emitir",
        "_selected_action": [str(factura.pk)],
    }, follow=True)

    assert respuesta.status_code == 200
    factura.refresh_from_db()
    assert factura.autorizado is True
    assert factura.clave_acceso == factura.comprobante.clave_acceso
    assert models.ComprobanteEmitido.objects.count() == 1
    # El listado muestra el estado con el que quedó.
    assert "Autorizado" in respuesta.content.decode()


# ------------------------------------------------------------------ detalles


def test_emitir_rehace_el_comprobante_devuelto(factura, cliente_falso):
    """Un devuelto se rehace con los datos actuales y con un secuencial nuevo.

    Reenviar el XML que el SRI ya devolvió daría el mismo error, y reutilizar su
    secuencial tampoco vale: el SRI lo registró al recibirlo, así que responde
    «ERROR SECUENCIAL REGISTRADO» y el documento no sale nunca. Se construye otro
    con número nuevo y se conserva el anterior como historial.
    """
    from factec.django import models

    cliente_falso.estado_recepcion = "DEVUELTA"
    primero = factura.emitir(encolar=False)
    assert primero.estado == models.EstadoComprobante.DEVUELTO

    cliente_falso.estado_recepcion = "RECIBIDA"
    segundo = factura.emitir(encolar=False)

    assert segundo.pk != primero.pk
    assert segundo.estado == models.EstadoComprobante.AUTORIZADO
    assert primero.secuencial != segundo.secuencial             # número nuevo
    assert int(factura.secuencial) == int(segundo.secuencial)
    # El rechazado queda como historial.
    assert models.ComprobanteEmitido.objects.filter(pk=primero.pk).exists()


def test_el_xml_sin_firmar_se_puede_pedir_sin_emitir(factura):
    xml = factura.xml()

    assert xml.startswith("<?xml")
    assert "<factura" in xml
    assert "<totalSinImpuestos>250.00</totalSinImpuestos>" in xml


def test_firmar_no_envia_nada(factura, cliente_falso):
    xml = factura.firmar()

    assert "ds:Signature" in xml
    assert cliente_falso.llamadas == []


def test_las_relaciones_de_django_se_leen_como_valores(factura):
    """Regresión: un ``RelatedManager`` es invocable y se ignoraba al adaptar."""
    from factec.django import adaptadores, facturacion

    comprobante = facturacion.comprobante_de(factura)
    lineas = adaptadores.AdaptadorFactura().detalles(factura)

    assert len(comprobante.detalles) == 2
    assert len(lineas) == 2
    assert comprobante.detalles[0].descripcion == "Servicio de desarrollo"


def test_el_receptor_del_paquete_sigue_funcionando(documentos, configuracion, cliente_falso):
    """Los comprobantes del paquete conviven con un receptor cualquiera."""
    from factec.comprobantes import Factura
    from factec.django import conf, services
    from factec.modelos import Detalle, Impuesto

    comprobante = Factura(
        emisor=conf.emisor(), ambiente=1, fecha_emision=HOY,
        secuencial=services.siguiente_secuencial("01"), receptor=_receptor(),
        detalles=[Detalle(descripcion="X", cantidad=1, precio_unitario=10,
                          impuestos=[Impuesto(codigo_porcentaje="4")])],
    )
    registro = services.emitir_ahora(comprobante)

    assert registro.estado == "AUTORIZADO"
    assert RUC in registro.clave_acceso


# ------------------------------------------- la línea se completa con el producto


def test_el_admin_crea_la_factura_solo_con_el_producto(admin_cliente, factura, producto):
    """Basta elegir el producto: no hay que repetir descripción, precio ni IVA."""
    from factec.django import documentos

    respuesta = admin_cliente.post("/admin/sri_fe/factura/add/", {
        "receptor": factura.receptor_id,
        "fecha_emision": "08/10/2026",
        "forma_pago": "19",
        # Una sola línea, indicando únicamente el producto y la cantidad.
        "detalles-TOTAL_FORMS": "1",
        "detalles-INITIAL_FORMS": "0",
        "detalles-MIN_NUM_FORMS": "0",
        "detalles-MAX_NUM_FORMS": "1000",
        "detalles-0-producto": str(producto.pk),
        "detalles-0-cantidad": "3",
        "detalles-0-id": "",
        "detalles-0-factura": "",
        "_save": "Guardar",
    }, follow=True)

    assert respuesta.status_code == 200
    nueva = documentos.Factura.objects.exclude(pk=factura.pk).get()
    linea = nueva.detalles.get()
    assert respuesta.redirect_chain or nueva.pk
    assert linea.descripcion == "Servicio de desarrollo"
    assert linea.codigo_principal == "SRV001"
    assert linea.unidad_medida == "hora"
    assert linea.precio_unitario == Decimal("100.000000")
    assert linea.codigo_porcentaje_iva == "4"
    assert linea.cantidad == Decimal("3.000000")
    assert nueva.total == Decimal("345.00")      # 300 + 15 %


def test_el_admin_pide_la_descripcion_si_no_hay_producto(admin_cliente, factura):
    """Sin producto, la línea suelta necesita descripción y precio."""
    respuesta = admin_cliente.post("/admin/sri_fe/factura/add/", {
        "receptor": factura.receptor_id,
        "fecha_emision": "08/10/2026",
        "forma_pago": "19",
        "detalles-TOTAL_FORMS": "1",
        "detalles-INITIAL_FORMS": "0",
        "detalles-MIN_NUM_FORMS": "0",
        "detalles-MAX_NUM_FORMS": "1000",
        "detalles-0-producto": "",
        "detalles-0-cantidad": "1",
        "detalles-0-id": "",
        "detalles-0-factura": "",
        "_save": "Guardar",
    })

    contenido = respuesta.content.decode()
    assert respuesta.status_code == 200      # vuelve al formulario con el error
    assert "Indique la descripción o elija un producto" in contenido


def test_el_producto_sirve_sus_datos_por_el_endpoint(admin_cliente, producto):
    """El formulario consulta los datos del producto para rellenar la línea."""
    respuesta = admin_cliente.get(f"/admin/sri_fe/producto/{producto.pk}/datos/")

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["descripcion"] == "Servicio de desarrollo"
    assert datos["codigo_principal"] == "SRV001"
    assert datos["unidad_medida"] == "hora"
    assert datos["precio_unitario"] == "100"
    assert datos["codigo_porcentaje_iva"] == "4"


def test_el_admin_carga_el_script_de_las_lineas(admin_cliente, factura):
    contenido = admin_cliente.get(
        f"/admin/sri_fe/factura/{factura.pk}/change/"
    ).content.decode()

    assert "sri_fe/js/lineas.js" in contenido


def test_una_linea_sin_producto_conserva_su_precio(admin_cliente, factura):
    """Si la línea no trae producto, lo escrito se respeta tal cual."""
    from factec.django import documentos

    respuesta = admin_cliente.post("/admin/sri_fe/factura/add/", {
        "receptor": factura.receptor_id,
        "fecha_emision": "08/10/2026",
        "forma_pago": "19",
        "detalles-TOTAL_FORMS": "1",
        "detalles-INITIAL_FORMS": "0",
        "detalles-MIN_NUM_FORMS": "0",
        "detalles-MAX_NUM_FORMS": "1000",
        "detalles-0-producto": "",
        "detalles-0-descripcion": "Ajuste manual",
        "detalles-0-cantidad": "1",
        "detalles-0-precio_unitario": "12.5",
        "detalles-0-codigo_porcentaje_iva": "0",
        "detalles-0-id": "",
        "detalles-0-factura": "",
        "_save": "Guardar",
    }, follow=True)

    assert respuesta.status_code == 200
    nueva = documentos.Factura.objects.exclude(pk=factura.pk).get()
    linea = nueva.detalles.get()
    assert linea.descripcion == "Ajuste manual"
    assert linea.precio_unitario == Decimal("12.500000")
    assert linea.codigo_porcentaje_iva == "0"
    assert linea.codigo_principal == ""            # sin producto, sin código
    assert nueva.total == Decimal("12.50")


def test_la_linea_nueva_no_trae_precio_ni_iva_puestos(admin_cliente, factura):
    """Una línea sin tocar no debe traer un precio 0 ni un IVA que no toque."""
    import re

    contenido = admin_cliente.get("/admin/sri_fe/factura/add/").content.decode()
    fila = contenido[contenido.find("detalles-0-producto"):]

    # El precio no viene con el 0 por omisión: o lo pone el producto, o se escribe.
    precio = re.search(r"<input[^>]*name=\"detalles-0-precio_unitario\"[^>]*>", fila)
    assert precio, "no se encontró el precio"
    assert 'value="' not in precio.group(0), f"el precio debería empezar vacío: {precio.group(0)}"

    # El IVA tampoco: el desplegable ofrece «el del producto» como primera opción.
    iva = re.search(r'<select name="detalles-0-codigo_porcentaje_iva".*?</select>', fila, re.S)
    assert iva, "no se encontró el desplegable del IVA"
    assert '<option value="" selected>— el del producto —</option>' in iva.group(0)


def test_una_linea_sin_iva_usa_el_del_catalogo(admin_cliente, factura):
    """Si no se indica IVA ni hay producto, se usa el 15 % por omisión."""
    from factec.django import documentos

    admin_cliente.post("/admin/sri_fe/factura/add/", {
        "receptor": factura.receptor_id,
        "fecha_emision": "08/10/2026",
        "forma_pago": "19",
        "detalles-TOTAL_FORMS": "1",
        "detalles-INITIAL_FORMS": "0",
        "detalles-MIN_NUM_FORMS": "0",
        "detalles-MAX_NUM_FORMS": "1000",
        "detalles-0-producto": "",
        "detalles-0-descripcion": "Servicio suelto",
        "detalles-0-cantidad": "1",
        "detalles-0-precio_unitario": "100",
        "detalles-0-codigo_porcentaje_iva": "",
        "detalles-0-id": "",
        "detalles-0-factura": "",
        "_save": "Guardar",
    }, follow=True)

    linea = documentos.FacturaDetalle.objects.latest("pk")
    assert linea.codigo_porcentaje_iva == "4"      # 15 %
    assert linea.total == Decimal("115.00")


# ------------------------------------------------ el índice del admin, agrupado


def _secciones(html: str) -> list:
    """Títulos de las secciones del índice, en el orden en que salen."""
    import re

    return re.findall(r'<a\b[^>]*class="section"[^>]*>([^<]+)</a>', html)


def _pagina_del_admin(cliente, ruta: str) -> str:
    """HTML de una página del admin, diciendo qué pasó si no es la esperada.

    Sin esto, un fallo en otra máquina (otra versión del admin, una redirección al
    login, una página sin permisos) sale como un «assert [] == [...]» que no dice
    nada: así el mensaje lleva el estado, la redirección y el principio del HTML.
    """
    respuesta = cliente.get(ruta)
    if respuesta.status_code != 200:
        destino = respuesta.get("Location") or "sin Location"
        raise AssertionError(f"{ruta} devolvió {respuesta.status_code} ({destino})")

    html = respuesta.content.decode()
    if not _secciones(html):
        posicion = html.find('class="section"')
        alrededor = html[max(0, posicion - 140):posicion + 180] if posicion >= 0 else ""
        raise AssertionError(
            f"{ruta} no trae secciones del admin ({len(html)} caracteres). "
            f"Alrededor de class=\"section\": {alrededor!r}"
        )
    return html


def test_el_indice_agrupa_por_temas(admin_cliente):
    """Configuración, catálogos, comprobantes y emisión van separados."""
    html = _pagina_del_admin(admin_cliente, "/admin/")
    propias = [
        titulo for titulo in _secciones(html)
        if titulo in ("Configuración del SRI", "Catálogos", "Comprobantes", "Emisión")
    ]

    # Las cuatro, en este orden. Las demás apps del proyecto no se tocan.
    assert propias == ["Configuración del SRI", "Catálogos", "Comprobantes", "Emisión"], (
        f"secciones encontradas: {_secciones(html)}"
    )


def test_cada_seccion_lleva_sus_modelos(admin_cliente):
    html = _pagina_del_admin(admin_cliente, "/admin/")

    def modelos_de(titulo: str) -> str:
        # Se busca por el texto del enlace (no por sus atributos: el admin puede
        # añadirle clases o títulos según la versión de Django).
        posicion = html.find(f">{titulo}</a>")
        if posicion < 0:
            return ""
        trozo = html[posicion:]
        return trozo[: trozo.find("</table>")]

    configuracion = modelos_de("Configuración del SRI")
    assert "Configuraciones del emisor" in configuracion
    assert "Secuenciales" in configuracion
    assert "Facturas" not in configuracion

    catalogos = modelos_de("Catálogos")
    assert "Clientes" in catalogos and "Productos" in catalogos
    assert "Facturas" not in catalogos

    comprobantes = modelos_de("Comprobantes")
    for nombre in ("Facturas", "Notas de crédito", "Notas de débito",
                   "Liquidaciones de compra", "Guías de remisión",
                   "Comprobantes de retención"):
        assert nombre in comprobantes, f"falta {nombre}"
    assert "Clientes" not in comprobantes

    emision = modelos_de("Emisión")
    assert "Comprobantes emitidos" in emision
    assert "Facturas" not in emision


def test_las_demas_apps_siguen_en_el_indice(admin_cliente):
    """La agrupación solo afecta a la app del paquete."""
    html = admin_cliente.get("/admin/").content.decode()

    # El bloque de la app de autenticación sigue tal cual (no se agrupa).
    assert 'class="app-auth module"' in html


def test_sin_permisos_no_aparecen_las_secciones(admin_cliente):
    """Un usuario sin permisos sobre la app no ve secciones vacías."""
    from django.contrib.auth import get_user_model

    limitado = get_user_model().objects.create_user(
        username="sin_permisos", password="x", is_staff=True
    )
    from django.test import Client

    cliente = Client(SERVER_NAME="localhost")
    cliente.force_login(limitado)
    html = cliente.get("/admin/").content.decode()

    for titulo in ("Configuración del SRI", "Catálogos", "Comprobantes", "Emisión"):
        assert f'class="section">{titulo}' not in html


def test_la_portada_de_la_app_tambien_agrupa(admin_cliente):
    """La portada /admin/sri_fe/ usa las mismas secciones."""
    html = _pagina_del_admin(admin_cliente, "/admin/sri_fe/")

    assert "Configuración del SRI" in _secciones(html), f"secciones: {_secciones(html)}"
    assert "Comprobantes" in _secciones(html), f"secciones: {_secciones(html)}"


def test_las_facturas_se_filtran_por_estado(admin_cliente, factura):
    """Se puede ver de un vistazo lo que está autorizado, devuelto o pendiente."""
    html = admin_cliente.get("/admin/sri_fe/factura/").content.decode()
    assert "comprobante__estado" in html

    # Con el filtro puesto, la página responde.
    assert admin_cliente.get(
        "/admin/sri_fe/factura/?comprobante__estado=AUTORIZADO"
    ).status_code == 200


def test_el_comprobante_emitido_enlaza_con_su_documento(admin_cliente, factura, cliente_falso):
    """Desde la emisión se llega al documento que la originó."""
    factura.emitir(encolar=False)
    html = admin_cliente.get("/admin/sri_fe/comprobanteemitido/").content.decode()

    assert f"/admin/sri_fe/factura/{factura.pk}/change/" in html


# --------------------------------------- campos que no llegaban al XML


def test_la_factura_envia_el_codigo_auxiliar(documentos, configuracion, cliente,
                                             producto, cliente_falso, esquemas):
    """El código auxiliar de la línea (y del producto) viaja al XML."""
    producto.codigo_auxiliar = "001"
    producto.save()

    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    linea = factura.detalles.create(producto=producto, cantidad=1)
    assert linea.codigo_auxiliar == "001"        # se copia del producto

    registro = factura.emitir(encolar=False)

    assert "<codigoPrincipal>SRV001</codigoPrincipal>" in registro.xml_sin_firma
    assert "<codigoAuxiliar>001</codigoAuxiliar>" in registro.xml_sin_firma
    _valida(esquemas, "factura_V1.1.0.xsd", registro.xml_sin_firma)


def test_el_codigo_auxiliar_tambien_en_nota_de_credito_y_liquidacion(
    documentos, configuracion, cliente, proveedor, cliente_falso, esquemas
):
    nota = documentos.NotaCredito.objects.create(
        receptor=cliente, fecha_emision=HOY, motivo="Ajuste",
        num_doc_modificado="001-001-000000001", fecha_emision_doc_sustento=HOY,
    )
    nota.detalles.create(descripcion="Servicio", cantidad=1, precio_unitario=10,
                          codigo_principal="SRV001", codigo_auxiliar="999")
    registro = nota.emitir(encolar=False)
    # La nota de crédito nombra los códigos ``codigoInterno``/``codigoAdicional``.
    assert "<codigoAdicional>999</codigoAdicional>" in registro.xml_sin_firma
    _valida(esquemas, "NotaCredito_V1.1.0.xsd", registro.xml_sin_firma)

    liquidacion = documentos.LiquidacionCompra.objects.create(
        proveedor=proveedor, fecha_emision=HOY)
    liquidacion.detalles.create(descripcion="Compra", cantidad=1, precio_unitario=50,
                                codigo_principal="C001", codigo_auxiliar="A-9")
    registro = liquidacion.emitir(encolar=False)
    assert "<codigoAuxiliar>A-9</codigoAuxiliar>" in registro.xml_sin_firma
    _valida(esquemas, "LiquidacionCompra_V1.1.0.xsd", registro.xml_sin_firma)


def test_los_datos_adicionales_del_detalle_llegan_al_xml(
    documentos, configuracion, cliente, cliente_falso, esquemas
):
    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    linea = factura.detalles.create(descripcion="Servicio", cantidad=1,
                                     precio_unitario=10)
    # El modelo del paquete no los tiene: se añaden por atributo, como haría
    # un modelo propio del proyecto.
    linea.detalles_adicionales = {"Marca": "ACME", "Modelo": "X1"}

    from factec.django import adaptadores, facturacion

    adaptador = adaptadores.AdaptadorFactura()
    detalle = adaptador.detalle_desde_linea(linea, factura)

    assert detalle.detalles_adicionales == {"Marca": "ACME", "Modelo": "X1"}
    assert adaptadores._datos_adicionales([{"nombre": "A", "valor": "1"}]) == {"A": "1"}
    assert adaptadores._datos_adicionales(None) == {}
    assert facturacion is not None


def test_la_guia_envia_el_documento_sustento(documentos, configuracion, cliente,
                                             cliente_falso, esquemas):
    guia = documentos.GuiaRemision.objects.create(
        fecha_emision=HOY, dir_partida="PANAMERICANA Y CARCHI",
        razon_social_transportista="TRANSPORTES DEL NORTE CÍA. LTDA.",
        ruc_transportista="1790012345001", placa="PBX-1234",
        fecha_ini_transporte=HOY, fecha_fin_transporte=HOY,
    )
    destinatario = guia.destinatarios.create(
        razon_social=cliente.razon_social, identificacion=cliente.identificacion,
        tipo_identificacion="04", direccion="AV. AMAZONAS 123, QUITO", motivo_traslado="01",
        cod_doc_sustento="01", num_doc_sustento="001001000000007",
        num_aut_doc_sustento="0810202601179001234500110010010000000071234567818",
        fecha_emision_doc_sustento=HOY,
    )
    destinatario.detalles.create(descripcion="Bien", cantidad=1, codigo_principal="B1")

    registro = guia.emitir(encolar=False)
    destinatario.refresh_from_db()

    assert destinatario.num_doc_sustento == "001-001-000000007"   # se normaliza
    assert "<codDocSustento>01</codDocSustento>" in registro.xml_sin_firma
    assert "<numDocSustento>001-001-000000007</numDocSustento>" in registro.xml_sin_firma
    _valida(esquemas, "GuiaRemision_V1.1.0.xsd", registro.xml_sin_firma)


def test_la_retencion_envia_pagos_al_exterior(documentos, configuracion, proveedor,
                                              cliente_falso, esquemas):
    retencion = documentos.Retencion.objects.create(
        sujeto_retenido=proveedor, fecha_emision=HOY, periodo_fiscal=HOY, parte_rel="SI")
    sustento = retencion.docs_sustento.create(
        cod_sustento="01", cod_doc_sustento="01", num_doc_sustento="001001000000012",
        fecha_emision=HOY, total_sin_impuestos=Decimal("100"),
        importe_total=Decimal("115"), pago_loc_ext="02", tipo_regi="01",
        pais_efec_pago="840", aplic_conv_dob_trib="NO", pag_ext_suj_ret_nor_leg="NO",
    )
    sustento.impuestos.create(codigo_porcentaje="4")
    sustento.retenciones.create(codigo="1", codigo_retencion="312",
                                 porcentaje_retener=Decimal("1.75"))

    registro = retencion.emitir(encolar=False)

    assert "<pagoLocExt>02</pagoLocExt>" in registro.xml_sin_firma
    assert "<paisEfecPago>840</paisEfecPago>" in registro.xml_sin_firma
    _valida(esquemas, "ComprobanteRetencion_V2.0.0.xsd", registro.xml_sin_firma)


def test_el_documento_no_pisa_la_configuracion_del_emisor(documentos, configuracion,
                                                          cliente, cliente_falso):
    """Los datos de establecimiento salen del emisor; el documento puede cambiarlos."""
    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    factura.detalles.create(descripcion="Servicio", cantidad=1, precio_unitario=10)

    from factec.django import adaptadores

    # Sin campos propios: se usan los de la configuración.
    assert adaptadores.AdaptadorFactura().datos_del_establecimiento(factura) == {}

    factura.contribuyente_especial = "1234"
    assert adaptadores.AdaptadorFactura().datos_del_establecimiento(factura) == {
        "contribuyente_especial": "1234"
    }


# ------------------------------------------------- campos adicionales de la tienda


def test_los_campos_adicionales_se_escriben_y_se_leen(entorno_django):
    from django.core.exceptions import ValidationError

    from factec.django.documentos import (
        escribir_campos_adicionales,
        leer_campos_adicionales,
    )

    assert leer_campos_adicionales("") == {}
    assert leer_campos_adicionales(None) == {}
    assert leer_campos_adicionales("MARCA=ACME; LOTE=2026-01") == {
        "MARCA": "ACME", "LOTE": "2026-01"
    }
    assert leer_campos_adicionales("MARCA=ACME\nLOTE=2026-01") == {
        "MARCA": "ACME", "LOTE": "2026-01"
    }
    # Un diccionario (por ejemplo de un JSONField propio) también sirve.
    assert leer_campos_adicionales({"MARCA": "ACME", "VACIO": ""}) == {"MARCA": "ACME"}
    assert escribir_campos_adicionales({"MARCA": "ACME", "LOTE": "1"}) == "MARCA=ACME; LOTE=1"

    for texto in ("MARCA", "=ACME", "MARCA=", f"{'N' * 301}=ACME"):
        with pytest.raises(ValidationError):
            leer_campos_adicionales(texto)


def test_los_datos_adicionales_de_la_linea_llegan_al_xml(
    documentos, configuracion, cliente, cliente_falso, esquemas
):
    """``detallesAdicionales`` de la línea viaja al SRI."""
    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    factura.detalles.create(
        descripcion="Servicio", cantidad=1, precio_unitario=10,
        datos_adicionales="MARCA=ACME; LOTE=2026-01",
    )

    registro = factura.emitir(encolar=False)

    assert '<detAdicional nombre="MARCA" valor="ACME"' in registro.xml_sin_firma
    assert '<detAdicional nombre="LOTE" valor="2026-01"' in registro.xml_sin_firma
    _valida(esquemas, "factura_V1.1.0.xsd", registro.xml_sin_firma)


def test_la_guia_envia_los_datos_adicionales_del_bien(
    documentos, configuracion, cliente, cliente_falso, esquemas
):
    guia = documentos.GuiaRemision.objects.create(
        fecha_emision=HOY, dir_partida="PANAMERICANA Y CARCHI",
        razon_social_transportista="TRANSPORTES DEL NORTE CÍA. LTDA.",
        ruc_transportista="1790012345001", placa="PBX-1234",
        fecha_ini_transporte=HOY, fecha_fin_transporte=HOY,
    )
    destinatario = guia.destinatarios.create(
        razon_social=cliente.razon_social, identificacion=cliente.identificacion,
        tipo_identificacion="04", direccion="AV. AMAZONAS 123, QUITO", motivo_traslado="01",
    )
    destinatario.detalles.create(
        descripcion="Bulto", cantidad=2, codigo_principal="B1", codigo_adicional="B1-A",
        datos_adicionales="MARCA=ACME",
    )

    registro = guia.emitir(encolar=False)

    assert "<codigoAdicional>B1-A</codigoAdicional>" in registro.xml_sin_firma
    assert '<detAdicional nombre="MARCA" valor="ACME"' in registro.xml_sin_firma
    _valida(esquemas, "GuiaRemision_V1.1.0.xsd", registro.xml_sin_firma)


def test_los_campos_adicionales_de_la_tienda_van_en_los_comprobantes(
    documentos, configuracion, cliente, cliente_falso, esquemas
):
    """La tienda define sus campos una vez y el comprobante puede añadir o pisar."""
    from factec.django import conf

    configuracion.campos_adicionales = "VENDEDOR=JOHNNY; SUCURSAL=NORTE"
    configuracion.save()
    conf.limpiar_cache()

    factura = documentos.Factura.objects.create(
        receptor=cliente, fecha_emision=HOY, observaciones="Pago a 30 días",
        informacion_adicional="VENDEDOR=MARÍA; ORDEN=1234",
    )
    factura.detalles.create(descripcion="Servicio", cantidad=1, precio_unitario=10)

    registro = factura.emitir(encolar=False)
    xml = registro.xml_sin_firma

    assert '<campoAdicional nombre="VENDEDOR">MARÍA</campoAdicional>' in xml
    assert '<campoAdicional nombre="SUCURSAL">NORTE</campoAdicional>' in xml
    assert '<campoAdicional nombre="ORDEN">1234</campoAdicional>' in xml
    assert '<campoAdicional nombre="Observaciones">Pago a 30 días</campoAdicional>' in xml
    _valida(esquemas, "factura_V1.1.0.xsd", xml)


def test_los_campos_adicionales_se_validan_al_guardar(documentos, configuracion, cliente):
    """El SRI admite 3 datos por línea y 15 campos por comprobante."""
    from django.core.exceptions import ValidationError

    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    linea = documentos.FacturaDetalle(
        factura=factura, descripcion="X", cantidad=1, precio_unitario=1,
        datos_adicionales="A=1; B=2; C=3; D=4",
    )
    with pytest.raises(ValidationError) as error:
        linea.full_clean()
    assert "datos_adicionales" in error.value.message_dict

    linea.datos_adicionales = "MARCA"
    with pytest.raises(ValidationError) as error:
        linea.full_clean()
    assert "datos_adicionales" in error.value.message_dict

    linea.datos_adicionales = "A=1; B=2; C=3"
    linea.full_clean()

    configuracion.campos_adicionales = "; ".join(f"C{numero}=x" for numero in range(16))
    with pytest.raises(ValidationError) as error:
        configuracion.full_clean()
    assert "campos_adicionales" in error.value.message_dict


def test_el_admin_ofrece_los_campos_adicionales(admin_cliente, factura, producto):
    respuesta = admin_cliente.get(f"/admin/sri_fe/factura/{factura.pk}/change/")
    contenido = respuesta.content.decode()

    assert "informacion_adicional" in contenido     # campos del comprobante
    assert "datos_adicionales" in contenido         # datos de cada línea
    assert respuesta.status_code == 200


def test_el_chequeo_avisa_de_migraciones_pendientes(configuracion, monkeypatch):
    """Con la base de datos a medias, el aviso es «migre», no «ninguna activa».

    Al actualizar el paquete se añaden columnas a la tabla de configuraciones;
    ``migrate`` lanza los chequeos antes de crearlas.
    """
    from django.db import connection

    from factec.django import checks, conf

    monkeypatch.setattr(conf, "configuracion_activa", lambda *a, **k: None)
    original = connection.introspection.get_table_description
    monkeypatch.setattr(
        connection.introspection, "get_table_description",
        lambda cursor, tabla, *resto, **extra: original(cursor, tabla, *resto, **extra)[:-1],
    )

    codigos = {problema.id for problema in checks.comprobar_configuracion()}

    assert "sri_fe.W008" in codigos
    assert "sri_fe.E009" not in codigos


# ------------------------------- fecha de emisión (ventana que exige el SRI)


def test_el_admin_no_deja_guardar_una_factura_con_fecha_futura(documentos, cliente):
    """El error sale en el propio campo, antes de gastar un secuencial."""
    from datetime import timedelta

    from django.core.exceptions import ValidationError

    from factec.sri.fechas import hoy_en_ecuador

    factura = documentos.Factura(
        receptor=cliente, fecha_emision=hoy_en_ecuador() + timedelta(days=1)
    )
    with pytest.raises(ValidationError) as error:
        factura.full_clean()

    mensaje = " ".join(error.value.message_dict["fecha_emision"])
    assert "FECHA EMISIÓN EXTEMPORANEA" in mensaje
    assert "posterior a hoy" in mensaje


def test_el_admin_no_deja_guardar_una_factura_de_mas_de_90_dias(documentos, cliente, monkeypatch):
    from datetime import timedelta

    from django.core.exceptions import ValidationError

    from factec.sri import fechas

    monkeypatch.setattr(fechas, "DIAS_TOLERANCIA", 90)
    factura = documentos.Factura(
        receptor=cliente, fecha_emision=fechas.hoy_en_ecuador() - timedelta(days=91)
    )
    with pytest.raises(ValidationError) as error:
        factura.full_clean()

    assert "tolerancia" in " ".join(error.value.message_dict["fecha_emision"])


def test_un_comprobante_autorizado_se_puede_seguir_editando(
    documentos, factura, cliente_falso, monkeypatch
):
    """La validación de la fecha no debe impedir editar algo ya autorizado."""
    from datetime import timedelta

    from factec.sri import fechas

    factura.emitir(encolar=False)
    assert factura.comprobante.autorizado

    monkeypatch.setattr(fechas, "DIAS_TOLERANCIA", 90)
    factura.fecha_emision = fechas.hoy_en_ecuador() - timedelta(days=200)
    factura.observaciones = "Nota interna posterior"
    factura.full_clean()          # no debe quejarse


def test_un_comprobante_no_se_puede_emitir_con_fecha_futura(documentos, factura):
    """Ni siquiera pidiendo la fecha a mano: el XML se niega a construirse."""
    from datetime import timedelta

    from factec.excepciones import ErrorValidacion
    from factec.sri.fechas import hoy_en_ecuador

    manana = hoy_en_ecuador() + timedelta(days=1)
    factura.fecha_emision = manana
    factura.save()

    with pytest.raises(ErrorValidacion, match="FECHA EMISIÓN EXTEMPORANEA"):
        factura.emitir(encolar=False, fecha_emision=manana)


def test_el_documento_con_fecha_futura_se_emite_con_la_fecha_de_hoy(
    documentos, factura, cliente_falso
):
    """Al emitir manda el día de la firma: la fecha del documento no se envía tal cual.

    Un documento con la fecha mal puesta (por ejemplo, guardado con la zona horaria
    en UTC) no deja el comprobante inutilizable: se emite con la fecha de hoy y el
    SRI lo acepta.
    """
    from datetime import timedelta

    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    factura.fecha_emision = hoy + timedelta(days=1)
    factura.save()

    registro = factura.emitir(encolar=False)

    assert registro.fecha_emision == hoy
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in registro.xml_sin_firma
    assert registro.autorizado


def test_corregir_un_comprobante_devuelto_lo_rehace(documentos, factura, cliente_falso):
    """El caso real: corregir el documento y reemitir genera un XML nuevo.

    Reenviar el XML rechazado repetiría el error, y volver a usar su secuencial
    también: el SRI lo registró al recibirlo. Así que se construye otro con los
    datos actuales **y con número nuevo**; el rechazado queda como historial.
    """
    from datetime import timedelta

    from factec.django import models
    from factec.sri.fechas import hoy_en_ecuador

    hoy = hoy_en_ecuador()
    ayer = hoy - timedelta(days=1)

    # 1) Se emite y el SRI lo devuelve (por ejemplo, por estructura). El
    #    comprobante se firma con la fecha del día, no con la del documento.
    factura.fecha_emision = ayer
    factura.save()

    cliente_falso.estado_recepcion = "DEVUELTA"
    primero = factura.emitir(encolar=False)

    assert primero.estado == models.EstadoComprobante.DEVUELTO
    assert primero.fecha_emision == hoy
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in primero.xml_sin_firma
    reservado = models.Secuencial.objects.count()

    # 2) Se corrige la fecha y se vuelve a emitir.
    factura.fecha_emision = hoy
    factura.save()

    cliente_falso.estado_recepcion = "RECIBIDA"
    segundo = factura.emitir(encolar=False)

    assert segundo.autorizado
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in segundo.xml_sin_firma
    assert segundo.pk != primero.pk                     # no se reenvía el rechazado
    assert primero.secuencial != segundo.secuencial     # ni se repite el número
    assert models.Secuencial.objects.count() >= reservado
    assert int(factura.secuencial) == int(segundo.secuencial)
    assert models.ComprobanteEmitido.objects.filter(pk=primero.pk).exists()


def test_reintentar_tambien_rehace_un_devuelto(documentos, factura, cliente_falso):
    from factec.django import models

    cliente_falso.estado_recepcion = "DEVUELTA"
    primero = factura.emitir(encolar=False)
    assert primero.estado == models.EstadoComprobante.DEVUELTO

    factura.observaciones = "Corregido tras la devolución"
    factura.save()

    cliente_falso.estado_recepcion = "RECIBIDA"
    segundo = factura.reintentar(encolar=False)

    assert segundo.autorizado
    assert segundo.pk != primero.pk
    assert primero.secuencial != segundo.secuencial
    assert "Corregido tras la devolución" in segundo.xml_sin_firma


def test_un_secuencial_registrado_no_se_reutiliza(documentos, factura, cliente_falso):
    """El caso que dejaba una factura sin autorizar para siempre.

    El SRI registra el comprobante aunque lo devuelva, así que su número queda
    quemado: reintentar con él da «ERROR SECUENCIAL REGISTRADO» (45) una y otra
    vez. Al rehacer un rechazado se estrena secuencial y el documento sale.
    """
    from factec.django import models

    cliente_falso.estado_recepcion = "DEVUELTA"
    primero = factura.emitir(encolar=False)
    assert primero.estado == models.EstadoComprobante.DEVUELTO

    # El documento se queda apuntando al número del comprobante rechazado.
    factura.refresh_from_db()
    quemado = primero.secuencial
    assert str(factura.secuencial) == str(int(quemado))

    cliente_falso.estado_recepcion = "RECIBIDA"
    segundo = factura.reintentar(encolar=False)

    assert segundo.secuencial != quemado          # no se reenvía el quemado
    assert int(segundo.secuencial) > int(quemado)
    assert segundo.autorizado
    factura.refresh_from_db()
    assert str(factura.secuencial) == str(int(segundo.secuencial))
    # El rechazado se conserva como historial con su número.
    assert models.ComprobanteEmitido.objects.filter(secuencial=quemado).exists()


# ------------------------------------ archivos: XML y respuestas del SRI


def test_la_emision_guarda_los_xml_y_las_respuestas(documentos, factura, cliente_falso):
    """Todo queda en disco por año, mes y día, dentro de MEDIA_ROOT."""
    from pathlib import Path

    from django.conf import settings

    from factec.django import archivos, models

    registro = factura.emitir(encolar=False)

    # La carpeta es año/mes/día + serie y clave de acceso.
    assert registro.carpeta.startswith(
        f"sri/comprobantes/{registro.fecha_emision:%Y/%m/%d}/"
    )
    assert registro.carpeta.endswith(registro.clave_acceso)
    assert registro.secuencial in registro.carpeta

    carpeta = Path(settings.MEDIA_ROOT) / registro.carpeta
    for nombre in (
        archivos.NOMBRE_SIN_FIRMA,
        archivos.NOMBRE_FIRMADO,
        archivos.NOMBRE_AUTORIZADO,
        archivos.NOMBRE_RESPUESTA_RECEPCION,
        archivos.NOMBRE_RESPUESTA_AUTORIZACION,
    ):
        assert (carpeta / nombre).is_file(), f"falta {nombre}"

    # El contenido es el del comprobante (y es XML válido del SRI).
    sin_firma = (carpeta / archivos.NOMBRE_SIN_FIRMA).read_text(encoding="utf-8")
    assert sin_firma == registro.xml_sin_firma
    assert f"<fechaEmision>{registro.fecha_emision:%d/%m/%Y}</fechaEmision>" in sin_firma
    assert (carpeta / archivos.NOMBRE_AUTORIZADO).read_text(encoding="utf-8") == (
        registro.xml_autorizado
    )

    # Las respuestas del SRI quedan en el registro y en su archivo.
    assert registro.estado == models.EstadoComprobante.AUTORIZADO
    assert "RECIBIDA" in registro.respuesta_recepcion
    assert "RespuestaAutorizacionComprobante" in registro.respuesta_autorizacion
    assert (carpeta / archivos.NOMBRE_RESPUESTA_RECEPCION).read_text(encoding="utf-8") == (
        registro.respuesta_recepcion
    )


def test_un_comprobante_devuelto_tambien_deja_sus_archivos(documentos, factura, cliente_falso):
    """Aunque el SRI lo devuelva, queda la evidencia completa en la carpeta."""
    from pathlib import Path

    from django.conf import settings

    from factec.django import archivos, models

    cliente_falso.estado_recepcion = "DEVUELTA"
    registro = factura.emitir(encolar=False)

    assert registro.estado == models.EstadoComprobante.DEVUELTO
    carpeta = Path(settings.MEDIA_ROOT) / registro.carpeta

    assert (carpeta / archivos.NOMBRE_SIN_FIRMA).is_file()
    assert (carpeta / archivos.NOMBRE_FIRMADO).is_file()
    assert (carpeta / archivos.NOMBRE_RESPUESTA_RECEPCION).is_file()
    assert (carpeta / archivos.NOMBRE_ERROR).is_file()
    assert not (carpeta / archivos.NOMBRE_AUTORIZADO).exists()

    assert "ARCHIVO NO CUMPLE ESTRUCTURA XML" in (
        carpeta / archivos.NOMBRE_ERROR
    ).read_text(encoding="utf-8")


def test_se_puede_desactivar_el_guardado_de_archivos(documentos, factura, cliente_falso,
                                                    monkeypatch):
    from pathlib import Path

    from django.conf import settings

    from factec.django import archivos, conf

    original = conf.obtener
    monkeypatch.setattr(
        conf,
        "obtener",
        lambda nombre, por_defecto=None: (
            False if nombre == "GUARDAR_ARCHIVOS" else original(nombre, por_defecto)
        ),
    )

    registro = factura.emitir(encolar=False)

    assert registro.carpeta == ""
    assert not (Path(settings.MEDIA_ROOT) / archivos.carpeta_de(registro)).exists()
    # El XML sigue en la base de datos.
    assert registro.xml_firmado


def test_el_admin_enlaza_y_sirve_los_archivos(admin_cliente, factura, cliente_falso):
    from django.conf import settings

    registro = factura.emitir(encolar=False)
    url_cambio = f"/admin/sri_fe/comprobanteemitido/{registro.pk}/change/"

    respuesta = admin_cliente.get(url_cambio)
    assert respuesta.status_code == 200
    cuerpo = respuesta.content.decode()
    assert "Archivos y respuestas del SRI" in cuerpo
    assert registro.carpeta in cuerpo
    assert "sin_firma.xml" in cuerpo and "respuesta_autorizacion.xml" in cuerpo

    # La descarga del admin devuelve el archivo…
    descarga = admin_cliente.get(
        f"/admin/sri_fe/comprobanteemitido/{registro.pk}/archivo/firmado.xml/"
    )
    assert descarga.status_code == 200
    assert b"<factura" in b"".join(descarga.streaming_content)

    # …y solo los nombres conocidos.
    assert admin_cliente.get(
        f"/admin/sri_fe/comprobanteemitido/{registro.pk}/archivo/secreto.txt/"
    ).status_code == 404

    assert settings.MEDIA_ROOT  # el proyecto tiene que definir MEDIA_ROOT


def test_el_comando_archiva_los_comprobantes_anteriores(documentos, factura, cliente_falso):
    """Los comprobantes ya emitidos recuperan sus carpetas desde la base de datos."""
    from pathlib import Path

    from django.conf import settings
    from django.core.management import call_command

    from factec.django import archivos

    registro = factura.emitir(encolar=False)

    # Se simula que los archivos se perdieron (o que se emitió con la versión
    # anterior, que no los guardaba).
    carpeta = Path(settings.MEDIA_ROOT) / registro.carpeta
    for archivo in carpeta.iterdir():
        archivo.unlink()
    carpeta.rmdir()
    registro.carpeta = ""
    registro.save(update_fields=["carpeta"])

    call_command("archivar_comprobantes", verbosity=0)

    registro.refresh_from_db()
    assert registro.carpeta.startswith(f"sri/comprobantes/{registro.fecha_emision:%Y/%m/%d}/")
    assert (Path(settings.MEDIA_ROOT) / registro.carpeta / archivos.NOMBRE_FIRMADO).is_file()


def test_el_comando_archiva_solo_lo_que_se_pide(documentos, factura, cliente_falso, capsys):
    from django.core.management import call_command

    factura.emitir(encolar=False)

    call_command("archivar_comprobantes", "--simular", verbosity=0)
    salida = capsys.readouterr().out

    assert "Se escribirían 1 comprobante" in salida


def test_el_comando_archiva_aunque_el_comprobante_este_mal(documentos, factura, cliente_falso,
                                                           tmp_path):
    from pathlib import Path

    from django.conf import settings
    from django.core.management import call_command

    from factec.django import archivos

    cliente_falso.estado_recepcion = "DEVUELTA"
    devuelto = factura.emitir(encolar=False)

    # Se borra la carpeta y se rehace desde la base de datos.
    carpeta = Path(settings.MEDIA_ROOT) / devuelto.carpeta
    for archivo in carpeta.iterdir():
        archivo.unlink()
    call_command("archivar_comprobantes", "--estado", "DEVUELTO", verbosity=0)

    assert (carpeta / archivos.NOMBRE_ERROR).is_file()
    assert (carpeta / archivos.NOMBRE_RESPUESTA_RECEPCION).is_file()
    assert not (carpeta / archivos.NOMBRE_AUTORIZADO).exists()


# ------------------------------ consultar y verificar desde una vista


def test_el_comprobante_se_puede_leer_desde_el_modelo(documentos, factura, cliente_falso):
    """Métodos del modelo, pensados para usarlos en una vista."""
    from decimal import Decimal

    registro = factura.emitir(encolar=False)

    leido = registro.leer()
    assert leido.numero == "001-001-000000001"
    assert leido.receptor.razon_social == "DISTRIBUIDORA ANDINA CÍA. LTDA."
    assert leido.totales.importe_total == Decimal("280.00")
    assert [detalle.descripcion for detalle in leido.detalles] == [
        "Servicio de desarrollo", "Soporte mensual",
    ]

    informe = registro.verificar()
    assert informe.ok is True
    assert informe.firma is True
    assert informe.clave_valida is True

    datos = registro.a_dict(verificar=True)
    assert datos["estado"] == "AUTORIZADO"
    assert datos["leido"]["tipo"] == "01"
    assert datos["verificacion"]["ok"] is True
    assert datos["archivos"]


def test_la_consulta_acepta_clave_pk_o_instancia(documentos, factura, cliente_falso):
    from factec.django import consulta

    registro = factura.emitir(encolar=False)

    assert consulta.leer(registro.clave_acceso).numero == "001-001-000000001"
    assert consulta.leer(registro.pk).numero == "001-001-000000001"
    assert consulta.leer(registro).numero == "001-001-000000001"
    assert consulta.datos_por_clave(registro.clave_acceso)["id"] == registro.pk
    assert consulta.datos_por_clave("0" * 49) is None
    assert consulta.archivos_de(registro.clave_acceso)


def test_se_puede_verificar_un_xml_de_un_proveedor(documentos):
    """El caso de las compras: llega un XML y se verifica antes de guardarlo."""
    from decimal import Decimal

    from factec.clave_acceso import generar_clave_acceso
    from factec.django import consulta

    # La clave se genera con el algoritmo del SRI: así la prueba comprueba de
    # verdad el dígito verificador y la coherencia con el documento.
    clave = generar_clave_acceso(
        fecha_emision="15/09/2026", tipo_comprobante="01", ruc="1790012345001",
        ambiente=1, serie="001001", secuencial="42", codigo_numerico="12345678",
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<factura id="comprobante" version="1.1.0">'
        "<infoTributaria><ambiente>1</ambiente><tipoEmision>1</tipoEmision>"
        "<razonSocial>PROVEEDOR S.A.</razonSocial><ruc>1790012345001</ruc>"
        f"<claveAcceso>{clave}</claveAcceso>"
        "<codDoc>01</codDoc><estab>001</estab><ptoEmi>001</ptoEmi>"
        "<secuencial>000000042</secuencial><dirMatriz>QUITO</dirMatriz></infoTributaria>"
        "<infoFactura><fechaEmision>15/09/2026</fechaEmision>"
        "<tipoIdentificacionComprador>04</tipoIdentificacionComprador>"
        "<razonSocialComprador>MI TIENDA</razonSocialComprador>"
        "<identificacionComprador>0703886697001</identificacionComprador>"
        "<totalSinImpuestos>100.00</totalSinImpuestos><totalDescuento>0.00</totalDescuento>"
        "<totalConImpuestos><totalImpuesto><codigo>2</codigo>"
        "<codigoPorcentaje>4</codigoPorcentaje><baseImponible>100.00</baseImponible>"
        "<tarifa>15.00</tarifa><valor>15.00</valor></totalImpuesto></totalConImpuestos>"
        "<importeTotal>115.00</importeTotal><moneda>DOLAR</moneda></infoFactura>"
        "<detalles><detalle><codigoPrincipal>P1</codigoPrincipal>"
        "<descripcion>Mercadería</descripcion><cantidad>1.000000</cantidad>"
        "<precioUnitario>100.000000</precioUnitario><descuento>0.00</descuento>"
        "<precioTotalSinImpuesto>100.00</precioTotalSinImpuesto></detalle></detalles>"
        "</factura>"
    )

    informe = consulta.verificar_xml(xml, exigir_firma=False)

    assert informe.ok is True                     # cuadra y la clave es coherente
    assert informe.emisor == "PROVEEDOR S.A."
    assert informe.receptor == "MI TIENDA"
    assert informe.importe_total == Decimal("115.00")

    # Y sus datos, listos para volcar en un modelo de compras propio.
    from factec.lectura import leer_comprobante

    datos = leer_comprobante(xml).a_dict()
    assert datos["emisor"]["ruc"] == "1790012345001"
    assert datos["emisor"]["razon_social"] == "PROVEEDOR S.A."
    assert datos["receptor"]["razon_social"] == "MI TIENDA"
    assert datos["detalles"][0]["descripcion"] == "Mercadería"
    assert datos["fecha_emision"] == "2026-09-15"


def test_verificar_en_el_sri_actualiza_el_comprobante(documentos, factura, cliente_falso):
    """La misma consulta del admin, pero desde código."""
    registro = factura.emitir(encolar=False)

    autorizacion = registro.verificar_en_el_sri()

    assert autorizacion.autorizada is True
    registro.refresh_from_db()
    assert registro.estado == "AUTORIZADO"
    assert registro.respuesta_autorizacion


# ------------------------------------------- filtros y búsquedas en todo el admin


#: Filtro de ejemplo para comprobar las rutas «mi_app.MiFiltro» de los ajustes.
class FiltroConDescripcionCorta(_admin.SimpleListFilter):
    title = "Descripción"
    parameter_name = "descripcion_corta"

    def lookups(self, request, model_admin):
        return (("si", "Descripción corta"), ("no", "Descripción larga"))

    def queryset(self, request, queryset):
        if self.value() == "si":
            return queryset.filter(descripcion__len__lte=15)
        if self.value() == "no":
            return queryset.filter(descripcion__len__gt=15)
        return queryset


def _campos_sri_fe():
    """Todos los admins del paquete registrados en este proyecto."""
    return {
        modelo: adm
        for modelo, adm in _admin.site._registry.items()
        if modelo._meta.app_label == "sri_fe"
    }


def test_todos_los_admins_tienen_filtros_y_busqueda(entorno_django):
    """El requisito: no debe quedar ningún modelo sin filtros ni búsqueda."""
    faltan = []
    for modelo, adm in sorted(_campos_sri_fe().items(), key=lambda par: par[0].__name__):
        if not adm.get_list_filter(None):
            faltan.append(f"{modelo.__name__}: sin filtros")
        if not adm.get_search_fields(None):
            faltan.append(f"{modelo.__name__}: sin búsqueda")

    assert not faltan, "\n".join(faltan)
    assert len(_campos_sri_fe()) >= 20      # el paquete trae todo lo modelado


def test_los_filtros_y_busquedas_apuntan_a_campos_que_existen(entorno_django):
    """Un nombre mal escrito no falla al arrancar: falla al abrir el listado."""
    from django.contrib.admin.utils import get_fields_from_path

    problemas = []
    for modelo, adm in _campos_sri_fe().items():
        for campo in adm.get_search_fields(None):
            try:
                get_fields_from_path(modelo, campo)
            except Exception as error:  # noqa: BLE001
                problemas.append(f"{modelo.__name__} búsqueda «{campo}»: {error}")
        for filtro in adm.get_list_filter(None):
            if isinstance(filtro, type):
                campo = getattr(filtro, "campo", None)
            else:
                campo = filtro[0] if isinstance(filtro, (list, tuple)) else filtro
            if not campo:
                continue
            try:
                get_fields_from_path(modelo, campo)
            except Exception as error:  # noqa: BLE001
                problemas.append(f"{modelo.__name__} filtro «{campo}»: {error}")
        for columna in adm.get_list_display(None):
            if not isinstance(columna, str) or hasattr(adm, columna):
                continue
            if hasattr(modelo, columna):
                continue
            try:
                get_fields_from_path(modelo, columna)
            except Exception as error:  # noqa: BLE001
                problemas.append(f"{modelo.__name__} columna «{columna}»: {error}")

    assert not problemas, "\n".join(problemas)


@pytest.mark.parametrize(
    "ruta",
    [
        "/admin/sri_fe/cliente/",
        "/admin/sri_fe/producto/",
        "/admin/sri_fe/factura/",
        "/admin/sri_fe/liquidacioncompra/",
        "/admin/sri_fe/notacredito/",
        "/admin/sri_fe/notadebito/",
        "/admin/sri_fe/guiaremision/",
        "/admin/sri_fe/retencion/",
        "/admin/sri_fe/guiadestinatario/",
        "/admin/sri_fe/retenciondocsustento/",
        "/admin/sri_fe/comprobanteemitido/",
        "/admin/sri_fe/secuencial/",
        "/admin/sri_fe/facturadetalle/",
        "/admin/sri_fe/liquidacioncompradetalle/",
        "/admin/sri_fe/notacreditodetalle/",
        "/admin/sri_fe/guiadetalle/",
        "/admin/sri_fe/notadebitomotivo/",
        "/admin/sri_fe/retencionimpuesto/",
        "/admin/sri_fe/retenciondocsustentoimpuesto/",
        "/admin/sri_fe/configuracionemisor/",
    ],
)
def test_los_listados_con_filtros_y_busqueda_responden(admin_cliente, ruta):
    """Todos los listados, con su barra de filtros y su buscador, abren bien."""
    respuesta = admin_cliente.get(ruta)

    assert respuesta.status_code == 200, respuesta.status_code
    contenido = respuesta.content.decode()
    assert 'id="searchbar"' in contenido          # buscador
    assert "changelist-filter" in contenido       # barra de filtros


def test_se_puede_filtrar_y_buscar_de_verdad(admin_cliente, factura, cliente_falso):
    factura.emitir(encolar=False)

    # Buscar por la razón social del receptor…
    respuesta = admin_cliente.get("/admin/sri_fe/factura/", {"q": "DISTRIBUIDORA"})
    assert "DISTRIBUIDORA ANDINA" in respuesta.content.decode()

    # …por su identificación, y filtrar por el estado del comprobante.
    assert "DISTRIBUIDORA ANDINA" in admin_cliente.get(
        "/admin/sri_fe/factura/", {"q": "1790012345001"}
    ).content.decode()
    assert "DISTRIBUIDORA ANDINA" in admin_cliente.get(
        "/admin/sri_fe/factura/", {"comprobante__estado": "AUTORIZADO"}
    ).content.decode()
    assert "DISTRIBUIDORA ANDINA" not in admin_cliente.get(
        "/admin/sri_fe/factura/", {"comprobante__estado": "DEVUELTO"}
    ).content.decode()


def test_los_filtros_propios_del_paquete_funcionan(admin_cliente, factura, cliente_falso,
                                                   fecha_de_referencia):
    """Rango de fechas, rango de importes y emitidos / sin emitir."""
    from factec.django import admin_filtros
    from factec.sri import fechas

    # «Hoy» es el día del SRI, así que la factura se emite con ese día: la prueba
    # mide el filtro, no la fecha del reloj de la máquina.
    factura.fecha_emision = fechas.hoy_en_ecuador()
    factura.save(update_fields=["fecha_emision"])
    factura.emitir(encolar=False)

    def aparece(parametros: dict) -> bool:
        respuesta = admin_cliente.get("/admin/sri_fe/factura/", parametros)
        assert respuesta.status_code == 200
        return "DISTRIBUIDORA ANDINA" in respuesta.content.decode()

    # Emitidos / sin emitir
    assert aparece({"emitido_comprobante": "si"})
    assert not aparece({"emitido_comprobante": "no"})

    # Rango de importes (la factura suma 280)
    assert aparece({"importe_comprobante_importe_total": "100_500"})
    assert not aparece({"importe_comprobante_importe_total": "0_10"})

    # Fechas: hoy, y dos rangos que no lo contienen
    assert aparece({"rango_fecha_emision": "hoy"})
    assert not aparece({"rango_fecha_emision": "sin_fecha"})
    assert not aparece({"rango_fecha_emision": "mes_pasado"})

    # Y las clases de filtro resuelven sus consultas
    assert admin_filtros.filtro_por_fecha("creado").parameter_name == "rango_creado"
    assert admin_filtros.filtro_emitido().parameter_name == "emitido_comprobante"


def test_se_puede_buscar_una_linea_en_todos_los_comprobantes(admin_cliente, factura,
                                                            cliente_falso):
    """El listado de líneas responde «¿en qué comprobantes vendí esto?»."""
    factura.emitir(encolar=False)

    respuesta = admin_cliente.get("/admin/sri_fe/facturadetalle/", {"q": "Soporte mensual"})
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "Soporte mensual" in contenido
    # Enlace al comprobante del que es la línea
    assert f"/admin/sri_fe/factura/{factura.pk}/change/" in contenido

    # Buscar por el producto: sale la línea que lo lleva…
    por_producto = admin_cliente.get(
        "/admin/sri_fe/facturadetalle/", {"q": "SRV001"}
    ).content.decode()
    assert "Servicio de desarrollo" in por_producto
    assert "Soporte mensual" not in por_producto

    # …y buscar por el cliente: salen todas las líneas de sus comprobantes.
    por_cliente = admin_cliente.get(
        "/admin/sri_fe/facturadetalle/", {"q": "DISTRIBUIDORA"}
    ).content.decode()
    assert "Servicio de desarrollo" in por_cliente
    assert "Soporte mensual" in por_cliente


def test_los_filtros_y_busquedas_se_configuran_desde_los_ajustes(entorno_django):
    """Dinámico: la tienda añade filtros, búsquedas y columnas sin tocar el paquete."""
    from django.test import override_settings

    from factec.django import admin as admin_paquete  # noqa: F401  (registra los admins)
    from factec.django import admin_filtros, documentos

    ajustes = {
        "CLAVE_CIFRADO": "x" * 44,
        "ADMIN": {
            "factura": {
                "filtros": ["forma_pago", admin_filtros.filtro_por_fecha("creado", "Alta")],
                "busqueda": ["observaciones", "receptor__email"],
                "columnas": ["observaciones"],
                "solo_lectura": ["observaciones"],
            },
            "producto": {
                "solo": True,                       # reemplaza lo que trae el paquete
                "filtros": ["activo"],
                "busqueda": ["descripcion"],
            },
        },
    }

    with override_settings(FACTURACION_ELECTRONICA=ajustes):
        admin_factura = _admin.site._registry[documentos.Factura]
        filtros = admin_factura.get_list_filter(None)
        busqueda = admin_factura.get_search_fields(None)
        columnas = admin_factura.get_list_display(None)

        assert "forma_pago" in filtros
        assert any(getattr(f, "parameter_name", "") == "rango_creado" for f in filtros)
        assert "receptor__email" in busqueda and "observaciones" in busqueda
        assert "comprobante__clave_acceso" in busqueda      # lo del paquete se conserva
        assert "observaciones" in columnas
        assert "observaciones" in admin_factura.get_readonly_fields(None)

        # ``solo`` deja únicamente lo indicado
        admin_producto = _admin.site._registry[documentos.Producto]
        assert admin_producto.get_list_filter(None) == ["activo"]
        assert admin_producto.get_search_fields(None) == ["descripcion"]


def test_los_ajustes_admiten_rutas_de_texto(entorno_django):
    """Se puede indicar el filtro con su ruta («modulo.Clase») en los ajustes."""
    from django.test import override_settings

    from factec.django import admin_filtros, documentos

    ajustes = {
        "CLAVE_CIFRADO": "x" * 44,
        "ADMIN": {"producto": {"filtros": ["test_documentos.FiltroConDescripcionCorta"]}},
    }

    with override_settings(FACTURACION_ELECTRONICA=ajustes):
        admin_producto = _admin.site._registry[documentos.Producto]

        assert FiltroConDescripcionCorta in admin_producto.get_list_filter(None)


def test_una_ruta_mala_en_los_ajustes_no_rompe_el_admin(entorno_django, caplog):
    """Si la ruta no existe, se avisa y se sigue con los filtros del paquete."""
    from django.test import override_settings

    from factec.django import admin_filtros, documentos

    ajustes = {
        "CLAVE_CIFRADO": "x" * 44,
        "ADMIN": {"producto": {"filtros": ["no.existe.EsteFiltro"]}},
    }

    with override_settings(FACTURACION_ELECTRONICA=ajustes):
        admin_producto = _admin.site._registry[documentos.Producto]
        filtros = admin_producto.get_list_filter(None)

    assert "activo" in filtros          # siguen los del paquete


# --------------------- revisión previa: certificado y fecha de emisión


def _con_certificado_de_otro_ruc():
    """Deja la configuración activa con un certificado de otro contribuyente."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from factec.django import conf

    configuracion = conf.configuracion_activa()
    configuracion.certificado = SimpleUploadedFile("otro.p12", _crear_p12("1790012345001"))
    configuracion.establecer_clave(CLAVE_CERTIFICADO)
    configuracion.save()
    conf.limpiar_cache()
    return configuracion


def test_no_se_emite_con_un_certificado_de_otro_contribuyente(
    documentos, factura, cliente_falso, configuracion
):
    """Antes de firmar se revisa: el certificado ajeno no llega ni a firmarse.

    El SRI rechaza los comprobantes firmados por otro contribuyente, así que no se
    envía nada, no se gasta secuencial y el motivo se explica en el error.
    """
    from factec.django import models
    from factec.excepciones import ErrorRevision

    _con_certificado_de_otro_ruc()

    with pytest.raises(ErrorRevision) as error:
        factura.emitir(encolar=False)

    assert "no coincide con el del emisor" in str(error.value)
    assert error.value.informe.puede_emitir is False
    assert cliente_falso.llamadas == []                      # no se envió nada
    assert models.ComprobanteEmitido.objects.count() == 0    # ni se consumió secuencial


def test_la_revision_se_puede_consultar_sin_emitir(documentos, factura, configuracion):
    """Sirve para avisar en una vista: se revisa y se decide si emitir."""
    from factec.django import facturacion, services

    informe = services.revisar_comprobante(facturacion.comprobante_de(factura))

    assert informe.puede_emitir is True
    assert informe.certificado.ruc == RUC
    assert informe.a_dict()["certificado"]["ok"] is True
    assert informe.fecha_de_hoy is not None


def test_se_puede_revisar_un_comprobante_guardado(documentos, factura, cliente_falso):
    from factec.django import consulta

    registro = factura.emitir(encolar=False)

    informe = registro.revisar()
    assert informe.puede_emitir is True
    assert informe.clave_acceso == registro.clave_acceso
    assert informe.totales_cuadran is True
    assert consulta.revisar(registro.pk).a_dict()["puede_emitir"] is True


def test_la_revision_avisa_cuando_la_firma_esta_por_vencer(
    documentos, factura, cliente_falso, configuracion
):
    """Aviso, no error: todavía se puede emitir, pero hay que renovar la firma."""
    from factec.django import services

    registro = factura.emitir(encolar=False)

    informe = services.revisar(registro, dias_aviso=365 * 10)

    assert informe.puede_emitir is True
    assert any("vence" in aviso for aviso in informe.avisos)


def test_un_comprobante_sin_enviar_de_otro_dia_se_refecha_al_emitir(
    documentos, factura, cliente_falso, configuracion
):
    """El comprobante se firma **el día en que se emite**, no el día del borrador.

    Es el caso real: el certificado estuvo vencido unos días y el borrador se quedó
    con la fecha vieja; al arreglarlo, se emite con la fecha de hoy y sin duplicar
    el comprobante ni gastar un secuencial nuevo.
    """
    from datetime import timedelta

    from factec.django import facturacion, services
    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    hace_dias = hoy - timedelta(days=3)

    factura.secuencial = int(services.siguiente_secuencial())
    factura.save()
    registro = services.registrar(
        facturacion.comprobante_de(factura, fecha_emision=hace_dias)
    )
    registro.vincular(factura)
    clave_vieja = registro.clave_acceso
    assert registro.fecha_desactualizada is True
    assert f"<fechaEmision>{hace_dias:%d/%m/%Y}</fechaEmision>" in registro.xml_sin_firma

    emitido = factura.emitir(encolar=False)
    emitido.refresh_from_db()

    assert emitido.pk == registro.pk                                   # no se duplica
    assert emitido.fecha_emision == hoy
    assert emitido.clave_acceso != clave_vieja                         # la clave lleva la fecha
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in emitido.xml_sin_firma
    assert emitido.autorizado
    assert emitido.fecha_desactualizada is False


def test_actualizar_la_fecha_a_mano_deja_el_comprobante_como_borrador(
    documentos, factura, configuracion
):
    from datetime import timedelta

    from factec.django import facturacion, models, services
    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    registro = services.registrar(
        facturacion.comprobante_de(factura, fecha_emision=hoy - timedelta(days=2))
    )
    services.firmar(registro, revisar=False)
    assert registro.xml_firmado

    services.actualizar_fecha(registro)

    assert registro.fecha_emision == hoy
    assert registro.estado == models.EstadoComprobante.BORRADOR
    assert registro.xml_firmado == ""                    # la firma vieja ya no vale
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in registro.xml_sin_firma
    assert registro.fecha_desactualizada is False
    assert registro.archivos()                           # el XML queda archivado


def test_no_se_puede_cambiar_la_fecha_de_un_comprobante_ya_enviado(
    documentos, factura, cliente_falso, configuracion
):
    """Una vez en el SRI, la clave está registrada allí: no se puede cambiar."""
    from factec.django import services
    from factec.excepciones import ErrorFacturacion

    registro = factura.emitir(encolar=False)
    assert registro.intentos == 1

    with pytest.raises(ErrorFacturacion, match="ya se envió"):
        services.actualizar_fecha(registro)


def test_se_puede_respetar_la_fecha_del_documento(documentos, factura, cliente_falso, configuracion):
    """Con ``FECHA_EMISION_AL_EMITIR = False`` manda la fecha del documento."""
    from django.test import override_settings

    from factec.django import conf

    conf.limpiar_cache()
    ajustes = {"CLAVE_CIFRADO": "x" * 44, "FECHA_EMISION_AL_EMITIR": False}

    with override_settings(FACTURACION_ELECTRONICA=ajustes):
        registro = factura.emitir(encolar=False)

    assert registro.fecha_emision == HOY
    assert f"<fechaEmision>{HOY:%d/%m/%Y}</fechaEmision>" in registro.xml_sin_firma


def test_el_admin_revisa_antes_de_emitir(documentos, factura, cliente_falso, admin_cliente,
                                        configuracion):
    """La acción del admin informa del motivo en lugar de emitir a ciegas."""
    from factec.django import conf, models

    _con_certificado_de_otro_ruc()
    registro = models.ComprobanteEmitido.objects.create(
        clave_acceso="0" * 49, secuencial="1", fecha_emision=HOY,
        xml_sin_firma=conf.configuracion_activa() and "<factura/>",
    )

    respuesta = admin_cliente.post(
        "/admin/sri_fe/comprobanteemitido/",
        {"action": "accion_revisar", "_selected_action": [registro.pk]},
        follow=True,
    )
    contenido = respuesta.content.decode()

    assert "El RUC del certificado" in contenido
    assert registro.estado == models.EstadoComprobante.BORRADOR   # no se emitió


def test_el_admin_puede_refechar_los_comprobantes(documentos, factura, cliente_falso,
                                                 admin_cliente, configuracion):
    from datetime import timedelta

    from factec.django import facturacion, models, services
    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    registro = services.registrar(
        facturacion.comprobante_de(factura, fecha_emision=hoy - timedelta(days=4))
    )

    respuesta = admin_cliente.post(
        "/admin/sri_fe/comprobanteemitido/",
        {"action": "accion_actualizar_fecha", "_selected_action": [registro.pk]},
        follow=True,
    )
    registro.refresh_from_db()

    assert "cambiada al" in respuesta.content.decode()
    assert registro.fecha_emision == hoy
    assert registro.estado == models.EstadoComprobante.BORRADOR


def test_el_listado_tiene_el_filtro_de_comprobantes_de_otro_dia(
    documentos, factura, cliente_falso, admin_cliente, configuracion
):
    from factec.django import admin_filtros, models

    assert admin_filtros.FiltroFechaDesactualizada in (
        _admin.site._registry[models.ComprobanteEmitido].get_list_filter(None)
    )

    registro = factura.emitir(encolar=False)          # ya enviado: no está «sin enviar»
    respuesta = admin_cliente.get(
        "/admin/sri_fe/comprobanteemitido/", {"fecha_desactualizada": "hoy"}
    )
    assert respuesta.status_code == 200
    assert registro.clave_acceso not in respuesta.content.decode()


def test_el_comando_revisar_firma_cuenta_el_estado_de_la_firma(configuracion):
    from io import StringIO

    from django.core.management import call_command

    salida = StringIO()
    call_command("revisar_firma", stdout=salida)

    contenido = salida.getvalue()
    assert "correcto" in contenido
    assert "Válido hasta" in contenido


def test_el_comando_revisar_firma_falla_y_avisa_por_correo(configuracion):
    """Programado (cron o Celery Beat) avisa antes de que falle una emisión."""
    from io import StringIO

    from django.core import mail
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.test import override_settings

    import django

    _con_certificado_de_otro_ruc()
    ajustes = {"CLAVE_CIFRADO": "x" * 44, "CORREOS_AVISO": ["avisos@mitienda.ec"]}
    salida = StringIO()
    backend = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}
    # Django 6.1 sustituye EMAIL_BACKEND por MAILERS.
    correo = {"MAILERS": {"default": backend}} if django.VERSION >= (6, 1) else {
        "EMAIL_BACKEND": backend["BACKEND"]
    }

    with override_settings(FACTURACION_ELECTRONICA=ajustes, **correo):
        with pytest.raises(CommandError, match="firma electrónica"):
            call_command("revisar_firma", correo=True, stdout=salida)

        assert "El RUC del certificado" in salida.getvalue()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["avisos@mitienda.ec"]
        assert "[SRI]" in mail.outbox[0].subject
        assert "no coincide con el del emisor" in mail.outbox[0].body


def test_la_tarea_periodica_revisa_la_firma(configuracion):
    from factec.django import tasks

    informe = tasks.revisar_certificado(avisar_por_correo=False)

    assert informe["revisados"] == 1
    assert informe["ok"] is True
    assert informe["certificados"][0]["dias_restantes"] > 0


def test_el_planificador_trae_las_tareas_periodicas():
    from factec.django import conf

    plan = conf.planificador()
    plan_pronto = conf.planificador(a_las=6, minuto=30, cada_pendientes=120)

    assert set(plan) == {
        "sri_fe.revisar_certificado",
        "sri_fe.reintentar_pendientes",
        "sri_fe.comprobar_actualizacion",
    }
    assert plan["sri_fe.revisar_certificado"]["task"] == "sri_fe.revisar_certificado"

    # La revisión de la firma va a una hora concreta (7:00 por omisión) y el
    # reintento cada 10 minutos; sin Celery instalado, cada 24 horas.
    if "celery" in sys.modules or importlib.util.find_spec("celery"):
        from celery.schedules import crontab

        assert plan["sri_fe.revisar_certificado"]["schedule"] == crontab(minute=0, hour=7)
        assert plan_pronto["sri_fe.revisar_certificado"]["schedule"] == crontab(
            minute=30, hour=6
        )
    else:
        assert plan["sri_fe.revisar_certificado"]["schedule"] == 86400.0

    assert plan["sri_fe.reintentar_pendientes"]["schedule"] == 600.0
    assert plan_pronto["sri_fe.reintentar_pendientes"]["schedule"] == 120

    # La comprobación de versión es semanal (los lunes, a la misma hora).
    if "celery" in sys.modules or importlib.util.find_spec("celery"):
        from celery.schedules import crontab

        assert plan["sri_fe.comprobar_actualizacion"]["schedule"] == crontab(
            minute=0, hour=7, day_of_week=1
        )
    else:
        assert plan["sri_fe.comprobar_actualizacion"]["schedule"] == 7 * 86400.0


def test_el_admin_avisa_cuando_la_firma_impide_emitir(
    documentos, factura, cliente_falso, admin_cliente, configuracion
):
    """El aviso aparece en los listados, que es donde se trabaja."""
    _con_certificado_de_otro_ruc()

    contenido = admin_cliente.get("/admin/sri_fe/comprobanteemitido/").content.decode()

    assert "El RUC del certificado" in contenido


# ------------------------------- producto o servicio y sus detalles adicionales


def test_el_producto_se_clasifica_como_producto_o_servicio(documentos):
    """Se elige al crearlo, y la unidad de medida se propone según el tipo."""
    from factec.django.documentos import Producto, TipoProducto

    servicio = Producto.objects.create(
        tipo=TipoProducto.SERVICIO, codigo_principal="SER1",
        descripcion="Asesoría mensual", precio_unitario=Decimal("200"),
    )
    bien = Producto.objects.create(
        tipo=TipoProducto.PRODUCTO, codigo_principal="BIE1",
        descripcion="Caja de tornillos", precio_unitario=Decimal("10"),
    )
    con_unidad = Producto.objects.create(
        tipo=TipoProducto.SERVICIO, codigo_principal="SER2", descripcion="Horas",
        unidad_medida="hora", precio_unitario=Decimal("30"),
    )

    assert servicio.es_servicio is True
    assert servicio.es_producto is False
    assert servicio.unidad_medida == "SERVICIO"          # propuesta
    assert bien.es_producto is True
    assert bien.unidad_medida == "UNIDAD"                # propuesta
    assert con_unidad.unidad_medida == "hora"            # la escrita manda


def test_los_detalles_adicionales_del_producto_van_a_la_linea(
    documentos, configuracion, cliente, cliente_falso, esquemas
):
    """Se configuran una vez en el producto y viajan en cada línea que lo use."""
    from factec.django.documentos import Producto, TipoProducto

    producto = Producto.objects.create(
        tipo=TipoProducto.PRODUCTO, codigo_principal="TOR1",
        descripcion="Tornillo 3/8", precio_unitario=Decimal("1.50"),
        codigo_porcentaje_iva="4",
        datos_adicionales="MARCA=ACME; GARANTIA=12 MESES",
    )

    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    linea = factura.detalles.create(producto=producto, cantidad=2)

    assert linea.datos_adicionales == "MARCA=ACME; GARANTIA=12 MESES"
    assert linea.campos_adicionales() == {"MARCA": "ACME", "GARANTIA": "12 MESES"}
    assert linea.unidad_medida == "UNIDAD"                # también la unidad

    registro = factura.emitir(encolar=False)

    assert '<detAdicional nombre="MARCA" valor="ACME"' in registro.xml_sin_firma
    assert '<detAdicional nombre="GARANTIA" valor="12 MESES"' in registro.xml_sin_firma
    _valida(esquemas, "factura_V1.1.0.xsd", registro.xml_sin_firma)


def test_la_linea_puede_cambiar_los_detalles_del_producto(
    documentos, configuracion, cliente, cliente_falso
):
    """Lo escrito en la línea tiene prioridad: sirve para el lote de esa venta."""
    from factec.django.documentos import Producto

    producto = Producto.objects.create(
        codigo_principal="TOR2", descripcion="Tornillo 1/2",
        precio_unitario=Decimal("2"), datos_adicionales="MARCA=ACME",
    )

    factura = documentos.Factura.objects.create(receptor=cliente, fecha_emision=HOY)
    linea = factura.detalles.create(
        producto=producto, cantidad=1, datos_adicionales="MARCA=OTRA; LOTE=7",
    )

    registro = factura.emitir(encolar=False)

    assert linea.datos_adicionales == "MARCA=OTRA; LOTE=7"
    assert '<detAdicional nombre="MARCA" valor="OTRA"' in registro.xml_sin_firma
    assert "ACME" not in registro.xml_sin_firma


def test_los_detalles_adicionales_del_producto_se_validan(documentos):
    from django.core.exceptions import ValidationError

    from factec.django.documentos import Producto

    producto = Producto(
        codigo_principal="X1", descripcion="X", precio_unitario=Decimal("1"),
        datos_adicionales="A=1; B=2; C=3; D=4",
    )
    with pytest.raises(ValidationError) as error:
        producto.full_clean()
    assert "datos_adicionales" in error.value.message_dict

    producto.datos_adicionales = "A=1; B=2; C=3"
    producto.full_clean()


def test_el_admin_ofrece_el_tipo_y_los_detalles_del_producto(admin_cliente, producto):
    """En el formulario se elige si es producto o servicio y sus campos adicionales."""
    from django.contrib import admin as admin_django

    from factec.django import documentos

    respuesta = admin_cliente.get(f"/admin/sri_fe/producto/{producto.pk}/change/")
    contenido = respuesta.content.decode()

    assert respuesta.status_code == 200
    assert "¿qué es?" in contenido
    assert "servicio" in contenido                      # la opción de servicio
    assert 'name="datos_adicionales"' in contenido      # sus detalles adicionales
    assert "Detalles adicionales" in contenido          # con su explicación

    admin_producto = admin_django.site._registry[documentos.Producto]
    assert "tipo" in admin_producto.get_list_display(None)
    assert "tipo" in admin_producto.get_list_filter(None)
    assert "datos_adicionales" in admin_producto.get_search_fields(None)
    assert "Un producto (bien) o un servicio" in str(admin_producto.fieldsets)

    listado = admin_cliente.get("/admin/sri_fe/producto/").content.decode()
    assert "Producto (bien)" in listado                 # la columna «qué es»


def test_el_endpoint_del_producto_devuelve_los_detalles_adicionales(admin_cliente, producto):
    """Es lo que rellena la línea de la factura al elegir el producto."""
    from factec.django.documentos import Producto

    producto.datos_adicionales = "MARCA=ACME"
    producto.save()

    respuesta = admin_cliente.get(f"/admin/sri_fe/producto/{producto.pk}/datos/")
    datos = respuesta.json()

    assert respuesta.status_code == 200
    assert datos["datos_adicionales"] == "MARCA=ACME"
    assert datos["unidad_medida"] == producto.unidad_medida
    assert set(datos) == {
        "descripcion", "codigo_principal", "codigo_auxiliar", "unidad_medida",
        "precio_unitario", "codigo_porcentaje_iva", "datos_adicionales",
    }


def test_la_migracion_clasifica_los_productos_que_ya_existian(documentos):
    """Lo guardado antes de «tipo» se clasifica por su unidad de medida."""
    import importlib

    from django.apps import apps as django_apps

    from factec.django.documentos import Producto, TipoProducto

    migracion = importlib.import_module(
        "factec.django.migrations.0010_tipo_y_detalles_adicionales_del_producto"
    )
    por_horas = Producto.objects.create(
        codigo_principal="H1", descripcion="Asesoría", unidad_medida="hora",
        precio_unitario=Decimal("20"),
    )
    sin_unidad = Producto.objects.create(
        codigo_principal="V1", descripcion="Genérico", precio_unitario=Decimal("1"),
    )
    # Se simula el estado anterior: sin tipo y sin unidad de medida.
    Producto.objects.filter(pk=por_horas.pk).update(tipo=TipoProducto.PRODUCTO, unidad_medida="hora")
    Producto.objects.filter(pk=sin_unidad.pk).update(unidad_medida="")

    migracion.clasificar_lo_que_ya_existe(django_apps, None)

    por_horas.refresh_from_db()
    sin_unidad.refresh_from_db()
    assert por_horas.tipo == TipoProducto.SERVICIO.value
    assert por_horas.es_servicio is True
    assert sin_unidad.unidad_medida == "UNIDAD"
    assert sin_unidad.tipo == TipoProducto.PRODUCTO.value


# ------------------------- comandos: servicios de Celery en Linux (systemd)


def _proyecto_falso(tmp_path):
    """Un proyecto Django mínimo: manage.py y el módulo de ajustes."""
    proyecto = tmp_path / "proyecto"
    (proyecto / "mi_proyecto").mkdir(parents=True, exist_ok=True)
    (proyecto / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (proyecto / "mi_proyecto" / "settings.py").write_text("", encoding="utf-8")
    return proyecto


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_el_comando_servicios_celery_genera_las_unidades(entorno_django, tmp_path):
    """Crea las unidades del worker, del beat y de Flower con las rutas del proyecto."""
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    destino = tmp_path / "unidades"

    call_command(
        "servicios_celery", "--solo-archivos", "--destino", str(destino),
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        "--flower-auth", "juan:secreta", "--concurrencia", "2", stdout=StringIO(),
    )

    assert sorted(p.name for p in destino.iterdir()) == [
        "proyecto-celery-beat.service",
        "proyecto-celery-worker.service",
        "proyecto-flower.service",
    ]

    worker = (destino / "proyecto-celery-worker.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={proyecto}" in worker
    assert 'Environment="DJANGO_SETTINGS_MODULE=mi_proyecto.settings"' in worker
    assert "worker -l info -c 2 -E" in worker          # -E: Flower necesita los eventos
    assert "Restart=always" in worker

    beat = (destino / "proyecto-celery-beat.service").read_text(encoding="utf-8")
    assert "celerybeat-schedule" in beat
    assert "StateDirectory=proyecto" in beat

    flower = (destino / "proyecto-flower.service").read_text(encoding="utf-8")
    assert "flower --address=127.0.0.1 --port=5555 --basic_auth=juan:secreta" in flower
    assert 'Environment="FLOWER_UNAUTHENTICATED_API=1"' in flower


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_el_comando_servicios_celery_admite_el_panel_aparte(entorno_django, tmp_path):
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    destino = tmp_path / "unidades"

    call_command(
        "servicios_celery", "--solo-archivos", "--destino", str(destino),
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        "--sin-flower", "--puerto", "9000", stdout=StringIO(),
    )

    assert sorted(p.name for p in destino.iterdir()) == [
        "proyecto-celery-beat.service",
        "proyecto-celery-worker.service",
    ]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_el_comando_servicios_celery_con_dry_run_no_escribe_nada(entorno_django, tmp_path):
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    destino = tmp_path / "unidades"
    salida = StringIO()

    call_command(
        "servicios_celery", "--dry-run", "--destino", str(destino),
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        stdout=salida,
    )

    assert not destino.exists()
    assert "systemctl enable --now" in salida.getvalue()
    assert "Nada se ha tocado" in salida.getvalue()


def test_el_comando_servicios_celery_dice_donde_esta_el_script(entorno_django):
    from io import StringIO
    from pathlib import Path

    from django.core.management import call_command

    salida = StringIO()
    call_command("servicios_celery", "--ruta", stdout=salida)

    ruta = Path(salida.getvalue().strip())
    assert ruta.exists()
    assert ruta.name == "instalar_servicios_celery.sh"
    assert "instalar_servicios_celery.sh" in ruta.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_el_comando_servicios_celery_enseña_los_comandos_del_proyecto(entorno_django, tmp_path):
    """Los comandos exactos, con el módulo y el entorno virtual ya sustituidos."""
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    salida = StringIO()

    call_command(
        "servicios_celery", "--comandos", "--proyecto-dir", str(proyecto),
        "--venv", sys.prefix, "--modulo", "mi_proyecto", "--concurrencia", "2",
        stdout=salida,
    )

    texto = salida.getvalue()
    assert "Comandos para proyecto" in texto
    assert f"{sys.prefix}/bin/celery -A mi_proyecto worker -l info -c 2" in texto
    assert f"{sys.prefix}/bin/celery -A mi_proyecto beat -l info" in texto
    assert f"{sys.prefix}/bin/celery -A mi_proyecto flower" in texto
    assert f"{sys.prefix}/bin/python manage.py servicios_celery" in texto
    assert "mi_proyecto" in texto and "celery del entorno virtual" in texto


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_el_comando_servicios_celery_copia_los_modelos(entorno_django, tmp_path):
    """Los modelos .service para editar a mano, con el nombre del proyecto."""
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    destino = tmp_path / "deploy" / "systemd"
    salida = StringIO()

    call_command(
        "servicios_celery", "--plantillas", "--destino", str(destino),
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        stdout=salida,
    )

    archivos = sorted(p.name for p in destino.iterdir())
    assert archivos == [
        "env.ejemplo",
        "proyecto-celery-beat.service",
        "proyecto-celery-worker.service",
        "proyecto-flower.service",
    ]

    worker = (destino / "proyecto-celery-worker.service").read_text(encoding="utf-8")
    # El módulo y el nombre ya están puestos; solo quedan los del servidor.
    assert "DJANGO_SETTINGS_MODULE=mi_proyecto.settings" in worker
    assert "celery -A mi_proyecto worker" in worker
    for marcador in ("__USUARIO__", "__GRUPO__", "__PROYECTO__", "__VENV__"):
        assert marcador in worker
    assert "__MODULO__" not in worker

    flower = (destino / "proyecto-flower.service").read_text(encoding="utf-8")
    assert "flower --address=127.0.0.1 --port=5555" in flower

    assert "Quedan cuatro marcadores" in salida.getvalue()
    assert "systemctl enable --now proyecto-celery-worker" in salida.getvalue()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_servicios_celery_puede_enlazarlos_desde_el_proyecto(entorno_django, tmp_path):
    """--enlazar deja las unidades en el proyecto y las habilita por su ruta."""
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    salida = StringIO()

    call_command(
        "servicios_celery", "--enlazar", "--dry-run",
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        stdout=salida,
    )

    texto = salida.getvalue()
    # Las unidades se quedan en el proyecto…
    assert f"Destino  : {proyecto}/deploy/systemd" in texto
    # …y systemd las habilita por su ruta absoluta (así crea el enlace y el arranque).
    for servicio in ("celery-worker", "celery-beat", "flower"):
        assert (
            f"systemctl enable --now {proyecto}/deploy/systemd/proyecto-{servicio}.service"
            in texto
        )
    assert "el archivo no se copia" in texto
    # Nada de copiar a /etc.
    assert "cp " not in texto.split("Comandos que ejecutaría")[1]


def test_el_filtro_de_fechas_usa_el_dia_del_sri_y_no_el_del_servidor(
    admin_cliente, factura, cliente_falso, fecha_de_referencia, monkeypatch
):
    """El reloj del servidor va un día por delante desde las 19:00 de Ecuador.

    El filtro «Hoy» debe mirar el mismo día con el que se emite y se valida (el
    del SRI) y no ``timezone.localdate()``: si no, con ``TIME_ZONE=UTC`` la lista
    sale vacía justo cuando más facturas se emiten.
    """
    from datetime import date

    from factec.sri import fechas

    elegido = date(2020, 1, 1)              # muy lejos del reloj de la máquina
    monkeypatch.setattr(fechas, "hoy_en_ecuador", lambda momento=None: elegido)

    factura.fecha_emision = elegido
    factura.save(update_fields=["fecha_emision"])
    factura.emitir(encolar=False)

    respuesta = admin_cliente.get("/admin/sri_fe/factura/", {"rango_fecha_emision": "hoy"})
    assert respuesta.status_code == 200
    assert "DISTRIBUIDORA ANDINA" in respuesta.content.decode()


def test_el_documento_queda_con_la_fecha_del_comprobante_emitido(
    documentos, factura, cliente_falso, fecha_de_referencia
):
    """El borrador puede ser de ayer; el documento debe enseñar el día firmado."""
    from factec.django import documentos as mod_documentos
    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    assert factura.fecha_emision == HOY != hoy

    registro = factura.emitir(encolar=False)

    assert registro.fecha_emision == hoy
    guardada = mod_documentos.Factura.objects.get(pk=factura.pk)
    assert guardada.fecha_emision == hoy
    assert f"<fechaEmision>{hoy:%d/%m/%Y}</fechaEmision>" in registro.xml_sin_firma


def test_al_refechar_un_comprobante_el_documento_cambia_con_el(documentos, factura,
                                                              configuracion):
    """Refechar el registro tiene que refechar también la factura."""
    from datetime import timedelta

    from factec.django import documentos as mod_documentos
    from factec.django import facturacion, services
    from factec.sri import fechas

    hoy = fechas.hoy_en_ecuador()
    vieja = hoy - timedelta(days=3)
    factura.fecha_emision = vieja
    factura.save(update_fields=["fecha_emision"])

    registro = services.registrar(facturacion.comprobante_de(factura, fecha_emision=vieja),
                                  objeto=factura)
    services.actualizar_fecha(registro)

    assert registro.fecha_emision == hoy
    guardada = mod_documentos.Factura.objects.get(pk=factura.pk)
    assert guardada.fecha_emision == hoy


def test_la_fecha_por_omision_es_la_del_sri(entorno_django):
    """El borrador nace con el día del SRI, no con el del servidor.

    Con ``date.today()`` (el reloj del servidor) una factura creada a las 20:00 de
    Ecuador en un servidor UTC nacería con la fecha de mañana.
    """
    from datetime import date

    from factec.django import documentos as mod_documentos
    from factec.sri import fechas

    campo = mod_documentos.Factura._meta.get_field("fecha_emision")
    assert campo.default is not date.today
    assert campo.get_default() == fechas.hoy_en_ecuador()


def test_la_carpeta_del_comprobante_sin_fecha_usa_el_dia_del_sri(
    entorno_django, fecha_de_referencia, monkeypatch
):
    """Un registro sin fecha se archiva con el día del SRI, no con el del servidor."""
    from datetime import date

    from factec.django import archivos
    from factec.sri import fechas

    monkeypatch.setattr(fechas, "hoy_en_ecuador", lambda momento=None: date(2020, 3, 4))

    class SinFecha:
        estab = "001"
        pto_emi = "001"
        secuencial = "1"
        clave_acceso = ""

    assert "/2020/03/04/" in archivos.carpeta_de(SinFecha())


@pytest.mark.skipif(sys.platform.startswith("win"), reason="los servicios son de Linux")
def test_servicios_celery_enlaza_aunque_la_carpeta_no_exista(entorno_django, tmp_path):
    """Si la carpeta de destino no existe, el script no puede morir por ``set -e``.

    Al enlazar, el script mira en qué sistema de archivos está la carpeta (para
    avisar si systemd no podrá leerla al arrancar). Si la carpeta todavía no existe,
    ``df`` falla: con ``set -e`` eso se llevaba por delante el script entero, y solo
    se veía en Linux.
    """
    from io import StringIO

    from django.core.management import call_command

    proyecto = _proyecto_falso(tmp_path)
    destino = tmp_path / "todavia" / "no" / "existe"
    salida = StringIO()

    call_command(
        "servicios_celery", "--enlazar", "--dry-run", "--destino", str(destino),
        "--proyecto-dir", str(proyecto), "--venv", sys.prefix, "--modulo", "mi_proyecto",
        stdout=salida,
    )

    assert f"Destino  : {destino}" in salida.getvalue()
    assert "systemctl enable --now" in salida.getvalue()


def test_la_consulta_no_pregunta_por_un_pk_imposible(documentos, factura, cliente_falso):
    """Una clave de acceso de 49 dígitos no cabe en un ``pk`` de SQLite.

    Preguntar por ella como clave primaria revienta en Python 3.9 y 3.10 con
    ``OverflowError: Python int too large to convert to SQLite INTEGER``; en las
    versiones nuevas simplemente no encuentra nada. Se comprueba que el número no
    llegue a la consulta.
    """
    from factec.django import consulta

    registro = factura.emitir(encolar=False)

    encontrado = consulta.leer(registro.clave_acceso)
    assert encontrado.numero == "001-001-000000001"

    # Y sigue aceptando el pk y la instancia.
    assert consulta.leer(registro.pk).clave_acceso == registro.clave_acceso
    assert consulta.leer(registro).clave_acceso == registro.clave_acceso
