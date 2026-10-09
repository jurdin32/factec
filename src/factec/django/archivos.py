"""Guarda en disco los XML y las respuestas del SRI de cada comprobante.

Todo comprobante —autorizado, devuelto o con error— deja sus archivos en una
carpeta por año, mes y día, dentro de ``MEDIA_ROOT``::

    media/sri/comprobantes/2026/10/08/001-001-000000012_0810202601.../
        sin_firma.xml
        firmado.xml
        autorizado.xml               (si el SRI autorizó)
        respuesta_recepcion.xml      (lo que contestó el SRI al recibirlo)
        respuesta_autorizacion.xml   (lo que contestó al autorizarlo)
        error.txt                    (si falló el envío)

Así queda la evidencia completa aunque el comprobante esté mal, y se puede
revisar desde el admin (los enlaces pasan por el admin, no hace falta publicar
``MEDIA_URL``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings

__all__ = [
    "NOMBRE_AUTORIZADO",
    "NOMBRE_ERROR",
    "NOMBRE_FIRMADO",
    "NOMBRE_RESPUESTA_AUTORIZACION",
    "NOMBRE_RESPUESTA_RECEPCION",
    "NOMBRE_SIN_FIRMA",
    "NOMBRES_CONOCIDOS",
    "archivos_del_registro",
    "base_de_archivos",
    "carpeta_de",
    "escribir",
    "ruta_de",
]

logger = logging.getLogger(__name__)

#: Carpeta raíz (relativa a ``MEDIA_ROOT``) donde se guarda todo.
CARPETA_RAIZ = "sri/comprobantes"

NOMBRE_SIN_FIRMA = "sin_firma.xml"
NOMBRE_FIRMADO = "firmado.xml"
NOMBRE_AUTORIZADO = "autorizado.xml"
NOMBRE_RESPUESTA_RECEPCION = "respuesta_recepcion.xml"
NOMBRE_RESPUESTA_AUTORIZACION = "respuesta_autorizacion.xml"
NOMBRE_ERROR = "error.txt"

#: Nombres que el admin puede descargar (evita leer cualquier ruta).
NOMBRES_CONOCIDOS = (
    NOMBRE_SIN_FIRMA,
    NOMBRE_FIRMADO,
    NOMBRE_AUTORIZADO,
    NOMBRE_RESPUESTA_RECEPCION,
    NOMBRE_RESPUESTA_AUTORIZACION,
    NOMBRE_ERROR,
)


def base_de_archivos() -> Path:
    """Carpeta raíz de los archivos, dentro de ``MEDIA_ROOT``."""
    media = getattr(settings, "MEDIA_ROOT", "") or ""
    return Path(media) if media else Path(settings.BASE_DIR) / "media"


def carpeta_de(registro: Any) -> str:
    """Carpeta del registro, relativa a ``MEDIA_ROOT``.

    ``sri/comprobantes/2026/10/08/001-001-000000012_<clave>``: por año, mes y día,
    y con la clave de acceso para que dos intentos del mismo documento no se
    pisen.
    """
    fecha = getattr(registro, "fecha_emision", None)
    if fecha is None:
        from datetime import date

        fecha = date.today()

    serie = "-".join(
        parte for parte in (
            str(getattr(registro, "estab", "") or "").zfill(3),
            str(getattr(registro, "pto_emi", "") or "").zfill(3),
            str(getattr(registro, "secuencial", "") or "").zfill(9),
        )
    )
    clave = str(getattr(registro, "clave_acceso", "") or "")
    nombre = f"{serie}_{clave}" if clave else serie
    return f"{CARPETA_RAIZ}/{fecha:%Y/%m/%d}/{nombre}"


def ruta_de(registro: Any, nombre: str) -> Path:
    """Ruta absoluta de un archivo del registro."""
    return base_de_archivos() / carpeta_de(registro) / nombre


def escribir(registro: Any, nombre: str, contenido: Optional[str]) -> Optional[str]:
    """Escribe un archivo del comprobante y devuelve su ruta absoluta.

    Devuelve ``None`` si no hay contenido o si no se pudo escribir (por ejemplo
    por permisos): el comprobante sigue su curso, el XML también queda en la base
    de datos.
    """
    if not contenido:
        return None

    destino = ruta_de(registro, nombre)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
    except OSError as error:  # noqa: BLE001 - disco lleno, permisos…
        logger.warning("No se pudo guardar %s: %s", destino, error)
        return None

    logger.info("Archivo guardado en %s", destino)
    return str(destino)


def ruta_relativa(registro: Any, nombre: str) -> str:
    """Ruta del archivo dentro de ``MEDIA_ROOT`` (la que se muestra)."""
    return f"{carpeta_de(registro)}/{nombre}"


def archivos_del_registro(registro: Any) -> List[Dict[str, Any]]:
    """Archivos del comprobante que existen en disco, con su ruta y tamaño."""
    encontrados: List[Dict[str, Any]] = []
    for nombre in NOMBRES_CONOCIDOS:
        ruta = ruta_de(registro, nombre)
        if ruta.exists():
            encontrados.append(
                {
                    "nombre": nombre,
                    "ruta": str(ruta),
                    "relativa": ruta_relativa(registro, nombre),
                    "bytes": ruta.stat().st_size,
                }
            )
    return encontrados
