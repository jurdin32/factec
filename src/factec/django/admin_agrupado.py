"""Agrupa el índice del admin por temas.

Todos los modelos del paquete viven en una sola app (``sri_fe``) para que las
tablas compartan prefijo, así que Django los mostraría en un único bloque
mezclado. Aquí se reparten en secciones para poder leerlos y verificar el estado
de un vistazo:

* **Configuración del SRI** — datos del emisor, certificado y secuenciales.
* **Catálogos** — clientes y productos.
* **Comprobantes** — los seis documentos del SRI, con sus detalles.
* **Emisión** — lo que se envió al SRI: clave, estado, autorización y XML.

No cambia tablas, permisos ni direcciones: solo cómo se agrupa el menú. Si el
proyecto usa un ``AdminSite`` propio, hay que llamarlo una vez::

    from factec.django import admin_agrupado

    admin_agrupado.organizar_el_indice(mi_site)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.urls import NoReverseMatch, reverse

__all__ = ["GRUPOS", "ETIQUETA_APP", "organizar_el_indice"]

#: Etiqueta (``label``) de la app del paquete.
ETIQUETA_APP = "sri_fe"

#: Secciones del índice, en orden: (etiqueta, título, modelos).
GRUPOS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "sri_fe_configuracion",
        "Configuración del SRI",
        ("ConfiguracionEmisor", "Secuencial"),
    ),
    (
        "sri_fe_catalogos",
        "Catálogos",
        ("Cliente", "Producto"),
    ),
    (
        "sri_fe_comprobantes",
        "Comprobantes",
        (
            "Factura",
            "NotaCredito",
            "NotaDebito",
            "LiquidacionCompra",
            "GuiaRemision",
            "Retencion",
            "GuiaDestinatario",
            "RetencionDocSustento",
        ),
    ),
    (
        "sri_fe_emision",
        "Emisión",
        ("ComprobanteEmitido",),
    ),
)

#: Título de la sección donde van los modelos que no estén en :data:`GRUPOS`.
TITULO_OTROS = "Otros (SRI)"


def organizar_el_indice(site: Any = None) -> None:
    """Reparte los modelos del paquete en secciones en el índice del admin.

    Se puede llamar varias veces sin efecto: solo actúa la primera.
    """
    if site is None:
        from django.contrib import admin

        site = admin.site

    if getattr(site, "_sri_fe_agrupado", False):
        return

    original = site.get_app_list

    def get_app_list(request: Any, app_label: Optional[str] = None) -> List[Dict[str, Any]]:
        return _reagrupar(original(request, app_label), app_label)

    site.get_app_list = get_app_list
    site._sri_fe_agrupado = True


def _reagrupar(
    listado: Optional[List[Dict[str, Any]]], app_label: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Sustituye el bloque de la app por las secciones, en el mismo sitio."""
    listado = list(listado or [])

    posicion: Optional[int] = None
    modelos: Dict[str, Dict[str, Any]] = {}
    for indice, app in enumerate(listado):
        if app.get("app_label") != ETIQUETA_APP:
            continue
        posicion = indice
        for modelo in app.get("models", []):
            modelos[modelo.get("object_name")] = modelo
        break

    if posicion is None:  # la app no está en el listado (sin permisos, p. ej.)
        return listado

    secciones: List[Dict[str, Any]] = []
    agrupados: set = set()

    for etiqueta, titulo, clases in GRUPOS:
        incluidos = [modelos[clase] for clase in clases if clase in modelos]
        if not incluidos:
            continue
        agrupados.update(clase for clase in clases if clase in modelos)
        secciones.append(_seccion(etiqueta, titulo, incluidos, app_label))

    sobrantes = [modelo for clase, modelo in modelos.items() if clase not in agrupados]
    if sobrantes:
        # Un modelo nuevo nunca debe quedar oculto por no estar en GRUPOS.
        secciones.append(_seccion("sri_fe_otros", TITULO_OTROS, sobrantes, app_label))

    return [*listado[:posicion], *secciones, *listado[posicion + 1:]]


def _seccion(
    etiqueta: str,
    titulo: str,
    modelos: List[Dict[str, Any]],
    app_label: Optional[str],
) -> Dict[str, Any]:
    """Construye una sección con el formato que espera la plantilla del admin."""
    return {
        "name": titulo,
        "app_label": etiqueta,
        "app_url": _url_de_la_app(app_label),
        "has_module_perms": True,
        "models": modelos,
    }


def _url_de_la_app(app_label: Optional[str]) -> str:
    """Dirección de la portada de secciones (el listado de la app)."""
    try:
        return reverse("admin:app_list", args=[app_label or ETIQUETA_APP])
    except NoReverseMatch:  # pragma: no cover - admin sin la vista de app
        return reverse("admin:index")
