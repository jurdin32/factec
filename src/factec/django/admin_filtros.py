"""Filtros del admin reutilizables y ajustes dinámicos de filtros y búsquedas.

Dos cosas:

1. **Filtros listos para usar** en cualquier modelo del paquete: rango de fechas,
   rango de importes, con/sin comprobante, con/sin firma cargada.
2. **Ajustes dinámicos**: cada tienda puede añadir (o reemplazar) filtros,
   búsquedas y columnas desde ``settings``, sin tocar el paquete::

       FACTURACION_ELECTRONICA = {
           "ADMIN": {
               # para todos los modelos
               "_todos": {"filtros": [FiltroPorFecha("fecha_emision", "Emitidas")]},
               "factura": {
                   "filtros": ["receptor__tipo_identificacion", "mi_app.filtros.PorSucursal"],
                   "busqueda": ["receptor__direccion", "detalles__descripcion"],
                   "columnas": ["mi_app.admin.columna_sucursal"],
               },
               "producto": {"solo": True, "filtros": ["activo"]},   # reemplaza los del paquete
           },
       }

   Las entradas pueden ser el nombre de un campo (``"forma_pago"``), la ruta a un
   filtro propio del proyecto (``"mi_app.filtros.MiFiltro"``) o, en las columnas,
   la ruta a una función.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django.contrib import admin

__all__ = [
    "AdminConAjustes",
    "FiltroConCertificado",
    "FiltroFechaDesactualizada",
    "FiltroEmitido",
    "FiltroPorFecha",
    "FiltroPorImporte",
    "entradas_del_modelo",
    "filtro_emitido",
    "filtro_por_fecha",
    "filtro_por_importe",
]

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------- filtros


class FiltroPorFecha(admin.SimpleListFilter):
    """Rangos de fecha útiles: hoy, últimos días, este mes, mes anterior, año.

    Django instancia los filtros con ``(request, params, model, model_admin)``, así
    que el campo y el título son atributos de clase: use
    :func:`filtro_por_fecha` para crear uno para su campo.
    """

    campo = "fecha_emision"
    parameter_name = "rango_fecha"
    title = "Fecha"

    def lookups(self, request: Any, model_admin: Any) -> Tuple[Tuple[str, str], ...]:
        return (
            ("hoy", "Hoy"),
            ("7dias", "Últimos 7 días"),
            ("30dias", "Últimos 30 días"),
            ("mes", "Este mes"),
            ("mes_pasado", "Mes anterior"),
            ("anio", "Este año"),
            ("sin_fecha", "Sin fecha"),
        )

    def queryset(self, request: Any, queryset: Any) -> Any:
        valor = self.value()
        if not valor:
            return queryset

        from ..sri import fechas

        # El mismo «hoy» con el que se emite y se valida: el de Ecuador. Con el
        # reloj del servidor (TIME_ZONE=UTC) el día va por delante desde las
        # 19:00 de Ecuador y «Hoy» saldría vacío.
        hoy = fechas.hoy_en_ecuador()
        campo = self.campo
        if valor == "hoy":
            return queryset.filter(**{campo: hoy})
        if valor == "7dias":
            return queryset.filter(**{f"{campo}__gte": hoy - timedelta(days=7)})
        if valor == "30dias":
            return queryset.filter(**{f"{campo}__gte": hoy - timedelta(days=30)})
        if valor == "mes":
            return queryset.filter(**{f"{campo}__year": hoy.year, f"{campo}__month": hoy.month})
        if valor == "mes_pasado":
            primero = hoy.replace(day=1) - timedelta(days=1)
            return queryset.filter(
                **{f"{campo}__year": primero.year, f"{campo}__month": primero.month}
            )
        if valor == "anio":
            return queryset.filter(**{f"{campo}__year": hoy.year})
        if valor == "sin_fecha":
            return queryset.filter(**{f"{campo}__isnull": True})
        return queryset


class FiltroPorImporte(admin.SimpleListFilter):
    """Rangos de importe: para ver de un vistazo las facturas grandes o pequeñas."""

    campo = "importe_total"
    parameter_name = "rango_importe"
    title = "Importe"

    RANGOS = (
        ("0_10", "Hasta 10"),
        ("10_50", "De 10 a 50"),
        ("50_100", "De 50 a 100"),
        ("100_500", "De 100 a 500"),
        ("500_1000", "De 500 a 1000"),
        ("1000_mas", "Más de 1000"),
    )

    def lookups(self, request: Any, model_admin: Any) -> Tuple[Tuple[str, str], ...]:
        return self.RANGOS

    def queryset(self, request: Any, queryset: Any) -> Any:
        valor = self.value()
        if not valor:
            return queryset

        campo = self.campo
        if valor == "1000_mas":
            return queryset.filter(**{f"{campo}__gte": Decimal("1000")})

        limites = {
            "0_10": (Decimal("0"), Decimal("10")),
            "10_50": (Decimal("10"), Decimal("50")),
            "50_100": (Decimal("50"), Decimal("100")),
            "100_500": (Decimal("100"), Decimal("500")),
            "500_1000": (Decimal("500"), Decimal("1000")),
        }
        minimo, maximo = limites[valor]
        return queryset.filter(**{f"{campo}__gte": minimo, f"{campo}__lt": maximo})


class FiltroEmitido(admin.SimpleListFilter):
    """Separa lo que ya se envió al SRI de lo que está a medias."""

    campo = "comprobante"
    parameter_name = "emitido"
    title = "Emisión"

    def lookups(self, request: Any, model_admin: Any) -> Tuple[Tuple[str, str], ...]:
        return (("si", "Emitidos al SRI"), ("no", "Sin emitir"))

    def queryset(self, request: Any, queryset: Any) -> Any:
        campo = self.campo
        if self.value() == "si":
            return queryset.filter(**{f"{campo}__isnull": False})
        if self.value() == "no":
            return queryset.filter(**{f"{campo}__isnull": True})
        return queryset


class FiltroConCertificado(admin.SimpleListFilter):
    """Firma electrónica cargada o no (y con contraseña guardada)."""

    parameter_name = "firma"
    title = "Firma electrónica"

    def lookups(self, request: Any, model_admin: Any) -> Tuple[Tuple[str, str], ...]:
        return (
            ("cargada", "Con archivo de firma"),
            ("sin_archivo", "Sin archivo de firma"),
            ("con_clave", "Con contraseña guardada"),
            ("sin_clave", "Sin contraseña"),
        )

    def queryset(self, request: Any, queryset: Any) -> Any:
        valor = self.value()
        if valor == "cargada":
            return queryset.exclude(certificado="")
        if valor == "sin_archivo":
            return queryset.filter(certificado="")
        if valor == "con_clave":
            return queryset.exclude(clave_certificado_cifrada="")
        if valor == "sin_clave":
            return queryset.filter(clave_certificado_cifrada="")
        return queryset


class FiltroFechaDesactualizada(admin.SimpleListFilter):
    """Comprobantes que quedaron sin enviar y ya no son del día de hoy.

    Son los que hay que refechar (o rehacer) antes de firmarlos: el SRI rechaza un
    comprobante firmado con la fecha de otro día.
    """

    parameter_name = "fecha_desactualizada"
    title = "Fecha de emisión"

    def lookups(self, request: Any, model_admin: Any) -> Tuple[Tuple[str, str], ...]:
        return (
            ("si", "Sin enviar, de otro día (hay que refechar)"),
            ("hoy", "Sin enviar, de hoy"),
        )

    def queryset(self, request: Any, queryset: Any) -> Any:
        from ..sri import fechas

        hoy = fechas.hoy_en_ecuador()
        if self.value() == "si":
            return queryset.filter(intentos=0).exclude(fecha_emision=hoy)
        if self.value() == "hoy":
            return queryset.filter(intentos=0, fecha_emision=hoy)
        return queryset


def _subclase(base: type, campo: Optional[str], titulo: Optional[str], prefijo: str) -> type:
    """Crea una subclase de ``base`` con los atributos de clase ya puestos."""
    atributos: Dict[str, Any] = {}
    nombre = base.__name__
    if campo:
        atributos["campo"] = campo
        nombre = f"{nombre}_{campo.replace('__', '_')}"
        atributos["parameter_name"] = f"{prefijo}_{campo.replace('__', '_')}"
    if titulo:
        atributos["title"] = titulo

    return type(nombre, (base,), atributos)


def filtro_por_fecha(campo: str = "fecha_emision", titulo: Optional[str] = None) -> type:
    """Filtro de rango de fechas para ``campo``."""
    return _subclase(FiltroPorFecha, campo, titulo or "Fecha", "rango")


def filtro_por_importe(campo: str = "importe_total", titulo: Optional[str] = None) -> type:
    """Filtro de rango de importes para ``campo``."""
    return _subclase(FiltroPorImporte, campo, titulo or "Importe", "importe")


def filtro_emitido(campo: str = "comprobante", titulo: Optional[str] = None) -> type:
    """Filtro «emitidos / sin emitir» para el campo indicado."""
    return _subclase(FiltroEmitido, campo, titulo or "Emisión", "emitido")


# -------------------------------------------------- ajustes dinámicos del admin


def entradas_del_modelo(modelo: Any, clave: str) -> Tuple[List[Any], bool]:
    """Filtros, búsquedas o columnas que la tienda añadió para ``modelo``.

    Devuelve ``(entradas, solo)``: ``solo=True`` significa reemplazar lo que trae
    el paquete en lugar de añadirse.
    """
    from . import conf

    ajustes = conf.obtener("ADMIN", {}) or {}
    nombre = modelo._meta.model_name.lower()

    def limpiar(valor: Any) -> List[Any]:
        if not valor:
            return []
        if isinstance(valor, (str, type)) or callable(valor):
            return [valor]
        return list(valor)

    general = ajustes.get("_todos", {}) or {}
    propio = ajustes.get(nombre, {}) or {}

    entradas = limpiar(general.get(clave)) + limpiar(propio.get(clave))
    solo = bool(propio.get("solo", general.get("solo", False)))
    return entradas, solo


def resolver_entrada(entrada: Any, *, con_clases: bool) -> Any:
    """Traduce una entrada de los ajustes al objeto que espera Django.

    * ``"campo"`` → el nombre del campo.
    * ``"mi_app.filtros.MiFiltro"`` → la clase o la función que haya en esa ruta.
    """
    if not isinstance(entrada, str) or "." not in entrada:
        return entrada

    from django.utils.module_loading import import_string

    try:
        return import_string(entrada)
    except ImportError as error:
        logger.warning(
            "No se pudo importar «%s» de los ajustes ADMIN de factec: %s", entrada, error
        )
        return None


class AdminConAjustes(admin.ModelAdmin):
    """Admin que aplica los ajustes ``ADMIN`` de ``FACTURACION_ELECTRONICA``.

    Los ajustes se leen en cada petición, así que cambiar ``settings`` no obliga a
    reiniciar nada más: el admin refleja lo que hay configurado.
    """

    def get_list_filter(self, request: Any) -> List[Any]:
        """Filtros del paquete más los que añada la tienda."""
        base = list(super().get_list_filter(request))
        entradas, solo = entradas_del_modelo(self.model, "filtros")
        resueltas = [valor for valor in (resolver_entrada(e, con_clases=True) for e in entradas) if valor]
        if not resueltas:
            return base
        return resueltas if solo else base + [r for r in resueltas if r not in base]

    def get_search_fields(self, request: Any) -> List[str]:
        """Campos de búsqueda del paquete más los de la tienda."""
        base = list(super().get_search_fields(request))
        entradas, solo = entradas_del_modelo(self.model, "busqueda")
        resueltas = [str(entrada) for entrada in entradas]
        if not resueltas:
            return base
        return resueltas if solo else base + [r for r in resueltas if r not in base]

    def get_list_display(self, request: Any) -> List[Any]:
        """Columnas del paquete más las de la tienda (admiten rutas a funciones)."""
        base = list(super().get_list_display(request))
        entradas, solo = entradas_del_modelo(self.model, "columnas")
        resueltas = [
            valor for valor in (resolver_entrada(e, con_clases=False) for e in entradas) if valor
        ]
        if not resueltas:
            return base
        return resueltas if solo else base + [r for r in resueltas if r not in base]

    def get_readonly_fields(self, request: Any, obj: Any = None) -> Sequence[str]:
        """Campos de solo lectura del paquete más los de la tienda."""
        base = list(super().get_readonly_fields(request, obj))
        entradas, solo = entradas_del_modelo(self.model, "solo_lectura")
        resueltas = [str(entrada) for entrada in entradas]
        if not resueltas:
            return base
        return resueltas if solo else base + [r for r in resueltas if r not in base]
