"""Comando ``servicios_celery``: deja Celery corriendo como servicio en Linux.

Crea las unidades de systemd del worker, del beat y de Flower con las rutas del
proyecto ya resueltas (módulo de ajustes, entorno virtual, carpeta del proyecto),
las habilita al arranque y comprueba que Redis responde::

    sudo python manage.py servicios_celery                  # crea y arranca
    sudo python manage.py servicios_celery --sin-flower     # sin el panel
    python manage.py servicios_celery --dry-run             # enseña y no toca nada
    python manage.py servicios_celery --estado              # ¿están funcionando?
    sudo python manage.py servicios_celery --reiniciar      # tras desplegar
    sudo python manage.py servicios_celery --quitar         # los elimina

    python manage.py servicios_celery --comandos            # los comandos, con sus nombres
    python manage.py servicios_celery --plantillas          # los modelos .service, para editarlos

Detrás está el script ``instalar_servicios_celery.sh`` que viaja dentro del
paquete (``python manage.py servicios_celery --ruta`` dice dónde está), así que
también se puede copiar a otro servidor y ejecutar a mano.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

#: Ruta del script dentro del paquete.
SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "instalar_servicios_celery.sh"

__all__ = ["Command"]


def _bandera(opciones: Dict[str, Any], nombre: str, argumento: Optional[Any] = None) -> List[str]:
    """Añade ``--nombre`` (con su valor) a la lista de argumentos si se indicó."""
    if argumento in (None, "", False):
        return []
    if argumento is True:
        return [nombre]
    return [nombre, str(argumento)]


class Command(BaseCommand):
    help = "Crea los servicios de systemd de Celery (worker, beat y Flower) en Linux."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--ruta", action="store_true", help="Solo muestra dónde está el script.")
        parser.add_argument("--dry-run", action="store_true", help="Enseña lo que haría, sin tocar nada.")
        parser.add_argument("--estado", action="store_true", help="Muestra si los servicios funcionan.")
        parser.add_argument("--comandos", action="store_true",
                            help="Enseña los comandos de Celery de este proyecto, ya con sus nombres.")
        parser.add_argument("--plantillas", action="store_true",
                            help="Copia los modelos de los servicios (.service y .env) para editarlos.")
        parser.add_argument("--reiniciar", action="store_true", help="Reinicia los servicios instalados.")
        parser.add_argument("--quitar", action="store_true", help="Para y borra los servicios.")
        parser.add_argument("--solo-archivos", action="store_true",
                            help="Escribe las unidades y no toca systemctl.")
        parser.add_argument("--destino", help="Carpeta de las unidades (por omisión /etc/systemd/system).")
        parser.add_argument("--proyecto-dir", dest="proyecto_dir",
                            help="Carpeta del proyecto (por omisión, la del settings).")
        parser.add_argument("--venv", help="Entorno virtual con celery (por omisión, el que ejecuta esto).")
        parser.add_argument("--modulo", help="Módulo de ajustes (por omisión, el de DJANGO_SETTINGS_MODULE).")
        parser.add_argument("--usuario", help="Usuario del servicio (por omisión, el suyo).")
        parser.add_argument("--grupo", help="Grupo del servicio.")
        parser.add_argument("--concurrencia", type=int, help="Procesos del worker (por omisión, 4).")
        parser.add_argument("--sin-flower", dest="sin_flower", action="store_true",
                            help="No crear el panel Flower.")
        parser.add_argument("--solo-worker", dest="solo_worker", action="store_true",
                            help="Solo el worker (sin beat ni Flower).")
        parser.add_argument("--puerto", help="Puerto de Flower (por omisión, 5555).")
        parser.add_argument("--direccion", help="Dirección de Flower (por omisión, 127.0.0.1).")
        parser.add_argument("--flower-auth", dest="flower_auth", help="Usuario y clave del panel (u:c).")

    # ------------------------------------------------------------- ayudantes

    def _argumentos_del_script(self, opciones: Dict[str, Any]) -> List[str]:
        """Traduce las opciones del comando a las del script."""
        argumentos: List[str] = []
        argumentos += _bandera(opciones, "--dry-run", opciones["dry_run"])
        argumentos += _bandera(opciones, "--estado", opciones["estado"])
        argumentos += _bandera(opciones, "--comandos", opciones["comandos"])
        argumentos += _bandera(opciones, "--plantillas", opciones["plantillas"])
        argumentos += _bandera(opciones, "--reiniciar", opciones["reiniciar"])
        argumentos += _bandera(opciones, "--quitar", opciones["quitar"])
        argumentos += _bandera(opciones, "--solo-archivos", opciones["solo_archivos"])
        argumentos += _bandera(opciones, "--destino", opciones["destino"])
        argumentos += _bandera(opciones, "--usuario", opciones["usuario"])
        argumentos += _bandera(opciones, "--grupo", opciones["grupo"])
        argumentos += _bandera(opciones, "--concurrencia", opciones["concurrencia"])
        argumentos += _bandera(opciones, "--puerto", opciones["puerto"])
        argumentos += _bandera(opciones, "--direccion", opciones["direccion"])
        argumentos += _bandera(opciones, "--flower-auth", opciones["flower_auth"])
        argumentos += _bandera(opciones, "--sin-flower", opciones["sin_flower"])
        argumentos += _bandera(opciones, "--solo-worker", opciones["solo_worker"])

        # Lo que el comando ya sabe: la carpeta del proyecto, el entorno virtual
        # que está ejecutando esto y el módulo de ajustes.
        proyecto = opciones["proyecto_dir"] or str(getattr(settings, "BASE_DIR", "") or "")
        if proyecto and Path(proyecto, "manage.py").exists():
            argumentos += ["--proyecto-dir", proyecto]

        venv = opciones["venv"] or sys.prefix
        if venv:
            argumentos += ["--venv", venv]

        modulo = opciones["modulo"] or (getattr(settings, "SETTINGS_MODULE", "") or "")
        if modulo:
            argumentos += ["--modulo", modulo.split(".")[0]]
        return argumentos

    # ---------------------------------------------------------------- handle

    def handle(self, *args: Any, **opciones: Any) -> str:
        if not SCRIPT.exists():  # pragma: no cover - paquete mal empaquetado
            raise CommandError(
                f"No encuentro el script de servicios en {SCRIPT}. Reinstale el paquete "
                "con: pip install -U factec[django]"
            )

        if opciones["ruta"]:
            self.stdout.write(str(SCRIPT))
            return ""

        if sys.platform.startswith("win"):  # pragma: no cover - solo Windows
            raise CommandError(
                "Los servicios de systemd son de Linux. En Windows use un servicio con "
                "NSSM o arranque el worker con el Programador de tareas."
            )

        argumentos = self._argumentos_del_script(opciones)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"servicios_celery: {SCRIPT.name} {' '.join(argumentos)}"
        ))

        resultado = subprocess.run(  # noqa: S603 - es nuestro propio script
            ["bash", str(SCRIPT), *argumentos],
            cwd=str(getattr(settings, "BASE_DIR", "") or Path.cwd()),
            capture_output=True,
            text=True,
            check=False,
        )
        # Lo que dice el script va por la salida del comando: así se ve igual al
        # ejecutarlo a mano y se puede capturar en una prueba o en un despliegue.
        if resultado.stdout:
            self.stdout.write(resultado.stdout.rstrip("\n"))
        if resultado.stderr:
            self.stderr.write(resultado.stderr.rstrip("\n"))

        if resultado.returncode != 0:
            raise CommandError(
                "El script de servicios terminó con errores "
                f"(código {resultado.returncode}). Revise los mensajes de arriba."
            )
        return ""
