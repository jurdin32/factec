"""Prueba de humo del paquete instalado con Django: migra y arranca.

No usa el repositorio: se ejecuta con el Django y el ``factec`` que estén
instalados, monta un proyecto mínimo en memoria, aplica las migraciones del
paquete sobre una base de datos temporal y comprueba que la app queda lista.

    pip install "dist/factec-1.10.1-py3-none-any.whl[django]"
    python tests/humo_django.py

Es lo que corre la integración continua después de instalar el wheel: así una
migración que apunte a un nombre que ya no existe (o un ``package-data`` al que
le falta un archivo) se ve al publicar, no al desplegar.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def configurar() -> None:
    from django.conf import settings

    temporal = tempfile.mkdtemp(prefix="factec_humo_")
    settings.configure(
        DEBUG=False,
        SECRET_KEY="solo-para-la-prueba-de-humo",
        ALLOWED_HOSTS=["testserver", "localhost"],
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.messages",
            "django.contrib.sessions",
            "django.contrib.admin",
            "factec.django",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.path.join(temporal, "db.sqlite3"),
            }
        },
        MEDIA_ROOT=os.path.join(temporal, "media"),
        MEDIA_URL="/media/",
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        USE_TZ=True,
        TIME_ZONE="America/Guayaquil",
        LANGUAGE_CODE="es-ec",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ]
                },
            }
        ],
        FACTURACION_ELECTRONICA={"CLAVE_CIFRADO": "x" * 44},
    )


def main() -> int:
    configurar()

    import django
    from django.core.management import call_command

    django.setup()

    print(f"Django {django.get_version()} · factec {__import__('factec').__version__}")

    # Las comprobaciones del paquete (sri_fe.E…/W…) tienen que pasar.
    call_command("check", verbosity=1)

    # Y las migraciones tienen que aplicarse: es donde se ve un nombre roto.
    call_command("migrate", verbosity=0, interactive=False)

    from django.apps import apps

    from factec.django import documentos, migrations as paquete_migraciones, models, services, tasks

    # Las migraciones del wheel instalado (no del repositorio).
    carpeta = Path(paquete_migraciones.__file__).parent
    migraciones = sorted(
        archivo.name for archivo in carpeta.glob("0*.py")
    )
    tareas = sorted(
        valor
        for nombre, valor in vars(tasks).items()
        if nombre.startswith("NOMBRE_") and isinstance(valor, str)
    )
    print(
        f"app sri_fe lista: {len(models.ComprobanteEmitido._meta.fields)} campos en "
        f"ComprobanteEmitido, {len(migraciones)} migraciones, "
        f"modelos {documentos.Factura.__name__}, {documentos.Producto.__name__}"
    )
    print(f"tareas de Celery: {tareas}")
    esperadas = (
        "emitir_comprobante",
        "consultar_autorizacion",
        "revisar_certificado",
        "reintentar_pendientes",
        "comprobar_actualizacion",
    )
    faltan = [tarea for tarea in esperadas if not any(nombre.endswith(tarea) for nombre in tareas)]
    if faltan or len(migraciones) < 11:
        print(f"✗ faltan migraciones o tareas en el paquete: {faltan}", file=sys.stderr)
        return 1
    print("✓ el paquete instalado migra, arranca y registra sus tareas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
