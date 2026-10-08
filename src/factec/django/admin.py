"""Administración de Django para la facturación electrónica."""

from __future__ import annotations

from typing import Any

from django.contrib import admin, messages
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from ..excepciones import ErrorFacturacion
from . import conf, models, services, sri_datos
from .admin_documentos import *  # noqa: F401,F403  (registra los comprobantes)
from .forms import ConfiguracionEmisorForm

__all__ = ["ConfiguracionEmisorAdmin", "ComprobanteEmitidoAdmin", "SecuencialAdmin"]


@admin.register(models.ConfiguracionEmisor)
class ConfiguracionEmisorAdmin(admin.ModelAdmin):
    """Datos del contribuyente y carga del archivo de firma.

    Al guardar se abre el ``.p12`` para comprobar la contraseña, la vigencia y que
    el RUC del certificado coincida con el del emisor.
    """

    form = ConfiguracionEmisorForm
    list_display = (
        "nombre", "ruc", "razon_social", "ambiente", "serie",
        "regimen_mostrado", "firma_estado", "activo",
    )
    list_filter = ("activo", "ambiente", "obligado_contabilidad")
    search_fields = ("nombre", "ruc", "razon_social", "nombre_comercial")
    fieldsets = (
        ("Identificación", {
            "fields": ("nombre", "activo", "ambiente"),
        }),
        ("Contribuyente", {
            "fields": ("ruc", "consultar_sri", "razon_social", "nombre_comercial",
                       "dir_matriz", "dir_establecimiento"),
            "description": "Escriba el RUC y guarde: el resto de datos del "
                           "contribuyente se consultan en el SRI automáticamente. "
                           "Solo hay que completar lo que no consta en el servicio "
                           "(nombre comercial y direcciones).",
        }),
        ("Establecimiento y punto de emisión", {
            "fields": ("estab", "pto_emi"),
            "description": "La serie del comprobante es establecimiento + punto de "
                           "emisión. No consta en el SRI: indíquelos usted.",
        }),
        ("Obligaciones declaradas en el SRI", {
            "fields": ("obligado_contabilidad", "contribuyente_especial",
                       "agente_retencion", "regimen", "categoria"),
            "description": "Se completan con el catastro del SRI. El SRI solo dice "
                           "si es contribuyente especial o agente de retención, no "
                           "el número de resolución: escríbalo si procede.",
        }),
        ("Campos adicionales de la tienda", {
            "fields": ("campos_adicionales",),
            "description": "Campos que se añaden a todos los comprobantes (hasta 15), "
                           "en formato NOMBRE=VALOR separados por «;»: "
                           "VENDEDOR=JOHNNY; SUCURSAL=NORTE. En cada comprobante puede "
                           "sobrescribirlos o añadir más.",
        }),
        ("Firma electrónica", {
            "fields": ("certificado", "clave_certificado", "firma_diagnostico"),
            "description": "El archivo .p12 y su contraseña se usan para firmar los "
                           "comprobantes. La contraseña se guarda cifrada.",
        }),
        ("Auditoría", {
            "classes": ("collapse",),
            "fields": ("creado", "actualizado"),
        }),
    )
    readonly_fields = ("creado", "actualizado", "firma_diagnostico")
    actions = ("accion_probar_firma", "accion_actualizar_desde_sri")
    save_on_top = True

    @admin.display(description="Serie")
    def serie(self, obj: models.ConfiguracionEmisor) -> str:
        return obj.serie

    @admin.display(description="Régimen")
    def regimen_mostrado(self, obj: models.ConfiguracionEmisor) -> str:
        if not obj.es_rimpe:
            return obj.regimen or "General"
        return f"RIMPE — {obj.categoria}" if obj.categoria else "RIMPE"

    @admin.display(description="Firma")
    def firma_estado(self, obj: models.ConfiguracionEmisor) -> str:
        """Resumen del estado de la firma para el listado (nunca lanza excepción)."""
        if not obj.tiene_certificado:
            return format_html('<span style="color:#a51f1f">{}</span>', "sin archivo")
        if not obj.clave_certificado_cifrada:
            return format_html('<span style="color:#a51f1f">{}</span>', "sin contraseña")
        try:
            certificado = obj.certificado_obj(validar_vigencia=False)
        except Exception as exc:  # noqa: BLE001 - el listado nunca debe romperse
            return format_html('<span style="color:#a51f1f">{}</span>', str(exc)[:60])
        if certificado.vencido():
            return format_html('<span style="color:#a51f1f">{}</span>', "vencido")
        return format_html('<span style="color:#0a7d33">{}</span>', "válido")

    @admin.display(description="Diagnóstico de la firma")
    def firma_diagnostico(self, obj: models.ConfiguracionEmisor) -> str:
        """Muestra el estado del certificado.

        Nunca lanza excepción: esta función se ejecuta al pintar el formulario y
        un error aquí dejaría la página inutilizable.
        """
        if not obj or not obj.pk:
            return "Guarde la configuración para ver el diagnóstico."
        if not obj.tiene_certificado:
            return "Adjunte el archivo .p12 y guarde para validarlo."
        if not obj.clave_certificado_cifrada:
            return "Escriba la contraseña del certificado y guarde para validarlo."
        try:
            certificado = obj.certificado_obj()
        except Exception as exc:  # noqa: BLE001 - el admin nunca debe romperse aquí
            return format_html('<span style="color:#a51f1f">{}</span>', exc)
        ruta_hasta = getattr(certificado.certificado, "not_valid_after_utc", None) or (
            certificado.certificado.not_valid_after
        )
        return format_html(
            "<b>Titular:</b> {}<br><b>Emisor:</b> {}<br><b>Válido hasta:</b> {}",
            certificado.titular, certificado.emisor, ruta_hasta,
        )

    @admin.action(description="Probar la firma del certificado")
    def accion_probar_firma(self, request: Any, queryset: Any) -> None:
        for configuracion in queryset:
            try:
                avisos = configuracion.validar_firma()
            except ErrorFacturacion as exc:
                self.message_user(
                    request, f"{configuracion}: {exc}", level=messages.ERROR
                )
                continue
            if avisos:
                for aviso in avisos:
                    self.message_user(request, f"{configuracion}: {aviso}", level=messages.WARNING)
            else:
                self.message_user(
                    request, f"{configuracion}: certificado correcto.", level=messages.SUCCESS
                )

    @admin.action(description="Actualizar los datos desde el SRI")
    def accion_actualizar_desde_sri(self, request: Any, queryset: Any) -> None:
        """Vuelve a consultar el catastro y sobrescribe los datos del contribuyente."""
        actualizadas = 0
        for configuracion in queryset:
            try:
                cambios = sri_datos.completar_configuracion(configuracion, forzar=True)
            except ErrorFacturacion as exc:
                self.message_user(
                    request, f"{configuracion}: {exc}", level=messages.ERROR
                )
                continue
            configuracion.save()
            actualizadas += 1
            if cambios:
                self.message_user(
                    request,
                    f"{configuracion}: actualizado {', '.join(cambios)}.",
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request, f"{configuracion}: sin cambios.", level=messages.INFO
                )
        if actualizadas:
            self.message_user(
                request,
                f"{actualizadas} configuración(es) consultadas en el SRI.",
                level=messages.SUCCESS,
            )

    def save_model(self, request: Any, obj: models.ConfiguracionEmisor, form: Any, change: bool) -> None:
        super().save_model(request, obj, form, change)

        # Avisos de la consulta al SRI: informan sin bloquear el guardado.
        for aviso in getattr(form, "avisos_sri", []) or []:
            self.message_user(request, aviso, level=messages.WARNING)

        faltan = sri_datos.faltantes_manuales(obj)
        campos_manuales_obligatorios = [
            campo for campo in faltan
            if campo in ("dir_matriz", "estab", "pto_emi", "ambiente")
        ]
        if campos_manuales_obligatorios:
            self.message_user(
                request,
                "Quedan campos por completar (no constan en el SRI): "
                + ", ".join(campos_manuales_obligatorios),
                level=messages.WARNING,
            )

        # La configuración pudo cambiar: se descarta la caché de cliente y certificado.
        conf.limpiar_cache()


@admin.register(models.ComprobanteEmitido)
class ComprobanteEmitidoAdmin(admin.ModelAdmin):
    """Listado y detalle de los comprobantes emitidos."""

    list_display = (
        "numero_comprobante", "tipo_comprobante", "fecha_emision",
        "razon_social_receptor", "importe_total", "estado_badge",
        "numero_autorizacion", "documento_origen", "creado",
    )
    list_select_related = ("content_type",)
    list_filter = ("estado", "tipo_comprobante", "ambiente", "fecha_emision")
    search_fields = (
        "clave_acceso", "numero_autorizacion", "razon_social_receptor",
        "identificacion_receptor", "secuencial",
    )
    date_hierarchy = "creado"
    ordering = ("-creado",)
    actions = ("accion_emitir", "accion_consultar_autorizacion")
    list_per_page = 50

    readonly_fields = (
        "clave_acceso", "tipo_comprobante", "ambiente", "tipo_emision",
        "estab", "pto_emi", "secuencial", "fecha_emision", "documento_origen",
        "razon_social_receptor", "identificacion_receptor", "importe_total",
        "estado", "numero_autorizacion", "fecha_autorizacion", "mensajes",
        "intentos", "error", "creado", "actualizado",
        "xml_sin_firma", "xml_firmado", "xml_autorizado",
    )
    fieldsets = (
        ("Identificación", {
            "fields": ("clave_acceso", "tipo_comprobante", "ambiente", "tipo_emision",
                       "fecha_emision"),
        }),
        ("Emisión", {
            "fields": ("estab", "pto_emi", "secuencial", "documento_origen"),
        }),
        ("Receptor", {
            "fields": ("razon_social_receptor", "identificacion_receptor", "importe_total"),
        }),
        ("Estado ante el SRI", {
            "fields": ("estado", "numero_autorizacion", "fecha_autorizacion",
                       "intentos", "error", "mensajes"),
        }),
        ("XML", {
            "classes": ("collapse",),
            "fields": ("xml_sin_firma", "xml_firmado", "xml_autorizado"),
        }),
        ("Auditoría", {
            "classes": ("collapse",),
            "fields": ("creado", "actualizado"),
        }),
    )

    @admin.display(description="Comprobante", ordering="secuencial")
    def numero_comprobante(self, obj: models.ComprobanteEmitido) -> str:
        return obj.numero_comprobante

    @admin.display(description="Documento de origen")
    def documento_origen(self, obj: models.ComprobanteEmitido) -> str:
        """Enlace al documento que originó el comprobante (factura, nota, guía…)."""
        if not obj.content_type_id or not obj.object_id:
            return "—"
        try:
            url = reverse(
                f"admin:{obj.content_type.app_label}_{obj.content_type.model}_change",
                args=[obj.object_id],
            )
        except NoReverseMatch:  # sin admin propio: se muestra el nombre y ya
            return f"{obj.content_type.name} #{obj.object_id}"
        return format_html(
            '<a href="{}">{} #{}</a>', url, obj.content_type.name, obj.object_id
        )

    @admin.display(description="Estado", ordering="estado")
    def estado_badge(self, obj: models.ComprobanteEmitido) -> str:
        colores = {
            models.EstadoComprobante.AUTORIZADO: "#0a7d33",
            models.EstadoComprobante.RECIBIDO: "#0b5fa5",
            models.EstadoComprobante.EN_PROCESO: "#8a6d00",
            models.EstadoComprobante.BORRADOR: "#555555",
            models.EstadoComprobante.FIRMADO: "#555555",
            models.EstadoComprobante.DEVUELTO: "#a51f1f",
            models.EstadoComprobante.NO_AUTORIZADO: "#a51f1f",
            models.EstadoComprobante.ERROR: "#a51f1f",
        }
        color = colores.get(obj.estado, "#555555")
        return format_html(
            '<strong style="color:{}">{}</strong>', color, obj.get_estado_display()
        )

    @admin.action(description="Firmar, enviar y esperar autorización (Celery)")
    def accion_emitir(
        self, request: Any, queryset: Any
    ) -> None:
        encolados = 0
        for registro in queryset:
            try:
                if services.encolar(registro):
                    encolados += 1
                else:
                    services.procesar(registro)
                    encolados += 1
            except ErrorFacturacion as exc:
                self.message_user(
                    request, f"{registro.clave_acceso}: {exc}", level=messages.ERROR
                )
        if encolados:
            self.message_user(
                request, f"{encolados} comprobante(s) en proceso de emisión.",
                level=messages.SUCCESS,
            )

    @admin.action(description="Consultar autorización en el SRI")
    def accion_consultar_autorizacion(self, request: Any, queryset: Any) -> None:
        actualizados = 0
        for registro in queryset:
            try:
                services.autorizar(registro)
                actualizados += 1
            except ErrorFacturacion as exc:
                self.message_user(
                    request, f"{registro.clave_acceso}: {exc}", level=messages.ERROR
                )
        if actualizados:
            self.message_user(
                request, f"{actualizados} comprobante(s) consultados.", level=messages.SUCCESS
            )


@admin.register(models.Secuencial)
class SecuencialAdmin(admin.ModelAdmin):
    """Contadores de secuenciales por tipo, establecimiento y ambiente."""

    list_display = ("tipo_comprobante", "estab", "pto_emi", "ambiente", "ultimo", "actualizado")
    list_filter = ("tipo_comprobante", "ambiente")
    ordering = ("tipo_comprobante", "estab", "pto_emi")
