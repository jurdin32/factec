"""Generación del XML de **Guía de Remisión** (SRI, esquema 1.1.0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, List, Optional, Sequence

from ..catalogos import (
    ETIQUETA_RAIZ,
    TipoComprobante,
    TipoIdentificacion,
    VERSIONES,
)
from ..excepciones import ErrorValidacion
from ..modelos import Destinatario, DetalleGuia
from .base import (
    Comprobante,
    Elemento,
    agregar,
    agregar_texto,
    crear,
    formatear_decimal,
    formatear_fecha,
)

__all__ = ["GuiaRemision", "generar_guia_remision"]

#: El esquema oficial admite máximo tres datos adicionales por detalle.
MAXIMO_DETALLES_ADICIONALES = 3


def _codigo(valor: Any) -> str:
    """Devuelve el código de un ``Enum`` de catálogo o el texto tal cual."""
    return str(getattr(valor, "value", valor))


def _elemento_detalles(detalles: Sequence[DetalleGuia]) -> Elemento:
    """Construye ``<detalles>`` de un destinatario (sin impuestos ni precios)."""
    contenedor = crear("detalles")
    for detalle in detalles:
        nodo = agregar(contenedor, "detalle")
        agregar_texto(nodo, "codigoInterno", detalle.codigo_principal)
        agregar_texto(nodo, "codigoAdicional", detalle.codigo_adicional)
        agregar_texto(nodo, "descripcion", detalle.descripcion)
        agregar_texto(nodo, "cantidad", formatear_decimal(detalle.cantidad, 6))
        if detalle.detalles_adicionales:
            adicionales = agregar(nodo, "detallesAdicionales")
            for nombre, valor in detalle.detalles_adicionales.items():
                agregar(adicionales, "detAdicional", nombre=nombre, valor=valor)
    return contenedor


@dataclass
class GuiaRemision(Comprobante):
    """Guía de remisión electrónica (``codDoc`` 06, esquema 1.1.0).

    Ejemplo::

        guia = GuiaRemision(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            destinatarios=[
                Destinatario(
                    razon_social="Cliente",
                    identificacion="0703886697001",
                    direccion="Loja",
                    motivo_traslado=MotivoTraslado.VENTA,
                    detalles=[DetalleGuia(descripcion="Mercadería", cantidad=10)],
                )
            ],
            dir_partida="Quito",
            razon_social_transportista="TRANSPORTES ANDES CÍA. LTDA.",
            ruc_transportista="1790012345001",
            fecha_ini_transporte=date(2026, 1, 2),
            fecha_fin_transporte=date(2026, 1, 3),
            placa="PBA1234",
        )
        xml = guia.to_xml()
    """

    TIPO: ClassVar[str] = TipoComprobante.GUIA_REMISION.value
    VERSION: ClassVar[str] = VERSIONES[TipoComprobante.GUIA_REMISION.value]
    ETIQUETA: ClassVar[str] = ETIQUETA_RAIZ[TipoComprobante.GUIA_REMISION.value]

    destinatarios: List[Destinatario] = field(default_factory=list)
    dir_partida: str = ""
    razon_social_transportista: str = ""
    ruc_transportista: str = ""
    fecha_ini_transporte: Optional[date] = None
    fecha_fin_transporte: Optional[date] = None
    placa: str = ""
    tipo_identificacion_transportista: str = TipoIdentificacion.RUC
    rise: Optional[str] = None
    direccion_establecimiento: Optional[str] = None
    obligado_contabilidad: Optional[bool] = None
    contribuyente_especial: Optional[str] = None

    def _info_guia_remision(self) -> Elemento:
        info = crear("infoGuiaRemision")
        agregar_texto(
            info,
            "dirEstablecimiento",
            self.direccion_establecimiento or self.emisor.dir_establecimiento,
        )
        agregar_texto(info, "dirPartida", self.dir_partida)
        agregar_texto(info, "razonSocialTransportista", self.razon_social_transportista)
        agregar_texto(
            info,
            "tipoIdentificacionTransportista",
            _codigo(self.tipo_identificacion_transportista),
        )
        agregar_texto(info, "rucTransportista", self.ruc_transportista)
        agregar_texto(info, "rise", self.rise)
        obligado = (
            self.emisor.obligado_contabilidad
            if self.obligado_contabilidad is None
            else self.obligado_contabilidad
        )
        agregar_texto(info, "obligadoContabilidad", "SI" if obligado else "NO")
        agregar_texto(
            info,
            "contribuyenteEspecial",
            self.contribuyente_especial or self.emisor.contribuyente_especial,
        )
        agregar_texto(info, "fechaIniTransporte", formatear_fecha(self.fecha_ini_transporte))
        agregar_texto(info, "fechaFinTransporte", formatear_fecha(self.fecha_fin_transporte))
        agregar_texto(info, "placa", self.placa)
        return info

    def _elemento_destinatarios(self) -> Elemento:
        contenedor = crear("destinatarios")
        for destinatario in self.destinatarios:
            nodo = agregar(contenedor, "destinatario")
            agregar_texto(nodo, "identificacionDestinatario", destinatario.identificacion)
            agregar_texto(nodo, "razonSocialDestinatario", destinatario.razon_social)
            agregar_texto(nodo, "dirDestinatario", destinatario.direccion)
            agregar_texto(nodo, "motivoTraslado", destinatario.motivo_traslado)
            agregar_texto(nodo, "docAduaneroUnico", destinatario.doc_aduanero_unico)
            agregar_texto(nodo, "codEstabDestino", destinatario.cod_estab_destino)
            agregar_texto(nodo, "ruta", destinatario.ruta)
            agregar_texto(nodo, "codDocSustento", destinatario.cod_doc_sustento)
            agregar_texto(nodo, "numDocSustento", destinatario.num_doc_sustento)
            agregar_texto(nodo, "numAutDocSustento", destinatario.num_aut_doc_sustento)
            if destinatario.fecha_emision_doc_sustento is not None:
                agregar_texto(
                    nodo,
                    "fechaEmisionDocSustento",
                    formatear_fecha(destinatario.fecha_emision_doc_sustento),
                )
            nodo.append(_elemento_detalles(destinatario.detalles))
        return contenedor

    def construir_cuerpo(self) -> List[Elemento]:
        return [self._info_guia_remision(), self._elemento_destinatarios()]

    def validar(self) -> None:
        super().validar()
        if not self.destinatarios:
            raise ErrorValidacion("La guía de remisión debe tener al menos un destinatario.")
        for indice, destinatario in enumerate(self.destinatarios, start=1):
            if not destinatario.detalles:
                raise ErrorValidacion(
                    f"El destinatario {indice} debe tener al menos un detalle."
                )
            for numero, detalle in enumerate(destinatario.detalles, start=1):
                if len(detalle.detalles_adicionales) > MAXIMO_DETALLES_ADICIONALES:
                    raise ErrorValidacion(
                        f"El detalle {numero} del destinatario {indice} admite máximo "
                        f"{MAXIMO_DETALLES_ADICIONALES} datos adicionales."
                    )
        if not str(self.placa).strip():
            raise ErrorValidacion("La placa del vehículo es obligatoria.")
        if len(str(self.ruc_transportista)) != 13:
            raise ErrorValidacion(
                f"El RUC del transportista debe tener 13 dígitos: {self.ruc_transportista!r}"
            )
        if self.fecha_ini_transporte is None or self.fecha_fin_transporte is None:
            raise ErrorValidacion(
                "La guía de remisión requiere la fecha de inicio y la de fin de transporte."
            )
        if self.fecha_fin_transporte < self.fecha_ini_transporte:
            raise ErrorValidacion(
                "La fecha de fin de transporte no puede ser anterior a la de inicio."
            )


def generar_guia_remision(**campos: Any) -> GuiaRemision:
    """Crea una guía de remisión con su clave de acceso ya generada.

    La clave (``claveAcceso``) la aporta :meth:`Comprobante.generar_clave_acceso`;
    aquí solo se invoca para dejarla lista antes de serializar.
    """
    guia = GuiaRemision(**campos)
    guia.generar_clave_acceso()
    return guia
