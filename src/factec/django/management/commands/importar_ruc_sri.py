"""Crea o actualiza la configuración del emisor consultando el RUC en el SRI.

Disponible en cualquier proyecto que tenga la app instalada::

    python manage.py importar_ruc_sri --ruc 0703886697001

Solo hay que aportar lo que **no** publica el SRI: el nombre comercial, las
direcciones, el establecimiento y el ambiente. La firma electrónica (``.p12`` y
su contraseña) se carga después en el admin, por seguridad.
"""

from __future__ import annotations

from typing import Any, List

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ....excepciones import ErrorFacturacion
from .. import models, sri_datos

__all__ = ["Command"]


class Command(BaseCommand):
    help = "Consulta el RUC en el SRI y crea o actualiza la configuración del emisor."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--ruc", required=True, help="RUC de 13 dígitos del emisor.")
        parser.add_argument("--dir-matriz", dest="dir_matriz", default="",
                            help="Dirección de la matriz (el SRI no la publica).")
        parser.add_argument("--dir-establecimiento", dest="dir_establecimiento", default="",
                            help="Dirección del establecimiento (si es distinta).")
        parser.add_argument("--nombre-comercial", dest="nombre_comercial", default="")
        parser.add_argument("--estab", default="001", help="Establecimiento (3 dígitos).")
        parser.add_argument("--pto-emi", dest="pto_emi", default="001",
                            help="Punto de emisión (3 dígitos).")
        parser.add_argument("--ambiente", type=int, default=1, choices=[1, 2],
                            help="1 = pruebas, 2 = producción.")
        parser.add_argument("--nombre", default="Principal",
                            help="Nombre con el que identificar la configuración.")
        parser.add_argument("--inactiva", action="store_true",
                            help="Crear la configuración sin activarla.")
        parser.add_argument("--sin-confirmar", dest="confirmar", action="store_false",
                            default=True, help="No pedir confirmación.")
        parser.add_argument("--sin-consultar", dest="consultar", action="store_false",
                            default=True,
                            help="No consultar el SRI; usar solo los datos indicados.")

    def handle(self, *args: Any, **opciones: Any) -> str:
        ruc = str(opciones["ruc"]).strip()
        datos = None

        if opciones["consultar"]:
            self.stdout.write("Consultando el catastro del SRI…")
            try:
                resultado = sri_datos.consultar_datos(ruc)
            except ErrorFacturacion as exc:
                raise CommandError(f"No se pudo consultar el RUC: {exc}") from exc
            if not resultado.encontrado:
                raise CommandError(f"El SRI no devolvió datos para el RUC {ruc}.")

            datos = resultado.datos
            self._mostrar(datos)
            for aviso in resultado.avisos:
                self.stdout.write(self.style.WARNING(f"  ⚠ {aviso}"))

            if opciones["confirmar"]:
                respuesta = input("\n¿Crear o actualizar la configuración con estos datos? [s/N] ")
                if respuesta.strip().lower() not in ("s", "si", "sí", "y"):
                    self.stdout.write("Cancelado.")
                    return ""

        valores: dict = {
            "nombre": opciones["nombre"],
            "activo": not opciones["inactiva"],
            "nombre_comercial": opciones["nombre_comercial"] or "",
            "dir_matriz": opciones["dir_matriz"] or "",
            "dir_establecimiento": opciones["dir_establecimiento"] or "",
        }
        if datos is not None:
            valores.update(sri_datos.mapear_datos(datos))

        configuracion, creada = models.ConfiguracionEmisor.objects.update_or_create(
            ruc=(datos.ruc if datos else ruc),
            estab=str(opciones["estab"]).zfill(3),
            pto_emi=str(opciones["pto_emi"]).zfill(3),
            ambiente=int(opciones["ambiente"]),
            defaults=valores,
        )

        accion = "creada" if creada else "actualizada"
        self.stdout.write(self.style.SUCCESS(f"\nConfiguración {accion}: {configuracion}"))
        self.stdout.write(f"  contribuyenteRimpe : {configuracion.rimpe_texto or '—'}")
        self.stdout.write(
            f"  obligado contab.   : {'SI' if configuracion.obligado_contabilidad else 'NO'}"
        )

        faltan = sri_datos.faltantes_manuales(configuracion)
        obligatorios = [c for c in faltan if c in ("dir_matriz", "estab", "pto_emi")]
        if obligatorios:
            self.stdout.write(
                self.style.WARNING(
                    "  No constan en el SRI, complete en el admin: "
                    + ", ".join(obligatorios)
                )
            )

        self.stdout.write(
            "\nFalta cargar la firma electrónica. En el admin:\n"
            f"  Configuraciones del emisor → {configuracion} → "
            "adjunte el archivo .p12 y escriba su contraseña."
        )
        return "OK"

    def _mostrar(self, datos: Any) -> None:
        filas: List[tuple] = [
            ("RUC", datos.ruc),
            ("Razón social", datos.razon_social),
            ("Estado", datos.estado),
            ("Tipo", datos.tipo_contribuyente),
            ("Régimen", datos.regimen),
            ("Categoría", datos.categoria),
            ("Obligado a llevar contabilidad", "SI" if datos.obligado_contabilidad else "NO"),
            ("Agente de retención", "SI" if datos.agente_retencion else "NO"),
            ("Contribuyente especial", "SI" if datos.contribuyente_especial else "NO"),
            ("Inicio de actividades", datos.fecha_inicio_actividades),
        ]
        self.stdout.write("")
        for etiqueta, valor in filas:
            self.stdout.write(f"  {etiqueta:32}: {valor}")
        if datos.es_rimpe:
            self.stdout.write(f"  {'contribuyenteRimpe':32}: {datos.regimen_rimpe_texto}")
        self.stdout.write(
            f"  {'(el SRI no publica direcciones)':32}: indique --dir-matriz"
        )
