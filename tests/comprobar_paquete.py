"""Comprueba que el wheel instalable lleva todo lo que el paquete promete.

Se usa en la integración continua (y a mano) sobre lo que produce ``python -m
build``; no importa el repo, mira dentro del archivo:

    python -m build
    python tests/comprobar_paquete.py dist/factec-*.whl

Comprueba los módulos, ``py.typed``, los modelos de systemd (que viajan en
``package-data`` y ya se olvidaron una vez), las migraciones y que los extras
declarados existan. Sale con código 1 y dice qué falta si algo no está.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

#: Archivos que tienen que estar sí o sí dentro del wheel.
OBLIGATORIOS = (
    "factec/__init__.py",
    "factec/__main__.py",
    "factec/emisor.py",
    "factec/revision.py",
    "factec/fechado.py",
    "factec/py.typed",
    "factec/deploy/instalar_servicios_celery.sh",
    "factec/deploy/systemd/celery-worker.service",
    "factec/deploy/systemd/celery-beat.service",
    "factec/deploy/systemd/flower.service",
    "factec/deploy/systemd/env.ejemplo",
    "factec/django/management/commands/servicios_celery.py",
    "factec/django/migrations/0011_el_dia_del_sri_en_la_fecha_de_emision.py",
)

#: Módulos del núcleo y de Django que deben venir (por si el paquete se recorta).
MODULOS = (
    "factec/catalogos.py",
    "factec/clave_acceso.py",
    "factec/lectura.py",
    "factec/modelos.py",
    "factec/verificacion.py",
    "factec/excepciones.py",
    "factec/comprobantes/factura.py",
    "factec/comprobantes/retencion.py",
    "factec/firma/xades.py",
    "factec/sri/soap.py",
    "factec/sri/fechas.py",
    "factec/django/models.py",
    "factec/django/admin.py",
    "factec/django/tasks.py",
)


def comprobar(ruta: Path) -> list[str]:
    """Devuelve la lista de problemas (vacía si el paquete está bien)."""
    with zipfile.ZipFile(ruta) as wheel:
        contenido = set(wheel.namelist())
        metadatos = wheel.read(
            next(n for n in contenido if n.endswith(".dist-info/METADATA"))
        ).decode("utf-8")

    problemas = [
        f"falta {nombre}" for nombre in OBLIGATORIOS + MODULOS if nombre not in contenido
    ]

    migraciones = [n for n in contenido if n.startswith("factec/django/migrations/00")]
    if len(migraciones) < 11:
        problemas.append(f"solo hay {len(migraciones)} migraciones")

    for extra in ("django", "flower"):
        if f"Provides-Extra: {extra}" not in metadatos:
            problemas.append(f"no declara el extra {extra}")

    if "Requires-Python: >=3.9" not in metadatos:
        problemas.append("no declara Python 3.9 o superior")

    return problemas


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Uso: comprobar_paquete.py dist/factec-*.whl", file=sys.stderr)
        return 2

    malos = 0
    for entrada in argv[1:]:
        ruta = Path(entrada)
        problemas = comprobar(ruta)
        if problemas:
            malos += 1
            print(f"✗ {ruta.name}")
            for problema in problemas:
                print(f"    · {problema}")
        else:
            print(f"✓ {ruta.name}: todo lo que necesita está dentro")
    return 1 if malos else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
