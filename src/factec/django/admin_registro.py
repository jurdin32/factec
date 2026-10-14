"""Registro del admin del paquete, a prueba de que el proyecto traiga el suyo.

El paquete trae el admin de sus modelos, pero un proyecto puede querer el suyo
(por ejemplo, los catálogos de conductores y vehículos, que se dan de alta junto a
sus guías). El admin se descubre **antes** de que corran los ``ready``, así que
registrar aquí mismo haría que un ``admin.site.register`` del proyecto fallara con
``AlreadyRegistered``.

Por eso se apunta cada modelo con :func:`registrar` (que se usa como el
``admin.register`` de Django) y se registran todos al final, desde
:meth:`factec.django.apps.FactecConfig.ready`: si el proyecto ya registró ese
modelo, se respeta **el del proyecto**; si no, se pone el del paquete.
"""

from __future__ import annotations

from typing import Any, List, Tuple

__all__ = ["registrar", "registrar_los_pendientes"]

#: Pares ``(modelo, clase de admin)`` a registrar cuando el admin esté descubierto.
PENDIENTES: List[Tuple[Any, Any]] = []


def registrar(modelo: Any) -> Any:
    """Como ``admin.register``, pero aplazado: registra en ``ready``, no al importar.

    Sirve de decorador igual que el de Django::

        @registrar(documentos.Factura)
        class FacturaAdmin(AdminConAjustes):
            ...
    """
    def _clase(clase: Any) -> Any:
        PENDIENTES.append((modelo, clase))
        return clase

    return _clase


def registrar_los_pendientes() -> None:
    """Registra los modelos aplazados, respetando lo que ya registró el proyecto."""
    from django.contrib import admin

    pendientes, PENDIENTES[:] = list(PENDIENTES), []
    for modelo, clase in pendientes:
        if not admin.site.is_registered(modelo):
            admin.site.register(modelo, clase)
