"""Los ajustes de la emisión, en la tabla del propio paquete.

Lo que antes había que escribir en ``settings.py``::

    FACTURACION_ELECTRONICA = {
        "AMBIENTE": "pruebas",
        "GUARDAR_XML": True,
        "GUARDAR_ARCHIVOS": True,
    }

ahora son filas de ``sri_fe_ajustedelsri``: el paquete se lleva su tabla, como
cualquier aplicación, así que funciona igual con o sin ``personalize_options``
puesto. Este módulo los **declara** (clave, valor de serie y para qué sirve), la
señal ``post_migrate`` los siembra y :func:`factec.django.conf.ajustes` los lee.

De la declaración viene todo menos el valor: la clave, el tipo, el grupo y la
descripción se refrescan al migrar y no se escriben a mano; lo que se cambia —en
el admin— es el valor.

La **clave de cifrado** es un ajuste de sólo lectura: no se enseña ni se edita en
el admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional, Sequence, Tuple

__all__ = [
    "AjusteDeSerie",
    "CLAVES_DE_CONF",
    "DE_SERIE",
    "declarados",
    "declarar",
    "de_la_base",
    "poner",
    "sembrar",
    "sembrar_al_migrar",
]

#: Verdadero tal como se escribe en la base.
_SI = ("1", "si", "sí", "true", "verdadero", "on", "yes")

#: Cómo se llaman en ``conf.ajustes`` las claves guardadas como ``sri_…``.
CLAVES_DE_CONF = {
    "sri_ambiente": "AMBIENTE",
    "sri_guardar_xml": "GUARDAR_XML",
    "sri_guardar_archivos": "GUARDAR_ARCHIVOS",
    "sri_clave_cifrado": "CLAVE_CIFRADO",
}


@dataclass(frozen=True)
class AjusteDeSerie:
    """Un ajuste que declara el paquete: su valor de serie y para qué sirve."""

    clave: str
    valor: str
    tipo: str = "texto"
    grupo: str = "Emisión"
    descripcion: str = ""
    orden: int = 0
    editable: bool = True


#: Los ajustes de la emisión: lo que se puede cambiar sin tocar código.
DE_SERIE: Tuple[AjusteDeSerie, ...] = (
    AjusteDeSerie(
        "sri_ambiente",
        "pruebas",
        grupo="Emisión",
        orden=1,
        descripcion="«pruebas»: los comprobantes no tienen validez fiscal. «produccion»: sí.",
    ),
    AjusteDeSerie(
        "sri_guardar_xml",
        "si",
        tipo="booleano",
        grupo="Emisión",
        orden=2,
        descripcion="Guardar el XML firmado en la base de datos, junto al comprobante.",
    ),
    AjusteDeSerie(
        "sri_guardar_archivos",
        "si",
        tipo="booleano",
        grupo="Emisión",
        orden=3,
        descripcion="Guardar además los XML y las respuestas del SRI como archivos.",
    ),
    AjusteDeSerie(
        "sri_clave_cifrado",
        "",
        grupo="Seguridad",
        orden=9,
        editable=False,
        descripcion=(
            "Clave con la que se cifra la contraseña del certificado. No se enseña "
            "ni se edita desde el admin."
        ),
    ),
)

#: Lo declarado (empieza con los de serie; ``declarar()`` puede añadir más).
_REGISTRO: Dict[str, AjusteDeSerie] = {ajuste.clave: ajuste for ajuste in DE_SERIE}


# ---------------------------------------------------------------- declarar


def declarar(*nuevos: AjusteDeSerie) -> None:
    """Declara ajustes de la emisión (los de serie ya lo están).

    La llama el ``ready()`` de la app, así que al migrar quedan sembrados.
    """
    for ajuste in nuevos:
        _REGISTRO[ajuste.clave] = ajuste
    _leidos_de_la_base.cache_clear()


def declarados() -> Sequence[AjusteDeSerie]:
    """Los ajustes declarados, en el orden en que se enseñan."""
    return tuple(
        sorted(
            _REGISTRO.values(),
            key=lambda ajuste: (ajuste.grupo, ajuste.orden, ajuste.clave),
        )
    )


# -------------------------------------------------------------------- leer


@lru_cache(maxsize=1)
def _leidos_de_la_base() -> Dict[str, str]:
    from django.db.utils import DatabaseError

    try:
        from .models import AjusteDelSri

        return {ajuste.clave: ajuste.valor for ajuste in AjusteDelSri.objects.all()}
    except (DatabaseError, ImportError):  # pragma: no cover - antes de migrar
        return {}


def de_la_base() -> Dict[str, Any]:
    """Los ajustes guardados, con las claves y los tipos de ``conf.ajustes``."""
    guardados = _leidos_de_la_base()
    if not guardados:
        return {}

    def si_no(clave: str, por_defecto: bool) -> bool:
        if clave not in guardados:
            return por_defecto
        return str(guardados[clave]).strip().lower() in _SI

    valores: Dict[str, Any] = {
        "AMBIENTE": str(guardados.get("sri_ambiente", "pruebas")).strip(),
        "GUARDAR_XML": si_no("sri_guardar_xml", True),
        "GUARDAR_ARCHIVOS": si_no("sri_guardar_archivos", True),
    }
    clave_cifrado = str(guardados.get("sri_clave_cifrado", "")).strip()
    if clave_cifrado:
        valores["CLAVE_CIFRADO"] = clave_cifrado

    # Cualquier otra clave guardada (``sri_x`` o ``x``) pasa tal cual.
    for clave, valor in guardados.items():
        if clave in CLAVES_DE_CONF:
            continue
        valores[clave.upper()] = valor

    return valores


def valor(clave: str, por_defecto: Any = None) -> Optional[str]:
    """El valor guardado de ese ajuste, o el de serie."""
    guardados = _leidos_de_la_base()
    if clave in guardados:
        return guardados[clave]
    declarado = _REGISTRO.get(clave)
    return declarado.valor if declarado else por_defecto


# --------------------------------------------------------------- escribir


def poner(clave: str, nuevo: Any) -> None:
    """Guarda un ajuste (lo crea si no está) y refresca lo que se lee."""
    from .models import AjusteDelSri

    declarado = _REGISTRO.get(clave)
    if isinstance(nuevo, bool):
        nuevo = "si" if nuevo else "no"
    AjusteDelSri.objects.update_or_create(
        clave=clave,
        defaults={
            "valor": str(nuevo),
            "tipo": declarado.tipo if declarado else "texto",
            "grupo": declarado.grupo if declarado else "Emisión",
            "descripcion": declarado.descripcion if declarado else "",
            "orden": declarado.orden if declarado else 0,
            "editable": declarado.editable if declarado else True,
        },
    )
    _leidos_de_la_base.cache_clear()


def sembrar(usando: str = "default") -> int:
    """Crea los ajustes declarados que falten. Nunca pisa el valor.

    Lo llama la señal ``post_migrate``: al terminar ``migrate`` quedan sembrados.
    A los que ya están se les refresca lo que viene de la declaración —el tipo, el
    grupo, para qué sirve— pero **no** el valor, que es lo que alguien cambió.
    """
    from .models import AjusteDelSri

    existentes = set(AjusteDelSri.objects.using(usando).values_list("clave", flat=True))
    creados = 0
    for declarado in _REGISTRO.values():
        if declarado.clave in existentes:
            AjusteDelSri.objects.using(usando).filter(clave=declarado.clave).update(
                tipo=declarado.tipo,
                grupo=declarado.grupo,
                descripcion=declarado.descripcion,
                orden=declarado.orden,
                editable=declarado.editable,
            )
            continue
        AjusteDelSri.objects.using(usando).create(
            clave=declarado.clave,
            valor=declarado.valor,
            tipo=declarado.tipo,
            grupo=declarado.grupo,
            descripcion=declarado.descripcion,
            orden=declarado.orden,
            editable=declarado.editable,
        )
        creados += 1
    _leidos_de_la_base.cache_clear()
    return creados


def sembrar_al_migrar(
    sender: Any = None, app_config: Any = None, using: str = "default", **kwargs: Any
) -> None:
    """La señal ``post_migrate``: deja los ajustes de serie en la base."""
    sembrar(usando=using)
