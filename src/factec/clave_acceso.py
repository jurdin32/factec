"""Generación y validación de la clave de acceso del SRI.

La clave de acceso es un número de **49 dígitos** con esta estructura:

======  =====  ==================================================
Posición  Largo  Campo
======  =====  ==================================================
1-8       8     ``fechaEmision`` en formato ``ddMMyyyy``
9-10      2     ``tipoComprobante`` (tabla 1 del SRI)
11-23    13     ``numeroRuc`` del emisor
24        1     ``tipoAmbiente`` (1 pruebas, 2 producción)
25-30     6     ``serie`` = establecimiento (3) + punto de emisión (3)
31-39     9     ``numeroSecuencial``
40-47     8     ``codigoNumerico``
48        1     ``tipoEmision`` (1 normal, 2 contingencia)
49        1     dígito verificador (módulo 11)
======  =====  ==================================================

El dígito verificador se calcula con el algoritmo **módulo 11** de pesos
``2,3,4,5,6,7`` aplicado de derecha a izquierda sobre los primeros 48 dígitos.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Union

from .catalogos import Ambiente, TipoEmision, codigo_documento
from .excepciones import ErrorValidacion

__all__ = [
    "LARGO_CLAVE_ACCESO",
    "calcular_digito_verificador",
    "generar_clave_acceso",
    "validar_clave_acceso",
    "descomponer_clave_acceso",
    "normalizar_serie",
    "normalizar_secuencial",
    "normalizar_codigo_numerico",
]

LARGO_CLAVE_ACCESO = 49
_PESOS = (2, 3, 4, 5, 6, 7)

_FECHA_ACCESO = re.compile(r"^\d{8}$")


def _solo_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _fecha_ddmmaaaa(fecha: Union[date, datetime, str]) -> str:
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    if isinstance(fecha, date):
        return fecha.strftime("%d%m%Y")
    texto = str(fecha).strip()
    texto = texto.split("T")[0].split(" ")[0]
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d%m%Y"):
        try:
            return datetime.strptime(texto, formato).strftime("%d%m%Y")
        except ValueError:
            continue
    if _FECHA_ACCESO.match(texto):
        return texto
    raise ErrorValidacion(f"Fecha de emisión no reconocida: {fecha!r}")


def calcular_digito_verificador(cadena: str) -> str:
    """Devuelve el dígito verificador módulo 11 de ``cadena``.

    Se recorren los dígitos de derecha a izquierda multiplicando por los pesos
    ``2,3,4,5,6,7`` cíclicamente.
    """
    if not cadena or not cadena.isdigit():
        raise ErrorValidacion("El dígito verificador solo se calcula sobre dígitos.")
    suma = 0
    peso = 2
    for digito in reversed(cadena):
        suma += int(digito) * peso
        peso = 2 if peso == 7 else peso + 1
    resto = suma % 11
    verificador = 11 - resto
    if verificador == 11:
        return "0"
    if verificador == 10:
        return "1"
    return str(verificador)


def normalizar_serie(serie: Any) -> str:
    """Normaliza ``estab`` + ``ptoEmi`` a 6 dígitos."""
    digitos = _solo_digitos(serie)
    if len(digitos) > 6:
        raise ErrorValidacion(f"La serie debe tener máximo 6 dígitos (estab+ptoEmi), no {serie!r}.")
    return digitos.zfill(6)


def normalizar_secuencial(secuencial: Any) -> str:
    """Normaliza el secuencial a 9 dígitos."""
    digitos = _solo_digitos(secuencial)
    if not digitos:
        raise ErrorValidacion("El secuencial es obligatorio.")
    if len(digitos) > 9:
        raise ErrorValidacion(f"El secuencial debe tener máximo 9 dígitos, no {secuencial!r}.")
    return digitos.zfill(9)


def normalizar_codigo_numerico(codigo: Any) -> str:
    """Normaliza el código numérico a 8 dígitos."""
    digitos = _solo_digitos(codigo)
    if not digitos:
        raise ErrorValidacion("El código numérico es obligatorio.")
    if len(digitos) > 8:
        raise ErrorValidacion(f"El código numérico debe tener máximo 8 dígitos, no {codigo!r}.")
    return digitos.zfill(8)


def generar_clave_acceso(
    *,
    fecha_emision: Union[date, datetime, str],
    tipo_comprobante: Any,
    ruc: Any,
    ambiente: Any,
    serie: Any,
    secuencial: Any,
    codigo_numerico: Any,
    tipo_emision: Any = TipoEmision.NORMAL,
) -> str:
    """Construye la clave de acceso de 49 dígitos.

    ``serie`` puede venir como ``"001002"`` o como ``"001"`` + ``"002"``.
    ``codigo_numerico`` es un valor de 8 dígitos elegido por el emisor (se
    recomienda aleatorio para evitar colisiones).
    """
    fecha = _fecha_ddmmaaaa(fecha_emision)
    comprobante = codigo_documento(tipo_comprobante).zfill(2)
    emisor = _solo_digitos(ruc)
    if len(emisor) != 13:
        raise ErrorValidacion(f"El RUC debe tener 13 dígitos, no {ruc!r}.")
    ambiente_valor = codigo_documento(ambiente)
    if ambiente_valor not in ("1", "2"):
        raise ErrorValidacion(f"El ambiente debe ser 1 (pruebas) o 2 (producción), no {ambiente!r}.")
    serie_valor = normalizar_serie(serie)
    secuencial_valor = normalizar_secuencial(secuencial)
    codigo_valor = normalizar_codigo_numerico(codigo_numerico)
    emision_valor = codigo_documento(tipo_emision)
    if emision_valor not in ("1", "2"):
        raise ErrorValidacion(
            f"El tipo de emisión debe ser 1 (normal) o 2 (contingencia), no {tipo_emision!r}."
        )

    cuerpo = (
        fecha
        + comprobante
        + emisor
        + ambiente_valor
        + serie_valor
        + secuencial_valor
        + codigo_valor
        + emision_valor
    )
    if len(cuerpo) != LARGO_CLAVE_ACCESO - 1:
        raise ErrorValidacion(
            f"El cuerpo de la clave debe tener 48 dígitos, se obtuvieron {len(cuerpo)}."
        )
    return cuerpo + calcular_digito_verificador(cuerpo)


def validar_clave_acceso(clave: Any) -> bool:
    """Comprueba que ``clave`` tenga 49 dígitos y un verificador correcto."""
    texto = str(clave or "").strip()
    if len(texto) != LARGO_CLAVE_ACCESO or not texto.isdigit():
        return False
    return calcular_digito_verificador(texto[:48]) == texto[48]


def descomponer_clave_acceso(clave: Any) -> Dict[str, Any]:
    """Separa una clave de acceso válida en sus campos."""
    texto = str(clave or "").strip()
    if not validar_clave_acceso(texto):
        raise ErrorValidacion(f"Clave de acceso inválida: {clave!r}")
    return {
        "fecha_emision": texto[0:8],
        "tipo_comprobante": texto[8:10],
        "ruc": texto[10:23],
        "ambiente": int(texto[23]),
        "serie": texto[24:30],
        "estab": texto[24:27],
        "pto_emi": texto[27:30],
        "secuencial": texto[30:39],
        "codigo_numerico": texto[39:47],
        "tipo_emision": texto[47],
        "digito_verificador": texto[48],
    }


def codigo_numerico_aleatorio() -> str:
    """Genera un código numérico de 8 dígitos aleatorio."""
    import secrets

    return f"{secrets.randbelow(100_000_000):08d}"
