"""App de prueba: extiende los modelos del paquete para añadir campos.

Imita lo que haría el proyecto: heredar del modelo del paquete (Django crea una
tabla hija con los campos nuevos) y seguir emitiendo con el adaptador que toca.
No se empaqueta: vive solo en las pruebas.
"""

from __future__ import annotations

from django.db import models

from factec.django import documentos


class ClienteExtendido(documentos.Cliente):
    """El cliente, con un cupo de crédito de la tienda."""

    cupo_credito = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class ProductoExtendido(documentos.Producto):
    """El producto, con su marca."""

    marca = models.CharField("marca", max_length=60, blank=True)


class FacturaExtendida(documentos.Factura):
    """La factura, con la sucursal que la emitió."""

    sucursal = models.CharField("sucursal", max_length=40, blank=True)


class GuiaExtendida(documentos.GuiaRemision):
    """La guía, con el nombre del chofer."""

    chofer_nombre = models.CharField("chofer", max_length=120, blank=True)
