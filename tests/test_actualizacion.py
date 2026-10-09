"""El aviso de versión: comparación, archivo de estado y recuadros.

Ninguna prueba sale a la red: donde haría falta, se le pasa a ``comprobar()`` un
``abrir`` de mentira.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from factec import actualizacion


class Respuesta:
    """Lo mínimo que ``comprobar`` le pide a la respuesta de GitHub."""

    def __init__(self, etiquetas):
        self._etiquetas = etiquetas

    def read(self) -> bytes:
        return json.dumps([{"name": etiqueta} for etiqueta in self._etiquetas]).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *excepcion):
        return False


def red(*etiquetas):
    """Un ``abrir`` de mentira que devuelve esas etiquetas."""

    def abrir(peticion, timeout=None):
        assert "api.github.com" in peticion.full_url
        assert timeout == actualizacion._TIEMPO_DE_ESPERA or timeout > 0
        return Respuesta(etiquetas)

    return abrir


def sin_red(*_args, **_kwargs):
    raise OSError("sin ruta al host")


class Terminal(StringIO):
    """Un flujo que dice ser una terminal (para que salgan los colores)."""

    def isatty(self) -> bool:
        return True


# ------------------------------------------------------------------ versiones


@pytest.mark.parametrize(
    ("version", "esperado"),
    [
        ("1.10.1", (1, 10, 1)),
        ("v1.10.1", (1, 10, 1)),
        ("v1.11.0rc1", (1, 11, 0)),
        ("1.9", (1, 9, 0)),
        ("2", (2, 0, 0)),
        ("", (0, 0, 0)),
    ],
)
def test_normaliza_las_versiones(version, esperado):
    assert actualizacion.normalizar_version(version) == esperado


def test_sabe_cual_es_mas_nueva():
    assert actualizacion.es_mas_nueva("1.11.0", "1.10.1")
    assert actualizacion.es_mas_nueva("v1.9.10", "1.9.9")
    assert not actualizacion.es_mas_nueva("1.10.1", "1.10.1")
    assert not actualizacion.es_mas_nueva("1.9.1", "1.10.1")


def test_la_version_se_lee_sin_la_v_de_la_etiqueta():
    assert actualizacion.obtener_ultima(abrir=red("v1.9.1", "v1.11.0", "v1.10.1")) == "1.11.0"


# ------------------------------------------------------- comprobar y guardar


def test_comprueba_y_lo_deja_anotado():
    informe = actualizacion.comprobar(abrir=red("v1.10.1", "v1.12.0"))

    assert informe.ultima == "1.12.0"
    assert informe.hay_actualizacion
    assert informe.instalada == actualizacion.version_instalada()
    assert not informe.acaba_de_actualizarse

    guardado = actualizacion.leer()
    assert guardado["ultima"] == "1.12.0"
    assert guardado["comprobado"]
    assert actualizacion.actualizacion_disponible()


def test_lo_comprobado_evita_salir_a_la_red():
    actualizacion.comprobar(abrir=red("1.12.0"))

    # El ``abrir`` de esta llamada reventaría: no se le debe llamar.
    informe = actualizacion.comprobar(abrir=sin_red)

    assert informe.desde_guardado
    assert informe.ultima == "1.12.0"
    assert informe.error == ""


def test_forzar_vuelve_a_mirar():
    actualizacion.comprobar(abrir=red("1.12.0"))

    informe = actualizacion.comprobar(forzar=True, abrir=red("v1.13.0"))

    assert informe.ultima == "1.13.0"
    assert not informe.desde_guardado


def test_sin_internet_no_rompe_y_lo_dice():
    informe = actualizacion.comprobar(abrir=sin_red)

    assert informe.ultima is None
    assert not informe.hay_actualizacion
    assert not informe.al_dia
    assert "sin ruta al host" in informe.error
    assert "no se ha podido comprobar" in str(informe)
    assert actualizacion.banner(informe) == ""


def test_sin_internet_conserva_lo_ultimo_que_se_sabia():
    actualizacion.comprobar(abrir=red("1.12.0"))
    actualizacion.guardar(comprobado=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())

    informe = actualizacion.comprobar(abrir=sin_red)

    assert informe.ultima == "1.12.0"        # lo de hace tres días sirve
    assert informe.hay_actualizacion
    assert "sin ruta al host" in informe.error


def test_olvidar_borra_lo_guardado():
    actualizacion.comprobar(abrir=red("1.12.0"))
    assert actualizacion.leer()

    actualizacion.olvidar()

    assert actualizacion.leer() == {}
    assert actualizacion.ultima_conocida() is None


def test_sin_la_variable_no_sale_a_la_red(monkeypatch):
    monkeypatch.setenv("FACTEC_SIN_COMPROBAR", "1")

    informe = actualizacion.comprobar(abrir=sin_red)

    assert "FACTEC_SIN_COMPROBAR" in informe.error


def test_esta_al_dia_cuando_lo_guardado_no_es_mas_nuevo():
    actualizacion.comprobar(abrir=red(actualizacion.version_instalada()))

    assert actualizacion.esta_al_dia()
    assert not actualizacion.actualizacion_disponible()


# ------------------------------------------------------------------ recuadros


def test_el_recuadro_de_version_nueva_dice_como_actualizar():
    informe = actualizacion.comprobar(abrir=red("1.12.0"))

    texto = actualizacion.banner(informe)

    assert texto.startswith("╔") and texto.endswith("╝")
    assert "Hay una versión nueva de factec" in texto
    assert f"Instalada   {actualizacion.version_instalada()}" in texto
    assert "Disponible  1.12.0" in texto
    assert 'pip install -U "factec @ git+https://github.com/jurdin32/factec.git"' in texto
    assert f"compare/v{actualizacion.version_instalada()}...v1.12.0" in texto


def test_la_linea_de_al_dia():
    informe = actualizacion.comprobar(abrir=red(actualizacion.version_instalada()))

    texto = actualizacion.banner(informe)

    assert texto.startswith("✓ factec")
    assert "es la última versión" in texto
    assert "╔" not in texto


def test_el_recuadro_de_recien_actualizado():
    actualizacion.guardar(vista="1.0.0")
    informe = actualizacion.comprobar(abrir=red("1.12.0"))

    texto = actualizacion.banner(informe)

    assert informe.acaba_de_actualizarse
    assert f"✓  factec actualizado: 1.0.0 → {actualizacion.version_instalada()}" in texto
    assert "Ya hay otra más nueva: 1.12.0" in texto


def test_los_colores_solo_salen_en_una_terminal():
    informe = actualizacion.comprobar(abrir=red("1.12.0"))

    assert "\033[" not in actualizacion.banner(informe, color=False)
    assert "\033[" in actualizacion.banner(informe, color=True)


# ------------------------------------------------------------------- avisos


def test_el_aviso_automatico_se_calla_si_no_hay_novedad():
    flujo = StringIO()
    actualizacion.comprobar(abrir=red(actualizacion.version_instalada()))

    assert actualizacion.avisar(flujo=flujo, automatico=True) is False
    assert flujo.getvalue() == ""


def test_el_aviso_a_mano_dice_que_esta_al_dia():
    flujo = StringIO()
    actualizacion.comprobar(abrir=red(actualizacion.version_instalada()))

    assert actualizacion.avisar(flujo=flujo) is True
    assert "es la última versión" in flujo.getvalue()


def test_el_aviso_a_mano_cuenta_cuando_no_se_pudo_comprobar():
    flujo = StringIO()
    informe = actualizacion.comprobar(abrir=sin_red)

    assert actualizacion.avisar(informe, flujo=flujo) is True
    assert "no se pudo comprobar" in flujo.getvalue()


def test_el_aviso_automatico_avisa_cuando_hay_version_nueva():
    flujo = StringIO()
    actualizacion.comprobar(abrir=red("1.12.0"))

    assert actualizacion.avisar(flujo=flujo, automatico=True) is True
    assert "Hay una versión nueva" in flujo.getvalue()


def test_sin_avisos_calla(monkeypatch):
    flujo = StringIO()
    actualizacion.comprobar(abrir=red("1.12.0"))
    monkeypatch.setenv("FACTEC_SIN_AVISOS", "1")

    assert actualizacion.avisar(flujo=flujo, automatico=True) is False
    assert flujo.getvalue() == ""


def test_el_arranque_avisa_solo_cuando_la_version_es_nueva():
    # Primera vez: se apunta la versión y no se dice nada.
    primera = Terminal()
    actualizacion.comprobar(abrir=red("1.12.0"))
    assert actualizacion.avisar_en_arranque(flujo=primera) is False
    assert primera.getvalue() == ""
    assert actualizacion.leer()["vista"] == actualizacion.version_instalada()

    # Y si la versión que había vista era otra, sí avisa.
    actualizacion.guardar(vista="1.0.0")
    segunda = Terminal()
    assert actualizacion.avisar_en_arranque(flujo=segunda) is True
    assert "factec actualizado" in segunda.getvalue()
    assert actualizacion.leer()["vista"] == actualizacion.version_instalada()


def test_el_arranque_avisa_aunque_no_quiera_colores(monkeypatch):
    """Sin colores (`NO_COLOR`) el aviso sigue saliendo: son dos cosas distintas."""
    actualizacion.guardar(ultima="1.12.0", vista="1.0.0")
    monkeypatch.setenv("NO_COLOR", "1")
    flujo = Terminal()

    assert actualizacion.avisar_en_arranque(flujo=flujo) is True

    texto = flujo.getvalue()
    assert "factec actualizado" in texto
    assert "\033[" not in texto


def test_el_arranque_no_ensucia_los_registros_sin_terminal():
    actualizacion.guardar(ultima="1.12.0", vista="1.0.0")
    flujo = StringIO()          # sin isatty: como un archivo de registro

    assert actualizacion.avisar_en_arranque(flujo=flujo) is False
    assert flujo.getvalue() == ""


def test_el_comando_para_actualizar_es_el_del_paquete():
    comando = actualizacion.comando_para_actualizar()

    assert comando.startswith("pip install -U ")
    assert "git+https://github.com/jurdin32/factec.git" in comando


def test_se_puede_apuntar_a_otro_repositorio(monkeypatch):
    monkeypatch.setenv("FACTEC_REPOSITORIO", "otro/factec")

    assert "otro/factec" in actualizacion.comando_para_actualizar()
    assert actualizacion.repositorio_actual() == "otro/factec"


# --------------------------------------------------- la API limitada y el plan B


def revienta(mensaje):
    def _revienta(*_args, **_kwargs):
        raise OSError(mensaje)

    return _revienta


def test_si_la_api_falla_se_pregunta_por_git(monkeypatch):
    """GitHub limita a 60 consultas por hora: ``git ls-remote`` no limita."""

    class Resultado:
        stdout = "abc123\trefs/tags/v1.11.0\ndef456\trefs/tags/v1.12.0\n"
        returncode = 0

    def git_falso(orden, **opciones):
        assert orden[:4] == ["git", "ls-remote", "--tags", "--refs"]
        assert opciones["env"]["GIT_TERMINAL_PROMPT"] == "0"     # que no pregunte nada
        return Resultado()

    monkeypatch.setattr(actualizacion, "_etiquetas_por_api", revienta("HTTP Error 403"))
    monkeypatch.setattr(actualizacion.subprocess, "run", git_falso)

    assert actualizacion.obtener_ultima() == "1.12.0"


def test_si_no_responde_ninguno_se_dice(monkeypatch):
    monkeypatch.setattr(actualizacion, "_etiquetas_por_api", revienta("403 rate limit"))
    monkeypatch.setattr(actualizacion, "_etiquetas_por_git", revienta("git no está"))

    with pytest.raises(actualizacion._ErrorDeConsulta, match="git tampoco"):
        actualizacion.obtener_ultima()

    # Y en el informe se ve, sin reventar nada.
    informe = actualizacion.comprobar()
    assert "403 rate limit" in informe.error
    assert informe.ultima is None


def test_se_puede_usar_un_token_de_github(monkeypatch):
    """Con ``GITHUB_TOKEN`` el límite de la API sube a 5000 consultas por hora."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_de-ejemplo")
    vistas = {}

    def abrir(peticion, timeout=None):
        vistas.update(peticion.headers)
        return Respuesta(["v1.12.0"])

    actualizacion.obtener_ultima(abrir=abrir)

    assert any(
        clave.lower() == "authorization" and valor == "Bearer ghp_de-ejemplo"
        for clave, valor in vistas.items()
    )
