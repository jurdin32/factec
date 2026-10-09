"""Lee comprobantes electrónicos del SRI: los propios y los de terceros.

Convierte cualquier XML de comprobante (factura, liquidación de compra, nota de
crédito, nota de débito, guía de remisión o comprobante de retención) en objetos
de Python con sus datos ya separados, para usarlos desde una vista, una tarea o
un script::

    from factec.lectura import leer_comprobante

    comprobante = leer_comprobante(open("factura.xml").read())
    comprobante.tipo               # "01"
    comprobante.numero             # "001-001-000000013"
    comprobante.emisor.ruc
    comprobante.receptor.identificacion
    comprobante.totales.importe_total
    [detalle.descripcion for detalle in comprobante.detalles]
    comprobante.a_dict()           # todo listo para JSON

Admite también el XML completo que devuelve el SRI al autorizar, del que saca el
comprobante y su autorización con :func:`leer_autorizacion`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Union

from .catalogos import DESCRIPCION_TIPO_COMPROBANTE, TipoComprobante
from .clave_acceso import descomponer_clave_acceso, validar_clave_acceso
from .excepciones import ErrorValidacion

__all__ = [
    "AutorizacionLeida",
    "ComprobanteLeido",
    "DetalleLeido",
    "EmisorLeido",
    "ImpuestoLeido",
    "ReceptorLeido",
    "TotalesLeidos",
    "es_comprobante",
    "leer_autorizacion",
    "leer_comprobante",
    "tipo_del_xml",
]

#: Códigos ``codDoc`` conocidos.
CODIGOS = {miembro.value for miembro in TipoComprobante}

#: Nombres de la sección de datos del emisor y del receptor por tipo de documento.
SECCIONES_INFO = {
    TipoComprobante.FACTURA.value: "infoFactura",
    TipoComprobante.LIQUIDACION_COMPRA.value: "infoLiquidacionCompra",
    TipoComprobante.NOTA_CREDITO.value: "infoNotaCredito",
    TipoComprobante.NOTA_DEBITO.value: "infoNotaDebito",
    TipoComprobante.GUIA_REMISION.value: "infoGuiaRemision",
    TipoComprobante.COMPROBANTE_RETENCION.value: "infoCompRetencion",
}

#: Cómo se llama el receptor en cada tipo (``razonSocialComprador``, ``…Proveedor``).
NOMBRES_RECEPTOR = {
    TipoComprobante.FACTURA.value: ("Comprador", "Receptor"),
    TipoComprobante.LIQUIDACION_COMPRA.value: ("Proveedor",),
    TipoComprobante.NOTA_CREDITO.value: ("Comprador",),
    TipoComprobante.NOTA_DEBITO.value: ("Comprador",),
    TipoComprobante.GUIA_REMISION.value: ("Destinatario",),
    TipoComprobante.COMPROBANTE_RETENCION.value: ("SujetoRetenido",),
}

#: Nombres que puede tener el importe total.
NOMBRES_TOTAL = ("importeTotal", "valorModificacion", "total")


# --------------------------------------------------------------------- ayudas


def _texto(nodo: Any, etiqueta: str) -> str:
    if nodo is None:
        return ""
    hijo = nodo.find(etiqueta)
    return (hijo.text or "").strip() if hijo is not None and hijo.text else ""


def _primero(nodo: Any, *etiquetas: str) -> str:
    for etiqueta in etiquetas:
        valor = _texto(nodo, etiqueta)
        if valor:
            return valor
    return ""


def _decimal(valor: Any) -> Decimal:
    texto = str(valor or "").strip()
    if not texto:
        return Decimal("0")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return Decimal("0")


def _fecha(valor: str) -> Optional[date]:
    texto = (valor or "").strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _si_no(valor: str) -> Optional[bool]:
    texto = (valor or "").strip().upper()
    if texto in ("SI", "SÍ", "TRUE", "1"):
        return True
    if texto in ("NO", "FALSE", "0"):
        return False
    return None


def _elementos(nodo: Any, ruta: str) -> List[Any]:
    contenedor = nodo.find(ruta)
    return list(contenedor) if contenedor is not None else []


def _espacios_de_nombres(xml: str) -> str:
    """Quita los prefijos de espacio de nombres (``ds:``, ``soap:``, ``ns2:``)."""
    import re

    return re.sub(r"<(/?)([A-Za-z0-9._-]+):([A-Za-z0-9._-]+)", r"<\1\3", xml)


# -------------------------------------------------------------------- objetos


@dataclass
class EmisorLeido:
    """Datos del contribuyente que emitió el comprobante."""

    ruc: str = ""
    razon_social: str = ""
    nombre_comercial: str = ""
    dir_matriz: str = ""
    dir_establecimiento: str = ""
    estab: str = ""
    pto_emi: str = ""
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: str = ""
    agente_retencion: str = ""
    rimpe: str = ""

    def a_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ReceptorLeido:
    """Comprador, proveedor, destinatario o sujeto retenido, según el documento."""

    identificacion: str = ""
    razon_social: str = ""
    tipo_identificacion: str = ""
    direccion: str = ""
    email: str = ""
    telefono: str = ""

    @property
    def es_consumidor_final(self) -> bool:
        return self.tipo_identificacion == "07" or self.identificacion == "9999999999999"

    def a_dict(self) -> Dict[str, Any]:
        datos = dict(self.__dict__)
        datos["es_consumidor_final"] = self.es_consumidor_final
        return datos


@dataclass
class ImpuestoLeido:
    """Impuesto de una línea o del resumen del comprobante."""

    codigo: str = ""
    codigo_porcentaje: str = ""
    tarifa: Decimal = Decimal("0")
    base_imponible: Decimal = Decimal("0")
    valor: Decimal = Decimal("0")

    def a_dict(self) -> Dict[str, Any]:
        return {
            "codigo": self.codigo,
            "codigo_porcentaje": self.codigo_porcentaje,
            "tarifa": str(self.tarifa),
            "base_imponible": str(self.base_imponible),
            "valor": str(self.valor),
        }


@dataclass
class DetalleLeido:
    """Línea del comprobante (``detalle``, ``motivo`` o retención)."""

    descripcion: str = ""
    cantidad: Decimal = Decimal("0")
    precio_unitario: Decimal = Decimal("0")
    descuento: Decimal = Decimal("0")
    precio_total_sin_impuesto: Decimal = Decimal("0")
    codigo_principal: str = ""
    codigo_auxiliar: str = ""
    unidad_medida: str = ""
    datos_adicionales: Dict[str, str] = field(default_factory=dict)
    impuestos: List[ImpuestoLeido] = field(default_factory=list)

    def a_dict(self) -> Dict[str, Any]:
        return {
            "descripcion": self.descripcion,
            "cantidad": str(self.cantidad),
            "precio_unitario": str(self.precio_unitario),
            "descuento": str(self.descuento),
            "precio_total_sin_impuesto": str(self.precio_total_sin_impuesto),
            "codigo_principal": self.codigo_principal,
            "codigo_auxiliar": self.codigo_auxiliar,
            "unidad_medida": self.unidad_medida,
            "datos_adicionales": dict(self.datos_adicionales),
            "impuestos": [impuesto.a_dict() for impuesto in self.impuestos],
        }


@dataclass
class TotalesLeidos:
    """Importes del comprobante."""

    subtotal: Decimal = Decimal("0")
    descuento: Decimal = Decimal("0")
    impuestos: List[ImpuestoLeido] = field(default_factory=list)
    propina: Decimal = Decimal("0")
    importe_total: Decimal = Decimal("0")

    @property
    def valor_impuestos(self) -> Decimal:
        return sum((impuesto.valor for impuesto in self.impuestos), Decimal("0"))

    def a_dict(self) -> Dict[str, Any]:
        return {
            "subtotal": str(self.subtotal),
            "descuento": str(self.descuento),
            "valor_impuestos": str(self.valor_impuestos),
            "propina": str(self.propina),
            "importe_total": str(self.importe_total),
            "impuestos": [impuesto.a_dict() for impuesto in self.impuestos],
        }


@dataclass
class ComprobanteLeido:
    """Comprobante electrónico ya interpretado."""

    tipo: str = ""
    version: str = ""
    etiqueta: str = ""
    clave_acceso: str = ""
    ambiente: str = ""
    tipo_emision: str = ""
    fecha_emision: Optional[date] = None
    secuencial: str = ""
    numero: str = ""
    moneda: str = ""
    emisor: EmisorLeido = field(default_factory=EmisorLeido)
    receptor: ReceptorLeido = field(default_factory=ReceptorLeido)
    totales: TotalesLeidos = field(default_factory=TotalesLeidos)
    detalles: List[DetalleLeido] = field(default_factory=list)
    info_adicional: Dict[str, str] = field(default_factory=dict)
    pagos: List[Dict[str, str]] = field(default_factory=list)
    #: Campos propios de algunos documentos: motivo, periodo fiscal, placa…
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def descripcion_tipo(self) -> str:
        """«Factura», «Nota de crédito»… según el ``codDoc``."""
        return DESCRIPCION_TIPO_COMPROBANTE.get(self.tipo, "")

    @property
    def autorizado_electronico(self) -> bool:
        """Todo lo emitido por este paquete lo es; el campo existe por claridad."""
        return bool(self.clave_acceso)

    def a_dict(self) -> Dict[str, Any]:
        """Diccionario listo para JSON (fechas y decimales como texto)."""
        return {
            "tipo": self.tipo,
            "descripcion_tipo": self.descripcion_tipo,
            "version": self.version,
            "clave_acceso": self.clave_acceso,
            "ambiente": self.ambiente,
            "tipo_emision": self.tipo_emision,
            "fecha_emision": self.fecha_emision.isoformat() if self.fecha_emision else None,
            "numero": self.numero,
            "secuencial": self.secuencial,
            "moneda": self.moneda,
            "emisor": self.emisor.a_dict(),
            "receptor": self.receptor.a_dict(),
            "totales": self.totales.a_dict(),
            "detalles": [detalle.a_dict() for detalle in self.detalles],
            "info_adicional": dict(self.info_adicional),
            "pagos": [dict(pago) for pago in self.pagos],
            "extras": dict(self.extras),
        }


@dataclass
class AutorizacionLeida:
    """Respuesta del SRI al autorizar un comprobante."""

    estado: str = ""
    numero_autorizacion: str = ""
    fecha_autorizacion: Optional[datetime] = None
    ambiente: str = ""
    clave_acceso: str = ""
    comprobante: Optional[ComprobanteLeido] = None
    mensajes: List[Dict[str, str]] = field(default_factory=list)
    crudo: str = ""

    @property
    def autorizada(self) -> bool:
        return self.estado.upper() == "AUTORIZADO"

    def a_dict(self) -> Dict[str, Any]:
        return {
            "estado": self.estado,
            "autorizada": self.autorizada,
            "numero_autorizacion": self.numero_autorizacion,
            "fecha_autorizacion": (
                self.fecha_autorizacion.isoformat() if self.fecha_autorizacion else None
            ),
            "ambiente": self.ambiente,
            "clave_acceso": self.clave_acceso,
            "mensajes": [dict(mensaje) for mensaje in self.mensajes],
            "comprobante": self.comprobante.a_dict() if self.comprobante else None,
        }


# ------------------------------------------------------------------- lectura


def _raiz(xml: Union[str, bytes]) -> Any:
    from xml.etree import ElementTree as ET

    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    if not (xml or "").strip():
        raise ErrorValidacion("No hay XML que leer.")

    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        # Puede ser un sobre SOAP con prefijos: se reintenta sin ellos.
        try:
            return ET.fromstring(_espacios_de_nombres(xml))
        except ET.ParseError as exc:
            raise ErrorValidacion(f"El XML no se pudo interpretar: {exc}") from exc


def es_comprobante(xml: Union[str, bytes]) -> bool:
    """¿El XML es un comprobante del SRI (y no, por ejemplo, una respuesta)?"""
    try:
        raiz = _raiz(xml)
    except ErrorValidacion:
        return False
    return raiz.tag in CODIGOS or raiz.find("infoTributaria") is not None


def tipo_del_xml(xml: Union[str, bytes]) -> str:
    """Código ``codDoc`` del comprobante que hay en el XML (``""`` si no se sabe)."""
    raiz = _raiz(xml)
    if raiz.tag in CODIGOS:
        return raiz.tag
    codigo = _texto(raiz.find("infoTributaria"), "codDoc")
    if codigo:
        return codigo
    for valor, seccion in SECCIONES_INFO.items():
        if raiz.find(seccion) is not None:
            return valor
    return ""


def _leer_receptor(info: Any, tipo: str) -> ReceptorLeido:
    for sufijo in NOMBRES_RECEPTOR.get(tipo, ("Comprador",)):
        if info is None or info.find(f"razonSocial{sufijo}") is None:
            continue
        return ReceptorLeido(
            razon_social=_primero(info, f"razonSocial{sufijo}"),
            identificacion=_primero(info, f"identificacion{sufijo}"),
            tipo_identificacion=_primero(info, f"tipoIdentificacion{sufijo}"),
            direccion=_primero(info, f"direccion{sufijo}"),
            email=_primero(info, f"correo{sufijo}", "email"),
            telefono=_primero(info, f"telefono{sufijo}"),
        )
    return ReceptorLeido()


def _leer_impuestos(nodo: Any) -> List[ImpuestoLeido]:
    return [
        ImpuestoLeido(
            codigo=_texto(impuesto, "codigo"),
            codigo_porcentaje=_texto(impuesto, "codigoPorcentaje"),
            tarifa=_decimal(_texto(impuesto, "tarifa")),
            base_imponible=_decimal(_texto(impuesto, "baseImponible")),
            valor=_decimal(_texto(impuesto, "valor")),
        )
        for impuesto in _elementos(nodo, "impuestos")
    ]


def _leer_detalles(raiz: Any, tipo: str) -> List[DetalleLeido]:
    contenedor = raiz.find("detalles")
    if contenedor is None:
        return []

    detalles = []
    for nodo in contenedor:
        # ``<detAdicional nombre="MARCA" valor="ACME"/>``: el valor es un atributo.
        adicionales = {
            adicional.get("nombre", ""): (
                adicional.get("valor") or adicional.text or ""
            ).strip()
            for adicional in _elementos(nodo, "detallesAdicionales")
        }
        detalles.append(
            DetalleLeido(
                descripcion=_texto(nodo, "descripcion"),
                cantidad=_decimal(_texto(nodo, "cantidad")),
                precio_unitario=_decimal(_texto(nodo, "precioUnitario")),
                descuento=_decimal(_texto(nodo, "descuento")),
                precio_total_sin_impuesto=_decimal(_texto(nodo, "precioTotalSinImpuesto")),
                codigo_principal=_primero(
                    nodo, "codigoPrincipal", "codigoInterno", "codigo"
                ),
                codigo_auxiliar=_primero(
                    nodo, "codigoAuxiliar", "codigoAdicional"
                ),
                unidad_medida=_texto(nodo, "unidadMedida"),
                datos_adicionales=adicionales,
                impuestos=_leer_impuestos(nodo),
            )
        )
    return detalles


def _leer_totales(info: Any, tipo: str) -> TotalesLeidos:
    total = Decimal("0")
    for nombre in NOMBRES_TOTAL:
        valor = _texto(info, nombre)
        if valor:
            total = _decimal(valor)
            break

    return TotalesLeidos(
        subtotal=_decimal(_texto(info, "totalSinImpuestos")),
        descuento=_decimal(_texto(info, "totalDescuento")),
        impuestos=[
            ImpuestoLeido(
                codigo=_texto(nodo, "codigo"),
                codigo_porcentaje=_texto(nodo, "codigoPorcentaje"),
                tarifa=_decimal(_texto(nodo, "tarifa")),
                base_imponible=_decimal(_texto(nodo, "baseImponible")),
                valor=_decimal(_texto(nodo, "valor")),
            )
            for nodo in _elementos(info, "totalConImpuestos")
        ],
        propina=_decimal(_texto(info, "propina")),
        importe_total=total,
    )


def _leer_info_adicional(raiz: Any) -> Dict[str, str]:
    return {
        campo.get("nombre", ""): (campo.text or "").strip()
        for campo in _elementos(raiz, "infoAdicional")
    }


def _leer_pagos(info: Any) -> List[Dict[str, str]]:
    return [
        {
            "forma_pago": _texto(pago, "formaPago"),
            "total": _texto(pago, "total"),
            "plazo": _texto(pago, "plazo"),
            "unidad_tiempo": _texto(pago, "unidadTiempo"),
        }
        for pago in _elementos(info, "pagos")
    ]


def _leer_extras(raiz: Any, info: Any, tipo: str) -> Dict[str, Any]:
    extras: Dict[str, Any] = {}

    if tipo == TipoComprobante.NOTA_CREDITO.value:
        extras["motivo"] = _texto(info, "motivo")
        extras["num_doc_modificado"] = _texto(info, "numDocModificado")
    if tipo in (TipoComprobante.NOTA_CREDITO.value, TipoComprobante.NOTA_DEBITO.value):
        extras["fecha_emision_doc_sustento"] = _texto(info, "fechaEmisionDocSustento")
        extras["cod_doc_modificado"] = _texto(info, "codDocModificado")
    if tipo == TipoComprobante.COMPROBANTE_RETENCION.value:
        extras["periodo_fiscal"] = _texto(info, "periodoFiscal")
        extras["parte_rel"] = _texto(info, "parteRel")
        extras["sujeto_retenido"] = {
            "tipo": _texto(info, "tipoSujetoRetenido"),
            "razon_social": _texto(info, "razonSocialSujetoRetenido"),
            "identificacion": _texto(info, "identificacionSujetoRetenido"),
        }
        extras["docs_sustento"] = [
            {
                "cod_doc_sustento": _texto(doc, "codDocSustento"),
                "num_doc_sustento": _texto(doc, "numDocSustento"),
                "fecha_emision": _texto(doc, "fechaEmisionDocSustento"),
                "importe_total": _texto(doc, "importeTotal"),
                "retenciones": [
                    {
                        "codigo": _texto(retencion, "codigo"),
                        "codigo_retencion": _texto(retencion, "codigoRetencion"),
                        "base_imponible": _texto(retencion, "baseImponible"),
                        "porcentaje": _texto(retencion, "porcentajeRetener"),
                        "valor": _texto(retencion, "valorRetenido"),
                    }
                    for retencion in _elementos(doc, "retenciones")
                ],
            }
            for doc in _elementos(raiz, "docsSustento")
        ]
    if tipo == TipoComprobante.GUIA_REMISION.value:
        extras["dir_partida"] = _texto(info, "dirPartida")
        extras["transportista"] = {
            "razon_social": _texto(info, "razonSocialTransportista"),
            "ruc": _texto(info, "rucTransportista"),
            "tipo_identificacion": _texto(info, "tipoIdentificacionTransportista"),
            "placa": _texto(info, "placa"),
            "rise": _texto(info, "rise"),
            "fecha_inicio": _texto(info, "fechaIniTransporte"),
            "fecha_fin": _texto(info, "fechaFinTransporte"),
        }
        extras["destinatarios"] = [
            {
                "razon_social": _texto(destino, "razonSocialDestinatario"),
                "identificacion": _texto(destino, "identificacionDestinatario"),
                "direccion": _texto(destino, "dirDestinatario"),
                "motivo_traslado": _texto(destino, "motivoTraslado"),
                "bienes": [_texto(bien, "descripcion") for bien in _elementos(destino, "detalles")],
            }
            for destino in _elementos(raiz, "destinatarios")
        ]
    if tipo == TipoComprobante.NOTA_DEBITO.value:
        extras["motivos"] = [
            {
                "razon": _texto(motivo, "razon"),
                "valor": _texto(motivo, "valor"),
            }
            for motivo in _elementos(raiz, "motivos")
        ]

    return extras


def leer_comprobante(xml: Union[str, bytes]) -> ComprobanteLeido:
    """Interpreta un comprobante electrónico del SRI.

    Sirve igual para los comprobantes propios y para los de terceros (por
    ejemplo, una factura de un proveedor que le entregan en XML).
    """
    raiz = _raiz(xml)
    if raiz.tag in ("RespuestaAutorizacionComprobante", "autorizacion"):
        autorizacion = leer_autorizacion(xml)
        if autorizacion.comprobante is not None:
            return autorizacion.comprobante

    tributaria = raiz.find("infoTributaria")
    if tributaria is None:
        raise ErrorValidacion(
            "El XML no parece un comprobante electrónico del SRI: falta «infoTributaria»."
        )

    tipo = _texto(tributaria, "codDoc") or raiz.tag
    info = raiz.find(SECCIONES_INFO.get(tipo, ""))
    estab = _texto(tributaria, "estab")
    pto_emi = _texto(tributaria, "ptoEmi")
    secuencial = _texto(tributaria, "secuencial")
    clave = _texto(tributaria, "claveAcceso")

    fecha_de_la_clave = None
    if validar_clave_acceso(clave):
        # La clave lleva la fecha en ddmmaaaa; sirve de respaldo.
        fecha_de_la_clave = _fecha(f"{clave[0:2]}/{clave[2:4]}/{clave[4:8]}")

    return ComprobanteLeido(
        tipo=tipo,
        version=raiz.get("version", ""),
        etiqueta=raiz.tag,
        clave_acceso=clave,
        ambiente=_texto(tributaria, "ambiente"),
        tipo_emision=_texto(tributaria, "tipoEmision"),
        fecha_emision=_fecha(_texto(info, "fechaEmision")) or fecha_de_la_clave,
        secuencial=secuencial,
        numero="-".join(parte for parte in (estab, pto_emi, secuencial) if parte),
        moneda=_texto(info, "moneda"),
        emisor=EmisorLeido(
            ruc=_texto(tributaria, "ruc"),
            razon_social=_texto(tributaria, "razonSocial"),
            nombre_comercial=_texto(tributaria, "nombreComercial"),
            dir_matriz=_texto(tributaria, "dirMatriz"),
            dir_establecimiento=_primero(info, "dirEstablecimiento", "dirPartida"),
            estab=estab,
            pto_emi=pto_emi,
            obligado_contabilidad=_si_no(_texto(info, "obligadoContabilidad")),
            contribuyente_especial=_texto(tributaria, "contribuyenteEspecial"),
            agente_retencion=_texto(tributaria, "agenteRetencion"),
            rimpe=_texto(tributaria, "contribuyenteRimpe"),
        ),
        receptor=_leer_receptor(info, tipo),
        totales=_leer_totales(info, tipo),
        detalles=_leer_detalles(raiz, tipo),
        info_adicional=_leer_info_adicional(raiz),
        pagos=_leer_pagos(info),
        extras=_leer_extras(raiz, info, tipo),
    )


def leer_autorizacion(xml: Union[str, bytes]) -> AutorizacionLeida:
    """Interpreta la respuesta del SRI (``RespuestaAutorizacionComprobante``).

    Devuelve el estado, el número y la fecha de autorización, los mensajes y, si
    viene, el comprobante autorizado ya interpretado.
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", "replace")
    raiz = _raiz(xml)

    nodo = raiz.find(".//autorizacion")
    if nodo is None:
        raise ErrorValidacion(
            "El XML no es una respuesta de autorización del SRI "
            "(no se encontró «autorizacion»)."
        )

    contenido = _texto(nodo, "comprobante")
    mensajes = [
        {
            "identificador": _texto(mensaje, "identificador"),
            "mensaje": _texto(mensaje, "mensaje"),
            "informacion_adicional": _texto(mensaje, "informacionAdicional"),
            "tipo": _texto(mensaje, "tipo"),
        }
        for mensaje in _elementos(nodo, "mensajes")
    ]

    comprobante = None
    if contenido.lstrip().startswith("<"):
        try:
            comprobante = leer_comprobante(contenido)
        except ErrorValidacion:
            comprobante = None

    fecha = _texto(nodo, "fechaAutorizacion")
    return AutorizacionLeida(
        estado=_texto(nodo, "estado"),
        numero_autorizacion=_texto(nodo, "numeroAutorizacion"),
        fecha_autorizacion=datetime.fromisoformat(fecha) if "T" in fecha else None,
        ambiente=_texto(nodo, "ambiente"),
        clave_acceso=comprobante.clave_acceso if comprobante else "",
        comprobante=comprobante,
        mensajes=mensajes,
        crudo=xml,
    )
