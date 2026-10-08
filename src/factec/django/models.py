"""Modelos para la integración con Django.

Tres tablas, todas gestionables desde el admin:

* :class:`ConfiguracionEmisor` — datos del contribuyente, el archivo de firma
  (``.p12``) y su contraseña (guardada cifrada).
* :class:`ComprobanteEmitido` — cada comprobante y su ciclo ante el SRI.
* :class:`Secuencial` — contador persistente de secuenciales.

Los XML se guardan tal cual, de modo que la tarea de Celery no necesita
reconstruir el objeto Python: basta con leerlo, firmarlo y enviarlo.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.utils import timezone

from .campos_adicionales import (
    MAXIMO_CAMPOS_ADICIONALES,
    leer_campos_adicionales,
    validar_cantidad,
)

__all__ = [
    "EstadoComprobante",
    "TipoComprobante",
    "ConfiguracionEmisor",
    "ComprobanteEmitido",
    "Secuencial",
    "EXTENSIONES_CERTIFICADO",
    "es_modelo_guardado",
]

#: Extensiones admitidas para el certificado de firma.
EXTENSIONES_CERTIFICADO = ["p12", "pfx"]

REGIMEN_RIMPE = "RIMPE"
CATEGORIA_NEGOCIO_POPULAR = "NEGOCIO POPULAR"


def extraer_ruc(certificado: Any) -> Optional[str]:
    """Devuelve el RUC del titular del certificado, si se puede deducir.

    En los certificados ecuatorianos el RUC suele figurar en el atributo
    ``serialNumber`` (OID 2.5.4.5) o dentro del nombre común.
    """
    import re

    from cryptography import x509

    sujeto = certificado.certificado.subject
    for atributo in sujeto:
        if atributo.oid == x509.oid.NameOID.SERIAL_NUMBER:
            encontrado = re.search(r"\d{13}", str(atributo.value))
            if encontrado:
                return encontrado.group(0)
    for atributo in sujeto:
        encontrado = re.search(r"\d{13}", str(atributo.value))
        if encontrado:
            return encontrado.group(0)
    return None


def es_modelo_guardado(objeto: Any) -> bool:
    """``True`` si el objeto es un modelo de Django ya guardado (tiene ``pk``).

    Sin esto, enlazar un comprobante con un objeto cualquiera (un diccionario o
    una clase suelta) fallaría con un ``AttributeError`` poco claro.
    """
    return (
        objeto is not None
        and hasattr(objeto, "_meta")
        and getattr(objeto, "pk", None) is not None
    )


class TipoComprobante(models.TextChoices):
    """Códigos ``codDoc`` del SRI (tabla 1)."""

    FACTURA = "01", "Factura"
    LIQUIDACION_COMPRA = "03", "Liquidación de compra"
    NOTA_CREDITO = "04", "Nota de crédito"
    NOTA_DEBITO = "05", "Nota de débito"
    GUIA_REMISION = "06", "Guía de remisión"
    COMPROBANTE_RETENCION = "07", "Comprobante de retención"


class EstadoComprobante(models.TextChoices):
    """Estados por los que pasa un comprobante."""

    BORRADOR = "BORRADOR", "Borrador"
    FIRMADO = "FIRMADO", "Firmado"
    RECIBIDO = "RECIBIDO", "Recibido por el SRI"
    DEVUELTO = "DEVUELTO", "Devuelto por el SRI"
    EN_PROCESO = "EN_PROCESO", "En proceso de autorización"
    AUTORIZADO = "AUTORIZADO", "Autorizado"
    NO_AUTORIZADO = "NO_AUTORIZADO", "No autorizado"
    ERROR = "ERROR", "Error"


class ConfiguracionEmisor(models.Model):
    """Datos del contribuyente, certificado de firma y su contraseña.

    La contraseña **nunca** se guarda en claro: se cifra con Fernet usando la
    clave de ``FACTURACION_ELECTRONICA['CLAVE_CIFRADO']`` (o la variable de
    entorno ``SRI_CLAVE_CIFRADO``). Genere una con::

        python -c "from factec.django.crypto import generar_clave; print(generar_clave())"
    """

    nombre = models.CharField(
        "nombre de la configuración", max_length=100, default="Principal",
        help_text="Solo para identificarla en el admin.",
    )
    activo = models.BooleanField(
        "activa", default=True,
        help_text="Solo se usa una configuración activa por ambiente.",
    )
    ambiente = models.PositiveSmallIntegerField(
        "ambiente", default=1, choices=[(1, "1 - Pruebas"), (2, "2 - Producción")],
        help_text="1 = pruebas, 2 = producción.",
    )

    # --- Identificación del contribuyente ---------------------------------
    ruc = models.CharField("RUC", max_length=13)
    razon_social = models.CharField("razón social", max_length=300)
    nombre_comercial = models.CharField("nombre comercial", max_length=300, blank=True)
    dir_matriz = models.CharField("dirección de la matriz", max_length=300)
    dir_establecimiento = models.CharField(
        "dirección del establecimiento", max_length=300, blank=True,
        help_text="Si se deja vacío se usa la dirección de la matriz.",
    )
    estab = models.CharField("establecimiento", max_length=3, default="001")
    pto_emi = models.CharField("punto de emisión", max_length=3, default="001")

    # --- Obligaciones declaradas en el SRI --------------------------------
    obligado_contabilidad = models.BooleanField("obligado a llevar contabilidad", default=False)
    contribuyente_especial = models.CharField(
        "n.º de contribuyente especial", max_length=13, blank=True,
        help_text="3 a 13 caracteres alfanuméricos. Dejar vacío si no lo es.",
    )
    agente_retencion = models.CharField(
        "n.º de resolución de agente de retención", max_length=8, blank=True,
        help_text="Solo dígitos. Dejar vacío si no lo es.",
    )
    regimen = models.CharField(
        "régimen", max_length=30, blank=True,
        help_text="Por ejemplo RIMPE. Dejar vacío si es régimen general.",
    )
    categoria = models.CharField(
        "categoría", max_length=30, blank=True,
        help_text="Por ejemplo NEGOCIO POPULAR (solo si es RIMPE).",
    )

    # --- Firma electrónica -------------------------------------------------
    certificado = models.FileField(
        "archivo de firma (.p12 / .pfx)", upload_to="sri/certificados/%Y/",
        validators=[FileExtensionValidator(EXTENSIONES_CERTIFICADO)],
        blank=True,
        help_text="Archivo PKCS#12 con la clave privada y el certificado.",
    )
    clave_certificado_cifrada = models.CharField(
        "contraseña del certificado (cifrada)", max_length=500, blank=True,
        help_text="Se cifra automáticamente; no se muestra en claro.",
    )

    campos_adicionales = models.TextField(
        "campos adicionales de la tienda", blank=True,
        help_text=(
            "Campos que se añaden a TODOS los comprobantes (hasta 15), en formato "
            "NOMBRE=VALOR separados por «;»: VENDEDOR=JOHNNY; SUCURSAL=NORTE. Lo que "
            "escriba en cada comprobante tiene prioridad sobre estos valores."
        ),
    )

    creado = models.DateTimeField("creado", auto_now_add=True)
    actualizado = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "configuración del emisor"
        verbose_name_plural = "configuraciones del emisor"
        ordering = ["-activo", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["ruc", "estab", "pto_emi", "ambiente"],
                name="sri_fe_emisor_ruc_serie_ambiente",
            )
        ]

    def __str__(self) -> str:
        return f"{self.razon_social} ({self.ruc}) — {self.get_ambiente_display()}"

    # ------------------------------------------------------------ propiedades

    @property
    def serie(self) -> str:
        return f"{self.estab:0>3}{self.pto_emi:0>3}"

    @property
    def es_rimpe(self) -> bool:
        return "RIMPE" in (self.regimen or "").upper()

    @property
    def es_negocio_popular(self) -> bool:
        return CATEGORIA_NEGOCIO_POPULAR in (self.categoria or "").upper()

    @property
    def rimpe_texto(self) -> Optional[str]:
        """Valor exacto que admite ``contribuyenteRimpe`` en el XML del SRI."""
        if not self.es_rimpe:
            return None
        from ..modelos import RIMPE_GENERAL, RIMPE_NEGOCIO_POPULAR

        return RIMPE_NEGOCIO_POPULAR if self.es_negocio_popular else RIMPE_GENERAL

    @property
    def tiene_certificado(self) -> bool:
        return bool(self.certificado)

    # --------------------------------------------------------- contraseña

    def establecer_clave(self, clave: str) -> None:
        """Cifra y guarda la contraseña del certificado."""
        from .crypto import cifrar
        from .conf import clave_cifrado

        texto = (clave or "").strip()
        self.clave_certificado_cifrada = cifrar(texto, clave_cifrado()) if texto else ""

    def obtener_clave(self) -> str:
        """Devuelve la contraseña del certificado en claro (para firmar)."""
        if not self.clave_certificado_cifrada:
            return ""
        from .conf import clave_cifrado
        from .crypto import descifrar

        return descifrar(self.clave_certificado_cifrada, clave_cifrado())

    # ------------------------------------------------------------ conversión

    def a_emisor(self) -> Any:
        """Construye el :class:`~factec.modelos.Emisor`."""
        from ..modelos import Emisor

        return Emisor(
            ruc=self.ruc,
            razon_social=self.razon_social,
            nombre_comercial=self.nombre_comercial or None,
            dir_matriz=self.dir_matriz,
            dir_establecimiento=self.dir_establecimiento or self.dir_matriz,
            estab=self.estab,
            pto_emi=self.pto_emi,
            obligado_contabilidad=self.obligado_contabilidad,
            contribuyente_especial=self.contribuyente_especial or None,
            agente_retencion=self.agente_retencion or None,
            contribuyente_rimpe=self.es_rimpe,
            regimen=self.categoria or self.regimen or None,
        )

    def _leer_certificado(self) -> bytes:
        """Lee los bytes del ``.p12``, rebobinando si ya se leyó antes.

        Se evita cerrar el archivo en memoria para que el objeto pueda validarse
        (formulario) y volver a usarse (firma) sin fallos por «I/O on closed file».
        """
        archivo = self.certificado
        try:
            archivo.seek(0)
        except Exception:  # noqa: BLE001 - archivo cerrado o sin soporte de seek
            try:
                archivo.open("rb")
            except Exception:  # noqa: BLE001
                pass
        datos = archivo.read()
        try:
            archivo.seek(0)
        except Exception:  # noqa: BLE001
            pass
        return datos

    def certificado_obj(self, *, validar_vigencia: bool = True) -> Any:
        """Abre el ``.p12`` almacenado y devuelve el certificado listo para firmar.

        Se lee en memoria, así que funciona con cualquier almacenamiento de
        archivos (disco local, S3, etc.).

        Lanza :class:`~factec.excepciones.ErrorCertificado` si
        falta el archivo, falta la contraseña o el ``.p12`` no se puede abrir.
        """
        from ..excepciones import ErrorCertificado
        from ..firma import Certificado

        if not self.certificado:
            raise ErrorCertificado(
                "La configuración no tiene archivo de firma. Súbalo en el campo "
                "«archivo de firma (.p12 / .pfx)»."
            )
        clave = self.obtener_clave()
        if not clave:
            raise ErrorCertificado(
                "La configuración no tiene contraseña del certificado. Escríbala en el "
                "campo «contraseña del certificado»."
            )
        certificado = Certificado.desde_bytes(self._leer_certificado(), clave)
        if validar_vigencia:
            certificado.validar_vigencia()
        return certificado

    def ruc_del_certificado(self) -> Optional[str]:
        """RUC del titular del certificado, si se puede extraer."""
        return extraer_ruc(self.certificado_obj(validar_vigencia=False))

    def validar_firma(self) -> List[str]:
        """Comprueba el certificado y devuelve avisos (o lanza si no se puede abrir)."""
        avisos: List[str] = []
        certificado = self.certificado_obj()
        if certificado.vencido():
            avisos.append("El certificado está vencido o aún no es válido.")
        ruc_cert = self.ruc_del_certificado()
        if ruc_cert and ruc_cert != self.ruc:
            avisos.append(
                f"El RUC del certificado ({ruc_cert}) no coincide con el del emisor ({self.ruc})."
            )
        return avisos

    # ------------------------------------------------------------- guardado

    def clean(self) -> None:
        super().clean()
        errores: Dict[str, str] = {}

        if self.ruc and (len(self.ruc) != 13 or not self.ruc.isdigit()):
            errores["ruc"] = "El RUC debe tener 13 dígitos numéricos."
        if self.estab and (len(self.estab) != 3 or not self.estab.isdigit()):
            errores["estab"] = "El establecimiento debe tener 3 dígitos (ej. 001)."
        if self.pto_emi and (len(self.pto_emi) != 3 or not self.pto_emi.isdigit()):
            errores["pto_emi"] = "El punto de emisión debe tener 3 dígitos (ej. 001)."
        if self.agente_retencion and not self.agente_retencion.isdigit():
            errores["agente_retencion"] = "El n.º de agente de retención debe ser numérico."

        try:
            validar_cantidad(
                leer_campos_adicionales(self.campos_adicionales),
                MAXIMO_CAMPOS_ADICIONALES,
                "«infoAdicional»",
            )
        except ValidationError as error:
            errores["campos_adicionales"] = " ".join(error.messages)

        if errores:
            raise ValidationError(errores)

    def save(self, *args: Any, **kwargs: Any) -> "ConfiguracionEmisor":
        # Una sola configuración activa por ambiente.
        if self.activo and self.ambiente:
            ConfiguracionEmisor.objects.filter(
                activo=True, ambiente=self.ambiente
            ).exclude(pk=self.pk).update(activo=False)
        return super().save(*args, **kwargs)


class ComprobanteEmitido(models.Model):
    """Un comprobante electrónico y su ciclo de vida ante el SRI."""

    configuracion = models.ForeignKey(
        ConfiguracionEmisor, verbose_name="configuración del emisor",
        null=True, blank=True, on_delete=models.SET_NULL, related_name="comprobantes",
    )

    # --- vínculo con el documento de origen del proyecto -------------------
    content_type = models.ForeignKey(
        "contenttypes.ContentType", verbose_name="tipo de documento",
        null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    object_id = models.PositiveBigIntegerField(
        "id del documento", null=True, blank=True, db_index=True,
    )
    objeto = GenericForeignKey("content_type", "object_id")

    clave_acceso = models.CharField(
        "clave de acceso", max_length=49, unique=True, db_index=True,
        help_text="Clave de 49 dígitos generada con el algoritmo del SRI.",
    )
    tipo_comprobante = models.CharField(
        "tipo de comprobante", max_length=2, choices=TipoComprobante.choices,
        default=TipoComprobante.FACTURA,
    )
    ambiente = models.PositiveSmallIntegerField(
        "ambiente", default=1, help_text="1 = pruebas, 2 = producción.",
    )
    tipo_emision = models.CharField("tipo de emisión", max_length=1, default="1")
    estab = models.CharField("establecimiento", max_length=3, default="001")
    pto_emi = models.CharField("punto de emisión", max_length=3, default="001")
    secuencial = models.CharField("secuencial", max_length=9)
    fecha_emision = models.DateField("fecha de emisión")

    razon_social_receptor = models.CharField("razón social del receptor", max_length=300, blank=True)
    identificacion_receptor = models.CharField("identificación del receptor", max_length=20, blank=True)
    importe_total = models.DecimalField(
        "importe total", max_digits=14, decimal_places=2, default=Decimal("0.00"),
    )

    estado = models.CharField(
        "estado", max_length=16, choices=EstadoComprobante.choices,
        default=EstadoComprobante.BORRADOR, db_index=True,
    )
    numero_autorizacion = models.CharField(
        "número de autorización", max_length=49, blank=True, db_index=True,
    )
    fecha_autorizacion = models.DateTimeField("fecha de autorización", null=True, blank=True)
    mensajes = models.JSONField("mensajes del SRI", default=list, blank=True)
    intentos = models.PositiveSmallIntegerField("intentos de envío", default=0)
    error = models.TextField("último error", blank=True)

    xml_sin_firma = models.TextField("XML sin firmar", blank=True)
    xml_firmado = models.TextField("XML firmado", blank=True)
    xml_autorizado = models.TextField("XML autorizado", blank=True)

    creado = models.DateTimeField("creado", auto_now_add=True)
    actualizado = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "comprobante emitido"
        verbose_name_plural = "comprobantes emitidos"
        ordering = ["-creado"]
        get_latest_by = "creado"
        indexes = [
            models.Index(fields=["estado", "-creado"], name="sri_fe_estado_creado"),
            models.Index(fields=["tipo_comprobante", "secuencial"], name="sri_fe_tipo_secuencial"),
            models.Index(fields=["content_type", "object_id"], name="sri_fe_documento"),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_comprobante_display()} {self.numero_comprobante}"

    # ----------------------------------------------------------- propiedades

    @property
    def autorizado(self) -> bool:
        return self.estado == EstadoComprobante.AUTORIZADO

    @property
    def en_proceso(self) -> bool:
        return self.estado == EstadoComprobante.EN_PROCESO

    @property
    def es_final(self) -> bool:
        return self.estado in ESTADOS_FINALES

    @property
    def puede_reintentarse(self) -> bool:
        return self.estado in ESTADOS_REINTENTABLES

    @property
    def numero_comprobante(self) -> str:
        """Número legible: ``estab-ptoEmi-secuencial``."""
        return f"{self.estab}-{self.pto_emi}-{self.secuencial}"

    @property
    def xml_para_archivar(self) -> str:
        """El XML con validez legal: el autorizado si existe, si no el firmado."""
        return self.xml_autorizado or self.xml_firmado or self.xml_sin_firma

    # ------------------------------------------------------------- mutadores

    def marcar(
        self,
        estado: str,
        *,
        mensajes: Optional[List[Dict[str, Any]]] = None,
        error: str = "",
        guardar: bool = True,
    ) -> "ComprobanteEmitido":
        """Actualiza el estado y los mensajes del comprobante."""
        self.estado = estado
        if mensajes is not None:
            self.mensajes = list(mensajes)
        self.error = error or ""
        if guardar:
            self.save(update_fields=["estado", "mensajes", "error", "actualizado"])
        return self

    def marcar_autorizado(
        self,
        numero_autorizacion: str,
        fecha_autorizacion: Optional[Any] = None,
        xml_autorizado: str = "",
        mensajes: Optional[List[Dict[str, Any]]] = None,
    ) -> "ComprobanteEmitido":
        self.estado = EstadoComprobante.AUTORIZADO
        self.numero_autorizacion = numero_autorizacion
        self.fecha_autorizacion = fecha_autorizacion or timezone.now()
        if xml_autorizado:
            self.xml_autorizado = xml_autorizado
        if mensajes is not None:
            self.mensajes = list(mensajes)
        self.error = ""
        self.save(
            update_fields=[
                "estado", "numero_autorizacion", "fecha_autorizacion",
                "xml_autorizado", "mensajes", "error", "actualizado",
            ]
        )
        return self

    def registrar_mensajes(self, mensajes: List[Dict[str, Any]]) -> None:
        """Guarda los mensajes del SRI como lista de diccionarios."""
        self.mensajes = list(mensajes)
        self.save(update_fields=["mensajes", "actualizado"])

    # ----------------------------------------------- vínculo con el origen

    def vincular(self, objeto: Any, *, guardar: bool = True) -> "ComprobanteEmitido":
        """Asocia el comprobante con el documento del proyecto que lo originó.

        Si ``objeto`` no es un modelo de Django (o no está guardado) no hay nada
        que enlazar y se deja el comprobante sin vínculo.
        """
        if not es_modelo_guardado(objeto):
            return self
        self.content_type = ContentType.objects.get_for_model(objeto)
        self.object_id = objeto.pk
        if guardar:
            self.save(update_fields=["content_type", "object_id", "actualizado"])
        return self

    @classmethod
    def para_objeto(
        cls, objeto: Any, tipo_comprobante: Optional[str] = None
    ) -> Optional["ComprobanteEmitido"]:
        """Devuelve el comprobante ya emitido para ``objeto``, si lo hay."""
        if not es_modelo_guardado(objeto):
            return None
        consulta = cls.objects.filter(
            content_type=ContentType.objects.get_for_model(objeto), object_id=objeto.pk
        )
        if tipo_comprobante:
            consulta = consulta.filter(tipo_comprobante=tipo_comprobante)
        return consulta.order_by("-creado").first()

    @classmethod
    def ya_emitido(cls, objeto: Any, tipo_comprobante: Optional[str] = None) -> bool:
        """Indica si ``objeto`` ya tiene un comprobante en estado autorizado."""
        comprobante = cls.para_objeto(objeto, tipo_comprobante)
        return bool(comprobante and comprobante.autorizado)


#: Estados desde los que ya no tiene sentido reintentar automáticamente.
ESTADOS_FINALES = frozenset(
    {
        EstadoComprobante.AUTORIZADO,
        EstadoComprobante.DEVUELTO,
        EstadoComprobante.NO_AUTORIZADO,
    }
)

# Los catálogos (clientes y productos) y los seis comprobantes del SRI viven en
# ``documentos``; se importan aquí para que Django los descubra al cargar la app.
from .documentos import *  # noqa: E402,F401,F403
from .documentos import __all__ as _NOMBRES_DOCUMENTOS  # noqa: E402

__all__ = [*__all__, *_NOMBRES_DOCUMENTOS]  # noqa: PLE0605

#: Estados que sí admiten un reintento.
ESTADOS_REINTENTABLES = frozenset(
    {
        EstadoComprobante.BORRADOR,
        EstadoComprobante.FIRMADO,
        EstadoComprobante.EN_PROCESO,
        EstadoComprobante.ERROR,
    }
)


class Secuencial(models.Model):
    """Contador persistente de secuenciales por tipo, establecimiento y ambiente.

    ``EmisorElectronico`` lleva el secuencial en memoria, lo que no sirve en un
    servidor web con varios procesos. Este modelo lo guarda en la base de datos y
    lo incrementa dentro de una transacción con bloqueo de fila.
    """

    tipo_comprobante = models.CharField(max_length=2, choices=TipoComprobante.choices)
    estab = models.CharField(max_length=3)
    pto_emi = models.CharField(max_length=3)
    ambiente = models.PositiveSmallIntegerField(default=1)
    ultimo = models.PositiveIntegerField(default=0)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "secuencial"
        verbose_name_plural = "secuenciales"
        constraints = [
            models.UniqueConstraint(
                fields=["tipo_comprobante", "estab", "pto_emi", "ambiente"],
                name="sri_fe_secuencial_unico",
            )
        ]

    def __str__(self) -> str:
        return f"{self.tipo_comprobante} {self.estab}-{self.pto_emi} ({self.ambiente}): {self.ultimo}"

    @classmethod
    def siguiente(
        cls,
        tipo_comprobante: str,
        estab: str = "001",
        pto_emi: str = "001",
        ambiente: int = 1,
    ) -> int:
        """Reserva y devuelve el siguiente secuencial de forma atómica."""
        with transaction.atomic():
            fila, _ = cls.objects.select_for_update().get_or_create(
                tipo_comprobante=tipo_comprobante,
                estab=estab,
                pto_emi=pto_emi,
                ambiente=ambiente,
            )
            fila.ultimo += 1
            fila.save(update_fields=["ultimo", "actualizado"])
            return fila.ultimo
