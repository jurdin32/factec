"""Fachada de alto nivel para emitir comprobantes electrónicos.

:class:`EmisorElectronico` reúne el certificado, el ambiente y los datos del
emisor, y ofrece un flujo de una sola llamada: **construir → firmar → enviar →
autorizar**.

Ejemplo::

    from factec import EmisorElectronico, Emisor, Receptor, Detalle, Impuesto

    emisor = EmisorElectronico(
        emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
        certificado="firmante.p12",
        clave_certificado="secreto",
        ambiente=1,  # pruebas
    )

    factura = emisor.factura(
        receptor=Receptor(identificacion="1790012345001", razon_social="Cliente"),
        detalles=[Detalle(descripcion="Servicio", cantidad=1, precio_unitario=100)],
    )
    resultado = emisor.emitir(factura)
    print(resultado.clave_acceso, resultado.autorizada)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .catalogos import Ambiente, TipoComprobante
from .comprobantes.base import Comprobante
from .comprobantes.factura import Factura
from .comprobantes.guia_remision import GuiaRemision
from .comprobantes.liquidacion_compra import LiquidacionCompra
from .comprobantes.nota_credito import NotaCredito
from .comprobantes.nota_debito import NotaDebito
from .comprobantes.retencion import ComprobanteRetencion
from .excepciones import ErrorValidacion
from .firma import Certificado, firmar_xml
from .modelos import (
    Compensacion,
    Destinatario,
    Detalle,
    DocSustento,
    Emisor,
    Impuesto,
    Motivo,
    Pago,
    Reembolso,
    Receptor,
)
from .sri.soap import ClienteSRI, RespuestaAutorizacion, RespuestaRecepcion

__all__ = ["EmisorElectronico", "ResultadoEmision"]


@dataclass
class ResultadoEmision:
    """Todo lo que produce una emisión: el XML, la clave y la respuesta del SRI."""

    comprobante: Optional[Comprobante] = None
    clave_acceso: str = ""
    xml_sin_firma: str = ""
    xml_firmado: str = ""
    recepcion: Optional[RespuestaRecepcion] = None
    autorizacion: Optional[RespuestaAutorizacion] = None

    @property
    def recepcionada(self) -> bool:
        return bool(self.recepcion and self.recepcion.recibida)

    @property
    def autorizada(self) -> bool:
        return bool(self.autorizacion and self.autorizacion.autorizada)

    @property
    def numero_autorizacion(self) -> str:
        ultima = self.autorizacion.ultima if self.autorizacion else None
        return ultima.numero_autorizacion if ultima else ""

    @property
    def xml_autorizado(self) -> str:
        """XML del comprobante tal como lo devolvió el SRI (con la autorización)."""
        ultima = self.autorizacion.ultima if self.autorizacion else None
        return ultima.comprobante if ultima else ""

    @property
    def mensajes(self) -> List[Any]:
        if self.autorizacion and self.autorizacion.ultima:
            return self.autorizacion.ultima.mensajes
        if self.recepcion:
            return self.recepcion.mensajes
        return []

    def lanzar_si_fallo(self) -> "ResultadoEmision":
        """Lanza el error correspondiente si la emisión no terminó autorizada."""
        if self.recepcion is not None:
            self.recepcion.lanzar_si_devuelta()
        if self.autorizacion is not None:
            self.autorizacion.lanzar_si_no_autorizada()
        return self


class EmisorElectronico:
    """Punto de entrada para emitir comprobantes electrónicos del SRI."""

    def __init__(
        self,
        emisor: Emisor,
        *,
        certificado: Optional[Union[Certificado, str, Path]] = None,
        clave_certificado: Optional[str] = None,
        ruta_ca: Optional[Union[str, Path]] = None,
        ambiente: Union[int, Ambiente] = Ambiente.PRUEBAS,
        host: Optional[str] = None,
        timeout: float = 30.0,
        secuencial_inicial: int = 1,
        validar_vigencia: bool = True,
    ) -> None:
        self.emisor = emisor
        self.ambiente = int(getattr(ambiente, "value", ambiente))
        self.host = host
        self.timeout = timeout
        self.secuencial_inicial = max(1, int(secuencial_inicial))
        self.validar_vigencia = validar_vigencia

        self._certificado: Optional[Certificado] = None
        if isinstance(certificado, Certificado):
            self._certificado = certificado
        elif certificado is not None:
            self._certificado = Certificado.desde_archivo(
                certificado, clave_certificado or "", ruta_ca=ruta_ca
            )
        if self._certificado is not None and validar_vigencia:
            self._certificado.validar_vigencia()

        self._secuenciales: Dict[str, int] = {}
        self._cliente: Optional[ClienteSRI] = None

    # ------------------------------------------------------------ propiedades

    @property
    def certificado(self) -> Certificado:
        if self._certificado is None:
            raise ErrorValidacion(
                "Se requiere un certificado para firmar. Pase 'certificado' y "
                "'clave_certificado' al crear el EmisorElectronico."
            )
        return self._certificado

    @property
    def cliente(self) -> ClienteSRI:
        """Cliente SOAP del SRI, creado de forma perezosa."""
        if self._cliente is None:
            self._cliente = ClienteSRI(
                ambiente=self.ambiente, host=self.host, timeout=self.timeout
            )
        return self._cliente

    # ------------------------------------------------------------- auxiliares

    def siguiente_secuencial(self, tipo: str = TipoComprobante.FACTURA.value) -> str:
        """Siguiente secuencial en memoria para el tipo de comprobante.

        El contador vive en el objeto: si la aplicación se reinicia, hay que
        reiniciarlo con ``secuencial_inicial`` o pasar ``secuencial`` explícito.
        """
        actual = self._secuenciales.get(tipo, self.secuencial_inicial - 1) + 1
        self._secuenciales[tipo] = actual
        return str(actual)

    def _comunes(self, fecha_emision: Optional[date], secuencial: Optional[str], tipo: str) -> Dict[str, Any]:
        return {
            "emisor": self.emisor,
            "ambiente": self.ambiente,
            "fecha_emision": fecha_emision or date.today(),
            "secuencial": secuencial or self.siguiente_secuencial(tipo),
        }

    # -------------------------------------------------- constructores de docs

    def factura(
        self,
        *,
        receptor: Receptor,
        detalles: List[Detalle],
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        pagos: Optional[List[Pago]] = None,
        **extra: Any,
    ) -> Factura:
        """Crea una factura electrónica."""
        return Factura(
            receptor=receptor,
            detalles=detalles,
            pagos=pagos or [],
            **self._comunes(fecha_emision, secuencial, TipoComprobante.FACTURA.value),
            **extra,
        )

    def liquidacion_compra(
        self,
        *,
        proveedor: Receptor,
        detalles: List[Detalle],
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        pagos: Optional[List[Pago]] = None,
        reembolsos: Optional[List[Reembolso]] = None,
        **extra: Any,
    ) -> LiquidacionCompra:
        """Crea una liquidación de compra."""
        return LiquidacionCompra(
            proveedor=proveedor,
            detalles=detalles,
            pagos=pagos or [],
            reembolsos=reembolsos or [],
            **self._comunes(fecha_emision, secuencial, TipoComprobante.LIQUIDACION_COMPRA.value),
            **extra,
        )

    def nota_credito(
        self,
        *,
        receptor: Receptor,
        detalles: List[Detalle],
        motivo: str,
        cod_doc_modificado: str,
        num_doc_modificado: str,
        fecha_emision_doc_sustento: date,
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        compensaciones: Optional[List[Compensacion]] = None,
        **extra: Any,
    ) -> NotaCredito:
        """Crea una nota de crédito."""
        return NotaCredito(
            receptor=receptor,
            detalles=detalles,
            motivo=motivo,
            cod_doc_modificado=cod_doc_modificado,
            num_doc_modificado=num_doc_modificado,
            fecha_emision_doc_sustento=fecha_emision_doc_sustento,
            compensaciones=compensaciones or [],
            **self._comunes(fecha_emision, secuencial, TipoComprobante.NOTA_CREDITO.value),
            **extra,
        )

    def nota_debito(
        self,
        *,
        receptor: Receptor,
        motivos: List[Motivo],
        cod_doc_modificado: str,
        num_doc_modificado: str,
        fecha_emision_doc_sustento: date,
        impuestos: Optional[List[Impuesto]] = None,
        total_sin_impuestos: Any = 0,
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        pagos: Optional[List[Pago]] = None,
        **extra: Any,
    ) -> NotaDebito:
        """Crea una nota de débito."""
        return NotaDebito(
            receptor=receptor,
            motivos=motivos,
            impuestos=impuestos or [],
            total_sin_impuestos=total_sin_impuestos,
            cod_doc_modificado=cod_doc_modificado,
            num_doc_modificado=num_doc_modificado,
            fecha_emision_doc_sustento=fecha_emision_doc_sustento,
            pagos=pagos or [],
            **self._comunes(fecha_emision, secuencial, TipoComprobante.NOTA_DEBITO.value),
            **extra,
        )

    def retencion(
        self,
        *,
        sujeto_retenido: Receptor,
        docs_sustento: List[DocSustento],
        periodo_fiscal: date,
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        **extra: Any,
    ) -> ComprobanteRetencion:
        """Crea un comprobante de retención."""
        return ComprobanteRetencion(
            sujeto_retenido=sujeto_retenido,
            docs_sustento=docs_sustento,
            periodo_fiscal=periodo_fiscal,
            **self._comunes(fecha_emision, secuencial, TipoComprobante.COMPROBANTE_RETENCION.value),
            **extra,
        )

    def guia_remision(
        self,
        *,
        destinatarios: List[Destinatario],
        dir_partida: str,
        razon_social_transportista: str,
        ruc_transportista: str,
        placa: str,
        fecha_ini_transporte: date,
        fecha_fin_transporte: date,
        fecha_emision: Optional[date] = None,
        secuencial: Optional[str] = None,
        **extra: Any,
    ) -> GuiaRemision:
        """Crea una guía de remisión."""
        return GuiaRemision(
            destinatarios=destinatarios,
            dir_partida=dir_partida,
            razon_social_transportista=razon_social_transportista,
            ruc_transportista=ruc_transportista,
            placa=placa,
            fecha_ini_transporte=fecha_ini_transporte,
            fecha_fin_transporte=fecha_fin_transporte,
            **self._comunes(fecha_emision, secuencial, TipoComprobante.GUIA_REMISION.value),
            **extra,
        )

    # ------------------------------------------------------------ flujo SRI

    def firmar(self, comprobante: Comprobante, *, algoritmo: str = "sha1") -> str:
        """Devuelve el XML del comprobante firmado con XAdES-BES."""
        return comprobante.firmar(self.certificado, algoritmo=algoritmo)

    def enviar(
        self,
        comprobante: Union[Comprobante, str],
        *,
        intentos: int = 5,
        espera: float = 3.0,
    ) -> RespuestaAutorizacion:
        """Firma (si hace falta), recepciona y espera la autorización."""
        if isinstance(comprobante, Comprobante):
            xml = self.firmar(comprobante)
            clave = comprobante.clave
        else:
            xml = comprobante
            clave = ""
        return self.cliente.enviar_y_autorizar(
            xml, clave_acceso=clave or None, intentos=intentos, espera=espera
        )

    def emitir(
        self,
        comprobante: Comprobante,
        *,
        algoritmo: str = "sha1",
        enviar: bool = True,
        intentos: int = 5,
        espera: float = 3.0,
    ) -> ResultadoEmision:
        """Ejecuta el ciclo completo de emisión.

        Con ``enviar=False`` solo construye y firma, sin contactar al SRI.
        """
        resultado = ResultadoEmision(
            comprobante=comprobante,
            clave_acceso=comprobante.clave,
            xml_sin_firma=comprobante.to_xml(),
        )
        resultado.xml_firmado = self.firmar(comprobante, algoritmo=algoritmo)
        if not enviar:
            return resultado

        resultado.recepcion = self.cliente.validar_comprobante(resultado.xml_firmado)
        resultado.recepcion.lanzar_si_devuelta()
        resultado.autorizacion = self.cliente.esperar_autorizacion(
            resultado.clave_acceso, intentos=intentos, espera=espera
        )
        return resultado

    def consultar_autorizacion(self, clave_acceso: str) -> RespuestaAutorizacion:
        """Consulta el estado de autorización de una clave de acceso."""
        return self.cliente.autorizar(clave_acceso)
