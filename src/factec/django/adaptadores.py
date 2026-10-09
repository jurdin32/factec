"""Adaptadores: convierten un modelo del proyecto en un comprobante del SRI.

La idea es que no tengas que construir los comprobantes a mano. Le pasas tu
objeto —una factura, un pedido, una venta, lo que sea— y el paquete se encarga de
armar el XML, firmarlo y enviarlo.

Hay dos formas de usarlos:

**1. Por convención.** Si tu modelo sigue nombres habituales, funciona sin
configurar nada::

    from factec.django import facturacion

    facturacion.emitir(mi_factura)      # ya crea, firma y envía

Los nombres que reconoce por convención son:

=========================  ======================================================
Concepto                   Atributos que busca (el primero que exista)
=========================  ======================================================
Receptor                   ``receptor``, ``cliente``, ``comprador``, ``adquiriente``
Fecha                      ``fecha_emision``, ``fecha``, ``creado``
Secuencial                 ``secuencial``, ``numero``
Líneas                     ``detalles``, ``lineas``, ``items``, ``productos``
Descripción de la línea    ``descripcion``, ``nombre``, ``producto``
Cantidad                   ``cantidad``, ``cant``
Precio unitario            ``precio_unitario``, ``precio``, ``valor_unitario``
Descuento                  ``descuento``
Código                     ``codigo_principal``, ``codigo``, ``codigo_interno``
IVA                        ``codigo_porcentaje_iva``, ``codigo_porcentaje``, ``impuesto``
Forma de pago              ``pagos``, ``forma_pago``, ``formas_pago``
Observaciones              ``observaciones``, ``notas``, ``info_adicional``
=========================  ======================================================

Del receptor busca ``razon_social``/``nombre``, ``identificacion``/``ruc``/
``cedula`` y ``direccion``.

**2. Con un adaptador propio.** Cuando los nombres no encajan (o quieres otro
comportamiento), se hereda de :class:`AdaptadorComprobante` y se registra::

    from factec.django import adaptadores, facturacion

    class AdaptadorMiFactura(adaptadores.AdaptadorFactura):
        def receptor(self, obj):
            return Receptor(
                razon_social=obj.cliente_nombre,
                identificacion=obj.cliente_documento,
            )

        def detalles(self, obj):
            return [
                Detalle(descripcion=l.concepto, cantidad=l.cant,
                        precio_unitario=l.valor,
                        impuestos=[Impuesto(codigo_porcentaje=l.iva_codigo)])
                for l in obj.renglones.all()
            ]

    adaptadores.registrar(MiFactura, AdaptadorMiFactura)
    facturacion.emitir(mi_factura)

También sirve para cualquier objeto que no sea un modelo de Django (un
``dataclass``, un diccionario, un objeto de una API): basta con que los atributos
existan.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Type

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

from ..catalogos import FormaPago, TarifaIva, TipoComprobante, TipoIdentificacion
from ..comprobantes import (
    Comprobante,
    ComprobanteRetencion,
    Factura,
    GuiaRemision,
    LiquidacionCompra,
    NotaCredito,
    NotaDebito,
)
from ..excepciones import ErrorValidacion
from ..modelos import (
    Destinatario,
    Detalle,
    DetalleGuia,
    DocSustento,
    Impuesto,
    ImpuestoDocSustento,
    ImpuestoRetencion,
    Motivo,
    Pago,
    PagoRetencion,
    Receptor,
    a_decimal,
    cuantizar,
)
from .campos_adicionales import leer_campos_adicionales

__all__ = [
    "AdaptadorComprobante",
    "AdaptadorFactura",
    "AdaptadorNotaCredito",
    "AdaptadorNotaDebito",
    "AdaptadorRetencion",
    "AdaptadorGuiaRemision",
    "AdaptadorLiquidacionCompra",
    "Adaptadores",
    "registrar",
    "registrar_los_del_paquete",
    "registrar_para",
    "adaptador_para",
    "obtener",
    "SIN_VALOR",
]

#: Marcador para distinguir «no encontrado» de ``None``.
SIN_VALOR = object()

#: Nombres que se buscan por convención.
NOMBRES_RECEPTOR = ("receptor", "cliente", "comprador", "adquiriente", "proveedor",
                    "sujeto_retenido")
NOMBRES_FECHA = ("fecha_emision", "fecha", "fecha_factura", "creado")
NOMBRES_SECUENCIAL = ("secuencial", "numero", "numero_comprobante")
NOMBRES_LINEAS = ("detalles", "lineas", "items", "productos", "renglones", "detalle_set")
NOMBRES_DESCRIPCION = ("descripcion", "concepto", "nombre", "producto", "detalle")
NOMBRES_MOTIVO = ("razon", "motivo") + NOMBRES_DESCRIPCION
NOMBRES_CANTIDAD = ("cantidad", "cant", "unidades")
NOMBRES_PRECIO = ("precio_unitario", "precio", "valor_unitario", "valor", "precio_venta")
NOMBRES_DESCUENTO = ("descuento", "descuento_unitario")
NOMBRES_CODIGO = ("codigo_principal", "codigo", "codigo_interno", "sku", "referencia")
NOMBRES_CODIGO_AUXILIAR = ("codigo_auxiliar", "codigoAuxiliar", "codigo_adicional",
                           "codigo_secundario")
NOMBRES_IVA = ("codigo_porcentaje_iva", "codigo_porcentaje", "tarifa_iva", "iva_codigo")
NOMBRES_FORMA_PAGO = ("pagos", "forma_pago", "formas_pago", "tipo_pago")
NOMBRES_OBSERVACIONES = ("observaciones", "notas", "comentario")

#: El texto libre de ``infoAdicional`` (``NOMBRE=VALOR; …`` o un diccionario).
NOMBRES_INFO_ADICIONAL = (
    "informacion_adicional",
    "info_adicional",
    "campos_adicionales",
    "datos_adicionales",
)

#: Los ``detAdicional`` de una línea.
NOMBRES_DETALLES_ADICIONALES = (
    "detalles_adicionales",
    "datos_adicionales",
    "detalle_adicional",
)
NOMBRES_PRODUCTO = ("producto", "articulo", "item_producto", "servicio", "bien")
NOMBRES_RAZON_SOCIAL = ("razon_social", "nombre", "nombre_completo", "razonSocial")
NOMBRES_IDENTIFICACION = ("identificacion", "ruc", "cedula", "numero_identificacion",
                          "documento", "cedula_ruc")
NOMBRES_DIRECCION = ("direccion", "direccion_cliente", "domicilio")
NOMBRES_TIPO_ID = ("tipo_identificacion", "tipo_id", "tipo_documento")


def _obtener(obj: Any, nombres: Sequence[str], por_defecto: Any = SIN_VALOR) -> Any:
    """Devuelve el primer atributo/clave de ``nombres`` que tenga valor.

    Admite objetos, diccionarios y métodos sin argumentos (se invocan).
    """
    if obj is None:
        return None if por_defecto is SIN_VALOR else por_defecto

    for nombre in nombres:
        valor: Any = SIN_VALOR
        if isinstance(obj, dict):
            if nombre in obj:
                valor = obj[nombre]
        else:
            try:
                valor = getattr(obj, nombre)
            except AttributeError:
                continue
            except Exception:  # noqa: BLE001 - propiedades perezosas que fallan
                continue

        if valor is SIN_VALOR or valor is None or valor == "":
            continue
        # Las relaciones de Django (``RelatedManager``) y los ``QuerySet`` también
        # son invocables: hay que tratarlos como valores, no como métodos.
        if callable(valor) and not hasattr(valor, "all"):
            try:
                valor = valor()
            except TypeError:
                continue
        if valor is None or valor == "":
            continue
        return valor

    if por_defecto is SIN_VALOR:
        return None
    return por_defecto


def _coleccion(valor: Any) -> List[Any]:
    """Normaliza un manager, queryset, lista o generador a una lista."""
    if valor is None:
        return []
    if hasattr(valor, "all") and callable(valor.all):
        valor = valor.all()
    if isinstance(valor, (str, bytes, dict)):
        return [valor]
    try:
        return list(valor)
    except TypeError:
        return [valor]


def _datos_adicionales(valor: Any) -> Dict[str, str]:
    """Normaliza los datos adicionales de un detalle a un diccionario.

    Admite el texto ``NOMBRE=VALOR; …`` que se escribe en el admin, un diccionario
    o una colección de filas con ``nombre``/``valor``.
    """
    if not valor:
        return {}
    if isinstance(valor, str):
        return leer_campos_adicionales(valor)
    if isinstance(valor, dict):
        return {str(nombre): str(dato) for nombre, dato in valor.items()}

    datos: Dict[str, str] = {}
    for elemento in _coleccion(valor):
        nombre = _obtener(elemento, ("nombre", "campo", "clave", "key"))
        dato = _obtener(elemento, ("valor", "value", "dato"))
        if nombre is not None and dato is not None:
            datos[str(nombre)] = str(dato)
    return datos


def _fecha(valor: Any) -> Optional[date]:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip().replace("-", "/")
    for formato in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ErrorValidacion(f"No se pudo interpretar la fecha {valor!r}.")


class AdaptadorComprobante:
    """Traduce un objeto del proyecto a un comprobante del paquete.

    Las subclases sobrescriben solo lo que necesiten; el resto son ganchos con
    valores por omisión razonables.
    """

    #: Código ``codDoc`` que genera este adaptador.
    tipo: str = TipoComprobante.FACTURA.value

    comprobante_clase: Type[Comprobante] = Factura

    def __init__(self, configuracion: Any = None, emisor: Any = None,
                 ambiente: Any = None, fecha_emision: Any = None) -> None:
        self.configuracion = configuracion
        self._emisor = emisor
        self._ambiente = ambiente
        self._fecha_emision = fecha_emision

    # ------------------------------------------------------ datos comunes

    def emisor(self, obj: Any) -> Any:
        """Emisor del comprobante.

        Se busca, por este orden: en el propio objeto (``emisor``,
        ``emisor_electronico``, ``contribuyente``), el que se pasó al adaptador y,
        por último, la configuración activa de la base de datos.
        """
        propio = self._clave_emisor(obj)
        if propio is not None:
            return propio
        if self._emisor is not None:
            return self._emisor
        from . import conf

        return conf.emisor()

    @staticmethod
    def _clave_emisor(obj: Any) -> Any:
        """Devuelve el emisor expuesto por el objeto, si lo hay."""
        for nombre in ("emisor", "emisor_electronico", "contribuyente"):
            if isinstance(obj, dict):
                if nombre in obj:
                    return obj[nombre]
                continue
            if hasattr(obj, nombre):
                valor = getattr(obj, nombre)
                if valor is not None:
                    return valor
        return None

    def ambiente(self, obj: Any) -> Any:
        """Ambiente (1 pruebas, 2 producción): del objeto o de la configuración."""
        propio = _obtener(obj, ("ambiente",))
        if propio is not None:
            return propio
        if self._ambiente is not None:
            return self._ambiente
        from . import conf

        return conf.ambiente()

    def fecha_emision(self, obj: Any) -> date:
        """Fecha de emisión del comprobante: la del documento.

        El adaptador solo traduce: la fecha con la que se **emite** la decide el
        flujo de emisión (ver ``FECHA_EMISION_AL_EMITIR`` en
        :func:`factec.django.facturacion.emitir`). Si el objeto no declara ninguna
        fecha se usa la de hoy en Ecuador, que es el reloj del SRI.
        """
        from ..sri import fechas

        if self._fecha_emision is not None:
            return _fecha(self._fecha_emision) or fechas.hoy_en_ecuador()
        return _fecha(_obtener(obj, NOMBRES_FECHA)) or fechas.hoy_en_ecuador()

    def secuencial(self, obj: Any) -> Optional[str]:
        """Secuencial del comprobante; ``None`` para que se reserve uno."""
        valor = _obtener(obj, NOMBRES_SECUENCIAL)
        return str(valor).strip() if valor not in (None, "") else None

    def info_adicional(self, obj: Any) -> Dict[str, Any]:
        """Campos de ``infoAdicional``: los de la tienda y los del comprobante.

        Primero van los campos adicionales configurados para toda la tienda
        (``ConfiguracionEmisor.campos_adicionales``) y después los del comprobante,
        que tienen prioridad. Las observaciones se envían como un campo más.
        """
        campos: Dict[str, str] = {}
        campos.update(self.campos_adicionales_de_la_tienda(obj))

        valor = _obtener(obj, NOMBRES_INFO_ADICIONAL)
        if valor:
            campos.update(_datos_adicionales(valor))

        observaciones = _obtener(obj, NOMBRES_OBSERVACIONES)
        if isinstance(observaciones, dict):
            campos.update({str(k): str(v) for k, v in observaciones.items()})
        elif observaciones:
            campos["Observaciones"] = str(observaciones)[:300]
        return campos

    def campos_adicionales_de_la_tienda(self, obj: Any = None) -> Dict[str, str]:
        """Campos adicionales configurados para todos los comprobantes.

        Salen de la configuración del emisor (o del objeto que se pasó al
        adaptador), así que la tienda los define una sola vez.
        """
        origen = self.configuracion
        if origen is None:
            propio = self._clave_emisor(obj) if obj is not None else None
            if propio is not None and _obtener(propio, ("campos_adicionales",)) is not None:
                origen = propio
        if origen is None:
            # El adaptador también sirve con objetos sueltos, sin Django montado.
            from django.conf import settings as django_settings

            if not django_settings.configured:
                return {}
            from . import conf

            try:
                origen = conf.configuracion_activa()
            except (DatabaseError, ImproperlyConfigured):
                return {}
        if origen is None:
            return {}
        valor = _obtener(origen, ("campos_adicionales",))
        return _datos_adicionales(valor)

    def receptor(self, obj: Any) -> Optional[Receptor]:
        """Receptor/proveedor/sujeto retenido del comprobante."""
        datos = _obtener(obj, NOMBRES_RECEPTOR)
        if datos is None:
            return None
        if isinstance(datos, Receptor):
            return datos

        tipo = _obtener(datos, NOMBRES_TIPO_ID)
        identificacion = _obtener(datos, NOMBRES_IDENTIFICACION, "")
        identificacion = str(identificacion or "").strip()
        if tipo is None:
            tipo = self._deducir_tipo_identificacion(identificacion)

        return Receptor(
            razon_social=str(_obtener(datos, NOMBRES_RAZON_SOCIAL, "") or ""),
            identificacion=identificacion,
            tipo_identificacion=tipo,
            direccion=_obtener(datos, NOMBRES_DIRECCION),
            email=_obtener(datos, ("email", "correo", "correo_electronico")),
        )

    @staticmethod
    def _deducir_tipo_identificacion(identificacion: str) -> str:
        """Deduce el código del SRI a partir del número."""
        digitos = "".join(c for c in (identificacion or "") if c.isdigit())
        if len(digitos) == 13:
            return TipoIdentificacion.RUC
        if len(digitos) == 10:
            return TipoIdentificacion.CEDULA
        if not digitos:
            return TipoIdentificacion.CONSUMIDOR_FINAL
        return TipoIdentificacion.PASAPORTE

    # ----------------------------------------------------------- detalles

    def detalles(self, obj: Any) -> List[Detalle]:
        """Líneas del comprobante."""
        lineas = _obtener(obj, NOMBRES_LINEAS)
        resultado: List[Detalle] = []
        for linea in _coleccion(lineas):
            resultado.append(self.detalle_desde_linea(linea, obj))
        return resultado

    def detalle_desde_linea(self, linea: Any, obj: Any = None) -> Detalle:
        """Convierte una línea del proyecto en un :class:`Detalle`."""
        if isinstance(linea, Detalle):
            return linea

        codigo_iva = _obtener(linea, NOMBRES_IVA)
        if codigo_iva is None and obj is not None:
            codigo_iva = _obtener(obj, NOMBRES_IVA)
        impuesto = self.impuesto_desde(codigo_iva, linea)

        return Detalle(
            descripcion=str(_obtener(linea, NOMBRES_DESCRIPCION, "") or "").strip(),
            cantidad=_obtener(linea, NOMBRES_CANTIDAD, 1),
            precio_unitario=_obtener(linea, NOMBRES_PRECIO, 0),
            descuento=_obtener(linea, NOMBRES_DESCUENTO, 0),
            codigo_principal=_obtener(linea, NOMBRES_CODIGO),
            codigo_auxiliar=_obtener(linea, NOMBRES_CODIGO_AUXILIAR),
            unidad_medida=_obtener(linea, ("unidad_medida", "unidad", "medida")),
            detalles_adicionales=self.detalles_adicionales_de_linea(linea),
            impuestos=[impuesto],
        )

    def detalles_adicionales_de_linea(self, linea: Any) -> Dict[str, str]:
        """``detAdicional`` de una línea: los suyos y, si no tiene, los del producto.

        Así el detalle adicional se configura una sola vez, en el producto o
        servicio, y viaja en todas las líneas que lo usen.
        """
        propios = _datos_adicionales(_obtener(linea, NOMBRES_DETALLES_ADICIONALES, None))
        if propios:
            return propios
        producto = _obtener(linea, NOMBRES_PRODUCTO)
        if producto is None:
            return {}
        return _datos_adicionales(_obtener(producto, NOMBRES_DETALLES_ADICIONALES, None))

    def impuesto_desde(self, codigo_iva: Any, linea: Any = None) -> Impuesto:
        """Construye el impuesto de una línea.

        Acepta el código del SRI (``"4"``), una instancia de ``TarifaIva`` o un
        objeto que exponga ``codigoPorcentaje``/``codigo_porcentaje``.
        """
        if codigo_iva is None:
            codigo_iva = TarifaIva.IVA_15

        if isinstance(codigo_iva, Impuesto):
            return codigo_iva
        if isinstance(codigo_iva, Decimal) or isinstance(codigo_iva, int):
            # Se pasó la tarifa numérica (15, 0, 5…): se busca su código.
            codigo_iva = self._codigo_desde_tarifa(codigo_iva)
        elif not isinstance(codigo_iva, (str, TarifaIva)):
            codigo_iva = _obtener(
                codigo_iva, ("codigo_porcentaje", "codigoPorcentaje", "codigo", "id")
            ) or TarifaIva.IVA_15

        return Impuesto(codigo_porcentaje=codigo_iva)

    @staticmethod
    def _codigo_desde_tarifa(tarifa: Any) -> str:
        from ..catalogos import PORCENTAJE_IVA

        valor = a_decimal(tarifa)
        for codigo, porcentaje in PORCENTAJE_IVA.items():
            if porcentaje == valor:
                return codigo
        return TarifaIva.IVA_15

    # -------------------------------------------------------------- pagos

    def total_documento(self, obj: Any) -> Any:
        """Importe total del comprobante.

        Sirve para los pagos que no indican su importe: el SRI exige que la suma
        de los pagos coincida con el total.
        """
        from ..comprobantes import calcular_totales

        try:
            return calcular_totales(self.detalles(obj))["importe_total"]
        except (ErrorValidacion, TypeError, KeyError):
            return Decimal("0")

    def _pago(self, obj: Any, datos: Any) -> Pago:
        """Construye un :class:`Pago` desde un código, un diccionario o un objeto.

        El importe se toma del propio pago y, si no lo indica, del total del
        comprobante. El plazo y la unidad de tiempo se heredan del documento.
        """
        if isinstance(datos, (str, FormaPago)):
            forma, resto = datos, None
        else:
            forma = _obtener(datos, ("forma_pago", "formaPago", "codigo", "tipo"))
            resto = datos

        valor = _obtener(resto, ("total", "valor", "monto")) if resto is not None else None
        plazo = _obtener(resto, ("plazo",)) if resto is not None else None
        unidad = _obtener(resto, ("unidad_tiempo", "unidadTiempo")) if resto is not None else None

        if plazo is None:
            plazo = _obtener(obj, ("plazo",))
        if unidad is None:
            unidad = _obtener(obj, ("unidad_tiempo", "unidadTiempo"))

        return Pago(
            forma_pago=forma or FormaPago.SIN_SISTEMA_FINANCIERO,
            total=valor if valor is not None else self.total_documento(obj),
            plazo=plazo,
            unidad_tiempo=unidad,
        )

    def pagos(self, obj: Any) -> List[Pago]:
        """Formas de pago del comprobante.

        Acepta el código de una forma de pago, un diccionario, un objeto ``Pago`` o
        una colección (lista, ``QuerySet``, gestión relacionada).
        """
        valor = _obtener(obj, NOMBRES_FORMA_PAGO)
        if valor is None:
            return []

        if isinstance(valor, (str, FormaPago, dict)):
            return [self._pago(obj, valor)]

        pagos: List[Pago] = []
        for elemento in _coleccion(valor):
            if isinstance(elemento, Pago):
                pagos.append(elemento)
                continue
            forma = _obtener(elemento, ("forma_pago", "formaPago", "codigo", "tipo"), None)
            total = _obtener(elemento, ("total", "valor", "monto"), None)
            if forma is None and total is None:
                continue
            pagos.append(self._pago(obj, elemento))
        return pagos

    # --------------------------------------------------------- ensamblado

    def parametros(self, obj: Any) -> Dict[str, Any]:
        """Argumentos para construir el comprobante (los fija cada subclase)."""
        raise NotImplementedError

    def comprobante(self, obj: Any) -> Comprobante:
        """Construye el comprobante a partir del objeto."""
        parametros = {
            "emisor": self.emisor(obj),
            "ambiente": self.ambiente(obj),
            "fecha_emision": self.fecha_emision(obj),
            "info_adicional": self.info_adicional(obj),
        }
        secuencial = self.secuencial(obj)
        if secuencial:
            parametros["secuencial"] = secuencial
        else:
            from . import services

            parametros["secuencial"] = services.siguiente_secuencial(self.tipo)

        parametros.update(self.parametros(obj))
        parametros.update(self.datos_del_establecimiento(obj))
        return self.comprobante_clase(**parametros)  # type: ignore[arg-type]

    def datos_del_establecimiento(self, obj: Any) -> Dict[str, Any]:
        """Datos del establecimiento que el documento puede sobrescribir.

        Si no se indican, se usan los de la configuración del emisor.
        """
        datos = {
            "direccion_establecimiento": _obtener(
                obj, ("direccion_establecimiento", "dir_establecimiento")
            ),
            "obligado_contabilidad": _obtener(obj, ("obligado_contabilidad",)),
            "contribuyente_especial": _obtener(obj, ("contribuyente_especial",)),
        }
        return {clave: valor for clave, valor in datos.items() if valor is not None}

    #: Código ``codDoc`` de los comprobantes que modifican a otro.
    TIPOS_MODIFICAN = (
        TipoComprobante.NOTA_CREDITO.value,
        TipoComprobante.NOTA_DEBITO.value,
    )

    #: Código ``codDoc`` de los comprobantes que modifican a otro.
    TIPOS_MODIFICAN = (
        TipoComprobante.NOTA_CREDITO.value,
        TipoComprobante.NOTA_DEBITO.value,
    )

    def documento_modificado(self, obj: Any) -> Dict[str, str]:
        """Datos del comprobante que se modifica (notas de crédito y débito)."""
        codigo = str(
            _obtener(obj, ("cod_doc_modificado", "codDocModificado"), TipoComprobante.FACTURA.value)
        )
        numero = _obtener(obj, ("num_doc_modificado", "numDocModificado", "documento_modificado"))
        fecha = _obtener(obj, ("fecha_emision_doc_sustento", "fecha_documento_modificado"))
        if not numero:
            raise ErrorValidacion(
                "Indique el número del documento que se modifica "
                "(num_doc_modificado) para emitir este comprobante."
            )
        return {
            "cod_doc_modificado": codigo,
            "num_doc_modificado": str(numero),
            "fecha_emision_doc_sustento": _fecha(fecha) or self.fecha_emision(obj),
        }


class AdaptadorFactura(AdaptadorComprobante):
    """Factura (``codDoc`` 01). Es el adaptador por omisión."""

    tipo = TipoComprobante.FACTURA.value
    comprobante_clase = Factura

    def datos_opcionales(self, obj: Any) -> Dict[str, Any]:
        """Campos de la factura que solo viajan al XML si se indican."""
        datos = {
            "propina": _obtener(obj, ("propina",)),
            "placa": _obtener(obj, ("placa", "vehiculo", "matricula")),
            "guia_remision": _obtener(obj, ("guia_remision", "guiaRemision")),
            "valor_ret_iva": _obtener(obj, ("valor_ret_iva",)),
            "valor_ret_renta": _obtener(obj, ("valor_ret_renta",)),
        }
        return {clave: valor for clave, valor in datos.items() if valor is not None}

    def parametros(self, obj: Any) -> Dict[str, Any]:
        receptor = self.receptor(obj)
        if receptor is None:
            raise ErrorValidacion(
                "No se encontró el receptor. Exponga 'receptor'/'cliente' en su modelo "
                "o defina el método receptor() en su adaptador."
            )
        parametros = {
            "receptor": receptor,
            "detalles": self.detalles(obj),
            "pagos": self.pagos(obj),
        }
        parametros.update(self.datos_opcionales(obj))
        return parametros


class AdaptadorLiquidacionCompra(AdaptadorFactura):
    """Liquidación de compra (``codDoc`` 03)."""

    tipo = TipoComprobante.LIQUIDACION_COMPRA.value
    comprobante_clase = LiquidacionCompra

    def parametros(self, obj: Any) -> Dict[str, Any]:
        proveedor = self.receptor(obj)
        if proveedor is None:
            raise ErrorValidacion("No se encontró el proveedor en el objeto.")
        return {
            "proveedor": proveedor,
            "detalles": self.detalles(obj),
            "pagos": self.pagos(obj),
            "correo_tipo_negociable": _obtener(
                obj, ("correo_tipo_negociable", "correo", "email")
            ),
        }


class AdaptadorNotaCredito(AdaptadorComprobante):
    """Nota de crédito (``codDoc`` 04)."""

    tipo = TipoComprobante.NOTA_CREDITO.value
    comprobante_clase = NotaCredito

    def motivo(self, obj: Any) -> str:
        return str(_obtener(obj, ("motivo", "razon", "descripcion"), "") or "").strip()

    def parametros(self, obj: Any) -> Dict[str, Any]:
        receptor = self.receptor(obj)
        if receptor is None:
            raise ErrorValidacion("No se encontró el receptor en el objeto.")
        motivo = self.motivo(obj)
        if not motivo:
            raise ErrorValidacion("La nota de crédito requiere un motivo.")
        return {
            "receptor": receptor,
            "detalles": self.detalles(obj),
            "motivo": motivo,
            "rise": _obtener(obj, ("rise", "numero_rise")),
            **self.documento_modificado(obj),
        }


class AdaptadorNotaDebito(AdaptadorComprobante):
    """Nota de débito (``codDoc`` 05)."""

    tipo = TipoComprobante.NOTA_DEBITO.value
    comprobante_clase = NotaDebito

    def motivos(self, obj: Any) -> List[Motivo]:
        valor = _obtener(obj, ("motivos", "detalles", "lineas"))
        motivos: List[Motivo] = []
        for elemento in _coleccion(valor):
            if isinstance(elemento, Motivo):
                motivos.append(elemento)
                continue
            motivos.append(
                Motivo(
                    razon=str(_obtener(elemento, NOMBRES_MOTIVO, "") or "").strip(),
                    valor=_obtener(elemento, ("valor", "total", "importe"), 0),
                )
            )
        return motivos

    def total_documento(self, obj: Any) -> Any:
        """``valorTotal`` de la nota de débito: motivos más impuestos."""
        motivos = self.motivos(obj)
        impuesto = _impuesto_de(self, obj, motivos).resolver()
        return cuantizar(
            sum((a_decimal(motivo.valor) for motivo in motivos), Decimal("0"))
            + a_decimal(impuesto.valor),
            2,
        )

    def parametros(self, obj: Any) -> Dict[str, Any]:
        receptor = self.receptor(obj)
        motivos = self.motivos(obj)
        if not motivos:
            raise ErrorValidacion("La nota de débito requiere al menos un motivo.")
        return {
            "receptor": receptor,
            "motivos": motivos,
            "impuestos": [_impuesto_de(self, obj, motivos)],
            "total_sin_impuestos": _obtener(
                obj, ("total_sin_impuestos", "subtotal", "base_imponible"), 0
            ),
            "pagos": self.pagos(obj),
            "rise": _obtener(obj, ("rise", "numero_rise")),
            **self.documento_modificado(obj),
        }


def _impuesto_de(adaptador: AdaptadorComprobante, obj: Any, motivos: List[Motivo]) -> Impuesto:
    """Impuesto de una nota de débito, calculado sobre el total si no se indica."""
    codigo = _obtener(obj, NOMBRES_IVA)
    impuesto = adaptador.impuesto_desde(codigo, obj)
    total = sum((a_decimal(m.valor) for m in motivos), Decimal("0"))
    impuesto.base_imponible = _obtener(obj, ("base_imponible",), total)
    return impuesto


class AdaptadorRetencion(AdaptadorComprobante):
    """Comprobante de retención (``codDoc`` 07)."""

    tipo = TipoComprobante.COMPROBANTE_RETENCION.value
    comprobante_clase = ComprobanteRetencion

    def periodo_fiscal(self, obj: Any) -> date:
        valor = _obtener(obj, ("periodo_fiscal", "periodo", "mes"))
        return _fecha(valor) or self.fecha_emision(obj)

    def docs_sustento(self, obj: Any) -> List[DocSustento]:
        valor = _obtener(obj, ("docs_sustento", "documentos", "documentos_sustento", "detalles"))
        resultado: List[DocSustento] = []
        for elemento in _coleccion(valor):
            if isinstance(elemento, DocSustento):
                resultado.append(elemento)
                continue
            resultado.append(self._doc_sustento(elemento, obj))
        return resultado

    def _doc_sustento(self, elemento: Any, obj: Any) -> DocSustento:
        retenciones: List[ImpuestoRetencion] = []
        for retencion in _coleccion(_obtener(elemento, ("retenciones", "impuestos"))):
            if isinstance(retencion, ImpuestoRetencion):
                retenciones.append(retencion)
                continue
            retenciones.append(
                ImpuestoRetencion(
                    codigo=_obtener(retencion, ("codigo", "codigo_impuesto"), "1"),
                    codigo_retencion=str(
                        _obtener(retencion, ("codigo_retencion", "codigoRetencion", "tipo"), "")
                    ),
                    base_imponible=_obtener(retencion, ("base_imponible", "base"), 0),
                    porcentaje_retener=_obtener(
                        retencion, ("porcentaje_retener", "porcentaje"), 0
                    ),
                    valor_retenido=_obtener(retencion, ("valor_retenido", "valor", "total"), 0),
                )
            )

        impuestos = self._impuestos_doc_sustento(elemento)
        pagos = [
            PagoRetencion(
                forma_pago=_obtener(elemento, ("forma_pago", "formaPago"), FormaPago.SIN_SISTEMA_FINANCIERO),
                total=_obtener(elemento, ("importe_total", "total", "valor"), 0),
            )
        ]
        return DocSustento(
            cod_sustento=str(_obtener(elemento, ("cod_sustento", "codSustento"), "01")),
            cod_doc_sustento=str(
                _obtener(elemento, ("cod_doc_sustento", "codDocSustento", "tipo_documento"), "01")
            ),
            num_doc_sustento=str(
                _obtener(elemento, ("num_doc_sustento", "numero", "numDocSustento"), "")
            ),
            fecha_emision=_fecha(_obtener(elemento, ("fecha_emision", "fecha"))) or self.fecha_emision(obj),
            total_sin_impuestos=_obtener(elemento, ("total_sin_impuestos", "subtotal"), 0),
            importe_total=_obtener(elemento, ("importe_total", "total"), 0),
            impuestos=impuestos,
            retenciones=retenciones,
            pagos=pagos,
            num_aut_doc_sustento=_obtener(elemento, ("num_aut_doc_sustento", "autorizacion")),
            fecha_registro_contable=_fecha(
                _obtener(elemento, ("fecha_registro_contable",))
            ),
            pago_loc_ext=_obtener(elemento, ("pago_loc_ext", "pagoLocExt"), "01"),
            tipo_regi=_obtener(elemento, ("tipo_regi", "tipoRegi")),
            pais_efec_pago=_obtener(elemento, ("pais_efec_pago", "paisEfecPago")),
            aplic_conv_dob_trib=_obtener(
                elemento, ("aplic_conv_dob_trib", "aplicConvDobTrib")
            ),
            pag_ext_suj_ret_nor_leg=_obtener(
                elemento, ("pag_ext_suj_ret_nor_leg", "pagExtSujRetNorLeg")
            ),
        )

    def _impuestos_doc_sustento(self, elemento: Any) -> List[ImpuestoDocSustento]:
        """Impuestos del documento sustento (``impuestosDocSustento``).

        Admite varios impuestos (el modelo suele exponer una colección) o un solo
        impuesto indicado con campos sueltos en el propio documento.
        """
        valor = _obtener(elemento, ("impuestos",))
        if valor is not None:
            resultado = []
            for impuesto in _coleccion(valor):
                if isinstance(impuesto, ImpuestoDocSustento):
                    resultado.append(impuesto)
                    continue
                resultado.append(
                    ImpuestoDocSustento(
                        codigo=str(_obtener(impuesto, ("codigo",))),
                        codigo_porcentaje=str(_obtener(impuesto, ("codigo_porcentaje",))),
                        base_imponible=_obtener(impuesto, ("base_imponible",), 0),
                        tarifa=_obtener(impuesto, ("tarifa",), 0),
                        valor=_obtener(impuesto, ("valor",), 0),
                    )
                )
            if resultado:
                return resultado

        return [
            ImpuestoDocSustento(
                codigo=str(_obtener(elemento, ("codigo_impuesto", "codigo"), "2")),
                codigo_porcentaje=str(
                    _obtener(elemento, ("codigo_porcentaje", "codigo_porcentaje_iva", "iva"), "4")
                ),
                base_imponible=_obtener(elemento, ("base_imponible", "subtotal", "base"), 0),
                tarifa=_obtener(elemento, ("tarifa", "porcentaje_iva"), 15),
                valor=_obtener(elemento, ("valor_impuesto", "iva", "valor"), 0),
            )
        ]

    def parametros(self, obj: Any) -> Dict[str, Any]:
        sujeto = self.receptor(obj)
        if sujeto is None:
            raise ErrorValidacion("No se encontró el sujeto retenido en el objeto.")
        return {
            "sujeto_retenido": sujeto,
            "docs_sustento": self.docs_sustento(obj),
            "periodo_fiscal": self.periodo_fiscal(obj),
            "parte_rel": _obtener(obj, ("parte_rel", "parteRel"), "SI"),
            "tipo_sujeto_retenido": _obtener(
                obj, ("tipo_sujeto_retenido", "tipoSujetoRetenido")
            ),
        }


class AdaptadorGuiaRemision(AdaptadorComprobante):
    """Guía de remisión (``codDoc`` 06)."""

    tipo = TipoComprobante.GUIA_REMISION.value
    comprobante_clase = GuiaRemision

    def destinatarios(self, obj: Any) -> List[Destinatario]:
        valor = _obtener(obj, ("destinatarios", "destinatario", "receptor", "cliente"))
        resultado: List[Destinatario] = []
        for elemento in _coleccion(valor):
            if isinstance(elemento, Destinatario):
                resultado.append(elemento)
                continue
            resultado.append(self._destinatario(elemento, obj))
        return resultado

    def _destinatario(self, elemento: Any, obj: Any) -> Destinatario:
        detalles = [
            DetalleGuia(
                descripcion=str(_obtener(linea, NOMBRES_DESCRIPCION, "") or "").strip(),
                cantidad=_obtener(linea, NOMBRES_CANTIDAD, 1),
                codigo_principal=_obtener(linea, NOMBRES_CODIGO),
                codigo_adicional=_obtener(linea, NOMBRES_CODIGO_AUXILIAR),
                detalles_adicionales=_datos_adicionales(
                    _obtener(linea, NOMBRES_DETALLES_ADICIONALES, None)
                ),
            )
            for linea in _coleccion(_obtener(elemento, NOMBRES_LINEAS) or _obtener(obj, NOMBRES_LINEAS))
        ]
        return Destinatario(
            razon_social=str(_obtener(elemento, NOMBRES_RAZON_SOCIAL, "") or ""),
            identificacion=str(_obtener(elemento, NOMBRES_IDENTIFICACION, "") or ""),
            direccion=str(_obtener(elemento, NOMBRES_DIRECCION, "") or ""),
            motivo_traslado=_obtener(
                elemento, ("motivo_traslado", "motivo"), "01"
            ),
            tipo_identificacion=_obtener(elemento, NOMBRES_TIPO_ID, TipoIdentificacion.RUC),
            detalles=detalles,
            doc_aduanero_unico=_obtener(
                elemento, ("doc_aduanero_unico", "documento_aduanero")
            ),
            cod_estab_destino=_obtener(
                elemento, ("cod_estab_destino", "establecimiento_destino")
            ),
            ruta=_obtener(elemento, ("ruta",)),
            cod_doc_sustento=_obtener(elemento, ("cod_doc_sustento", "codDocSustento")),
            num_doc_sustento=_obtener(elemento, ("num_doc_sustento", "numDocSustento")),
            num_aut_doc_sustento=_obtener(
                elemento, ("num_aut_doc_sustento", "numAutDocSustento")
            ),
            fecha_emision_doc_sustento=_fecha(
                _obtener(elemento, ("fecha_emision_doc_sustento", "fechaEmisionDocSustento"))
            ),
        )

    def parametros(self, obj: Any) -> Dict[str, Any]:
        destinatarios = self.destinatarios(obj)
        if not destinatarios:
            raise ErrorValidacion("La guía de remisión requiere al menos un destinatario.")
        return {
            "destinatarios": destinatarios,
            "dir_partida": str(_obtener(obj, ("dir_partida", "direccion_partida", "origen"), "") or ""),
            "razon_social_transportista": str(
                _obtener(obj, ("razon_social_transportista", "transportista"), "") or ""
            ),
            "ruc_transportista": str(_obtener(obj, ("ruc_transportista", "ruc_transporte"), "") or ""),
            "tipo_identificacion_transportista": _obtener(
                obj, ("tipo_identificacion_transportista",), TipoIdentificacion.RUC
            ),
            "placa": str(_obtener(obj, ("placa", "vehiculo", "matricula"), "") or ""),
            "rise": _obtener(obj, ("rise", "numero_rise")),
            "fecha_ini_transporte": _fecha(_obtener(obj, ("fecha_ini_transporte", "fecha_inicio")))
            or self.fecha_emision(obj),
            "fecha_fin_transporte": _fecha(_obtener(obj, ("fecha_fin_transporte", "fecha_fin")))
            or _fecha(_obtener(obj, ("fecha_ini_transporte", "fecha_inicio")))
            or self.fecha_emision(obj),
        }


# --------------------------------------------------------------- registro


class Adaptadores:
    """Registro de adaptadores: qué adaptador usar para cada modelo."""

    def __init__(self) -> None:
        # Se indexa por ruta de la clase ("app.Modelo") para no retener clases.
        self._por_clase: Dict[Type[Any], Type[AdaptadorComprobante]] = {}
        self._por_tipo: Dict[str, Type[AdaptadorComprobante]] = {
            TipoComprobante.FACTURA.value: AdaptadorFactura,
            TipoComprobante.LIQUIDACION_COMPRA.value: AdaptadorLiquidacionCompra,
            TipoComprobante.NOTA_CREDITO.value: AdaptadorNotaCredito,
            TipoComprobante.NOTA_DEBITO.value: AdaptadorNotaDebito,
            TipoComprobante.COMPROBANTE_RETENCION.value: AdaptadorRetencion,
            TipoComprobante.GUIA_REMISION.value: AdaptadorGuiaRemision,
        }

    def registrar(self, modelo: Any, adaptador: Type[AdaptadorComprobante]) -> None:
        """Asocia un modelo del proyecto con su adaptador."""
        if isinstance(modelo, str):
            clave: Any = modelo
        elif hasattr(modelo, "_meta"):  # modelo de Django
            clave = f"{modelo._meta.app_label}.{modelo._meta.object_name}"
        else:
            clave = modelo
        self._por_clase[clave] = adaptador

    def obtener(self, modelo: Any, tipo: Optional[str] = None) -> Type[AdaptadorComprobante]:
        """Devuelve el adaptador para un modelo (o para un tipo de comprobante)."""
        if isinstance(modelo, str) and modelo in self._por_clase:
            return self._por_clase[modelo]
        if hasattr(modelo, "_meta"):
            clave = f"{modelo._meta.app_label}.{modelo._meta.object_name}"
            if clave in self._por_clase:
                return self._por_clase[clave]
        if isinstance(modelo, type) and modelo in self._por_clase:
            return self._por_clase[modelo]
        if tipo and tipo in self._por_tipo:
            return self._por_tipo[tipo]
        return AdaptadorFactura

    def sabe_de(self, modelo: Any) -> bool:
        """¿El registro tiene algo para este modelo?"""
        if isinstance(modelo, str):
            return modelo in self._por_clase
        if hasattr(modelo, "_meta"):
            clave = f"{modelo._meta.app_label}.{modelo._meta.object_name}"
            if clave in self._por_clase:
                return True
        return isinstance(modelo, type) and modelo in self._por_clase

    def limpiar(self) -> None:
        """Olvida lo registrado (los proyectos la usan para poner los suyos).

        Deja también fuera los seis del paquete: vuelven solos la próxima vez que
        se pida un adaptador (ver :func:`obtener`).
        """
        self._por_clase.clear()


#: Registro global.
registro = Adaptadores()


def registrar(modelo: Any, adaptador: Type[AdaptadorComprobante]) -> Type[AdaptadorComprobante]:
    """Registra el adaptador de un modelo del proyecto y devuelve la clase."""
    registro.registrar(modelo, adaptador)
    return adaptador


def registrar_para(modelo: Any) -> Callable[[Type[AdaptadorComprobante]], Type[AdaptadorComprobante]]:
    """Decorador que registra la clase decorada como adaptador de ``modelo``::

        @adaptadores.registrar_para(MiVenta)
        class AdaptadorMiVenta(AdaptadorFactura): ...
    """

    def decorador(clase: Type[AdaptadorComprobante]) -> Type[AdaptadorComprobante]:
        registro.registrar(modelo, clase)
        return clase

    return decorador


def _es_del_paquete(modelo: Any) -> bool:
    """¿Es uno de los seis modelos que trae el paquete?"""
    meta = getattr(modelo, "_meta", None)
    return meta is not None and getattr(meta, "app_label", "") == "sri_fe"


def registrar_los_del_paquete() -> None:
    """Vuelve a registrar los adaptadores de los seis modelos del paquete.

    Hace falta porque ``registro.limpiar()`` los borra: quien la usa para poner
    los suyos puede olvidarse de devolverlos, y entonces una nota de crédito se
    emitiría como factura (el adaptador por omisión) sin avisar.
    """
    try:
        from . import documentos

        documentos.registrar_adaptadores()
    except Exception:  # noqa: BLE001 - sin Django configurado no hay nada que registrar
        return


def obtener(modelo: Any, tipo: Optional[str] = None) -> Type[AdaptadorComprobante]:
    """Adaptador que corresponde a un modelo o a un tipo de comprobante.

    Si el registro se ha vaciado (``registro.limpiar()``) se devuelven a su sitio
    los del paquete antes de caer en :class:`AdaptadorFactura`.
    """
    adaptador = registro.obtener(modelo, tipo)
    if adaptador is AdaptadorFactura and _es_del_paquete(modelo) and not registro.sabe_de(modelo):
        registrar_los_del_paquete()
        adaptador = registro.obtener(modelo, tipo)
    return adaptador


#: Alias en español, por comodidad.
adaptador_para = obtener
