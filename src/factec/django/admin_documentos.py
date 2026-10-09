"""Admin de los catálogos y de los comprobantes del SRI.

Cada comprobante se puede emitir desde su propia pantalla: la acción
**Emitir** arma el XML, lo firma, lo envía al SRI y espera la autorización.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from django.contrib import admin, messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html, format_html_join

from . import documentos, facturacion, models
from .admin_filtros import (
    AdminConAjustes,
    FiltroConCertificado,
    filtro_emitido,
    filtro_por_fecha,
    filtro_por_importe,
)
from .forms import LineaDocumentoForm

__all__ = [
    "ClienteAdmin",
    "ProductoAdmin",
    "FacturaAdmin",
    "LiquidacionCompraAdmin",
    "NotaCreditoAdmin",
    "NotaDebitoAdmin",
    "GuiaRemisionAdmin",
    "RetencionAdmin",
]

#: Colores del estado ante el SRI (los mismos del listado de comprobantes).
COLORES_ESTADO = {
    models.EstadoComprobante.AUTORIZADO: "#0a7d33",
    models.EstadoComprobante.RECIBIDO: "#0b5fa5",
    models.EstadoComprobante.EN_PROCESO: "#8a6d00",
    models.EstadoComprobante.BORRADOR: "#555555",
    models.EstadoComprobante.FIRMADO: "#555555",
    models.EstadoComprobante.DEVUELTO: "#a51f1f",
    models.EstadoComprobante.NO_AUTORIZADO: "#a51f1f",
    models.EstadoComprobante.ERROR: "#a51f1f",
}


# ------------------------------------------------------------------ catálogos


@admin.register(documentos.Cliente)
class ClienteAdmin(AdminConAjustes):
    """Clientes, proveedores y sujetos retenidos."""

    list_display = (
        "razon_social", "identificacion", "tipo_identificacion", "direccion",
        "facturas_emitidas", "notas_de_credito_emitidas",
    )
    list_filter = ("tipo_identificacion", filtro_por_fecha("creado", "Alta"))
    search_fields = ("razon_social", "identificacion", "direccion", "email", "telefono")
    list_per_page = 50
    ordering = ("razon_social",)

    def get_queryset(self, request: Any) -> Any:
        from django.db.models import Count

        return super().get_queryset(request).annotate(
            _facturas=Count("facturas", distinct=True),
            _notas=Count("notas_credito", distinct=True),
        )

    @admin.display(description="Facturas", ordering="_facturas")
    def facturas_emitidas(self, obj: Any) -> int:
        return getattr(obj, "_facturas", 0) or 0

    @admin.display(description="Notas de crédito", ordering="_notas")
    def notas_de_credito_emitidas(self, obj: Any) -> int:
        return getattr(obj, "_notas", 0) or 0


@admin.register(documentos.Producto)
class ProductoAdmin(AdminConAjustes):
    list_display = (
        "codigo_principal", "descripcion", "unidad_medida",
        "precio_unitario", "codigo_porcentaje_iva", "activo",
    )
    list_filter = (
        "activo",
        "codigo_porcentaje_iva",
        "unidad_medida",
        filtro_por_fecha("creado", "Alta"),
    )
    search_fields = ("codigo_principal", "codigo_auxiliar", "descripcion", "unidad_medida")
    date_hierarchy = "creado"
    list_editable = ("activo",)
    list_per_page = 50
    ordering = ("descripcion",)

    @admin.display(description="IVA", ordering="codigo_porcentaje_iva")
    def iva_mostrado(self, obj: Any) -> str:
        return f"{obj.get_codigo_porcentaje_iva_display()} ({obj.tarifa_iva} %)"

    def get_urls(self) -> List[Any]:
        return [
            path(
                "<int:pk>/datos/",
                self.admin_site.admin_view(self.datos),
                name="sri_fe_producto_datos",
            ),
        ] + super().get_urls()

    def datos(self, request: Any, pk: int) -> JsonResponse:
        """Datos del producto en JSON, para rellenar la línea del comprobante."""
        producto = get_object_or_404(documentos.Producto, pk=pk)
        return JsonResponse(
            {
                "descripcion": producto.descripcion,
                "codigo_principal": producto.codigo_principal,
                "codigo_auxiliar": producto.codigo_auxiliar,
                "unidad_medida": producto.unidad_medida,
                "precio_unitario": f"{producto.precio_unitario:.6f}".rstrip("0").rstrip("."),
                "codigo_porcentaje_iva": producto.codigo_porcentaje_iva,
            }
        )


# ------------------------------------------------------------------ auxiliares


class DocumentoAdmin(AdminConAjustes):
    """Base del admin de los comprobantes: listado, estado y acciones.

    Trae filtros y búsquedas de serie, y acepta los que añada la tienda desde el
    ajuste ``ADMIN`` (ver :mod:`factec.django.admin_filtros`).
    """

    #: Nombre del campo con la otra parte (receptor, proveedor, sujeto retenido).
    campo_contraparte = "receptor"

    #: Nombre de la propiedad con el importe total, si el comprobante tiene una.
    campo_total: Optional[str] = "total"

    #: Etiqueta del importe total (cambia según el comprobante).
    etiqueta_total = "Total"

    #: Relaciones que conviene traer de una vez para el listado.
    prefetch_relacionado: Tuple[str, ...] = ()

    #: Campos por los que se puede buscar además de los comunes.
    busqueda_propia: Tuple[str, ...] = ()

    #: Filtros propios del comprobante (además de los comunes).
    filtros_propios: Tuple[Any, ...] = ()

    list_display = (
        "numero", "fecha_emision", "contraparte", "total_mostrado",
        "estado_badge", "numero_autorizacion_mostrado",
    )
    list_filter = (
        "comprobante__estado",
        filtro_emitido("comprobante"),
        "comprobante__ambiente",
        filtro_por_fecha("fecha_emision", "Fecha de emisión"),
        filtro_por_fecha("comprobante__fecha_autorizacion", "Fecha de autorización"),
        filtro_por_importe("comprobante__importe_total", "Importe"),
    )
    search_fields = (
        "secuencial",
        "observaciones",
        "comprobante__clave_acceso",
        "comprobante__numero_autorizacion",
        "comprobante__razon_social_receptor",
        "comprobante__identificacion_receptor",
        "comprobante__carpeta",
        "comprobante__error",
    )
    date_hierarchy = "fecha_emision"
    ordering = ("-fecha_emision", "-pk")
    actions = ("accion_emitir", "accion_reintentar")
    save_on_top = True
    list_per_page = 50
    readonly_fields = (
        "secuencial", "comprobante", "estado_mostrado", "clave_acceso_mostrada",
        "importes_mostrados", "creado", "actualizado",
    )

    def get_search_fields(self, request: Any) -> List[str]:
        """Búsqueda común + la propia del comprobante + la que añada la tienda."""
        base = list(super().get_search_fields(request))
        for campo in self.busqueda_propia:
            if campo not in base:
                base.append(campo)
        return base

    def get_list_filter(self, request: Any) -> List[Any]:
        """Filtros comunes + los propios + los que añada la tienda."""
        base = list(super().get_list_filter(request))
        for filtro in self.filtros_propios:
            if filtro not in base:
                base.append(filtro)
        return base

    def get_queryset(self, request: Any) -> Any:
        consulta = super().get_queryset(request)
        if self.prefetch_relacionado:
            return consulta.prefetch_related(*self.prefetch_relacionado)
        return consulta

    # -------------------------------------------------------------- listado

    @admin.display(description="N.º")
    def numero(self, obj: documentos.DocumentoElectronico) -> str:
        return f"{int(obj.secuencial):09d}" if obj.secuencial else "—"

    @admin.display(description="Contraparte")
    def contraparte(self, obj: Any) -> str:
        return str(getattr(obj, self.campo_contraparte, "") or "—")

    @admin.display(description="Total")
    def total_mostrado(self, obj: Any) -> Any:
        if not self.campo_total:
            return "—"
        try:
            return getattr(obj, self.campo_total, None) or "—"
        except Exception:  # noqa: BLE001 - el total es informativo, nunca debe fallar
            return "—"

    @admin.display(description="Estado")
    def estado_badge(self, obj: documentos.DocumentoElectronico) -> str:
        estado = obj.estado
        color = COLORES_ESTADO.get(estado, "#555555")
        etiqueta = models.EstadoComprobante(estado).label if estado else ""
        return format_html('<strong style="color:{}">{}</strong>', color, etiqueta)

    @admin.display(description="N.º de autorización")
    def numero_autorizacion_mostrado(self, obj: documentos.DocumentoElectronico) -> str:
        return obj.numero_autorizacion or "—"

    @admin.display(description="Estado ante el SRI")
    def estado_mostrado(self, obj: documentos.DocumentoElectronico) -> str:
        if not obj.comprobante_id:
            return "Sin emitir. Use la acción «Emitir…»."
        enlace = f"/admin/sri_fe/comprobanteemitido/{obj.comprobante_id}/change/"
        return format_html(
            '<a href="{}">{}</a> — {}{}',
            enlace,
            obj.comprobante,
            obj.comprobante.get_estado_display(),
            f" — {obj.comprobante.error}" if obj.comprobante.error else "",
        )

    @admin.display(description="Clave de acceso")
    def clave_acceso_mostrada(self, obj: documentos.DocumentoElectronico) -> str:
        return obj.clave_acceso or "—"

    @admin.display(description="Importes calculados")
    def importes_mostrados(self, obj: Any) -> Any:
        """Subtotal, impuestos y total tal como los enviará el SRI."""
        if obj.pk is None:
            return "Se calculan al guardar el comprobante."
        importes = self._importes(obj)
        if importes is None:
            return format_html(
                "<em>{}</em>", "No se pudieron calcular: revise las líneas o los motivos."
            )
        if not importes:
            return "—"
        return format_html_join(" · ", "{}: <strong>{}</strong>", importes)

    def _importes(self, obj: Any) -> Optional[List[Tuple[str, Any]]]:
        """Pares (etiqueta, importe) del comprobante; ``None`` si no se pueden calcular."""
        try:
            importes: List[Tuple[str, Any]] = []
            for etiqueta, nombre in (("Subtotal", "subtotal"), ("IVA", "valor_iva")):
                valor = getattr(obj, nombre, None)
                if valor is not None:
                    importes.append((etiqueta, valor))
            if self.campo_total:
                total = getattr(obj, self.campo_total, None)
                if total is not None:
                    importes.append((self.etiqueta_total, total))
            return importes
        except Exception:  # noqa: BLE001 - el admin nunca debe fallar por esto
            return None

    # -------------------------------------------------------------- acciones

    @admin.action(description="Emitir: firmar, enviar al SRI y esperar autorización")
    def accion_emitir(self, request: Any, queryset: Any) -> None:
        """Emite los comprobantes marcados de forma síncrona.

        Se hace aquí mismo para poder informar del resultado en pantalla; el SRI
        suele responder en unos segundos.
        """
        for documento in queryset:
            try:
                registro = facturacion.emitir(documento, encolar=False)
            except Exception as exc:  # noqa: BLE001 - se informa en pantalla
                self.message_user(request, f"{documento}: {exc}", level=messages.ERROR)
                continue

            if registro.autorizado:
                nivel = messages.SUCCESS
            elif registro.estado in (
                models.EstadoComprobante.ERROR,
                models.EstadoComprobante.DEVUELTO,
                models.EstadoComprobante.NO_AUTORIZADO,
            ):
                nivel = messages.ERROR
            else:
                nivel = messages.WARNING
            self.message_user(
                request,
                f"{documento}: {registro.get_estado_display()} — clave {registro.clave_acceso}"
                f"{f' — {registro.error}' if registro.error else ''}",
                level=nivel,
            )

    @admin.action(description="Reintentar la emisión de los que quedaron pendientes")
    def accion_reintentar(self, request: Any, queryset: Any) -> None:
        for documento in queryset:
            try:
                registro = facturacion.reintentar(documento, encolar=False)
            except Exception as exc:  # noqa: BLE001 - se informa en pantalla
                self.message_user(request, f"{documento}: {exc}", level=messages.ERROR)
                continue
            if registro is None:
                self.message_user(
                    request, f"{documento}: todavía no se ha emitido.",
                    level=messages.WARNING,
                )
                continue
            self.message_user(
                request,
                f"{documento}: {registro.get_estado_display()} — clave {registro.clave_acceso}",
                level=messages.SUCCESS if registro.autorizado else messages.WARNING,
            )


class LineaInline(admin.TabularInline):
    """Líneas de un comprobante, con el producto seleccionable.

    Basta con elegir el producto: la descripción, el código, la unidad de medida,
    el precio y el IVA se completan solos (aquí, al elegirlo, y en el servidor al
    guardar). Solo hay que escribirlos si la línea va sin producto.
    """

    form = LineaDocumentoForm
    verbose_name = "línea"
    verbose_name_plural = "líneas — elija el producto y lo demás se completa solo"
    extra = 1
    autocomplete_fields = ("producto",)
    readonly_fields = ("total_linea",)
    fields = (
        "producto", "codigo_principal", "descripcion", "cantidad",
        "precio_unitario", "descuento", "unidad_medida", "codigo_porcentaje_iva",
        "datos_adicionales", "total_linea",
    )

    class Media:
        js = ("sri_fe/js/lineas.js",)

    @admin.display(description="Total de la línea")
    def total_linea(self, obj: Any) -> Any:
        if not obj.pk:
            return ""
        try:
            return obj.total
        except Exception:  # noqa: BLE001 - informativo
            return ""


# ------------------------------------------------------------------- factura


class FacturaDetalleInline(LineaInline):
    model = documentos.FacturaDetalle


@admin.register(documentos.Factura)
class FacturaAdmin(DocumentoAdmin):
    """Facturas electrónicas."""

    list_select_related = ("receptor", "comprobante")
    prefetch_relacionado = ("detalles",)
    filtros_propios = (
        "forma_pago",
        "unidad_tiempo",
        "receptor__tipo_identificacion",
        filtro_por_fecha("creado", "Alta"),
    )
    busqueda_propia = (
        "receptor__razon_social",
        "receptor__identificacion",
        "receptor__email",
        "receptor__direccion",
        "forma_pago",
        "placa",
        "guia_remision",
        "propina",
    )
    autocomplete_fields = ("receptor",)
    inlines = (FacturaDetalleInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "observaciones")}),
        ("Receptor", {"fields": ("receptor",)}),
        ("Pago", {"fields": ("forma_pago", "plazo", "unidad_tiempo")}),
        ("Datos opcionales", {
            "classes": ("collapse",),
            "fields": ("propina", "placa", "guia_remision", "valor_ret_iva", "valor_ret_renta"),
            "description": "Solo se envían al SRI los que se completen.",
        }),
        ("Totales", {"fields": ("importes_mostrados",)}),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )


# ------------------------------------------------------ liquidación de compra


class LiquidacionCompraDetalleInline(LineaInline):
    model = documentos.LiquidacionCompraDetalle


@admin.register(documentos.LiquidacionCompra)
class LiquidacionCompraAdmin(DocumentoAdmin):
    """Liquidaciones de compra."""

    campo_contraparte = "proveedor"
    list_select_related = ("proveedor", "comprobante")
    prefetch_relacionado = ("detalles",)
    filtros_propios = (
        "forma_pago",
        "unidad_tiempo",
        "proveedor__tipo_identificacion",
        filtro_por_importe("comprobante__importe_total", "Importe"),
    )
    busqueda_propia = (
        "proveedor__razon_social",
        "proveedor__identificacion",
        "proveedor__email",
        "proveedor__direccion",
        "correo",
        "forma_pago",
    )
    autocomplete_fields = ("proveedor",)
    inlines = (LiquidacionCompraDetalleInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "observaciones")}),
        ("Proveedor", {"fields": ("proveedor", "correo")}),
        ("Pago", {"fields": ("forma_pago", "plazo", "unidad_tiempo")}),
        ("Totales", {"fields": ("importes_mostrados",)}),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )


# ---------------------------------------------------------- nota de crédito


class NotaCreditoDetalleInline(LineaInline):
    model = documentos.NotaCreditoDetalle


@admin.register(documentos.NotaCredito)
class NotaCreditoAdmin(DocumentoAdmin):
    """Notas de crédito."""

    list_select_related = ("receptor", "comprobante")
    prefetch_relacionado = ("detalles",)
    filtros_propios = (
        "cod_doc_modificado",
        "receptor__tipo_identificacion",
        filtro_por_fecha("fecha_emision_doc_sustento", "Fecha del documento sustento"),
    )
    busqueda_propia = (
        "receptor__razon_social",
        "receptor__identificacion",
        "receptor__email",
        "motivo",
        "num_doc_modificado",
        "cod_doc_modificado",
        "rise",
    )
    autocomplete_fields = ("receptor",)
    inlines = (NotaCreditoDetalleInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "observaciones")}),
        ("Receptor", {"fields": ("receptor",)}),
        ("Documento que modifica", {
            "fields": ("cod_doc_modificado", "num_doc_modificado", "fecha_emision_doc_sustento"),
        }),
        ("Motivo", {"fields": ("motivo", "rise")}),
        ("Totales", {"fields": ("importes_mostrados",)}),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )


# ----------------------------------------------------------- nota de débito


class NotaDebitoMotivoInline(admin.TabularInline):
    model = documentos.NotaDebitoMotivo
    extra = 1


@admin.register(documentos.NotaDebito)
class NotaDebitoAdmin(DocumentoAdmin):
    """Notas de débito."""

    list_select_related = ("receptor", "comprobante")
    prefetch_relacionado = ("motivos",)
    filtros_propios = (
        "cod_doc_modificado",
        "codigo_porcentaje_iva",
        "forma_pago",
        "unidad_tiempo",
        "receptor__tipo_identificacion",
        filtro_por_fecha("fecha_emision_doc_sustento", "Fecha del documento sustento"),
    )
    busqueda_propia = (
        "receptor__razon_social",
        "receptor__identificacion",
        "receptor__email",
        "num_doc_modificado",
        "cod_doc_modificado",
        "rise",
        "motivos__razon",
    )
    autocomplete_fields = ("receptor",)
    inlines = (NotaDebitoMotivoInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "observaciones")}),
        ("Receptor", {"fields": ("receptor",)}),
        ("Documento que modifica", {
            "fields": ("cod_doc_modificado", "num_doc_modificado", "fecha_emision_doc_sustento"),
        }),
        ("Valores", {
            "fields": ("codigo_porcentaje_iva", "base_imponible", "forma_pago", "plazo",
                       "unidad_tiempo", "rise"),
            "description": "Si la base imponible se deja vacía se usa la suma de los motivos.",
        }),
        ("Totales", {"fields": ("importes_mostrados",)}),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )


# -------------------------------------------------------- guía de remisión


class GuiaDestinatarioInline(admin.StackedInline):
    model = documentos.GuiaDestinatario
    extra = 1
    show_change_link = True


@admin.register(documentos.GuiaRemision)
class GuiaRemisionAdmin(DocumentoAdmin):
    """Guías de remisión."""

    campo_total = None
    list_select_related = ("comprobante",)
    filtros_propios = (
        "placa",
        "tipo_identificacion_transportista",
        filtro_por_fecha("fecha_ini_transporte", "Inicio del traslado"),
    )
    busqueda_propia = (
        "razon_social_transportista",
        "ruc_transportista",
        "placa",
        "dir_partida",
        "destinatarios__razon_social",
        "destinatarios__identificacion",
    )
    inlines = (GuiaDestinatarioInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "observaciones")}),
        ("Traslado", {"fields": ("dir_partida", "fecha_ini_transporte", "fecha_fin_transporte")}),
        ("Transportista", {
            "fields": ("razon_social_transportista", "ruc_transportista",
                       "tipo_identificacion_transportista", "placa", "rise"),
        }),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )

    @admin.display(description="Destinatarios")
    def contraparte(self, obj: documentos.GuiaRemision) -> str:
        return f"{obj.destinatarios.count()} destinatario(s)"


class GuiaDetalleInline(admin.TabularInline):
    model = documentos.GuiaDetalle
    verbose_name = "bien transportado"
    verbose_name_plural = "bienes transportados — los datos adicionales son opcionales"
    extra = 1
    fields = ("descripcion", "cantidad", "codigo_principal", "codigo_adicional",
              "datos_adicionales")


@admin.register(documentos.GuiaDestinatario)
class GuiaDestinatarioAdmin(AdminConAjustes):
    """Destinatarios de las guías, con los bienes que se transportan."""

    list_display = (
        "razon_social", "identificacion", "motivo_traslado", "guia",
        "cod_doc_sustento", "bienes", "estado_de_la_guia",
    )
    list_filter = (
        "motivo_traslado",
        "tipo_identificacion",
        "cod_doc_sustento",
        filtro_por_fecha("fecha_emision_doc_sustento", "Fecha del sustento"),
    )
    search_fields = (
        "razon_social", "identificacion", "direccion", "ruta",
        "num_doc_sustento", "num_aut_doc_sustento",
        "guia__secuencial", "guia__dir_partida", "guia__placa",
        "detalles__descripcion", "detalles__codigo_principal",
    )
    list_select_related = ("guia", "guia__comprobante")
    inlines = (GuiaDetalleInline,)
    readonly_fields = ("guia",)
    list_per_page = 50
    ordering = ("guia", "pk")

    def get_queryset(self, request: Any) -> Any:
        from django.db.models import Count

        return super().get_queryset(request).annotate(_bienes=Count("detalles"))

    @admin.display(description="Bienes", ordering="_bienes")
    def bienes(self, obj: Any) -> int:
        return getattr(obj, "_bienes", 0) or 0

    @admin.display(description="Estado del SRI")
    def estado_de_la_guia(self, obj: Any) -> str:
        comprobante = obj.guia.comprobante if obj.guia_id else None
        return comprobante.get_estado_display() if comprobante else "Sin emitir"


# ----------------------------------------------------- comprobante de retención


class RetencionDocSustentoInline(admin.StackedInline):
    model = documentos.RetencionDocSustento
    extra = 1
    show_change_link = True


@admin.register(documentos.Retencion)
class RetencionAdmin(DocumentoAdmin):
    """Comprobantes de retención."""

    campo_contraparte = "sujeto_retenido"
    campo_total = "total_retenido"
    etiqueta_total = "Total retenido"
    list_select_related = ("sujeto_retenido", "comprobante")
    prefetch_relacionado = ("docs_sustento__retenciones",)
    filtros_propios = (
        "parte_rel",
        "tipo_sujeto_retenido",
        "sujeto_retenido__tipo_identificacion",
        filtro_por_fecha("periodo_fiscal", "Período fiscal"),
    )
    busqueda_propia = (
        "sujeto_retenido__razon_social",
        "sujeto_retenido__identificacion",
        "sujeto_retenido__email",
        "docs_sustento__num_doc_sustento",
        "docs_sustento__num_aut_doc_sustento",
        "docs_sustento__retenciones__codigo_retencion",
    )
    autocomplete_fields = ("sujeto_retenido",)
    inlines = (RetencionDocSustentoInline,)
    fieldsets = (
        ("Emisión", {"fields": ("fecha_emision", "secuencial", "periodo_fiscal", "observaciones")}),
        ("Sujeto retenido", {"fields": ("sujeto_retenido", "parte_rel", "tipo_sujeto_retenido")}),
        ("Totales", {"fields": ("importes_mostrados",)}),
        ("Estado ante el SRI", {"fields": ("estado_mostrado", "clave_acceso_mostrada", "comprobante")}),
        ("Campos adicionales", {
            "classes": ("collapse",),
            "fields": ("informacion_adicional",),
            "description": "Campos propios de la tienda que viajan al SRI en "
                           "«infoAdicional» (hasta 15). Formato NOMBRE=VALOR separados "
                           "por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE.",
        }),
        ("Auditoría", {"classes": ("collapse",), "fields": ("creado", "actualizado")}),
    )


class RetencionDocSustentoImpuestoInline(admin.TabularInline):
    model = documentos.RetencionDocSustentoImpuesto
    extra = 1


class RetencionImpuestoInline(admin.TabularInline):
    model = documentos.RetencionImpuesto
    extra = 1


@admin.register(documentos.RetencionDocSustento)
class RetencionDocSustentoAdmin(AdminConAjustes):
    """Documentos que sustentan cada retención, con sus impuestos y retenciones."""

    list_display = (
        "num_doc_sustento", "cod_doc_sustento", "fecha_emision",
        "total_sin_impuestos", "importe_total", "total_retenciones", "retencion",
    )
    list_filter = (
        "cod_doc_sustento",
        "cod_sustento",
        "pago_loc_ext",
        "aplic_conv_dob_trib",
        "pag_ext_suj_ret_nor_leg",
        "tipo_regi",
        filtro_por_fecha("fecha_emision", "Fecha del sustento"),
        filtro_por_importe("importe_total", "Importe"),
    )
    search_fields = (
        "num_doc_sustento", "num_aut_doc_sustento", "retencion__secuencial",
        "retencion__sujeto_retenido__razon_social",
        "retencion__sujeto_retenido__identificacion",
        "retenciones__codigo", "retenciones__codigo_retencion",
        "impuestos__codigo", "impuestos__codigo_porcentaje",
        "pais_efec_pago", "tipo_regi",
    )
    ordering = ("-fecha_emision", "-pk")
    list_per_page = 50

    def get_queryset(self, request: Any) -> Any:
        from django.db.models import Count

        return super().get_queryset(request).annotate(_retenciones=Count("retenciones"))

    list_select_related = ("retencion",)
    inlines = (RetencionDocSustentoImpuestoInline, RetencionImpuestoInline)
    readonly_fields = ("retencion",)

    @admin.display(description="Retenciones", ordering="_retenciones")
    def total_retenciones(self, obj: Any) -> int:
        return getattr(obj, "_retenciones", 0) or 0


# ----------------------------------------- líneas y detalles (búsqueda global)


class LineaAdminBase(AdminConAjustes):
    """Listado de líneas de comprobantes, para buscar dentro de todos ellos.

    Sirve para responder preguntas como «¿en qué facturas vendí este producto?» o
    «¿qué comprobantes llevan este código de barras?».
    """

    #: Comprobante al que pertenece la línea (para el enlace y los filtros).
    campo_documento = "factura"

    #: Contraparte del comprobante, para poder buscarla.
    ruta_contraparte = "factura__receptor"

    list_display = (
        "documento", "producto", "descripcion", "cantidad", "precio_unitario",
        "total_linea_mostrado", "iva_mostrado", "codigo_mostrado",
    )
    #: La primera columna enlaza al comprobante, así que el enlace para editar la
    #: línea va en la descripción (Django no admite un enlace dentro de otro).
    list_display_links = ("descripcion",)
    #: Los filtros se arman en :meth:`get_list_filter`, porque dependen del
    #: comprobante al que pertenece la línea (cada subclase cambia el campo).
    list_filter: Tuple[Any, ...] = ()
    search_fields = (
        "descripcion", "codigo_principal", "codigo_auxiliar", "unidad_medida",
        "datos_adicionales", "producto__codigo_principal", "producto__descripcion",
    )
    autocomplete_fields = ("producto",)
    list_per_page = 50
    ordering = ("-pk",)

    def get_list_select_related(self, request: Any) -> Tuple[str, ...]:
        """El comprobante cambia en cada línea, así que se resuelve aquí."""
        return ("producto", self.campo_documento)

    def get_list_filter(self, request: Any) -> List[Any]:
        """Filtros de la línea + los que añada la tienda desde los ajustes."""
        documento = self.campo_documento
        contraparte = self.ruta_contraparte
        propios: List[Any] = [
            filtro_por_fecha(f"{documento}__fecha_emision", "Fecha del comprobante"),
            "codigo_porcentaje_iva",
            f"{documento}__comprobante__estado",
            f"{contraparte}__tipo_identificacion",
            filtro_por_importe(f"{documento}__comprobante__importe_total", "Importe"),
        ]
        # ``super()`` añade los filtros configurados en los ajustes ADMIN.
        for filtro in super().get_list_filter(request):
            if filtro not in propios:
                propios.append(filtro)
        return propios

    def get_search_fields(self, request: Any) -> List[str]:
        base = list(super().get_search_fields(request))
        for campo in (
            f"{self.ruta_contraparte}__razon_social",
            f"{self.ruta_contraparte}__identificacion",
            f"{self.campo_documento}__secuencial",
            f"{self.campo_documento}__comprobante__clave_acceso",
        ):
            if campo not in base:
                base.append(campo)
        return base

    @admin.display(description="Comprobante")
    def documento(self, obj: Any) -> Any:
        from django.urls import reverse
        from django.utils.html import format_html

        documento = getattr(obj, self.campo_documento, None)
        if documento is None:
            return "—"
        try:
            url = reverse(
                f"admin:sri_fe_{documento._meta.model_name}_change", args=[documento.pk]
            )
        except NoReverseMatch:
            return str(documento)
        return format_html('<a href="{}">{}</a>', url, documento)

    @admin.display(description="Total")
    def total_linea_mostrado(self, obj: Any) -> Any:
        try:
            return obj.total
        except Exception:  # noqa: BLE001 - informativo
            return "—"

    @admin.display(description="IVA")
    def iva_mostrado(self, obj: Any) -> str:
        return obj.get_codigo_porcentaje_iva_display()

    @admin.display(description="Código")
    def codigo_mostrado(self, obj: Any) -> str:
        if obj.codigo_auxiliar:
            return f"{obj.codigo_principal} / {obj.codigo_auxiliar}"
        return obj.codigo_principal or "—"


def _admin_de_linea(nombre: str, modelo: Any, campo_documento: str, contraparte: str) -> None:
    """Registra el admin de una línea con el comprobante y la contraparte correctos."""
    clase = type(
        nombre,
        (LineaAdminBase,),
        {
            "campo_documento": campo_documento,
            "ruta_contraparte": contraparte,
            "__module__": __name__,
        },
    )
    admin.site.register(modelo, clase)


_admin_de_linea("FacturaDetalleAdmin", documentos.FacturaDetalle, "factura", "factura__receptor")
_admin_de_linea(
    "LiquidacionCompraDetalleAdmin", documentos.LiquidacionCompraDetalle,
    "liquidacion", "liquidacion__proveedor",
)
_admin_de_linea(
    "NotaCreditoDetalleAdmin", documentos.NotaCreditoDetalle,
    "nota_credito", "nota_credito__receptor",
)


@admin.register(documentos.GuiaDetalle)
class GuiaDetalleAdmin(AdminConAjustes):
    """Bienes transportados en las guías."""

    list_display = ("destinatario", "descripcion", "cantidad", "codigo_principal",
                    "codigo_adicional", "datos_adicionales")
    list_filter = ("destinatario__motivo_traslado",)
    search_fields = (
        "descripcion", "codigo_principal", "codigo_adicional",
        "destinatario__razon_social", "destinatario__identificacion",
        "destinatario__guia__secuencial", "destinatario__guia__placa",
    )
    list_select_related = ("destinatario",)
    list_per_page = 50
    ordering = ("destinatario", "pk")


@admin.register(documentos.NotaDebitoMotivo)
class NotaDebitoMotivoAdmin(AdminConAjustes):
    """Motivos de las notas de débito."""

    list_display = ("razon", "valor", "nota_debito")
    list_filter = ("nota_debito__comprobante__estado",
                   filtro_por_fecha("nota_debito__fecha_emision", "Fecha de la nota"))
    search_fields = ("razon", "valor", "nota_debito__secuencial",
                     "nota_debito__receptor__razon_social",
                     "nota_debito__receptor__identificacion",
                     "nota_debito__comprobante__clave_acceso")
    list_select_related = ("nota_debito",)
    list_per_page = 50
    ordering = ("nota_debito", "pk")


@admin.register(documentos.RetencionImpuesto)
class RetencionImpuestoAdmin(AdminConAjustes):
    """Impuestos retenidos (renta, IVA, ISD…) de cada documento sustento."""

    list_display = ("codigo_retencion", "codigo", "base_imponible", "porcentaje_retener",
                    "valor_retenido", "doc_sustento")
    list_filter = ("codigo", "codigo_retencion", "porcentaje_retener",
                   "doc_sustento__cod_sustento")
    search_fields = (
        "codigo_retencion", "codigo", "porcentaje_retener",
        "doc_sustento__num_doc_sustento",
        "doc_sustento__retencion__secuencial",
        "doc_sustento__retencion__sujeto_retenido__razon_social",
        "doc_sustento__retencion__sujeto_retenido__identificacion",
    )
    list_select_related = ("doc_sustento",)
    list_per_page = 50
    ordering = ("-pk",)


@admin.register(documentos.RetencionDocSustentoImpuesto)
class RetencionDocSustentoImpuestoAdmin(AdminConAjustes):
    """Impuestos del documento sustento (base para calcular la retención)."""

    list_display = ("codigo", "codigo_porcentaje", "base_imponible", "valor", "doc_sustento")
    list_filter = ("codigo", "codigo_porcentaje")
    search_fields = ("codigo", "codigo_porcentaje", "doc_sustento__num_doc_sustento",
                     "doc_sustento__retencion__secuencial")
    list_select_related = ("doc_sustento",)
    list_per_page = 50
    ordering = ("-pk",)
