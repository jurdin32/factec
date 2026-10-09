"""Aviso de versión: ¿está al día el factec que tiene instalado?

Flutter avisa al terminar cualquier comando; aquí es parecido, con dos reglas:

* **La red solo la toca quien la pide.** Salen a comprobar ``sri-fe
  actualizacion``, ``manage.py comprobar_actualizacion`` y la tarea
  ``sri_fe.comprobar_actualizacion``. Lo automático (la consola, el arranque de
  Django) lee lo último guardado en el archivo de estado, que caduca a las 24
  horas: sin internet no hay aviso, ni error, ni espera.
* **No se dice lo que no se sabe.** Si no se ha podido comprobar, no se anuncia
  una versión nueva ni se felicita por estar al día.

Variables de entorno:

===================================  ===========================================
``FACTEC_SIN_AVISOS=1``              no enseñar ningún aviso
``FACTEC_SIN_COMPROBAR=1``           no salir a la red (solo lo ya guardado)
``FACTEC_REPOSITORIO=usuario/repo``  otro repositorio de GitHub que mirar
``FACTEC_CACHE_DIR=...``             otra carpeta para el estado
``NO_COLOR=1``                       sin colores (también sin terminal)
===================================  ===========================================

Uso:

.. code-block:: python

    from factec.actualizacion import avisar, comprobar

    informe = comprobar()                 # sale a la red (una vez al día)
    print(informe.ultima, informe.hay_actualizacion, informe.acaba_de_actualizarse)
    avisar()                              # imprime el recuadro, si hay algo que contar
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

__all__ = [
    "ANCHO",
    "HORAS_DE_CADUCIDAD",
    "REPOSITORIO",
    "InformeActualizacion",
    "actualizacion_disponible",
    "avisar",
    "avisar_en_arranque",
    "banner",
    "comando_para_actualizar",
    "comprobar",
    "es_mas_nueva",
    "esta_al_dia",
    "guardar",
    "leer",
    "normalizar_version",
    "obtener_ultima",
    "olvidar",
    "sin_avisos",
    "ultima_conocida",
    "version_instalada",
]

#: Dónde se mira si hay versiones nuevas.
REPOSITORIO = "jurdin32/factec"

#: Cuánto vale lo comprobado antes de volver a salir a la red.
HORAS_DE_CADUCIDAD = 24

#: Ancho del recuadro (cabe en una terminal de 80 columnas).
ANCHO = 74

#: Cómo se actualiza, tal cual se le enseña al usuario.
COMANDO_ACTUALIZAR = 'pip install -U "factec @ git+https://github.com/{repositorio}.git"'

_URL_TAGS = "https://api.github.com/repos/{repositorio}/tags"
_URL_CAMBIOS = "https://github.com/{repositorio}/compare/v{desde}...v{hasta}"

_TIEMPO_DE_ESPERA = 5.0

#: Códigos ANSI; se apagan solos si la salida no es una terminal.
_APAGADO = "\033[0m"
_ESTILOS = {
    "titulo": "\033[1;36m",
    "bien": "\033[1;32m",
    "aviso": "\033[1;33m",
    "tenue": "\033[2m",
    "error": "\033[1;31m",
}


# ------------------------------------------------------------------- versiones


def version_instalada() -> str:
    """Versión del factec que se está ejecutando."""
    from . import __version__

    return __version__


def normalizar_version(version: str) -> Tuple[int, ...]:
    """Convierte ``"v1.10.2rc1"`` en ``(1, 10, 2)`` para poder comparar.

    Se queda con los números: ``rc``, ``beta`` y compañía cuentan como la versión
    a la que acompañan (lo que importa aquí es si hay algo más nuevo, no el orden
    de las preversiones).
    """
    numeros: List[int] = []
    for parte in str(version).strip().lstrip("vV").split("."):
        digitos = ""
        for caracter in parte:
            if caracter.isdigit():
                digitos += caracter
            else:
                break
        numeros.append(int(digitos) if digitos else 0)
    while len(numeros) < 3:
        numeros.append(0)
    return tuple(numeros)


def es_mas_nueva(candidata: str, referencia: str) -> bool:
    """``True`` si ``candidata`` es posterior a ``referencia``."""
    return normalizar_version(candidata) > normalizar_version(referencia)


def ultima_conocida() -> Optional[str]:
    """La última versión de la que se tiene noticia, sin salir a la red."""
    guardado = leer()
    valor = guardado.get("ultima")
    return str(valor) if valor else None


def esta_al_dia() -> bool:
    """``True`` si lo último comprobado dice que no hay nada más nuevo."""
    ultima = ultima_conocida()
    return bool(ultima) and not es_mas_nueva(ultima, version_instalada())


def actualizacion_disponible() -> bool:
    """``True`` si lo último comprobado dice que hay una versión nueva."""
    ultima = ultima_conocida()
    return bool(ultima) and es_mas_nueva(ultima, version_instalada())


def comando_para_actualizar(repositorio: Optional[str] = None) -> str:
    """El comando exacto que hay que pegar para actualizar."""
    return COMANDO_ACTUALIZAR.format(repositorio=repositorio or repositorio_actual())


# ------------------------------------------------------------------- el archivo


def repositorio_actual() -> str:
    """Repositorio de GitHub que se mira (``usuario/repo``)."""
    return os.environ.get("FACTEC_REPOSITORIO") or REPOSITORIO


def carpeta_de_estado() -> Path:
    """Carpeta donde se guarda lo último comprobado."""
    propia = os.environ.get("FACTEC_CACHE_DIR")
    if propia:
        return Path(propia)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "factec"


def archivo_de_estado() -> Path:
    return carpeta_de_estado() / "actualizacion.json"


def sin_avisos() -> bool:
    """``True`` si se pidió no enseñar avisos (``FACTEC_SIN_AVISOS``)."""
    return _verdadero("FACTEC_SIN_AVISOS")


def sin_comprobar() -> bool:
    """``True`` si se pidió no salir a la red (``FACTEC_SIN_COMPROBAR``)."""
    return _verdadero("FACTEC_SIN_COMPROBAR")


def _verdadero(variable: str) -> bool:
    return str(os.environ.get(variable, "")).strip().lower() in {"1", "si", "sí", "true", "yes"}


def leer() -> Dict[str, Any]:
    """Lo último guardado (``{}`` si no hay nada o está corrupto)."""
    try:
        with open(archivo_de_estado(), encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def guardar(**campos: Any) -> Dict[str, Any]:
    """Actualiza el archivo de estado y devuelve lo que queda guardado."""
    datos = leer()
    datos.update({clave: valor for clave, valor in campos.items() if valor is not None})
    try:
        archivo = archivo_de_estado()
        archivo.parent.mkdir(parents=True, exist_ok=True)
        with open(archivo, "w", encoding="utf-8") as salida:
            json.dump(datos, salida, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError:
        pass  # es solo un aviso: si no se puede guardar, no se rompe nada
    return datos


def olvidar() -> None:
    """Borra lo guardado (la próxima comprobación sale a la red)."""
    try:
        archivo_de_estado().unlink()
    except OSError:
        pass


def _momento(texto: Any) -> Optional[datetime]:
    try:
        momento = datetime.fromisoformat(str(texto))
    except (TypeError, ValueError):
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------- la consulta


def _etiquetas(
    *,
    repositorio: Optional[str] = None,
    tiempo: float = _TIEMPO_DE_ESPERA,
    abrir: Optional[Callable[..., Any]] = None,
) -> List[str]:
    """Etiquetas del repositorio, de la más vieja a la más nueva (lanza si falla).

    Primero se pregunta a la API de GitHub y, si no responde (suele ser porque
    limita a 60 consultas por hora y dirección: con ``GITHUB_TOKEN`` en el entorno
    el límite es mucho mayor), se pregunta por ``git ls-remote``, que no limita.
    Con ``abrir`` propio —lo que usan las pruebas— se usa solo la API.
    """
    if abrir is not None:
        return _etiquetas_por_api(repositorio=repositorio, tiempo=tiempo, abrir=abrir)

    try:
        return _etiquetas_por_api(repositorio=repositorio, tiempo=tiempo, abrir=None)
    except Exception as exc_api:  # noqa: BLE001 - se prueba el otro camino
        try:
            return _etiquetas_por_git(repositorio=repositorio, tiempo=tiempo)
        except Exception as exc_git:  # noqa: BLE001 - ninguno de los dos respondió
            raise _ErrorDeConsulta(
                f"la API de GitHub falló ({exc_api}) y git tampoco respondió ({exc_git})"
            ) from exc_git


class _ErrorDeConsulta(RuntimeError):
    """No se ha podido preguntar si hay versiones nuevas."""


def _etiquetas_por_api(
    *,
    repositorio: Optional[str] = None,
    tiempo: float = _TIEMPO_DE_ESPERA,
    abrir: Optional[Callable[..., Any]] = None,
) -> List[str]:
    """Lee las etiquetas con la API de GitHub (60 consultas por hora sin credencial)."""
    if abrir is None:
        abrir = urllib.request.urlopen

    url = _URL_TAGS.format(repositorio=repositorio or repositorio_actual())
    cabeceras = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"factec/{version_instalada()}",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        # Con credencial, el límite por dirección sube de 60 a 5000 consultas/hora.
        cabeceras["Authorization"] = f"Bearer {token}"

    with abrir(urllib.request.Request(url, headers=cabeceras), timeout=tiempo) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))

    etiquetas = sorted(
        (str(etiqueta.get("name", "")).strip().lstrip("vV") for etiqueta in datos),
        key=normalizar_version,
    )
    etiquetas = [etiqueta for etiqueta in etiquetas if etiqueta]
    if not etiquetas:
        raise ValueError("El repositorio no tiene etiquetas de versión.")
    return etiquetas


def _etiquetas_por_git(
    *, repositorio: Optional[str] = None, tiempo: float = _TIEMPO_DE_ESPERA
) -> List[str]:
    """Lee las etiquetas con ``git ls-remote``, que no tiene límite de consultas."""
    url = f"https://github.com/{repositorio or repositorio_actual()}.git"
    entorno = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo")
    resultado = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", url],
        capture_output=True,
        text=True,
        timeout=max(tiempo, 10.0),
        env=entorno,
        check=True,
    )

    etiquetas = sorted(
        (
            linea.split("refs/tags/")[-1].strip().lstrip("vV")
            for linea in resultado.stdout.splitlines()
            if "refs/tags/" in linea
        ),
        key=normalizar_version,
    )
    etiquetas = [etiqueta for etiqueta in etiquetas if etiqueta]
    if not etiquetas:
        raise ValueError("El repositorio no tiene etiquetas de versión.")
    return etiquetas


def obtener_ultima(
    *,
    repositorio: Optional[str] = None,
    tiempo: float = _TIEMPO_DE_ESPERA,
    abrir: Optional[Callable[..., Any]] = None,
) -> str:
    """La versión más alta publicada en el repositorio (lanza si no se puede).

    Se lee la lista de etiquetas de GitHub (``v1.11.0``, ``v1.10.1``…). Con
    ``abrir`` se puede cambiar de dónde se lee: se usa en las pruebas para no
    depender de la red.
    """
    return _etiquetas(repositorio=repositorio, tiempo=tiempo, abrir=abrir)[-1]


@dataclass(frozen=True)
class InformeActualizacion:
    """Lo que se sabe de la versión, con un ``str()`` que se lee solo."""

    instalada: str
    ultima: Optional[str] = None
    comprobado: Optional[datetime] = None
    anterior: Optional[str] = None
    desde_guardado: bool = False
    error: str = ""
    etiquetas: Tuple[str, ...] = field(default_factory=tuple, repr=False)

    @property
    def hay_actualizacion(self) -> bool:
        """``True`` si hay una versión más nueva publicada."""
        return bool(self.ultima) and es_mas_nueva(str(self.ultima), self.instalada)

    @property
    def acaba_de_actualizarse(self) -> bool:
        """``True`` si esta versión es nueva respecto a la última vez."""
        return bool(self.anterior) and es_mas_nueva(self.instalada, str(self.anterior))

    @property
    def al_dia(self) -> bool:
        """``True`` si se comprobó y no hay nada más nuevo."""
        return bool(self.ultima) and not self.hay_actualizacion

    @property
    def caducado(self) -> bool:
        """``True`` si lo que se sabe tiene más de :data:`HORAS_DE_CADUCIDAD`."""
        if self.comprobado is None:
            return True
        return datetime.now(timezone.utc) - self.comprobado > timedelta(
            hours=HORAS_DE_CADUCIDAD
        )

    def __str__(self) -> str:
        if self.acaba_de_actualizarse:
            return f"factec actualizado de {self.anterior} a {self.instalada}"
        if self.hay_actualizacion:
            return f"hay una versión nueva de factec: {self.ultima} (tiene {self.instalada})"
        if self.al_dia:
            return f"factec {self.instalada} está al día"
        return f"factec {self.instalada} (no se ha podido comprobar: {self.error or 'sin datos'})"


def comprobar(
    *,
    forzar: bool = False,
    guardar_como: bool = True,
    repositorio: Optional[str] = None,
    tiempo: float = _TIEMPO_DE_ESPERA,
    abrir: Optional[Callable[..., Any]] = None,
) -> InformeActualizacion:
    """Dice si hay una versión nueva, usando lo guardado si es reciente.

    Con ``forzar=True`` se sale a la red aunque lo guardado sea de hoy. Si no hay
    internet (o GitHub responde cualquier cosa), no se lanza excepción: se devuelve
    lo último que se sabía y el motivo en ``error``.
    """
    instalada = version_instalada()
    datos = leer()
    anterior = str(datos["vista"]) if datos.get("vista") else None
    conocido = {
        "instalada": instalada,
        "ultima": datos.get("ultima"),
        "comprobado": _momento(datos.get("comprobado")),
        "anterior": anterior,
        "etiquetas": tuple(datos.get("etiquetas") or ()),
    }

    if not forzar and conocido["comprobado"] is not None:
        informe = InformeActualizacion(desde_guardado=True, **conocido)
        if not informe.caducado:
            return informe

    if sin_comprobar():
        return InformeActualizacion(
            instalada=instalada,
            ultima=conocido["ultima"],
            comprobado=conocido["comprobado"],
            anterior=anterior,
            desde_guardado=True,
            error="FACTEC_SIN_COMPROBAR está activo: no se sale a la red.",
        )

    momento = datetime.now(timezone.utc)
    try:
        ultima, etiquetas = _consultar(repositorio=repositorio, tiempo=tiempo, abrir=abrir)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo es "no se pudo comprobar"
        return InformeActualizacion(
            instalada=instalada,
            ultima=conocido["ultima"],
            comprobado=conocido["comprobado"],
            anterior=anterior,
            desde_guardado=True,
            error=f"{type(exc).__name__}: {exc}",
        )

    if guardar_como:
        guardar(comprobado=momento.isoformat(), ultima=ultima, etiquetas=list(etiquetas))

    return InformeActualizacion(
        instalada=instalada,
        ultima=ultima,
        comprobado=momento,
        anterior=anterior,
        etiquetas=tuple(etiquetas),
    )


def _consultar(
    *,
    repositorio: Optional[str] = None,
    tiempo: float,
    abrir: Optional[Callable[..., Any]],
) -> Tuple[str, List[str]]:
    """Lee las etiquetas del repositorio y devuelve la más alta con la lista."""
    etiquetas = _etiquetas(repositorio=repositorio, tiempo=tiempo, abrir=abrir)
    return etiquetas[-1], etiquetas


# ------------------------------------------------------------------ el recuadro


def _colores(flujo: TextIO) -> bool:
    """¿Se puede pintar? (hace falta una terminal y no vale ``NO_COLOR``)."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(flujo.isatty())
    except Exception:  # noqa: BLE001 - flujos sin isatty (redirecciones raras)
        return False


def _pinta(texto: str, estilo: str, color: bool) -> str:
    if not color:
        return texto
    return f"{_ESTILOS[estilo]}{texto}{_APAGADO}"


def _recuadro(lineas: List[str], *, estilo: str = "aviso", color: bool = False) -> str:
    """Recuadro de doble línea, con el ancho del contenido (estilo Flutter)."""
    contenido: List[str] = []
    for linea in lineas:
        if not linea:
            contenido.append("")
            continue
        contenido.extend(textwrap.wrap(linea, ANCHO) or [""])

    ancho = max(len(linea) for linea in contenido)
    ancho = max(ancho, 24)
    borde = _pinta("╔" + "═" * (ancho + 2) + "╗", estilo, color)
    pie = _pinta("╚" + "═" * (ancho + 2) + "╝", estilo, color)
    filas = [
        f"{_pinta('║', estilo, color)} {linea.ljust(ancho)} {_pinta('║', estilo, color)}"
        for linea in contenido
    ]
    return "\n".join([borde, *filas, pie])


def banner(informe: Optional[InformeActualizacion] = None, *, color: bool = False) -> str:
    """El texto del aviso: recuadro si hay novedad, una línea si está al día.

    Devuelve ``""`` cuando no hay nada que contar (sin datos y sin novedad), para
    que quien lo llame no tenga que adivinar.
    """
    informe = informe or comprobar()
    if informe.acaba_de_actualizarse:
        return _recuadro_actualizado(informe, color=color)
    if informe.hay_actualizacion:
        return _recuadro_nueva(informe, color=color)
    if informe.al_dia:
        return _linea_al_dia(informe, color=color)
    return ""


def _recuadro_nueva(informe: InformeActualizacion, *, color: bool) -> str:
    desde = informe.instalada
    hasta = str(informe.ultima)
    lineas = [
        "↑  Hay una versión nueva de factec",
        "",
        f"Instalada   {desde}",
        f"Disponible  {hasta}",
        "",
        "Para actualizar:",
        f"  {comando_para_actualizar()}",
        "",
        f"Qué cambia: {_URL_CAMBIOS.format(desde=desde, hasta=hasta, repositorio=repositorio_actual())}",
    ]
    return _recuadro(lineas, estilo="aviso", color=color)


def _recuadro_actualizado(informe: InformeActualizacion, *, color: bool) -> str:
    lineas = [
        f"✓  factec actualizado: {informe.anterior} → {informe.instalada}",
    ]
    if informe.hay_actualizacion:
        lineas += [
            "",
            f"Ya hay otra más nueva: {informe.ultima}",
            f"  {comando_para_actualizar()}",
        ]
    return _recuadro(lineas, estilo="bien", color=color)


def _linea_al_dia(informe: InformeActualizacion, *, color: bool) -> str:
    cuando = ""
    if informe.comprobado is not None:
        cuando = f" (comprobado el {informe.comprobado.astimezone().strftime('%d/%m/%Y a las %H:%M')})"
    return f"✓ factec {informe.instalada} · es la última versión{cuando}"


def avisar(
    informe: Optional[InformeActualizacion] = None,
    *,
    flujo: Optional[TextIO] = None,
    automatico: bool = False,
    forzar: bool = False,
    color: Optional[bool] = None,
) -> bool:
    """Imprime el aviso y devuelve si dijo algo.

    ``automatico=True`` es para las llamadas que hace el paquete por su cuenta (la
    consola, el arranque de Django): se calla si no hay novedad, para no repetir el
    «está al día» en cada comando. Con ``automatico=False`` (el comando explícito)
    siempre dice algo, aunque sea que no se pudo comprobar.
    """
    if sin_avisos():
        return False
    flujo = flujo if flujo is not None else sys.stderr
    informe = informe or comprobar(forzar=forzar)
    pintar = _colores(flujo) if color is None else color

    hay_novedad = informe.acaba_de_actualizarse or informe.hay_actualizacion
    if automatico and not hay_novedad:
        # Lo automático no repite «está al día» en cada comando: solo cuenta algo
        # cuando hay algo que contar.
        _recordar_vista()
        return False

    texto = banner(informe, color=pintar)
    if not texto:
        if not informe.error:
            _recordar_vista()
            return False
        texto = _pinta(
            f"· factec {informe.instalada}: no se pudo comprobar si hay novedades"
            f" ({informe.error})",
            "tenue",
            pintar,
        )

    print(texto, file=flujo, flush=True)
    _recordar_vista()
    return True


def _recordar_vista() -> None:
    """Apunta la versión que acaba de ver el usuario (para detectar el cambio)."""
    try:
        if leer().get("vista") != version_instalada():
            guardar(vista=version_instalada())
    except Exception:  # noqa: BLE001 - un aviso nunca puede romper nada
        pass


def avisar_en_arranque(*, flujo: Optional[TextIO] = None) -> bool:
    """Aviso del arranque de Django: solo si se acaba de actualizar el paquete.

    No sale a la red ni repite lo ya sabido: si no hay constancia de que esta
    versión sea nueva respecto a la última vista, no imprime nada. Además solo
    escribe cuando hay una terminal detrás, para no ensuciar los registros del
    servidor.
    """
    try:
        flujo = flujo if flujo is not None else sys.stderr
        if sin_avisos() or not _colores(flujo):
            return False
        informe = comprobar()
        if not informe.acaba_de_actualizarse:
            _recordar_vista()
            return False
        return avisar(informe, flujo=flujo, automatico=True)
    except Exception:  # noqa: BLE001 - jamás un aviso puede impedir arrancar
        return False
