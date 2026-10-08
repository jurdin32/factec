"""Infraestructura común a todos los comprobantes electrónicos.

Define los helpers de construcción de XML y la clase :class:`Comprobante`, que
aporta la clave de acceso, la sección ``infoTributaria`` (idéntica en los seis
comprobantes del SRI) y el ensamblado del documento.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: N817  (ET es el alias habitual)
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Union

from ..catalogos import (
    Ambiente,
    ETIQUETA_RAIZ,
    Moneda,
    TipoEmision,
    VERSIONES,
)
from ..clave_acceso import (
    codigo_numerico_aleatorio,
    generar_clave_acceso,
    normalizar_secuencial,
    validar_clave_acceso,
)
from ..excepciones import ErrorValidacion
from ..modelos import Emisor, InfoAdicional, a_decimal, cuantizar

__all__ = [
    "Comprobante",
    "DECLARACION_XML",
    "formatear_decimal",
    "formatear_fecha",
    "formatear_fecha_hora",
    "crear",
    "agregar",
    "agregar_texto",
    "Elemento",
]

DECLARACION_XML = '<?xml version="1.0" encoding="UTF-8"?>'

Elemento = ET.Element


def crear(etiqueta: str, **atributos: Any) -> Elemento:
    """Crea un elemento con atributos opcionales (los ``None`` se omiten)."""
    return ET.Element(etiqueta, {k: str(v) for k, v in atributos.items() if v is not None})


def agregar(padre: Elemento, etiqueta: str, **atributos: Any) -> Elemento:
    """Añade un elemento hijo y lo devuelve."""
    return ET.SubElement(padre, etiqueta, {k: str(v) for k, v in atributos.items() if v is not None})


def agregar_texto(padre: Elemento, etiqueta: str, valor: Any) -> Optional[Elemento]:
    """Añade ``<etiqueta>valor</etiqueta>`` omitiendo los valores vacíos."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "":
        return None
    elemento = ET.SubElement(padre, etiqueta)
    elemento.text = texto
    return elemento


def formatear_decimal(valor: Any, decimales: int = 2) -> str:
    """Formatea un número con la cantidad exacta de decimales que exige el SRI."""
    return f"{cuantizar(a_decimal(valor), decimales):.{decimales}f}"


def formatear_fecha(valor: Union[date, datetime, str]) -> str:
    """Formatea una fecha como ``dd/mm/aaaa`` (formato del SRI)."""
    if isinstance(valor, datetime):
        valor = valor.date()
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor).strip().replace("-", "/")
    partes = texto.split("/")
    if len(partes) == 3 and len(partes[0]) == 4:
        return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return texto


def formatear_fecha_hora(valor: Union[date, datetime, str]) -> str:
    """Formatea fecha y hora como ``dd/mm/aaaa hh:mm:ss``."""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M:%S")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y 00:00:00")
    return str(valor)


def _texto_si_no(valor: Any) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "SI" if valor else "NO"
    texto = str(valor).strip().upper()
    if texto in ("SI", "NO"):
        return texto
    return None


@dataclass
class Comprobante:
    """Base de todos los comprobantes electrónicos.

    Las subclases fijan :attr:`TIPO` y :attr:`VERSION` e implementan
    :meth:`construir_cuerpo` para aportar las secciones propias.
    """

    emisor: Emisor
    ambiente: int = Ambiente.PRUEBAS
    tipo_emision: str = TipoEmision.NORMAL
    fecha_emision: date = field(default_factory=date.today)
    secuencial: str = "1"
    codigo_numerico: Optional[str] = None
    moneda: str = Moneda.DOLAR
    info_adicional: Dict[str, Any] = field(default_factory=dict)
    clave_acceso: Optional[str] = None

    #: Código ``codDoc`` del SRI (tabla 1).
    TIPO: ClassVar[str] = ""
    #: Versión del esquema del SRI.
    VERSION: ClassVar[str] = ""
    #: Etiqueta del elemento raíz.
    ETIQUETA: ClassVar[str] = ""

    def __post_init__(self) -> None:
        if not self.ETIQUETA:
            self.ETIQUETA = ETIQUETA_RAIZ.get(self.TIPO, "")
        if not self.VERSION:
            self.VERSION = VERSIONES.get(self.TIPO, "")

    # ------------------------------------------------------------------ clave

    @property
    def serie(self) -> str:
        """Serie de 6 dígitos (``estab`` + ``ptoEmi``)."""
        return self.emisor.serie

    @property
    def secuencial_normalizado(self) -> str:
        return normalizar_secuencial(self.secuencial)

    def generar_clave_acceso(self, codigo_numerico: Optional[str] = None) -> str:
        """Genera (y memoriza) la clave de acceso del comprobante."""
        self.codigo_numerico = self.codigo_numerico or codigo_numerico or codigo_numerico_aleatorio()
        self.clave_acceso = generar_clave_acceso(
            fecha_emision=self.fecha_emision,
            tipo_comprobante=self.TIPO,
            ruc=self.emisor.ruc,
            ambiente=self.ambiente,
            serie=self.serie,
            secuencial=self.secuencial_normalizado,
            codigo_numerico=self.codigo_numerico,
            tipo_emision=self.tipo_emision,
        )
        return self.clave_acceso

    @property
    def clave(self) -> str:
        """Clave de acceso, generándola si aún no existe."""
        if not self.clave_acceso:
            return self.generar_clave_acceso()
        return self.clave_acceso

    # ------------------------------------------------------------------- XML

    def _info_tributaria(self) -> Elemento:
        info = crear("infoTributaria")
        agregar_texto(info, "ambiente", int(self.ambiente))
        agregar_texto(info, "tipoEmision", self.tipo_emision)
        agregar_texto(info, "razonSocial", self.emisor.razon_social)
        agregar_texto(info, "nombreComercial", self.emisor.nombre_comercial)
        agregar_texto(info, "ruc", self.emisor.ruc)
        agregar_texto(info, "claveAcceso", self.clave)
        agregar_texto(info, "codDoc", self.TIPO)
        agregar_texto(info, "estab", f"{self.emisor.estab:0>3}")
        agregar_texto(info, "ptoEmi", f"{self.emisor.pto_emi:0>3}")
        agregar_texto(info, "secuencial", self.secuencial_normalizado)
        agregar_texto(info, "dirMatriz", self.emisor.dir_matriz)
        agregar_texto(info, "agenteRetencion", self.emisor.agente_retencion)
        agregar_texto(info, "contribuyenteRimpe", self.emisor.rimpe_texto)
        return info

    def _seccion_info_adicional(self) -> Optional[Elemento]:
        datos = InfoAdicional(dict(self.info_adicional)).a_lista()
        if not datos:
            return None
        info = crear("infoAdicional")
        for campo in datos:
            agregar(info, "campoAdicional", nombre=campo["nombre"]).text = campo["valor"]
        return info

    def construir_cuerpo(self) -> List[Elemento]:
        """Devuelve las secciones propias del comprobante, en orden del esquema."""
        raise NotImplementedError

    def construir_xml(self) -> Elemento:
        """Ensambla el documento completo (sin firma)."""
        self.validar()
        raiz = crear(self.ETIQUETA, id="comprobante", version=self.VERSION)
        raiz.append(self._info_tributaria())
        for seccion in self.construir_cuerpo():
            if seccion is not None:
                raiz.append(seccion)
        adicional = self._seccion_info_adicional()
        if adicional is not None:
            raiz.append(adicional)
        return raiz

    def to_xml(self, pretty: bool = False) -> str:
        """Serializa el comprobante a XML (sin firmar).

        ``pretty=True`` añade sangría, útil para revisar el documento. El XML
        firmado se obtiene con :meth:`firmar`, que siempre usa la forma compacta.
        """
        raiz = self.construir_xml()
        if pretty:
            ET.indent(raiz, space="  ")
        cuerpo = ET.tostring(raiz, encoding="unicode")
        return f"{DECLARACION_XML}\n{cuerpo}" if pretty else f"{DECLARACION_XML}{cuerpo}"

    def guardar(self, ruta: Union[str, Path], pretty: bool = True) -> Path:
        """Escribe el XML sin firmar en disco."""
        destino = Path(ruta)
        destino.write_text(self.to_xml(pretty=pretty), encoding="utf-8")
        return destino

    def firmar(
        self,
        certificado: Any = None,
        *,
        ruta_certificado: Optional[Union[str, Path]] = None,
        clave_certificado: Optional[str] = None,
        ruta_salida: Optional[Union[str, Path]] = None,
        pretty: bool = False,
        **opciones: Any,
    ) -> str:
        """Firma el comprobante con XAdES-BES y devuelve el XML firmado.

        ``certificado`` admite una instancia de
        :class:`factec.firma.Certificado`; como alternativa se
        puede indicar ``ruta_certificado`` y ``clave_certificado``.
        """
        from ..firma import Certificado, firmar_xml

        if certificado is None:
            if not ruta_certificado:
                raise ErrorValidacion(
                    "Indique un certificado o la ruta del .p12 (ruta_certificado)."
                )
            certificado = Certificado.desde_archivo(ruta_certificado, clave_certificado or "")
        xml_sin_firma = self.to_xml(pretty=False)
        xml_firmado = firmar_xml(xml_sin_firma, certificado, **opciones)
        if ruta_salida is not None:
            Path(ruta_salida).write_text(xml_firmado, encoding="utf-8")
        return xml_firmado

    # ------------------------------------------------------------ validación

    def validar(self) -> None:
        """Comprueba los datos mínimos. Las subclases amplían esta validación."""
        self.emisor.validar()
        if int(self.ambiente) not in (1, 2):
            raise ErrorValidacion(f"Ambiente inválido: {self.ambiente!r}")
        emision = str(getattr(self.tipo_emision, "value", self.tipo_emision))
        if emision not in ("1", "2"):
            raise ErrorValidacion(f"Tipo de emisión inválido: {self.tipo_emision!r}")
        self.secuencial_normalizado  # lanza si el secuencial no es válido
        if self.clave_acceso and not validar_clave_acceso(self.clave_acceso):
            raise ErrorValidacion(f"La clave de acceso no es válida: {self.clave_acceso!r}")
