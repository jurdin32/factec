"""El aviso de versión dentro de Django: comprobación del sistema, comando y tarea.

Ninguna prueba sale a la red: se sustituye ``actualizacion.comprobar`` (o los
datos guardados) y se comprueba qué enseña el paquete.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from factec import actualizacion


def informe(ultima=None, *, error="", anterior=None):
    """Un informe a medida, sin salir a la red."""
    return actualizacion.InformeActualizacion(
        instalada=actualizacion.version_instalada(),
        ultima=ultima,
        comprobado=datetime.now(timezone.utc),
        anterior=anterior,
        error=error,
    )


def guardar_comprobado(ultima):
    actualizacion.guardar(ultima=ultima, comprobado=datetime.now(timezone.utc).isoformat())


# --------------------------------------------------- comprobación del sistema


def test_el_check_avisa_cuando_hay_version_nueva(entorno_django):
    from factec.django import checks

    assert checks.comprobar_version() == []          # sin datos: no se dice nada

    guardar_comprobado("99.0.0")

    avisos = checks.comprobar_version()
    assert [aviso.id for aviso in avisos] == [checks.ID_VERSION]
    assert "99.0.0" in avisos[0].msg
    assert "pip install -U" in avisos[0].hint


def test_el_check_esta_al_dia_no_dice_nada(entorno_django):
    from factec.django import checks

    guardar_comprobado(actualizacion.version_instalada())

    assert checks.comprobar_version() == []


def test_el_check_calla_con_los_avisos_apagados(entorno_django, monkeypatch):
    from factec.django import checks

    guardar_comprobado("99.0.0")
    monkeypatch.setenv("FACTEC_SIN_AVISOS", "1")

    assert checks.comprobar_version() == []


def test_el_check_nunca_sale_a_la_red(entorno_django, monkeypatch):
    from factec.django import checks

    def prohibido(*_args, **_kwargs):
        raise AssertionError("las comprobaciones del sistema no salen a la red")

    monkeypatch.setattr(actualizacion, "_etiquetas", prohibido)
    guardar_comprobado("99.0.0")

    # Lo de 99.0.0 se lee del archivo; de la red, nada.
    assert [aviso.id for aviso in checks.comprobar_version()] == [checks.ID_VERSION]


def test_el_check_esta_registrado_y_sale_con_manage_py(entorno_django, capsys):
    """El aviso sale con ``manage.py check``, con su etiqueta y su código."""
    from django.core.checks import run_checks
    from django.core.management import call_command

    from factec.django.checks import ETIQUETA

    guardar_comprobado("99.0.0")

    # Registrado: Django lo ejecuta con los demás.
    problemas = run_checks(tags=[ETIQUETA])
    assert [p.id for p in problemas if p.id == "sri_fe.W011"], "no está en el registro"

    call_command("check")

    salida = capsys.readouterr()
    texto = salida.out + salida.err          # Django escribe el informe en stderr
    assert "sri_fe.W011" in texto
    assert "99.0.0" in texto


# ------------------------------------------------------------------ el comando


def test_el_comando_dice_como_actualizar(entorno_django, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr(actualizacion, "comprobar", lambda **_: informe("99.0.0"))
    salida = StringIO()

    with pytest.raises(SystemExit) as salida_del_sistema:
        call_command("comprobar_actualizacion", stdout=salida)

    assert salida_del_sistema.value.code == 10       # 10 = hay versión nueva
    texto = salida.getvalue()
    assert "Hay una versión nueva de factec" in texto
    assert "99.0.0" in texto
    assert "pip install -U" in texto


def test_el_comando_cuando_esta_al_dia(entorno_django, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr(
        actualizacion, "comprobar", lambda **_: informe(actualizacion.version_instalada())
    )
    salida = StringIO()

    call_command("comprobar_actualizacion", stdout=salida)

    assert "es la última versión" in salida.getvalue()


def test_el_comando_en_json(entorno_django, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr(actualizacion, "comprobar", lambda **_: informe("99.0.0"))
    salida = StringIO()

    with pytest.raises(SystemExit):
        call_command("comprobar_actualizacion", "--json", stdout=salida)

    datos = json.loads(salida.getvalue())
    assert datos["ultima"] == "99.0.0"
    assert datos["hay_actualizacion"] is True
    assert datos["comando"].startswith("pip install -U")


def test_el_comando_avisa_cuando_no_pudo_comprobar(entorno_django, monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    monkeypatch.setattr(
        actualizacion, "comprobar", lambda **_: informe(None, error="OSError: sin ruta al host")
    )

    with pytest.raises(CommandError, match="sin ruta al host"):
        call_command("comprobar_actualizacion", stdout=StringIO())


def test_el_comando_no_instala_nada_si_esta_al_dia(entorno_django, monkeypatch):
    from django.core.management import call_command

    monkeypatch.setattr(
        actualizacion, "comprobar", lambda **_: informe(actualizacion.version_instalada())
    )
    salida = StringIO()

    call_command("comprobar_actualizacion", "--instalar", stdout=salida)

    assert "No hay nada que instalar" in salida.getvalue()


# -------------------------------------------------------- la tarea y el plan


def test_el_planificador_programa_la_comprobacion_de_version(entorno_django):
    from factec.django import conf, tasks

    plan = conf.planificador()

    assert tasks.NOMBRE_COMPROBAR_ACTUALIZACION in plan
    assert plan[tasks.NOMBRE_COMPROBAR_ACTUALIZACION]["task"] == tasks.NOMBRE_COMPROBAR_ACTUALIZACION
    assert set(plan) >= {
        tasks.NOMBRE_REVISAR_CERTIFICADO,
        tasks.NOMBRE_REINTENTAR_PENDIENTES,
        tasks.NOMBRE_COMPROBAR_ACTUALIZACION,
    }


def test_la_tarea_deja_la_comprobacion_hecha(entorno_django, monkeypatch):
    from factec.django import tasks

    monkeypatch.setattr(actualizacion, "comprobar", lambda **_: informe("99.0.0"))

    assert "99.0.0" in tasks.comprobar_actualizacion()


def test_la_tarea_no_revienta_sin_internet(entorno_django, monkeypatch):
    from factec.django import tasks

    def revienta(**_kwargs):
        raise OSError("sin ruta al host")

    monkeypatch.setattr(actualizacion, "comprobar", revienta)

    assert tasks.comprobar_actualizacion() == ""


def test_el_arranque_de_django_no_rompe(entorno_django, monkeypatch):
    """Aunque el estado guardado sea un desastre, arrancar tiene que funcionar."""
    from factec.django import apps

    ruta = actualizacion.archivo_de_estado()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("{esto no es json", encoding="utf-8")

    apps.FactecConfig.ready(object())        # sin excepción: se sigue arrancando

    actualizacion.olvidar()
    assert actualizacion.leer() == {}


def test_lo_guardado_tiene_la_fecha_de_la_comprobacion(entorno_django):
    guardar_comprobado("99.0.0")

    guardado = actualizacion.leer()

    momento = datetime.fromisoformat(guardado["comprobado"])
    assert datetime.now(timezone.utc) - momento < timedelta(minutes=5)
