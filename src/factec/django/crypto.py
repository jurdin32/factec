"""Cifrado en reposo de la contraseña del certificado.

Permite guardar la contraseña del ``.p12`` cifrada (por ejemplo en un archivo de
variables o en un gestor de secretos) en lugar de en texto plano.

Generar una clave::

    python -c "from factec.django.crypto import generar_clave; print(generar_clave())"

Cifrar una contraseña::

    python -c "from factec.django.crypto import cifrar; \\
        print(cifrar('mi-clave', 'LA_CLAVE_GENERADA'))"

La clave de cifrado nunca debe ir en el repositorio: pásala por
``SRI_CLAVE_CIFRADO``.
"""

from __future__ import annotations

from typing import Union

from cryptography.fernet import Fernet, InvalidToken

from ..excepciones import ErrorValidacion

__all__ = ["generar_clave", "cifrar", "descifrar"]


def _fernet(clave: Union[str, bytes]) -> Fernet:
    material = clave.encode("utf-8") if isinstance(clave, str) else clave
    try:
        return Fernet(material)
    except (ValueError, TypeError) as exc:
        raise ErrorValidacion(
            "La clave de cifrado no es válida. Debe ser una clave Fernet de 32 "
            "bytes en base64 urlsafe; genérela con generar_clave()."
        ) from exc


def generar_clave() -> str:
    """Genera una clave Fernet nueva para cifrar secretos."""
    return Fernet.generate_key().decode("ascii")


def cifrar(texto: str, clave: Union[str, bytes]) -> str:
    """Cifra ``texto`` y devuelve el token en base64."""
    return _fernet(clave).encrypt(str(texto).encode("utf-8")).decode("ascii")


def descifrar(token: str, clave: Union[str, bytes]) -> str:
    """Descifra un token creado con :func:`cifrar`."""
    try:
        return _fernet(clave).decrypt(str(token).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ErrorValidacion(
            "No se pudo descifrar el secreto: la clave de cifrado no coincide o "
            "el dato está corrupto."
        ) from exc
