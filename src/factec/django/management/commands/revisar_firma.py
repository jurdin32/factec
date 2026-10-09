"""Comando ``revisar_firma``: revisa la firma electrónica antes de que falle algo.

Programado (cron o Celery Beat) avisa por correo cuando el certificado está vencido
o a punto de vencer::

    python manage.py revisar_firma                # informe por consola
    python manage.py revisar_firma --correo       # además, envía el aviso
    python manage.py revisar_firma --sin-pendientes

El código de salida es 1 cuando hay algo que impide emitir, para que el cron o el
sistema de monitorización lo detecten.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from ... import avisos


class Command(BaseCommand):
    help = "Revisa la firma electrónica (certificado vencido o por vencer) y avisa."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--correo", action="store_true",
            help="Envía el informe a los correos de FACTURACION_ELECTRONICA['CORREOS_AVISO'].",
        )
        parser.add_argument(
            "--sin-pendientes", action="store_true",
            help="No lista los comprobantes sin enviar de días anteriores.",
        )
        parser.add_argument(
            "--dias", type=int, default=None,
            help="Días de antelación para avisar de que el certificado va a vencer.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        informe = avisos.revisar_firma(
            dias_aviso=options.get("dias"),
            con_pendientes=not options.get("sin_pendientes"),
        )

        self.stdout.write(avisos.texto(informe))
        if options.get("correo") and informe["problemas"]:
            enviados = avisos.avisar_por_correo(informe)
            self.stdout.write(
                self.style.WARNING(f"Aviso enviado a {enviados} destinatario(s).")
                if enviados
                else self.style.WARNING("No hay correos configurados: no se envió nada.")
            )

        if not informe["revisados"]:
            self.stdout.write(
                self.style.WARNING(
                    "No hay ninguna configuración del emisor: cree una en el admin."
                )
            )
        elif informe["problemas"]:
            raise CommandError(
                "La firma electrónica tiene problemas: mientras no se arregle no se "
                "puede emitir."
            )
        elif informe["avisos"]:
            self.stdout.write(self.style.WARNING("Revisión con avisos."))
        else:
            self.stdout.write(self.style.SUCCESS("Firma electrónica correcta."))
        return ""
