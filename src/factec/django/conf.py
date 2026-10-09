"""Resolución de la configuración de facturación electrónica en Django.

Orden de búsqueda:

1. La ``ConfiguracionEmisor`` **activa** en la base de datos (lo normal: se
   gestiona desde el admin).
2. ``settings.FACTURACION_ELECTRONICA`` y las variables de entorno, como respaldo
   para cuando la tabla aún no existe (por ejemplo, antes de ``migrate``) o para
   despliegues sin acceso al admin.

La clave de cifrado y la contraseña del certificado se leen siempre del entorno o
de los ajustes, nunca en claro desde la base de datos.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, Optional

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from ..catalogos import Ambiente, leer_ambiente
from ..excepciones import ErrorValidacion
from ..modelos import Emisor
from ..revision import DIAS_AVISO_CERTIFICADO

__all__ = [
    "AJUSTES_POR_DEFECTO",
    "ajustes",
    "obtener",
    "planificador",
    "configuracion_activa",
    "emisor",
    "certificado",
    "cliente",
    "clave_certificado",
    "clave_cifrado",
    "ambiente",
    "limpiar_cache",
    "nombre_tarea",
]

AJUSTES_POR_DEFECTO: Dict[str, Any] = {
    #: «pruebas» (1) o «producción» (2): se admite el nombre o el número.
    "AMBIENTE": int(Ambiente.PRUEBAS),
    "CERTIFICADO": None,
    "CLAVE_CERTIFICADO": None,
    "CLAVE_CIFRADO": None,
    "EMISOR": None,
    "REINTENTOS_AUTORIZACION": 6,
    "ESPERA_AUTORIZACION": 4.0,
    "GUARDAR_XML": True,
    #: Guardar los XML y las respuestas del SRI como archivos en MEDIA_ROOT.
    "GUARDAR_ARCHIVOS": True,
    "CELERY_QUEUE": None,
    "CELERY_PREFIX": "sri_fe",
    "TIMEOUT": 30.0,
    "TIMEOUT_CONSULTA_SRI": 15.0,
    "CONSULTAR_SRI_AUTOMATICAMENTE": True,
    #: Si es True, emitir() encola en Celery; si False, procesa en el acto.
    "EMITIR_CON_CELERY": True,
    "VALIDAR_VIGENCIA": True,
    "ALGORITMO_FIRMA": "sha1",
    "USAR_BASE_DE_DATOS": True,
    #: Revisar el certificado y los datos antes de firmar y enviar: si algo
    #: fallaría en el SRI, no se emite y se informa del motivo.
    "REVISAR_ANTES_DE_EMITIR": True,
    #: Días de antelación con los que se avisa de que la firma va a vencer.
    "DIAS_AVISO_CERTIFICADO": DIAS_AVISO_CERTIFICADO,
    #: Emitir con la fecha del día en que se firma (el SRI solo admite la fecha
    #: del día o de los 90 días anteriores).
    "FECHA_EMISION_AL_EMITIR": True,
    #: Correos a los que avisar cuando la revisión periódica encuentre problemas.
    "CORREOS_AVISO": [],
    #: Filtros, búsquedas y columnas que la tienda añade al admin (ver
    #: :mod:`factec.django.admin_filtros`).
    "ADMIN": {},
}

_VARIABLES_ENTORNO = {
    "SRI_AMBIENTE": "AMBIENTE",
    "SRI_CERTIFICADO": "CERTIFICADO",
    "SRI_CLAVE_CERTIFICADO": "CLAVE_CERTIFICADO",
    "SRI_CLAVE_CIFRADO": "CLAVE_CIFRADO",
    "SRI_TIMEOUT": "TIMEOUT",
    "SRI_ALGORITMO_FIRMA": "ALGORITMO_FIRMA",
}

PREFIJO_TAREAS_POR_DEFECTO = "sri_fe"


def ajustes() -> Dict[str, Any]:
    """Devuelve los ajustes resueltos (settings + variables de entorno)."""
    from django.conf import settings as django_settings

    configuracion = dict(AJUSTES_POR_DEFECTO)
    propios = getattr(django_settings, "FACTURACION_ELECTRONICA", None) or {}
    if not isinstance(propios, dict):
        raise ImproperlyConfigured("settings.FACTURACION_ELECTRONICA debe ser un diccionario.")
    configuracion.update({k: v for k, v in propios.items() if v is not None})

    for variable, clave in _VARIABLES_ENTORNO.items():
        valor = os.environ.get(variable)
        if valor not in (None, ""):
            configuracion[clave] = valor

    # «pruebas» y «producción» valen igual que 1 y 2.
    configuracion["AMBIENTE"] = leer_ambiente(configuracion["AMBIENTE"])
    configuracion["REINTENTOS_AUTORIZACION"] = int(configuracion["REINTENTOS_AUTORIZACION"])
    configuracion["ESPERA_AUTORIZACION"] = float(configuracion["ESPERA_AUTORIZACION"])
    configuracion["TIMEOUT"] = float(configuracion["TIMEOUT"])
    return configuracion


def obtener(nombre: str, por_defecto: Any = None) -> Any:
    """Devuelve un ajuste concreto."""
    return ajustes().get(nombre, por_defecto)


def clave_cifrado() -> str:
    """Clave Fernet con la que se cifra la contraseña del certificado."""
    clave = obtener("CLAVE_CIFRADO")
    if not clave:
        raise ImproperlyConfigured(
            "Falta la clave de cifrado de secretos. Genérela con:\n"
            '  python -c "from factec.django.crypto '
            'import generar_clave; print(generar_clave())"\n'
            "y defínala en FACTURACION_ELECTRONICA['CLAVE_CIFRADO'] o en la variable "
            "de entorno SRI_CLAVE_CIFRADO (recomendado)."
        )
    return str(clave)


# ------------------------------------------------------------- base de datos


def configuracion_activa(ambiente: Optional[int] = None) -> Optional[Any]:
    """Devuelve la ``ConfiguracionEmisor`` activa, o ``None``.

    Nunca lanza excepción: si la tabla todavía no existe (antes de ``migrate``)
    devuelve ``None`` para que se use la configuración de ``settings``.
    """
    if not obtener("USAR_BASE_DE_DATOS", True):
        return None

    try:
        from .models import ConfiguracionEmisor

        consulta = ConfiguracionEmisor.objects.filter(activo=True)
        if ambiente is not None:
            consulta = consulta.filter(ambiente=int(ambiente))
        return consulta.order_by("-ambiente").first()
    except Exception:  # noqa: BLE001 - tabla inexistente, apps sin cargar, etc.
        return None


def ambiente() -> int:
    """Ambiente configurado: el de la configuración activa o el de los ajustes."""
    activa = configuracion_activa()
    if activa is not None:
        return int(activa.ambiente)
    return leer_ambiente(obtener("AMBIENTE", int(Ambiente.PRUEBAS)))


# --------------------------------------------------------------- resolución


def _resolver_emisor(valor: Any) -> Emisor:
    if isinstance(valor, Emisor):
        return valor
    if isinstance(valor, str):
        valor = import_string(valor)()
    if callable(valor):
        valor = valor()
    if isinstance(valor, dict):
        return Emisor(**valor)
    raise ImproperlyConfigured(
        "FACTURACION_ELECTRONICA['EMISOR'] debe ser un diccionario, un Emisor o una "
        "ruta importable que devuelva uno de los dos."
    )


def emisor() -> Emisor:
    """Emisor configurado, desde la base de datos o desde los ajustes."""
    activa = configuracion_activa()
    if activa is not None:
        return activa.a_emisor()

    valor = obtener("EMISOR")
    if valor is None:
        raise ImproperlyConfigured(
            "No hay ningún emisor configurado. Cree una «configuración del emisor» en "
            "el admin de Django (o defina FACTURACION_ELECTRONICA['EMISOR'])."
        )
    emisor_obj = _resolver_emisor(valor)
    if not emisor_obj.ruc:
        raise ErrorValidacion("El emisor configurado no tiene RUC.")
    return emisor_obj


def clave_certificado() -> str:
    """Contraseña del certificado: de la configuración activa o de los ajustes."""
    activa = configuracion_activa()
    if activa is not None and activa.clave_certificado_cifrada:
        return activa.obtener_clave()

    directa = obtener("CLAVE_CERTIFICADO")
    if directa:
        return str(directa)

    raise ImproperlyConfigured(
        "Falta la contraseña del certificado. Escríbala en la «configuración del "
        "emisor» del admin o defina SRI_CLAVE_CERTIFICADO."
    )


@lru_cache(maxsize=1)
def certificado():
    """Certificado de firma: el de la configuración activa o el de los ajustes."""
    from ..firma import Certificado

    activa = configuracion_activa()
    if activa is not None and activa.certificado:
        return activa.certificado_obj(validar_vigencia=bool(obtener("VALIDAR_VIGENCIA", True)))

    ruta = obtener("CERTIFICADO")
    if not ruta:
        raise ImproperlyConfigured(
            "No hay certificado de firma. Súbalo en la «configuración del emisor» del "
            "admin (o defina FACTURACION_ELECTRONICA['CERTIFICADO'])."
        )
    certificado_obj = Certificado.desde_archivo(ruta, clave_certificado())
    if obtener("VALIDAR_VIGENCIA", True):
        certificado_obj.validar_vigencia()
    return certificado_obj


@lru_cache(maxsize=1)
def cliente():
    """Cliente SOAP del SRI (memorizado)."""
    from ..sri import ClienteSRI

    return ClienteSRI(ambiente=ambiente(), timeout=obtener("TIMEOUT"))


def limpiar_cache() -> None:
    """Descarta cliente y certificado memorizados (útil al editar la configuración).

    Es tolerante: si alguna de las dos está sustituida por una función simple (por
    ejemplo en pruebas), simplemente se omite.
    """
    for funcion in (certificado, cliente):
        vaciar = getattr(funcion, "cache_clear", None)
        if callable(vaciar):
            vaciar()


def celery_queue() -> Optional[str]:
    """Cola de Celery donde encolar las tareas (``None`` = cola por defecto)."""
    return obtener("CELERY_QUEUE")


def planificador(
    *,
    a_las: int = 7,
    minuto: int = 0,
    cada_pendientes: float = 600.0,
) -> Dict[str, Any]:
    """Tareas periódicas de Celery listas para ``CELERY_BEAT_SCHEDULE``.

    Con esto queda **programada** en el paquete la revisión del certificado (todos
    los días a la hora indicada) y el reintento de los comprobantes que quedaron a
    medias (cada diez minutos)::

        # settings.py
        from factec.django.conf import planificador

        CELERY_BEAT_SCHEDULE = {**planificador()}                 # a las 7:00
        CELERY_BEAT_SCHEDULE = {**planificador(a_las=6, minuto=30)}

    La hora se interpreta en la zona del proyecto (``CELERY_TIMEZONE``, que conviene
    poner igual que ``TIME_ZONE``). Si Celery no está instalado se devuelve el
    intervalo en segundos, para que los ajustes nunca fallen al leerse.
    """
    revisar: Dict[str, Any] = {
        "task": nombre_tarea("revisar_certificado"),
    }
    try:
        from celery.schedules import crontab
    except ImportError:  # sin Celery: cada 24 horas desde que arranca beat
        revisar["schedule"] = 86400.0
    else:
        revisar["schedule"] = crontab(minute=int(minuto), hour=int(a_las))

    return {
        nombre_tarea("revisar_certificado"): revisar,
        nombre_tarea("reintentar_pendientes"): {
            "task": nombre_tarea("reintentar_pendientes"),
            "schedule": cada_pendientes,
        },
    }


def nombre_tarea(clave: str) -> str:
    """Nombre completo de una tarea, con el prefijo configurado.

    No lanza excepción si los ajustes aún no están disponibles: se usa en los
    decoradores de Celery, que pueden evaluarse muy pronto.
    """
    prefijo = PREFIJO_TAREAS_POR_DEFECTO
    try:
        prefijo = str(obtener("CELERY_PREFIX") or PREFIJO_TAREAS_POR_DEFECTO)
    except Exception:  # noqa: BLE001 - importación temprana sin settings
        pass
    return f"{prefijo}.{clave}"
