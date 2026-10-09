"""Comprobaciones del sistema de Django para la integración con el SRI.

Se ejecutan con ``manage.py check`` y, por tanto, también antes de ``migrate`` y
``runserver``. Así, en cuanto la app se añade a ``INSTALLED_APPS``, Django avisa
de lo que falta indicando exactamente dónde completarlo (el admin).

Se usa ``Warning`` cuando aún no hay configuración (para no impedir el arranque:
primero hay que entrar al admin a rellenarla) y ``Error`` cuando hay algo
configurado pero incorrecto, que sí impediría emitir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from django.core.checks import Error, Warning, register

from ..excepciones import ErrorFacturacion
from ..revision import DIAS_AVISO_CERTIFICADO, revisar_certificado
from . import conf

__all__ = ["comprobar_configuracion"]

ETIQUETA = "factec"

ID_EMISOR = "sri_fe.W001"
ID_CERTIFICADO_FALTA = "sri_fe.W002"
ID_CLAVE_FALTA = "sri_fe.W003"
ID_CLAVE_CIFRADO = "sri_fe.E004"
ID_CERTIFICADO_INVALIDO = "sri_fe.E005"
ID_VIGENCIA = "sri_fe.E006"
ID_VIGENCIA_AVISO = "sri_fe.W010"
ID_RUC = "sri_fe.E007"
ID_TABLAS = "sri_fe.W008"
ID_ACTIVA = "sri_fe.E009"

AYUDA_ADMIN = "Admin de Django → Facturación electrónica (SRI) → Configuraciones del emisor."


@register(ETIQUETA)
def comprobar_configuracion(app_configs: Any = None, **kwargs: Any) -> List[Any]:
    """Valida que la app tenga lo necesario para emitir comprobantes."""
    problemas: List[Any] = []

    try:
        configuracion = conf.ajustes()
    except Exception as exc:  # noqa: BLE001 - el ajuste puede estar mal formado
        return [
            Error(
                f"La configuración de facturación electrónica no se pudo leer: {exc}",
                hint="Revise settings.FACTURACION_ELECTRONICA.",
                id=ID_EMISOR,
            )
        ]

    # Si la clave de cifrado falta y además no hay contraseña guardada, el problema
    # se reporta más abajo; aquí solo se avisa cuando hará falta de verdad.
    activa = conf.configuracion_activa()

    if activa is None:
        emisor_ajustes = configuracion.get("EMISOR")
        if not emisor_ajustes:
            # Si hay configuraciones pero ninguna activa, el motivo es otro.
            try:
                from .models import ConfiguracionEmisor

                hay_configuraciones = ConfiguracionEmisor.objects.exists()
            except Exception:  # noqa: BLE001 - tabla inexistente
                hay_configuraciones = False

            if hay_configuraciones:
                if _esquema_desactualizado():
                    problemas.append(
                        Warning(
                            "La base de datos no está al día: faltan columnas por migrar.",
                            hint="Ejecute: python manage.py migrate --skip-checks",
                            id=ID_TABLAS,
                        )
                    )
                    return problemas

                problemas.append(
                    Error(
                        "Hay configuraciones del emisor, pero ninguna está activa.",
                        hint=(
                            "Marque «activa» en la que deba usarse. Sin una "
                            "configuración activa no se puede emitir."
                        ),
                        id=ID_ACTIVA,
                    )
                )
                return problemas

            problemas.append(
                Warning(
                    "No hay ninguna configuración del emisor activa.",
                    hint=(
                        "Ejecute las migraciones (python manage.py migrate) y cree una "
                        f"configuración con el certificado. {AYUDA_ADMIN}"
                    ),
                    id=ID_EMISOR,
                )
            )
            problemas.append(
                Warning(
                    "No se ha cargado ningún archivo de firma electrónica (.p12).",
                    hint=f"Adjúntelo en el admin. {AYUDA_ADMIN}",
                    id=ID_CERTIFICADO_FALTA,
                )
            )
            problemas.append(
                Warning(
                    "No hay contraseña de certificado configurada.",
                    hint=f"Escríbala en el admin (se guarda cifrada). {AYUDA_ADMIN}",
                    id=ID_CLAVE_FALTA,
                )
            )
            return problemas

        return _comprobar_desde_ajustes(configuracion, problemas)

    # --- hay configuración en la base de datos -----------------------------
    try:
        datos = activa.a_emisor()
        datos.validar()
    except ErrorFacturacion as exc:
        problemas.append(
            Error(
                f"Los datos del emisor «{activa}» no son válidos: {exc}",
                hint=AYUDA_ADMIN,
                id=ID_EMISOR,
            )
        )

    if not activa.certificado:
        problemas.append(
            Warning(
                f"La configuración «{activa}» no tiene archivo de firma (.p12).",
                hint=f"Adjúntelo en el campo «archivo de firma». {AYUDA_ADMIN}",
                id=ID_CERTIFICADO_FALTA,
            )
        )
    if not activa.clave_certificado_cifrada:
        problemas.append(
            Warning(
                f"La configuración «{activa}» no tiene contraseña del certificado.",
                hint=f"Escríbala en el campo «contraseña del certificado». {AYUDA_ADMIN}",
                id=ID_CLAVE_FALTA,
            )
        )

    if activa.certificado and activa.clave_certificado_cifrada:
        _comprobar_certificado(activa, problemas)

    return problemas


def _esquema_desactualizado() -> bool:
    """¿La tabla de configuraciones no tiene todas las columnas del modelo?

    ``migrate`` ejecuta los chequeos **antes** de crear las columnas nuevas. Sin
    esta comprobación, la consulta de la configuración activa fallaría al leer una
    columna que todavía no existe y el admin diría, por error, que ninguna está
    activa; el mensaje correcto es que hay que terminar de migrar.
    """
    from django.db import connection

    from .models import ConfiguracionEmisor

    try:
        with connection.cursor() as cursor:
            descripcion = connection.introspection.get_table_description(
                cursor, ConfiguracionEmisor._meta.db_table
            )
    except Exception:  # noqa: BLE001 - todavía no existe la tabla
        return False

    presentes = {columna.name for columna in descripcion}
    esperadas = {campo.column for campo in ConfiguracionEmisor._meta.local_fields}
    return not esperadas <= presentes


def _comprobar_desde_ajustes(configuracion: dict, problemas: List[Any]) -> List[Any]:
    """Comprueba la configuración de ``settings`` (sin base de datos)."""
    ruta = configuracion.get("CERTIFICADO")
    if not ruta:
        problemas.append(
            Warning(
                "No hay certificado de firma configurado.",
                hint="Defina FACTURACION_ELECTRONICA['CERTIFICADO'].",
                id=ID_CERTIFICADO_FALTA,
            )
        )
    elif not Path(ruta).exists():
        problemas.append(
            Error(
                f"El archivo de certificado no existe: {ruta}",
                hint="Corrija la ruta en FACTURACION_ELECTRONICA['CERTIFICADO'].",
                id=ID_CERTIFICADO_INVALIDO,
            )
        )
    return problemas


def _comprobar_certificado(activa: Any, problemas: List[Any]) -> None:
    """Abre el certificado y comprueba vigencia, titular y RUC.

    Un certificado vencido es un ``Error`` (no un aviso): con él no se puede emitir
    nada, el SRI devuelve los comprobantes.
    """
    try:
        certificado = activa.certificado_obj(validar_vigencia=False)
    except ErrorFacturacion as exc:
        problemas.append(
            Error(
                f"No se pudo abrir el certificado de «{activa}»: {exc}",
                hint=(
                    "Suele ser la contraseña incorrecta, la clave de cifrado "
                    f"(SRI_CLAVE_CIFRADO) equivocada, o el archivo dañado. {AYUDA_ADMIN}"
                ),
                id=ID_CERTIFICADO_INVALIDO,
            )
        )
        return
    except Exception as exc:  # noqa: BLE001 - falta la clave de cifrado, etc.
        problemas.append(
            Error(
                f"No se pudo descifrar la contraseña del certificado de «{activa}»: {exc}",
                hint=AYUDA_ADMIN,
                id=ID_CLAVE_CIFRADO,
            )
        )
        return

    revisado = revisar_certificado(
        certificado,
        emisor=activa.a_emisor(),
        dias_aviso=int(conf.obtener("DIAS_AVISO_CERTIFICADO", DIAS_AVISO_CERTIFICADO)),
    )

    if not revisado.vigente:
        for texto in revisado.problemas or [
            "El certificado está vencido o aún no es válido."
        ]:
            problemas.append(
                Error(
                    f"«{activa}»: {texto}",
                    hint=(
                        "Mientras el certificado no sea válido no se puede emitir: "
                        "renueve su firma electrónica y vuelva a cargarla en el admin. "
                        f"{AYUDA_ADMIN}"
                    ),
                    id=ID_VIGENCIA,
                )
            )
        return

    for texto in revisado.avisos:
        problemas.append(
            Warning(f"«{activa}»: {texto}", hint=AYUDA_ADMIN, id=ID_VIGENCIA_AVISO)
        )

    if revisado.ruc and activa.ruc and revisado.ruc != activa.ruc:
        problemas.append(
            Error(
                f"El RUC del certificado ({revisado.ruc}) no coincide con el RUC del emisor "
                f"({activa.ruc}).",
                hint=(
                    "El SRI rechaza los comprobantes firmados por otro contribuyente. "
                    f"Cargue el certificado correcto. {AYUDA_ADMIN}"
                ),
                id=ID_RUC,
            )
        )
