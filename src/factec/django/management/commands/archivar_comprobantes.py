"""Escribe en disco los XML y las respuestas ya guardados en la base de datos.

Sirve para los comprobantes que se emitieron antes de que existiera el guardado en
archivos, o si la carpeta se perdió::

    python manage.py archivar_comprobantes
    python manage.py archivar_comprobantes --desde 2026-10-01 --estado DEVUELTO
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.db.models import QuerySet

from ... import archivos, models


class Command(BaseCommand):
    help = "Vuelve a escribir en sus carpetas los XML y las respuestas de los comprobantes."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--desde", default="", help="Solo los emitidos desde esta fecha (AAAA-MM-DD)."
        )
        parser.add_argument(
            "--hasta", default="", help="Solo los emitidos hasta esta fecha (AAAA-MM-DD)."
        )
        parser.add_argument(
            "--estado", default="", help="Solo los que estén en ese estado (por ejemplo DEVUELTO)."
        )
        parser.add_argument(
            "--limite", type=int, default=0, help="Procesar como máximo N comprobantes."
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Mostrar qué se escribiría, sin escribir nada.",
        )

    def handle(self, *args: Any, **opciones: Any) -> str:
        comprobantes = self._consulta(opciones)
        if opciones["limite"]:
            comprobantes = comprobantes[: opciones["limite"]]

        escritos = 0
        sin_contenido = 0
        for registro in comprobantes:
            piezas = self._piezas(registro)
            if not piezas:
                sin_contenido += 1
                continue

            if opciones["simular"]:
                self.stdout.write(
                    f"  {registro.clave_acceso} → {archivos.carpeta_de(registro)} "
                    f"({', '.join(piezas)})"
                )
                escritos += 1
                continue

            for nombre, contenido in piezas.items():
                archivos.escribir(registro, nombre, contenido)

            carpeta = archivos.carpeta_de(registro)
            if registro.carpeta != carpeta:
                models.ComprobanteEmitido.objects.filter(pk=registro.pk).update(carpeta=carpeta)
            escritos += 1

        accion = "Se escribirían" if opciones["simular"] else "Archivados"
        self.stdout.write(
            self.style.SUCCESS(f"{accion} {escritos} comprobante(s) en {archivos.base_de_archivos()}.")
        )
        if sin_contenido:
            self.stdout.write(
                self.style.WARNING(f"{sin_contenido} sin XML guardado: no se puede archivar.")
            )
        return ""

    # ------------------------------------------------------------------ ayudas

    @staticmethod
    def _consulta(opciones: Dict[str, Any]) -> QuerySet:
        consulta = models.ComprobanteEmitido.objects.all().order_by("fecha_emision", "pk")
        if opciones["desde"]:
            consulta = consulta.filter(
                fecha_emision__gte=Command._fecha(opciones["desde"], "desde")
            )
        if opciones["hasta"]:
            consulta = consulta.filter(
                fecha_emision__lte=Command._fecha(opciones["hasta"], "hasta")
            )
        if opciones["estado"]:
            consulta = consulta.filter(estado=opciones["estado"].strip().upper())
        return consulta

    @staticmethod
    def _fecha(valor: str, nombre: str):
        try:
            return datetime.strptime(valor.strip(), "%Y-%m-%d").date()
        except ValueError as error:
            raise ValueError(f"--{nombre} debe ser AAAA-MM-DD, no {valor!r}.") from error

    @staticmethod
    def _piezas(registro: models.ComprobanteEmitido) -> Dict[str, Optional[str]]:
        """Archivos que se pueden reconstruir a partir del registro."""
        return {
            archivos.NOMBRE_SIN_FIRMA: registro.xml_sin_firma,
            archivos.NOMBRE_FIRMADO: registro.xml_firmado,
            archivos.NOMBRE_AUTORIZADO: registro.xml_autorizado,
            archivos.NOMBRE_RESPUESTA_RECEPCION: registro.respuesta_recepcion,
            archivos.NOMBRE_RESPUESTA_AUTORIZACION: registro.respuesta_autorizacion,
            archivos.NOMBRE_ERROR: registro.error,
        }
