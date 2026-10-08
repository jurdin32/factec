"""Configuración de la aplicación Django."""

from __future__ import annotations

from django.apps import AppConfig

__all__ = ["FactecConfig"]


class FactecConfig(AppConfig):
    """App de Django para emitir y conservar comprobantes electrónicos.

    Se registra en ``INSTALLED_APPS`` como::

        INSTALLED_APPS = [
            ...,
            "factec.django",
        ]

    El ``label`` es ``sri_fe`` para que las tablas queden como
    ``sri_fe_comprobanteemitido`` y ``sri_fe_secuencial``.
    """

    name = "factec.django"
    label = "sri_fe"
    verbose_name = "Facturación electrónica (SRI)"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Registra las comprobaciones y agrupa el índice del admin.

        Así ``manage.py check`` (y por tanto ``migrate`` y ``runserver``) avisa en
        cuanto la app se añade a ``INSTALLED_APPS`` si falta algún dato, y el menú
        del admin muestra la configuración, los catálogos, los comprobantes y la
        emisión en secciones separadas.
        """
        from django.apps import apps

        from . import checks  # noqa: F401  (el import registra los checks)

        if apps.is_installed("django.contrib.admin"):
            from . import admin_agrupado

            admin_agrupado.organizar_el_indice()
