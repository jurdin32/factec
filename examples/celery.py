"""Aplicación de Celery del proyecto (cópielo como <proyecto>/celery.py).

Es lo único que no trae hecho el paquete: sin este archivo, un worker arranca pero
no encuentra las tareas de factec («Received unregistered task of type
'sri_fe.emitir_comprobante'») y el paquete emite de forma síncrona.

Pasos para dejarlo funcionando:

1. Copie este archivo como ``<proyecto>/celery.py`` (sustituya «mi_proyecto»).
2. En ``<proyecto>/__init__.py``::

       from .celery import app as celery_app

       __all__ = ("celery_app",)

3. En ``settings.py``::

       from factec.django.conf import planificador

       CELERY_BROKER_URL = "redis://localhost:6379/0"
       CELERY_TIMEZONE = TIME_ZONE
       CELERY_WORKER_SEND_TASK_EVENTS = True      # alimenta el panel de Flower
       CELERY_BEAT_SCHEDULE = {**planificador()}  # revisión diaria y reintentos

4. Levante los servicios (en Linux, con ``sudo python manage.py servicios_celery``)::

       redis-server
       celery -A mi_proyecto worker -l info -c 4
       celery -A mi_proyecto beat -l info          # tareas periódicas
       celery -A mi_proyecto flower                # panel, http://127.0.0.1:5555
"""

import os

# En macOS y Windows el pool de procesos usa «spawn» (no «fork»): sin esta
# variable, Celery no prepara el registro de tareas en los procesos hijos y la
# tarea entra al worker pero falla con
# «ValueError: not enough values to unpack (expected 3, got 0)».
os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

from celery import Celery  # noqa: E402  (después de la variable, a propósito)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mi_proyecto.settings")

app = Celery("mi_proyecto")

# La configuración de Celery se lee de settings.py con el prefijo CELERY_:
# CELERY_BROKER_URL, CELERY_BEAT_SCHEDULE, CELERY_TIMEZONE…
app.config_from_object("django.conf:settings", namespace="CELERY")

# Encuentra las tareas de las apps instaladas, incluida factec.django
# (sri_fe.emitir_comprobante, sri_fe.consultar_autorizacion,
#  sri_fe.reintentar_pendientes y sri_fe.revisar_certificado).
app.autodiscover_tasks()
