"""Pruebas de que los modelos del paquete se pueden **extender** para añadir campos.

Un proyecto puede heredar de un modelo del paquete (una marca al producto, una
sucursal a la factura, el chofer a la guía…) y Django le crea una tabla hija con
los campos nuevos, sin tocar el paquete. Aquí se comprueba que, al hacerlo, todo
lo demás sigue funcionando:

* el comprobante se emite con **su** adaptador (no con el de factura, que es el de
  por omisión) porque el paquete lo elige por el tipo del modelo;
* el XML sigue validando contra el esquema oficial del SRI;
* el admin se puede ampliar heredando del del paquete, sin chocar con él.

Los modelos viven en ``apps_de_extension``, el "proyecto de mentira" que hereda
del paquete (ver ``conftest``). Se importan dentro de las pruebas porque Django no
está configurado hasta que corre la fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

HOY = date(2026, 10, 14)


def _modelos() -> Any:
    """Los modelos del proyecto de mentira que extiende el paquete."""
    from apps_de_extension import models

    return models


@pytest.fixture
def extension(entorno_django):
    """Deja vacías las tablas del paquete que usan los modelos extendidos."""
    from factec.django import documentos

    def vaciar() -> None:
        documentos.GuiaRemision.objects.all().delete()   # arrastra a la guía hija
        documentos.Factura.objects.all().delete()        # arrastra a la factura hija
        documentos.Producto.objects.all().delete()
        documentos.Cliente.objects.all().delete()        # tras las facturas (PROTECT)

    vaciar()
    yield
    vaciar()


def test_el_modelo_extendido_es_el_del_paquete_con_sus_campos(extension):
    """Heredar de un modelo del paquete añade campos sin perder nada de él."""
    from factec.django import documentos

    modelos = _modelos()
    cliente = modelos.ClienteExtendido.objects.create(
        razon_social="CLIENTE EXTENDIDO", identificacion="0703886697001",
        tipo_identificacion="05", cupo_credito=Decimal("500"),
    )
    producto = modelos.ProductoExtendido.objects.create(
        codigo_principal="EXT01", descripcion="PRODUCTO EXTENDIDO",
        precio_unitario=Decimal("10"), codigo_porcentaje_iva="4", marca="ACME",
    )

    # Sigue siendo un cliente y un producto del paquete: sirve donde se espere uno.
    assert isinstance(cliente, documentos.Cliente)
    assert isinstance(producto, documentos.Producto)
    assert (cliente.cupo_credito, producto.marca) == (Decimal("500"), "ACME")
    assert documentos.Cliente.objects.filter(pk=cliente.pk).exists()


def test_el_comprobante_extendido_usa_su_adaptador(entorno_django):
    """La subclase no se emite como factura: se elige el adaptador por su tipo."""
    from factec.django import adaptadores

    modelos = _modelos()

    assert adaptadores.obtener(modelos.FacturaExtendida) is adaptadores.AdaptadorFactura
    assert adaptadores.obtener(modelos.GuiaExtendida) is adaptadores.AdaptadorGuiaRemision


def _factura_extendida() -> Any:
    modelos = _modelos()
    cliente = modelos.ClienteExtendido.objects.create(
        razon_social="DISTRIBUIDORA EXTENDIDA CÍA. LTDA.",
        identificacion="1790012345001", tipo_identificacion="04",
    )
    producto = modelos.ProductoExtendido.objects.create(
        codigo_principal="EXT01", descripcion="PRODUCTO EXTENDIDO",
        precio_unitario=Decimal("10"), codigo_porcentaje_iva="4",
    )
    factura = modelos.FacturaExtendida.objects.create(
        receptor=cliente, fecha_emision=HOY, sucursal="NORTE",
    )
    factura.detalles.create(producto=producto, cantidad=Decimal("2"))
    return factura


def _valida(esquemas: Any, nombre_xsd: str, xml: str) -> None:
    from lxml import etree

    esquema = esquemas(nombre_xsd)
    documento = etree.fromstring(xml.encode("utf-8"))
    assert esquema.validate(documento), "\n".join(str(e) for e in esquema.error_log)


def test_el_xml_de_la_factura_extendida_valida_contra_el_xsd(extension, configuracion, esquemas):
    """Los campos añadidos no estropean el XML: sigue pasando el esquema del SRI."""
    factura = _factura_extendida()

    _valida(esquemas, "factura_V1.1.0.xsd", factura.xml())


def test_el_xml_de_la_guia_extendida_valida_contra_el_xsd(extension, configuracion, esquemas):
    modelos = _modelos()
    guia = modelos.GuiaExtendida.objects.create(
        fecha_emision=HOY, dir_partida="QUITO",
        razon_social_transportista="TRANSPORTES DEL NORTE CÍA. LTDA.",
        ruc_transportista="1790012345001", placa="PBX-1234",
        fecha_ini_transporte=HOY, fecha_fin_transporte=HOY, chofer_nombre="JUAN PÉREZ",
    )
    destinatario = guia.destinatarios.create(
        razon_social="DESTINO", identificacion="0703886697001", tipo_identificacion="05",
        direccion="LOJA", motivo_traslado="01",
    )
    destinatario.detalles.create(descripcion="CAJA", cantidad=1)

    assert guia.chofer_nombre == "JUAN PÉREZ"
    _valida(esquemas, "GuiaRemision_V1.1.0.xsd", guia.xml())


def test_el_admin_de_la_subclase_hereda_el_del_paquete(entorno_django):
    """Se puede registrar el admin de la subclase sin chocar con el del paquete."""
    from django.contrib import admin

    from factec.django import documentos
    from factec.django.admin_documentos import FacturaAdmin

    modelos = _modelos()

    class FacturaExtendidaAdmin(FacturaAdmin):
        list_display = ("numero", "fecha_emision", "sucursal", "estado_badge")

    assert not admin.site.is_registered(modelos.FacturaExtendida)
    try:
        admin.site.register(modelos.FacturaExtendida, FacturaExtendidaAdmin)
        assert admin.site.is_registered(modelos.FacturaExtendida)
        # La factura del paquete sigue donde estaba: son dos modelos distintos.
        assert admin.site.is_registered(documentos.Factura)
    finally:
        admin.site.unregister(modelos.FacturaExtendida)
