"""Comando ``comprobar_actualizacion``: ¿hay una versión nueva del paquete?

Por consola, en el cron o con Celery Beat (``sri_fe.comprobar_actualizacion``)::

    python manage.py comprobar_actualizacion              # mira y lo deja anotado
    python manage.py comprobar_actualizacion --forzar     # aunque ya se miró hoy
    python manage.py comprobar_actualizacion --instalar   # y la instala con pip
    python manage.py comprobar_actualizacion --json       # para un script

El código de salida es 10 cuando hay una versión nueva (0 si está al día o no se
pudo comprobar), así que sirve para el cron::

    0 7 * * 1  cd /srv/facturero && venv/bin/python manage.py comprobar_actualizacion || echo "hay versión nueva"

Lo que se comprueba queda guardado, y de ahí sale el aviso ``sri_fe.W011`` de
``manage.py check`` sin que la comprobación del sistema tenga que salir a la red.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from .... import actualizacion

__all__ = ["Command"]


class Command(BaseCommand):
    help = "Comprueba si hay una versión nueva de factec y dice cómo actualizar."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--forzar", action="store_true",
                            help="Mira ahora, aunque lo guardado sea de hoy.")
        parser.add_argument("--olvidar", action="store_true",
                            help="Borra lo guardado y vuelve a mirar.")
        parser.add_argument("--instalar", action="store_true",
                            help="Instala la versión nueva con pip (reinicie el worker después).")
        parser.add_argument("--json", action="store_true", help="Salida en JSON.")

    def handle(self, *args: Any, **opciones: Any) -> None:
        if opciones["olvidar"]:
            actualizacion.olvidar()

        informe = actualizacion.comprobar(forzar=bool(opciones["forzar"] or opciones["olvidar"]))

        if opciones["json"]:
            self.stdout.write(_en_json(informe))
        else:
            # El aviso se enseña siempre que se pida a mano: también dice cuando
            # está al día o cuando no se ha podido comprobar.
            actualizacion.avisar(informe, flujo=self.stdout)

        if opciones["instalar"]:
            self._instalar(informe)

        if informe.hay_actualizacion:
            # Código 10 para el cron: 1 es un error, y esto no lo es.
            raise SystemExit(10)
        if informe.error and not informe.ultima:
            raise CommandError(f"No se pudo comprobar si hay versiones nuevas: {informe.error}")

    def _instalar(self, informe: actualizacion.InformeActualizacion) -> None:
        """Instala la última versión con pip, en este mismo entorno."""
        if not informe.hay_actualizacion:
            self.stdout.write(self.style.WARNING(
                f"No hay nada que instalar: factec {informe.instalada} es la última versión."
            ))
            return

        referencia = f"factec @ git+https://github.com/{actualizacion.repositorio_actual()}.git"
        comando = [sys.executable, "-m", "pip", "install", "-U", referencia]
        self.stdout.write(f"Instalando {informe.ultima}…")
        resultado = subprocess.run(comando, text=True, capture_output=True)
        if resultado.returncode != 0:
            raise CommandError(
                "pip no pudo actualizar el paquete:\n"
                f"{resultado.stdout}\n{resultado.stderr}".strip()
            )
        self.stdout.write(resultado.stdout.strip())
        self.stdout.write(self.style.SUCCESS(
            f"factec {informe.ultima} instalado. Reinicie el proceso y el worker de Celery "
            "para que usen la versión nueva."
        ))
        actualizacion.olvidar()


def _en_json(informe: actualizacion.InformeActualizacion) -> str:
    import json

    return json.dumps(
        {
            "instalada": informe.instalada,
            "ultima": informe.ultima,
            "hay_actualizacion": informe.hay_actualizacion,
            "acaba_de_actualizarse": informe.acaba_de_actualizarse,
            "comprobado": informe.comprobado.isoformat() if informe.comprobado else None,
            "desde_guardado": informe.desde_guardado,
            "comando": actualizacion.comando_para_actualizar(),
            "error": informe.error,
        },
        ensure_ascii=False,
    )
