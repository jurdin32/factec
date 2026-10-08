"""Modelos de facturación electrónica del SRI: catálogos y comprobantes.

Se registran solos: basta con añadir la app a ``INSTALLED_APPS`` y ejecutar
``python manage.py migrate``.

* **Catálogos**: :class:`Cliente` (adquirente) y :class:`Producto`.
* **Comprobantes** con sus líneas: :class:`Factura` (``01``),
  :class:`LiquidacionCompra` (``03``), :class:`NotaCredito` (``04``),
  :class:`NotaDebito` (``05``), :class:`GuiaRemision` (``06``) y
  :class:`Retencion` (``07``).

Cada campo se llama y mide lo mismo que el elemento del SRI (la longitud sale del
esquema oficial), así que los adaptadores del paquete emiten cualquiera de estos
modelos sin configuración::

    factura = Factura.objects.create(receptor=cliente)
    factura.detalles.create(producto=producto, cantidad=2)
    factura.emitir()                      # XML, firma y envío al SRI
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from ..catalogos import (
    DESCRIPCION_FORMA_PAGO,
    DESCRIPCION_MOTIVO_TRASLADO,
    DESCRIPCION_TARIFA_IVA,
    DESCRIPCION_TIPO_COMPROBANTE,
    DESCRIPCION_TIPO_IDENTIFICACION,
    PORCENTAJE_IVA,
    CodigoImpuesto,
    CodigoRetencion,
    FormaPago,
    TarifaIva,
    TipoComprobante,
    TipoIdentificacion,
    TipoSujetoRetenido,
)
from ..comprobantes import calcular_totales
from ..modelos import Detalle, Impuesto, cuantizar
from .campos_adicionales import (
    MAXIMO_CAMPOS_ADICIONALES,
    MAXIMO_DATOS_ADICIONALES,
    escribir_campos_adicionales,
    leer_campos_adicionales,
    validar_cantidad,
)

__all__ = [
    "escribir_campos_adicionales",
    "leer_campos_adicionales",
    "Cliente",
    "Producto",
    "DocumentoElectronico",
    "LineaDocumento",
    "Factura",
    "FacturaDetalle",
    "LiquidacionCompra",
    "LiquidacionCompraDetalle",
    "NotaCredito",
    "NotaCreditoDetalle",
    "NotaDebito",
    "NotaDebitoMotivo",
    "Retencion",
    "RetencionDocSustento",
    "RetencionDocSustentoImpuesto",
    "RetencionImpuesto",
    "GuiaRemision",
    "GuiaDestinatario",
    "GuiaDetalle",
]


# --------------------------------------------------------------------- ayudas


def _opciones(descripciones: Dict[str, str]) -> List[Tuple[str, str]]:
    """Convierte un catálogo del paquete en ``choices`` de Django."""
    return [(codigo, texto) for codigo, texto in descripciones.items()]


def _opciones_enum(enum: Any, textos: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    """``choices`` a partir de un catálogo del paquete, con o sin descripciones."""
    textos = textos or {}
    return [(miembro.value, textos.get(miembro.value, miembro.name.replace("_", " ").title()))
            for miembro in enum]


#: El secuencial del SRI son 9 dígitos; se guarda sin ceros a la izquierda.
SECUENCIAL_VALIDO = RegexValidator(r"^\d{1,9}$", "El secuencial debe tener hasta 9 dígitos.")

#: ``numDocModificado`` del SRI: ``001-001-000000001``.
SERIE_NUMERO_VALIDO = RegexValidator(
    r"^\d{3}-\d{3}-\d{9}$", "Escriba el número como 001-001-000000001."
)


def _completar_serie(valor: str, valido: RegexValidator = SERIE_NUMERO_VALIDO) -> str:
    """Normaliza ``001001000000001`` (15 dígitos) a ``001-001-000000001``."""
    texto = (valor or "").strip()
    if texto and valido.regex.match(texto) is None:
        digitos = "".join(caracter for caracter in texto if caracter.isdigit())
        if len(digitos) == 15:
            return f"{digitos[:3]}-{digitos[3:6]}-{digitos[6:]}"
    return texto


def _solo_digitos(valor: str) -> str:
    return "".join(caracter for caracter in (valor or "") if caracter.isdigit())


def _importe(valor: Any) -> Decimal:
    """Convierte a ``Decimal`` con dos decimales."""
    return cuantizar(Decimal(str(valor or 0)), 2)


#: Tipos de identificación del SRI (tabla 2: ``04`` a ``08``).
OPCIONES_TIPO_IDENTIFICACION = _opciones(DESCRIPCION_TIPO_IDENTIFICACION)

#: Códigos de porcentaje de IVA del SRI (tabla 16).
OPCIONES_TARIFA_IVA = _opciones(DESCRIPCION_TARIFA_IVA)

#: Formas de pago del SRI (tabla 24).
OPCIONES_FORMA_PAGO = _opciones(DESCRIPCION_FORMA_PAGO)

#: Tipos de comprobante del SRI (tabla 1).
OPCIONES_TIPO_COMPROBANTE = _opciones(DESCRIPCION_TIPO_COMPROBANTE)

#: Valores por omisión de los catálogos más usados.
IVA_POR_OMISION = str(TarifaIva.IVA_15.value)
IDENTIFICACION_POR_OMISION = str(TipoIdentificacion.RUC.value)
PAGO_POR_OMISION = str(FormaPago.SIN_SISTEMA_FINANCIERO.value)


# ------------------------------------------------------------------ catálogos


class Cliente(models.Model):
    """Adquirente, proveedor o sujeto retenido de un comprobante.

    Tiene exactamente los datos que pide el SRI para identificar a la otra parte
    (``razonSocial``, ``identificacion``, ``tipoIdentificacion`` y ``direccion``).
    """

    razon_social = models.CharField(
        "razón social", max_length=300, help_text="Como consta en el RUC o la cédula."
    )
    identificacion = models.CharField(
        "identificación", max_length=20, blank=True, db_index=True,
        help_text="RUC (13), cédula (10), pasaporte, o 9999999999999 para consumidor final.",
    )
    tipo_identificacion = models.CharField(
        "tipo de identificación", max_length=2, choices=OPCIONES_TIPO_IDENTIFICACION,
        default=IDENTIFICACION_POR_OMISION,
    )
    direccion = models.CharField(
        "dirección", max_length=300, blank=True,
        help_text="Opcional para el SRI, pero es la que sale impresa en el comprobante.",
    )

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["razon_social"]

    def __str__(self) -> str:
        return f"{self.razon_social} ({self.identificacion})"

    # ------------------------------------------------------------------ validación

    #: Longitud que debe tener la identificación según el tipo (tabla 2).
    LONGITUD_POR_TIPO: ClassVar[Dict[str, int]] = {
        TipoIdentificacion.RUC.value: 13,
        TipoIdentificacion.CEDULA.value: 10,
    }

    #: Identificación del consumidor final.
    CONSUMIDOR_FINAL = "9" * 13

    def clean(self) -> None:
        super().clean()
        tipo = str(self.tipo_identificacion)
        identificacion = _solo_digitos(self.identificacion)

        if tipo == TipoIdentificacion.CONSUMIDOR_FINAL.value:
            if not identificacion:
                self.identificacion = self.CONSUMIDOR_FINAL
            elif identificacion != self.CONSUMIDOR_FINAL:
                raise ValidationError(
                    {"identificacion": "El consumidor final debe usar la identificación "
                                       f"{self.CONSUMIDOR_FINAL}."}
                )
            return

        if not identificacion:
            raise ValidationError({"identificacion": "La identificación es obligatoria."})

        esperado = self.LONGITUD_POR_TIPO.get(tipo)
        if esperado and len(identificacion) != esperado:
            raise ValidationError(
                {"identificacion": f"El {self.get_tipo_identificacion_display()} debe tener "
                                   f"{esperado} dígitos."}
            )
        if tipo == TipoIdentificacion.RUC.value and not identificacion.endswith("001"):
            raise ValidationError(
                {"identificacion": "El RUC de una persona jurídica termina en 001; revise el "
                                   "número o use el tipo de identificación correcto."}
            )
        self.identificacion = identificacion

    @classmethod
    def consumidor_final(cls) -> "Cliente":
        """Devuelve (creándolo si hace falta) el cliente genérico de consumidor final."""
        cliente, _ = cls.objects.get_or_create(
            tipo_identificacion=TipoIdentificacion.CONSUMIDOR_FINAL.value,
            identificacion=cls.CONSUMIDOR_FINAL,
            defaults={"razon_social": "CONSUMIDOR FINAL"},
        )
        return cliente


class Producto(models.Model):
    """Bien o servicio que se factura.

    Guarda los datos del detalle del SRI (``codigoPrincipal``, ``codigoAuxiliar``,
    ``descripcion``, ``unidadMedida``, ``precioUnitario`` y el código de IVA) para
    reutilizarlos en cada línea.
    """

    codigo_principal = models.CharField(
        "código principal", max_length=25, unique=True,
        help_text="Su código interno del producto o servicio (máximo 25 caracteres).",
    )
    codigo_auxiliar = models.CharField(
        "código auxiliar", max_length=25, blank=True,
        help_text="Opcional: código de barras, del fabricante, etc.",
    )
    descripcion = models.CharField("descripción", max_length=300)
    unidad_medida = models.CharField(
        "unidad de medida", max_length=50, blank=True,
        help_text="Texto libre: unidad, caja, kg, litro, hora…",
    )
    precio_unitario = models.DecimalField(
        "precio unitario", max_digits=18, decimal_places=6, default=0,
        help_text="Sin impuestos. Se admiten hasta 6 decimales.",
    )
    codigo_porcentaje_iva = models.CharField(
        "IVA que aplica", max_length=2, choices=OPCIONES_TARIFA_IVA,
        default=IVA_POR_OMISION,
    )
    activo = models.BooleanField(
        "activo", default=True, help_text="No afecta al XML: sirve para no ofrecerlo más."
    )

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["descripcion"]

    def __str__(self) -> str:
        return f"{self.codigo_principal} — {self.descripcion}"


# ------------------------------------------------------------- bases comunes


class DocumentoElectronico(models.Model):
    """Base de los comprobantes que se envían al SRI.

    Aporta la fecha, el secuencial, las observaciones, el enlace con el
    comprobante emitido y los métodos para firmar y enviar.
    """

    #: Código ``codDoc`` del SRI. Lo fija cada documento.
    TIPO_COMPROBANTE: ClassVar[str] = ""

    fecha_emision = models.DateField("fecha de emisión", default=date.today)
    secuencial = models.CharField(
        "secuencial", max_length=9, blank=True, validators=[SECUENCIAL_VALIDO],
        help_text="Se asigna solo al emitir, con el contador del SRI (sri_fe_secuencial).",
    )
    observaciones = models.TextField(
        "observaciones", max_length=300, blank=True,
        help_text="Viaja al SRI como campo adicional «Observaciones» (máximo 300 caracteres).",
    )
    informacion_adicional = models.TextField(
        "campos adicionales", blank=True,
        help_text=(
            "Opcional, hasta 15 campos propios de la tienda. Formato NOMBRE=VALOR "
            "separados por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE. Viaja al SRI en "
            "«infoAdicional»."
        ),
    )
    comprobante = models.OneToOneField(
        "sri_fe.ComprobanteEmitido", verbose_name="comprobante electrónico",
        null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        editable=False,
    )
    creado = models.DateTimeField("creado", auto_now_add=True)
    actualizado = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-fecha_emision", "-pk"]

    def __str__(self) -> str:
        numero = f"{int(self.secuencial):09d}" if self.secuencial else "sin secuencial"
        return f"{self.descripcion_tipo} {numero}"

    # ------------------------------------------------------ campos adicionales

    def campos_adicionales(self) -> Dict[str, str]:
        """Campos adicionales del comprobante, ya interpretados."""
        return leer_campos_adicionales(self.informacion_adicional)

    def clean(self) -> None:
        super().clean()
        try:
            campos = self.campos_adicionales()
        except ValidationError as error:
            raise ValidationError({"informacion_adicional": error.messages}) from error
        try:
            validar_cantidad(campos, MAXIMO_CAMPOS_ADICIONALES, "«infoAdicional»")
        except ValidationError as error:
            raise ValidationError({"informacion_adicional": error.messages}) from error

    # ------------------------------------------------------------------ estado

    @property
    def tipo_comprobante(self) -> str:
        """Código ``codDoc`` (tabla 1) del comprobante."""
        return self.TIPO_COMPROBANTE

    @property
    def descripcion_tipo(self) -> str:
        """Nombre del comprobante según el SRI («Factura», «Nota de crédito»…)."""
        return DESCRIPCION_TIPO_COMPROBANTE.get(self.TIPO_COMPROBANTE, self.TIPO_COMPROBANTE)

    @property
    def estado(self) -> str:
        """Estado ante el SRI del comprobante emitido (``BORRADOR`` si no se ha emitido)."""
        from .models import EstadoComprobante

        if self.comprobante_id is None:
            return EstadoComprobante.BORRADOR
        return self.comprobante.estado

    @property
    def autorizado(self) -> bool:
        """``True`` si el SRI ya autorizó el comprobante."""
        return bool(self.comprobante_id and self.comprobante.autorizado)

    @property
    def clave_acceso(self) -> str:
        return self.comprobante.clave_acceso if self.comprobante_id else ""

    @property
    def numero_autorizacion(self) -> str:
        return self.comprobante.numero_autorizacion if self.comprobante_id else ""

    @property
    def xml_autorizado(self) -> str:
        """XML con validez legal. Solo existe una vez autorizado."""
        return self.comprobante.xml_autorizado if self.comprobante_id else ""

    # --------------------------------------------------------------- emisión

    def emitir(self, **kwargs: Any) -> Any:
        """Arma el XML, lo firma y lo envía al SRI (ver ``facturacion.emitir``)."""
        from . import facturacion

        return facturacion.emitir(self, **kwargs)

    def firmar(self, **kwargs: Any) -> str:
        """Devuelve el XML firmado, sin enviarlo."""
        from . import facturacion

        return facturacion.firmar_modelo(self, **kwargs)

    def xml(self, **kwargs: Any) -> str:
        """Devuelve el XML sin firmar."""
        from . import facturacion

        return facturacion.comprobante_de(self, **kwargs).to_xml()

    def reintentar(self, **kwargs: Any) -> Any:
        """Retoma el envío de un comprobante que quedó sin autorizar."""
        from . import facturacion

        return facturacion.reintentar(self, **kwargs)

    def asociar_comprobante(self, registro: Any, guardar: bool = True) -> "DocumentoElectronico":
        """Guarda el enlace con el comprobante emitido y su secuencial.

        Lo llama el paquete al emitir; no hace falta invocarlo a mano.
        """
        self.comprobante = registro
        if registro.secuencial:
            self.secuencial = str(registro.secuencial).lstrip("0") or "0"
        if guardar and self.pk:
            self.save(update_fields=["comprobante", "secuencial", "actualizado"])
        return self


class LineaDocumento(models.Model):
    """Línea de un comprobante de venta (factura, nota de crédito, liquidación).

    Son los campos del ``detalle`` del SRI. Si se elige un :class:`Producto`, se
    completan solos la descripción, el código, la unidad de medida, el precio y el
    IVA.
    """

    descripcion = models.CharField("descripción", max_length=300)
    cantidad = models.DecimalField("cantidad", max_digits=18, decimal_places=6, default=1)
    precio_unitario = models.DecimalField(
        "precio unitario", max_digits=18, decimal_places=6, default=0
    )
    descuento = models.DecimalField("descuento", max_digits=14, decimal_places=2, default=0)
    codigo_principal = models.CharField("código principal", max_length=25, blank=True)
    codigo_auxiliar = models.CharField("código auxiliar", max_length=25, blank=True)
    unidad_medida = models.CharField("unidad de medida", max_length=50, blank=True)
    codigo_porcentaje_iva = models.CharField(
        "IVA", max_length=2, choices=OPCIONES_TARIFA_IVA, default=IVA_POR_OMISION
    )
    datos_adicionales = models.CharField(
        "datos adicionales", max_length=1000, blank=True,
        help_text=(
            "Opcional, hasta 3 campos. Formato NOMBRE=VALOR separados por «;»: "
            "MARCA=ACME; LOTE=2026-01. Viaja al SRI en «detallesAdicionales»."
        ),
    )

    class Meta:
        abstract = True
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.descripcion} × {self.cantidad}"

    # ------------------------------------------------------ campos adicionales

    def campos_adicionales(self) -> Dict[str, str]:
        """Datos adicionales de la línea, ya interpretados."""
        return leer_campos_adicionales(self.datos_adicionales)

    def clean(self) -> None:
        super().clean()
        try:
            campos = self.campos_adicionales()
            validar_cantidad(campos, MAXIMO_DATOS_ADICIONALES, "«detallesAdicionales»")
        except ValidationError as error:
            raise ValidationError({"datos_adicionales": error.messages}) from error

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._completar_desde_producto()
        super().save(*args, **kwargs)

    def _completar_desde_producto(self) -> None:
        """Copia del producto los datos que falten en la línea."""
        producto = getattr(self, "producto", None)
        if producto is None:
            return
        if not self.descripcion:
            self.descripcion = producto.descripcion
        if not self.codigo_principal:
            self.codigo_principal = producto.codigo_principal
        if not self.codigo_auxiliar:
            self.codigo_auxiliar = producto.codigo_auxiliar
        if not self.unidad_medida:
            self.unidad_medida = producto.unidad_medida
        if not self.precio_unitario:
            self.precio_unitario = producto.precio_unitario
        if not self.codigo_porcentaje_iva:
            self.codigo_porcentaje_iva = producto.codigo_porcentaje_iva

    def a_detalle(self) -> Detalle:
        """Convierte la línea en el objeto que entiende el generador de XML."""
        return Detalle(
            descripcion=self.descripcion,
            cantidad=self.cantidad,
            precio_unitario=self.precio_unitario,
            descuento=self.descuento,
            codigo_principal=self.codigo_principal or None,
            codigo_auxiliar=self.codigo_auxiliar or None,
            unidad_medida=self.unidad_medida or None,
            detalles_adicionales=self.campos_adicionales(),
            impuestos=[Impuesto(codigo_porcentaje=self.codigo_porcentaje_iva)],
        )

    # ------------------------------------------------------------------ importes

    @property
    def tarifa_iva(self) -> Decimal:
        """Porcentaje de IVA de la línea, según el catálogo del SRI."""
        return PORCENTAJE_IVA.get(str(self.codigo_porcentaje_iva), Decimal("0"))

    @property
    def precio_total_sin_impuesto(self) -> Decimal:
        """``cantidad × precioUnitario − descuento``, como en el XML."""
        return self.a_detalle().precio_total_sin_impuesto

    @property
    def valor_iva(self) -> Decimal:
        return _importe(sum(i.valor for i in self.a_detalle().impuestos_resueltos()))

    @property
    def total(self) -> Decimal:
        return _importe(self.precio_total_sin_impuesto + self.valor_iva)


class DocumentoConDetalles(models.Model):
    """Base de los comprobantes que llevan líneas: calcula los totales del XML."""

    class Meta:
        abstract = True

    def a_detalles(self) -> List[Detalle]:
        """Líneas convertidas a los objetos del generador de XML."""
        return [linea.a_detalle() for linea in self.detalles.all()]

    def totales(self) -> Dict[str, Any]:
        """Mismos totales que calculará el XML (reutiliza el cálculo del paquete).

        Claves: ``total_sin_impuestos``, ``total_descuento``, ``total_impuestos``,
        ``importe_total`` y ``total_con_impuestos``.
        """
        return calcular_totales(self.a_detalles())

    @property
    def subtotal(self) -> Decimal:
        return self.totales()["total_sin_impuestos"]

    @property
    def valor_iva(self) -> Decimal:
        return self.totales()["total_impuestos"]

    @property
    def total(self) -> Decimal:
        """``importeTotal`` del comprobante."""
        return self.totales()["importe_total"]


# ------------------------------------------------------------------- factura


class Factura(DocumentoElectronico, DocumentoConDetalles):
    """Factura electrónica (``codDoc`` 01, esquema 1.1.0)."""

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.FACTURA.value

    receptor = models.ForeignKey(
        Cliente, verbose_name="receptor", on_delete=models.PROTECT, related_name="facturas",
        help_text="Adquirente de la factura.",
    )
    forma_pago = models.CharField(
        "forma de pago", max_length=2, choices=OPCIONES_FORMA_PAGO,
        default=PAGO_POR_OMISION,
    )
    plazo = models.DecimalField(
        "plazo", max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Solo si se paga a crédito: número de días, meses o años.",
    )
    unidad_tiempo = models.CharField(
        "unidad de tiempo", max_length=10, blank=True,
        help_text="Solo con plazo: días, meses o años.",
    )
    propina = models.DecimalField(
        "propina", max_digits=14, decimal_places=2, null=True, blank=True
    )
    placa = models.CharField(
        "placa del vehículo", max_length=20, blank=True,
        help_text="Obligatoria cuando se factura la venta de un vehículo.",
    )
    guia_remision = models.CharField(
        "guía de remisión", max_length=17, blank=True,
        help_text="Si los bienes se transportan: 001-001-000000001.",
    )
    valor_ret_iva = models.DecimalField(
        "IVA retenido", max_digits=14, decimal_places=2, null=True, blank=True
    )
    valor_ret_renta = models.DecimalField(
        "renta retenida", max_digits=14, decimal_places=2, null=True, blank=True
    )

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "factura"
        verbose_name_plural = "facturas"

    def clean(self) -> None:
        super().clean()
        if self.guia_remision and not SERIE_NUMERO_VALIDO.regex.match(self.guia_remision):
            raise ValidationError(
                {"guia_remision": "Escriba el número como 001-001-000000001."}
            )


class FacturaDetalle(LineaDocumento):
    """Línea de una factura."""

    factura = models.ForeignKey(
        Factura, verbose_name="factura", on_delete=models.CASCADE, related_name="detalles"
    )
    producto = models.ForeignKey(
        Producto, verbose_name="producto", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "línea de factura"
        verbose_name_plural = "líneas de factura"


# ------------------------------------------------------- liquidación de compra


class LiquidacionCompra(DocumentoElectronico, DocumentoConDetalles):
    """Liquidación de compra (``codDoc`` 03) a quien no puede emitir factura."""

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.LIQUIDACION_COMPRA.value

    proveedor = models.ForeignKey(
        Cliente, verbose_name="proveedor", on_delete=models.PROTECT,
        related_name="liquidaciones_compra",
        help_text="Persona a la que se le compra y que no emite factura.",
    )
    forma_pago = models.CharField(
        "forma de pago", max_length=2, choices=OPCIONES_FORMA_PAGO,
        default=PAGO_POR_OMISION,
    )
    plazo = models.DecimalField(
        "plazo", max_digits=14, decimal_places=2, null=True, blank=True
    )
    unidad_tiempo = models.CharField("unidad de tiempo", max_length=10, blank=True)
    correo = models.CharField(
        "correo del proveedor", max_length=100, blank=True,
        help_text="Se envía al SRI en la sección «tipoNegociable».",
    )

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "liquidación de compra"
        verbose_name_plural = "liquidaciones de compra"


class LiquidacionCompraDetalle(LineaDocumento):
    """Línea de una liquidación de compra."""

    liquidacion = models.ForeignKey(
        LiquidacionCompra, verbose_name="liquidación de compra", on_delete=models.CASCADE,
        related_name="detalles",
    )
    producto = models.ForeignKey(
        Producto, verbose_name="producto", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "línea de liquidación de compra"
        verbose_name_plural = "líneas de liquidación de compra"


# ---------------------------------------------------------- nota de crédito


class DocumentoModificado(models.Model):
    """Datos del comprobante que modifica una nota de crédito o de débito."""

    cod_doc_modificado = models.CharField(
        "tipo de documento que modifica", max_length=2,
        choices=OPCIONES_TIPO_COMPROBANTE,
        default=TipoComprobante.FACTURA.value,
    )
    num_doc_modificado = models.CharField(
        "número del documento que modifica", max_length=17,
        help_text="001-001-000000001 (también se acepta 001001000000001).",
    )
    fecha_emision_doc_sustento = models.DateField(
        "fecha del documento que modifica",
        help_text="Fecha de emisión del comprobante modificado.",
    )

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.num_doc_modificado = _completar_serie(self.num_doc_modificado)
        super().save(*args, **kwargs)


class NotaCredito(DocumentoElectronico, DocumentoConDetalles, DocumentoModificado):
    """Nota de crédito (``codDoc`` 04): anula o corrige una factura u otro documento."""

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.NOTA_CREDITO.value

    receptor = models.ForeignKey(
        Cliente, verbose_name="receptor", on_delete=models.PROTECT,
        related_name="notas_credito",
    )
    motivo = models.CharField(
        "motivo", max_length=300,
        help_text="Razón de la nota: devolución, descuento, error de facturación…",
    )
    rise = models.CharField(
        "RISE", max_length=40, blank=True,
        help_text="Solo para contribuyentes del régimen RISE (en desuso).",
    )

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "nota de crédito"
        verbose_name_plural = "notas de crédito"


class NotaCreditoDetalle(LineaDocumento):
    """Línea de una nota de crédito."""

    nota_credito = models.ForeignKey(
        NotaCredito, verbose_name="nota de crédito", on_delete=models.CASCADE,
        related_name="detalles",
    )
    producto = models.ForeignKey(
        Producto, verbose_name="producto", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "línea de nota de crédito"
        verbose_name_plural = "líneas de nota de crédito"


# ----------------------------------------------------------- nota de débito


class NotaDebito(DocumentoElectronico, DocumentoModificado):
    """Nota de débito (``codDoc`` 05): cobra valores no incluidos en la factura.

    No lleva líneas: se detallan los **motivos** y el IVA se calcula sobre la suma
    de esos valores (o sobre ``base_imponible`` si se indica).
    """

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.NOTA_DEBITO.value

    receptor = models.ForeignKey(
        Cliente, verbose_name="receptor", on_delete=models.PROTECT, related_name="notas_debito"
    )
    codigo_porcentaje_iva = models.CharField(
        "IVA", max_length=2, choices=OPCIONES_TARIFA_IVA, default=IVA_POR_OMISION
    )
    base_imponible = models.DecimalField(
        "base imponible", max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Si se deja vacío se usa la suma de los motivos.",
    )
    forma_pago = models.CharField(
        "forma de pago", max_length=2, choices=OPCIONES_FORMA_PAGO,
        default=PAGO_POR_OMISION,
    )
    plazo = models.DecimalField(
        "plazo", max_digits=14, decimal_places=2, null=True, blank=True
    )
    unidad_tiempo = models.CharField("unidad de tiempo", max_length=10, blank=True)
    rise = models.CharField("RISE", max_length=40, blank=True)

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "nota de débito"
        verbose_name_plural = "notas de débito"

    @property
    def total_sin_impuestos(self) -> Decimal:
        """Suma de los motivos: es el ``totalSinImpuestos`` del XML."""
        return _importe(sum((motivo.valor for motivo in self.motivos.all()), Decimal("0")))

    @property
    def valor_iva(self) -> Decimal:
        base = self.base_imponible if self.base_imponible is not None else self.total_sin_impuestos
        return _importe(base * self.tarifa_iva / Decimal("100"))

    @property
    def tarifa_iva(self) -> Decimal:
        return PORCENTAJE_IVA.get(str(self.codigo_porcentaje_iva), Decimal("0"))

    @property
    def total(self) -> Decimal:
        """``valorTotal`` del XML: total sin impuestos más impuestos."""
        return _importe(self.total_sin_impuestos + self.valor_iva)


class NotaDebitoMotivo(models.Model):
    """Motivo (razón y valor) de una nota de débito."""

    nota_debito = models.ForeignKey(
        NotaDebito, verbose_name="nota de débito", on_delete=models.CASCADE,
        related_name="motivos",
    )
    razon = models.CharField("razón", max_length=300)
    valor = models.DecimalField("valor", max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "motivo de nota de débito"
        verbose_name_plural = "motivos de nota de débito"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.razon}: {self.valor}"


# -------------------------------------------------------- guía de remisión


class GuiaRemision(DocumentoElectronico):
    """Guía de remisión (``codDoc`` 06): transporta bienes."""

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.GUIA_REMISION.value

    dir_partida = models.CharField(
        "dirección de partida", max_length=300, help_text="Desde dónde salen los bienes."
    )
    razon_social_transportista = models.CharField(
        "razón social del transportista", max_length=300
    )
    ruc_transportista = models.CharField(
        "RUC del transportista", max_length=13,
        validators=[RegexValidator(r"^\d{13}$", "El RUC debe tener 13 dígitos.")],
        help_text="El del transportista; si transporta usted mismo, su propio RUC.",
    )
    tipo_identificacion_transportista = models.CharField(
        "tipo de identificación del transportista", max_length=2,
        choices=OPCIONES_TIPO_IDENTIFICACION, default=IDENTIFICACION_POR_OMISION,
    )
    placa = models.CharField("placa", max_length=20)
    fecha_ini_transporte = models.DateField("inicio del transporte")
    fecha_fin_transporte = models.DateField("fin del transporte")
    rise = models.CharField("RISE", max_length=40, blank=True)

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "guía de remisión"
        verbose_name_plural = "guías de remisión"

    def clean(self) -> None:
        super().clean()
        if (
            self.fecha_ini_transporte
            and self.fecha_fin_transporte
            and self.fecha_fin_transporte < self.fecha_ini_transporte
        ):
            raise ValidationError(
                {"fecha_fin_transporte": "No puede ser anterior al inicio del transporte."}
            )


class GuiaDestinatario(models.Model):
    """Destinatario de una guía de remisión, con los bienes que se le entregan."""

    guia = models.ForeignKey(
        GuiaRemision, verbose_name="guía de remisión", on_delete=models.CASCADE,
        related_name="destinatarios",
    )
    razon_social = models.CharField("razón social", max_length=300)
    identificacion = models.CharField("identificación", max_length=20)
    tipo_identificacion = models.CharField(
        "tipo de identificación", max_length=2, choices=OPCIONES_TIPO_IDENTIFICACION,
        default=IDENTIFICACION_POR_OMISION,
    )
    direccion = models.CharField("dirección de entrega", max_length=300)
    motivo_traslado = models.CharField(
        "motivo del traslado", max_length=2,
        choices=_opciones(DESCRIPCION_MOTIVO_TRASLADO),
        default="01",
    )
    doc_aduanero_unico = models.CharField(
        "documento aduanero único", max_length=20, blank=True
    )
    cod_estab_destino = models.CharField(
        "código del establecimiento destino", max_length=3, blank=True
    )
    ruta = models.CharField("ruta", max_length=300, blank=True)
    cod_doc_sustento = models.CharField(
        "documento que sustenta el traslado", max_length=3, blank=True,
        choices=OPCIONES_TIPO_COMPROBANTE,
        help_text="Solo si los bienes se amparan en otro comprobante: 01 factura, 03 liquidación…",
    )
    num_doc_sustento = models.CharField(
        "número del documento sustento", max_length=17, blank=True,
        help_text="001-001-000000001 (también se acepta 001001000000001).",
    )
    num_aut_doc_sustento = models.CharField(
        "autorización del documento sustento", max_length=49, blank=True
    )
    fecha_emision_doc_sustento = models.DateField(
        "fecha del documento sustento", null=True, blank=True
    )

    class Meta:
        verbose_name = "destinatario de guía"
        verbose_name_plural = "destinatarios de guía"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.razon_social} ({self.identificacion})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.num_doc_sustento = _completar_serie(self.num_doc_sustento)
        super().save(*args, **kwargs)


class GuiaDetalle(models.Model):
    """Bien transportado dentro de una guía de remisión."""

    destinatario = models.ForeignKey(
        GuiaDestinatario, verbose_name="destinatario", on_delete=models.CASCADE,
        related_name="detalles",
    )
    descripcion = models.CharField("descripción", max_length=300)
    cantidad = models.DecimalField("cantidad", max_digits=18, decimal_places=6, default=1)
    codigo_principal = models.CharField("código principal", max_length=25, blank=True)
    codigo_adicional = models.CharField("código adicional", max_length=25, blank=True)
    datos_adicionales = models.CharField(
        "datos adicionales", max_length=1000, blank=True,
        help_text=(
            "Opcional, hasta 3 campos. Formato NOMBRE=VALOR separados por «;»: "
            "MARCA=ACME; LOTE=2026-01."
        ),
    )

    class Meta:
        verbose_name = "bien transportado"
        verbose_name_plural = "bienes transportados"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.descripcion} × {self.cantidad}"

    def campos_adicionales(self) -> Dict[str, str]:
        """Datos adicionales del bien transportado, ya interpretados."""
        return leer_campos_adicionales(self.datos_adicionales)

    def clean(self) -> None:
        super().clean()
        try:
            campos = self.campos_adicionales()
            validar_cantidad(campos, MAXIMO_DATOS_ADICIONALES, "«detallesAdicionales»")
        except ValidationError as error:
            raise ValidationError({"datos_adicionales": error.messages}) from error


# ----------------------------------------------------- comprobante de retención


class Retencion(DocumentoElectronico):
    """Comprobante de retención (``codDoc`` 07) de IVA y/o renta."""

    TIPO_COMPROBANTE: ClassVar[str] = TipoComprobante.COMPROBANTE_RETENCION.value

    sujeto_retenido = models.ForeignKey(
        Cliente, verbose_name="sujeto retenido", on_delete=models.PROTECT,
        related_name="retenciones_recibidas",
        help_text="A quien se le retiene: el proveedor.",
    )
    periodo_fiscal = models.DateField(
        "periodo fiscal", help_text="Cualquier día del mes que se retiene (se envía MM/AAAA)."
    )
    parte_rel = models.CharField(
        "parte relacionada", max_length=2,
        choices=[("SI", "Sí"), ("NO", "No")], default="SI",
        help_text="Indique SI solo si el sujeto retenido es parte relacionada.",
    )
    tipo_sujeto_retenido = models.CharField(
        "tipo de sujeto retenido", max_length=2, blank=True,
        choices=_opciones_enum(TipoSujetoRetenido, {"01": "Contribuyente", "02": "No contribuyente"}),
    )

    class Meta(DocumentoElectronico.Meta):
        abstract = False
        verbose_name = "comprobante de retención"
        verbose_name_plural = "comprobantes de retención"

    @property
    def total_retenido(self) -> Decimal:
        """Suma de todo lo retenido en los documentos sustento."""
        total = Decimal("0")
        for documento in self.docs_sustento.all():
            for retencion in documento.retenciones.all():
                total += retencion.valor_retenido
        return _importe(total)


class RetencionDocSustento(models.Model):
    """Documento que sustenta una retención (la factura del proveedor)."""

    retencion = models.ForeignKey(
        Retencion, verbose_name="comprobante de retención", on_delete=models.CASCADE,
        related_name="docs_sustento",
    )
    cod_sustento = models.CharField(
        "código de sustento", max_length=2, default="01",
        validators=[RegexValidator(r"^\d{2}$", "Use el código de 2 dígitos del SRI.")],
        help_text="Tabla 5 del SRI: 01 crédito tributario de IVA, 02 costo o gasto deducible…",
    )
    cod_doc_sustento = models.CharField(
        "tipo de documento", max_length=3,
        choices=OPCIONES_TIPO_COMPROBANTE,
        default=TipoComprobante.FACTURA.value,
        help_text="Tipo de comprobante que se le emitió al proveedor.",
    )
    num_doc_sustento = models.CharField(
        "número del documento", max_length=15,
        help_text="15 dígitos sin guiones: 001001000000123.",
    )
    fecha_emision = models.DateField("fecha de emisión")
    num_aut_doc_sustento = models.CharField(
        "número de autorización", max_length=49, blank=True,
        help_text="Clave de acceso o autorización del documento (10 a 49 dígitos).",
    )
    total_sin_impuestos = models.DecimalField(
        "total sin impuestos", max_digits=14, decimal_places=2, default=0
    )
    importe_total = models.DecimalField(
        "importe total", max_digits=14, decimal_places=2, default=0
    )
    pago_loc_ext = models.CharField(
        "pago", max_length=2, choices=[("01", "Local"), ("02", "Al exterior")], default="01"
    )
    tipo_regi = models.CharField(
        "tipo de régimen", max_length=2, blank=True,
        help_text="Solo para pagos al exterior. Tabla del SRI.",
    )
    pais_efec_pago = models.CharField(
        "país del pago", max_length=4, blank=True,
        help_text="Código del país (3 o 4 dígitos). Solo para pagos al exterior.",
    )
    aplic_conv_dob_trib = models.CharField(
        "aplica convenio de doble tributación", max_length=2, blank=True,
        choices=[("SI", "Sí"), ("NO", "No")],
    )
    pag_ext_suj_ret_nor_leg = models.CharField(
        "pago a sujeto no obligado a llevar contabilidad", max_length=2, blank=True,
        choices=[("SI", "Sí"), ("NO", "No")],
    )

    class Meta:
        verbose_name = "documento sustento de retención"
        verbose_name_plural = "documentos sustento de retención"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.get_cod_doc_sustento_display()} {self.num_doc_sustento}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.num_doc_sustento = _solo_digitos(self.num_doc_sustento)
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.num_doc_sustento and len(_solo_digitos(self.num_doc_sustento)) != 15:
            raise ValidationError(
                {"num_doc_sustento": "Debe tener 15 dígitos (sin guiones)."}
            )


class RetencionDocSustentoImpuesto(models.Model):
    """Impuesto del documento sustento (``impuestosDocSustento``)."""

    doc_sustento = models.ForeignKey(
        RetencionDocSustento, verbose_name="documento sustento", on_delete=models.CASCADE,
        related_name="impuestos",
    )
    codigo = models.CharField(
        "impuesto", max_length=2, default=str(CodigoImpuesto.IVA.value),
        choices=_opciones_enum(CodigoImpuesto),
    )
    codigo_porcentaje = models.CharField(
        "IVA del documento", max_length=2, choices=OPCIONES_TARIFA_IVA,
        default=IVA_POR_OMISION,
    )
    base_imponible = models.DecimalField(
        "base imponible", max_digits=14, decimal_places=2, default=0,
        help_text="Si es 0 se usa el total sin impuestos del documento sustento.",
    )
    tarifa = models.DecimalField(
        "tarifa", max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="Se completa según el IVA elegido (15.00 para el 15 %).",
    )
    valor = models.DecimalField(
        "valor del impuesto", max_digits=14, decimal_places=2, default=0,
        help_text="Se calcula solo si se deja en 0.",
    )

    class Meta:
        verbose_name = "impuesto del documento sustento"
        verbose_name_plural = "impuestos del documento sustento"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.codigo_porcentaje} → {self.valor}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.tarifa:
            self.tarifa = PORCENTAJE_IVA.get(
                str(self.codigo_porcentaje), PORCENTAJE_IVA[TarifaIva.IVA_15.value]
            )
        if not self.base_imponible and self.doc_sustento_id:
            self.base_imponible = self.doc_sustento.total_sin_impuestos
        if not self.valor:
            self.valor = _importe(self.base_imponible * Decimal(str(self.tarifa)) / Decimal("100"))
        super().save(*args, **kwargs)


class RetencionImpuesto(models.Model):
    """Retención aplicada dentro de un documento sustento."""

    doc_sustento = models.ForeignKey(
        RetencionDocSustento, verbose_name="documento sustento", on_delete=models.CASCADE,
        related_name="retenciones",
    )
    codigo = models.CharField(
        "impuesto", max_length=2, default=str(CodigoRetencion.RENTA.value),
        choices=_opciones_enum(CodigoRetencion, {"1": "Renta", "2": "IVA", "6": "ISD"}),
    )
    codigo_retencion = models.CharField(
        "código de retención", max_length=5,
        help_text="Tabla 17 (renta) o 20 (IVA) del SRI: 312, 725, 9…",
    )
    base_imponible = models.DecimalField(
        "base imponible", max_digits=14, decimal_places=2, default=0,
        help_text="Si es 0 se usa el total sin impuestos del documento sustento.",
    )
    porcentaje_retener = models.DecimalField(
        "porcentaje a retener", max_digits=6, decimal_places=2, default=0,
        help_text="Porcentaje 0.00 % … 100.00 %.",
    )
    valor_retenido = models.DecimalField(
        "valor retenido", max_digits=14, decimal_places=2, default=0,
        help_text="Se calcula solo a partir de la base y el porcentaje.",
    )

    class Meta:
        verbose_name = "retención"
        verbose_name_plural = "retenciones"
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.get_codigo_display()} {self.codigo_retencion}: {self.valor_retenido}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.base_imponible and self.doc_sustento_id:
            self.base_imponible = self.doc_sustento.total_sin_impuestos
        if not self.valor_retenido:
            self.valor_retenido = _importe(
                self.base_imponible * self.porcentaje_retener / Decimal("100")
            )
        super().save(*args, **kwargs)


# ------------------------------------------------------------------ registro


def registrar_adaptadores() -> None:
    """Asocia cada modelo con el adaptador que sabe emitirlo.

    Así ``emitir(modelo)`` funciona sin indicar nada: el paquete sabe qué
    comprobante corresponde a cada modelo.
    """
    from . import adaptadores

    for modelo, adaptador in (
        (Factura, adaptadores.AdaptadorFactura),
        (LiquidacionCompra, adaptadores.AdaptadorLiquidacionCompra),
        (NotaCredito, adaptadores.AdaptadorNotaCredito),
        (NotaDebito, adaptadores.AdaptadorNotaDebito),
        (GuiaRemision, adaptadores.AdaptadorGuiaRemision),
        (Retencion, adaptadores.AdaptadorRetencion),
    ):
        adaptadores.registro.registrar(modelo, adaptador)


registrar_adaptadores()
