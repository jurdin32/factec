"""Campos adicionales que cada tienda define a su gusto.

El SRI admite dos secciones libres: ``detallesAdicionales`` (``detAdicional`` en
cada línea, máximo 3) e ``infoAdicional`` (``campoAdicional`` en el comprobante,
máximo 15). Este módulo las edita con la misma sintaxis sencilla en el admin::

    MARCA=ACME; LOTE=2026-01

Es decir, pares ``NOMBRE=VALOR`` separados por ``;`` (o por renglones). Así la
tienda añade los campos que quiera —vendedor, sucursal, orden de compra…— sin
tocar el código del paquete.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from django.core.exceptions import ValidationError

__all__ = [
    "MAXIMO_CAMPOS_ADICIONALES",
    "MAXIMO_DATOS_ADICIONALES",
    "LONGITUD_CAMPO_ADICIONAL",
    "leer_campos_adicionales",
    "escribir_campos_adicionales",
    "validar_cantidad",
]

#: El esquema del SRI admite máximo tres ``detAdicional`` por línea.
MAXIMO_DATOS_ADICIONALES = 3

#: El esquema del SRI admite máximo quince ``campoAdicional`` por comprobante.
MAXIMO_CAMPOS_ADICIONALES = 15

#: ``nombre`` y ``valor`` de un campo adicional: hasta 300 caracteres.
LONGITUD_CAMPO_ADICIONAL = 300

#: Separa los pares; sirve tanto el punto y coma como el salto de línea.
SEPARADOR = ";"


def leer_campos_adicionales(valor: Any) -> Dict[str, str]:
    """Interpreta ``NOMBRE=VALOR; OTRO=VALOR`` como diccionario.

    Admite además un diccionario ya hecho, para que sirva igual cuando el
    proyecto guarda los campos adicionales en un ``JSONField``.
    """
    if not valor:
        return {}
    if isinstance(valor, Mapping):
        return {
            str(nombre).strip(): str(dato).strip()
            for nombre, dato in valor.items()
            if str(dato).strip()
        }

    campos: Dict[str, str] = {}
    texto = str(valor).replace("\r\n", "\n").replace("\r", "\n").replace("\n", SEPARADOR)
    for trozo in texto.split(SEPARADOR):
        trozo = trozo.strip()
        if not trozo:
            continue
        nombre, separador, dato = trozo.partition("=")
        nombre, dato = nombre.strip(), dato.strip()
        if not separador or not nombre or not dato:
            raise ValidationError(
                f"«{trozo}» no tiene el formato NOMBRE=VALOR "
                "(por ejemplo: MARCA=ACME; LOTE=2026-01)."
            )
        if len(nombre) > LONGITUD_CAMPO_ADICIONAL or len(dato) > LONGITUD_CAMPO_ADICIONAL:
            raise ValidationError(
                f"«{nombre}» supera los {LONGITUD_CAMPO_ADICIONAL} caracteres que admite el SRI."
            )
        campos[nombre] = dato
    return campos


def escribir_campos_adicionales(campos: Mapping[str, Any]) -> str:
    """Devuelve el texto ``NOMBRE=VALOR; …`` que se edita en el admin."""
    return f"{SEPARADOR} ".join(f"{nombre}={dato}" for nombre, dato in campos.items())


def validar_cantidad(campos: Mapping[str, Any], maximo: int, destino: str) -> None:
    """Comprueba que no se pasen del máximo que admite el esquema."""
    if len(campos) > maximo:
        raise ValidationError(
            f"{destino} admite máximo {maximo} campos adicionales; se escribieron {len(campos)}."
        )
