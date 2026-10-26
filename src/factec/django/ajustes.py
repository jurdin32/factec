"""Los ajustes de la emisión, en la base de datos.

Lo que antes había que escribir en ``settings.py``::

    FACTURACION_ELECTRONICA = {
        "AMBIENTE": "pruebas",
        "GUARDAR_XML": True,
        "GUARDAR_ARCHIVOS": True,
    }

ahora son ajustes de la aplicación, con su valor de serie. Si el proyecto tiene
puesto ``personalize_options`` (lo normal, porque es el paquete de los permisos),
este módulo los declara al arrancar y ``migrate`` los deja sembrados; y
:func:`factec.django.conf.ajustes` los lee de ahí. Si no lo tiene, se usan los
valores de serie y ``FACTURACION_ELECTRONICA``, como siempre.

La **clave de cifrado** no es un ajuste: es un secreto. Va en la variable de
entorno ``SRI_CLAVE_CIFRADO`` (o en ``settings.FACTURACION_ELECTRONICA``), fuera
de la base de datos: si estuviera al lado de la contraseña que cifra, cifrarla no
serviría de nada.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["declarar", "de_la_base"]


def _paquete() -> Any:
    """El registro de ajustes del proyecto, si está puesto ``personalize_options``."""
    try:
        from personalize_options import ajustes as registro
    except Exception:  # noqa: BLE001 - factec funciona sin el paquete
        return None
    return registro


def declarar() -> None:
    """Declara los ajustes del SRI en el registro del proyecto (una vez)."""
    registro = _paquete()
    if registro is None:
        return
    try:
        from personalize_options.ajustes import AjusteDeSerie
    except Exception:  # noqa: BLE001
        return

    if registro.por_clave("sri_ambiente") is not None:  # ya declarados
        return

    registro.registrar(
        AjusteDeSerie(
            "sri_ambiente",
            "pruebas",
            grupo="Facturación electrónica",
            orden=1,
            descripcion="«pruebas»: los comprobantes no tienen validez fiscal. «produccion»: sí.",
        ),
        AjusteDeSerie(
            "sri_guardar_xml",
            "si",
            tipo="booleano",
            grupo="Facturación electrónica",
            orden=2,
            descripcion="Guardar el XML firmado en la base de datos, junto al comprobante.",
        ),
        AjusteDeSerie(
            "sri_guardar_archivos",
            "si",
            tipo="booleano",
            grupo="Facturación electrónica",
            orden=3,
            descripcion="Guardar además los XML y las respuestas del SRI como archivos.",
        ),
        AjusteDeSerie(
            "sri_clave_cifrado",
            "",
            grupo="Facturación electrónica",
            orden=9,
            editable=False,
            descripcion=(
                "Clave con la que se cifra la contraseña del certificado. Mejor en la "
                "variable de entorno SRI_CLAVE_CIFRADO. No se enseña ni se edita desde la "
                "aplicación."
            ),
        ),
    )


def de_la_base() -> Dict[str, Any]:
    """Los ajustes del SRI que están en la base, con las claves de ``conf.ajustes``."""
    registro = _paquete()
    if registro is None:
        return {}
    if registro.por_clave("sri_ambiente") is None:
        return {}
    valores: Dict[str, Any] = {
        "AMBIENTE": registro.texto("sri_ambiente", "pruebas"),
        "GUARDAR_XML": registro.booleano("sri_guardar_xml", True),
        "GUARDAR_ARCHIVOS": registro.booleano("sri_guardar_archivos", True),
    }
    clave = registro.texto("sri_clave_cifrado", "")
    if clave:
        valores["CLAVE_CIFRADO"] = clave
    return valores
