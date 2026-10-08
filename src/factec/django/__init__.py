"""Integración con Django: modelos, admin, servicios y tareas de Celery.

Añada la app a ``INSTALLED_APPS``::

    INSTALLED_APPS = [
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        ...
        "factec.django",
    ]

Después ejecute las migraciones, que crean las tres tablas
(``sri_fe_configuracionemisor``, ``sri_fe_comprobanteemitido`` y
``sri_fe_secuencial``)::

    python manage.py migrate

A partir de ahí, los datos del contribuyente, el archivo de firma (``.p12``) y su
contraseña se gestionan desde el admin de Django, y los comprobantes se emiten
encolando tareas de Celery::

    from factec.django import services

    registro = services.crear_factura(receptor=..., detalles=[...])
    services.encolar(registro)
"""

from __future__ import annotations

default_app_config = "factec.django.apps.FactecConfig"

__all__ = ["default_app_config"]
