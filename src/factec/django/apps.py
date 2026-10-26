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

        Además, si esta versión del paquete es nueva respecto a la última que se
        ejecutó, se enseña el aviso de actualización (ver
        :mod:`factec.actualizacion`).
        """
        from django.apps import apps

        from .. import actualizacion
        from . import checks  # noqa: F401  (el import registra los checks)

        if apps.is_installed("django.contrib.admin"):
            from . import admin_agrupado, admin_registro

            # El admin se descubre antes de este ``ready``: aquí se registran los
            # modelos del paquete que el proyecto no haya registrado ya, para que
            # el suyo (si lo tiene) mande en vez de chocar con un
            # ``AlreadyRegistered``.
            admin_registro.registrar_los_pendientes()
            admin_agrupado.organizar_el_indice()

        # Si se acaba de actualizar el paquete, se cuenta una vez (y solo con una
        # terminal detrás: no ensucia los registros del servidor).
        actualizacion.avisar_en_arranque()

        # Los ajustes de la emisión viven en la base de datos, como los del resto
        # de la aplicación: si el proyecto tiene `personalize_options` puesto, se
        # declaran aquí y al migrar quedan sembrados (ver `factec.django.ajustes`).
        from . import ajustes as ajustes_del_sri

        ajustes_del_sri.declarar()
