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
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("razon_social", "identificacion", "tipo_identificacion", "direccion")
    list_filter = ("tipo_identificacion",)
    search_fields = ("razon_social", "identificacion")
    ordering = ("razon_social",)


@admin.register(documentos.Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "codigo_principal", "descripcion", "unidad_medida",
        "precio_unitario", "codigo_porcentaje_iva", "activo",
    )
    list_filter = ("activo", "codigo_porcentaje_iva")
    search_fields = ("codigo_principal", "codigo_auxiliar", "descripcion")
    list_editable = ("activo",)
    ordering = ("descripcion",)

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


class DocumentoAdmin(admin.ModelAdmin):
    """Base del admin de los comprobantes: listado, estado y acciones."""

    #: Nombre del campo con la otra parte (receptor, proveedor, sujeto retenido).
    campo_contraparte = "receptor"

    #: Nombre de la propiedad con el importe total, si el comprobante tiene una.
    campo_total: Optional[str] = "total"

    #: Etiqueta del importe total (cambia según el comprobante).
    etiqueta_total = "Total"

    #: Relaciones que conviene traer de una vez para el listado.
    prefetch_relacionado: Tuple[str, ...] = ()

    list_display = (
        "numero", "fecha_emision", "contraparte", "total_mostrado",
        "estado_badge", "numero_autorizacion_mostrado",
    )
    list_filter = ("comprobante__estado", "fecha_emision")
    search_fields = ("secuencial", "observaciones", "comprobante__clave_acceso")
    date_hierarchy = "fecha_emision"
    ordering = ("-fecha_emision", "-pk")
    actions = ("accion_emitir", "accion_reintentar")
    save_on_top = True
    list_per_page = 50
    readonly_fields = (
        "secuencial", "comprobante", "estado_mostrado", "clave_acceso_mostrada",
        "importes_mostrados", "creado", "actualizado",
    )

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
    list_filter = ("comprobante__estado", "fecha_emision", "forma_pago")
    search_fields = ("secuencial", "receptor__razon_social", "receptor__identificacion",
                     "comprobante__clave_acceso")
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
    list_filter = ("comprobante__estado", "fecha_emision", "forma_pago")
    search_fields = ("secuencial", "proveedor__razon_social", "proveedor__identificacion",
                     "comprobante__clave_acceso")
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
class GuiaDestinatarioAdmin(admin.ModelAdmin):
    """Destinatarios de las guías, con los bienes que se transportan."""

    list_display = ("razon_social", "identificacion", "motivo_traslado", "guia")
    list_filter = ("motivo_traslado",)
    search_fields = ("razon_social", "identificacion", "guia__secuencial")
    list_select_related = ("guia",)
    inlines = (GuiaDetalleInline,)
    readonly_fields = ("guia",)


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
    list_filter = ("comprobante__estado", "fecha_emision", "parte_rel")
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
class RetencionDocSustentoAdmin(admin.ModelAdmin):
    """Documentos que sustentan cada retención, con sus impuestos y retenciones."""

    list_display = ("num_doc_sustento", "cod_doc_sustento", "fecha_emision",
                    "total_sin_impuestos", "importe_total", "retencion")
    list_filter = ("cod_doc_sustento", "cod_sustento", "fecha_emision")
    search_fields = ("num_doc_sustento", "num_aut_doc_sustento", "retencion__secuencial")
    list_select_related = ("retencion",)
    inlines = (RetencionDocSustentoImpuestoInline, RetencionImpuestoInline)
    readonly_fields = ("retencion",)
