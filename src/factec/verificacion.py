"""Verifica comprobantes electrónicos: los propios y los que le entreguen.

Comprueba, sin tocar la red, todo lo que se puede revisar dentro del XML:

* que la **clave de acceso** sea válida (dígito verificador) y corresponda al
  comprobante (fecha, tipo, RUC, serie y secuencial);
* que la **firma XAdES-BES** sea auténtica (digest del documento, digest de
  ``SignedProperties`` y firma RSA), usando el certificado que trae el propio XML;
* que el **certificado** esté vigente y sea del mismo RUC que el emisor;
* que la **fecha de emisión** esté dentro de la ventana que acepta el SRI;
* que los **totales** cuadren con las líneas y con los impuestos.

Y con :func:`verificar_en_el_sri` se consulta además el **estado en el SRI**::

    from factec.verificacion import verificar_comprobante, verificar_en_el_sri

    informe = verificar_comprobante(xml_de_mi_proveedor)
    informe.ok                # ¿todo bien?
    informe.problemas         # ["La firma del comprobante no es válida"]
    informe.certificado.nombre
    informe.a_dict()          # listo para una vista o para guardarlo

    autorizacion = verificar_en_el_sri(informe.clave_acceso)
    autorizacion.autorizada
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from .catalogos import DESCRIPCION_TIPO_COMPROBANTE
from .clave_acceso import descomponer_clave_acceso, validar_clave_acceso
from .excepciones import ErrorFirma, ErrorValidacion
from .firma import CertificadoPublico, certificado_del_xml, verificar_firma
from .lectura import (
    AutorizacionLeida,
    ComprobanteLeido,
    leer_autorizacion,
    leer_comprobante,
)
from .sri.fechas import DIAS_TOLERANCIA, hoy_en_ecuador

__all__ = [
    "InformeVerificacion",
    "verificar_clave",
    "verificar_comprobante",
    "verificar_en_el_sri",
    "verificar_totales",
]

#: Diferencia máxima admitida al comparar importes (redondeo del SRI).
TOLERANCIA = Decimal("0.01")

#: Versiones de esquema que usa el SRI por tipo de comprobante.
VERSIONES = {
    "01": "1.1.0",
    "03": "1.1.0",
    "04": "1.1.0",
    "05": "1.0.0",
    "06": "1.1.0",
    "07": "2.0.0",
}


@dataclass
class InformeVerificacion:
    """Resultado de verificar un comprobante."""

    ok: bool = True
    tipo: str = ""
    clave_acceso: str = ""
    numero: str = ""
    fecha_emision: Optional[date] = None
    emisor: str = ""
    receptor: str = ""
    importe_total: Decimal = Decimal("0")
    problemas: List[str] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)
    firma: Optional[bool] = None
    clave_valida: Optional[bool] = None
    clave_coincide: Optional[bool] = None
    fecha_en_rango: Optional[bool] = None
    totales_cuadran: Optional[bool] = None
    version_esperada: Optional[bool] = None
    certificado: Optional[CertificadoPublico] = None
    comprobante: Optional[ComprobanteLeido] = None

    @property
    def descripcion_tipo(self) -> str:
        return DESCRIPCION_TIPO_COMPROBANTE.get(self.tipo, self.tipo)

    def añadir_problema(self, texto: str) -> None:
        self.problemas.append(texto)
        self.ok = False

    def añadir_aviso(self, texto: str) -> None:
        self.avisos.append(texto)

    def a_dict(self) -> Dict[str, Any]:
        """Diccionario listo para JSON."""
        return {
            "ok": self.ok,
            "tipo": self.tipo,
            "descripcion_tipo": self.descripcion_tipo,
            "clave_acceso": self.clave_acceso,
            "numero": self.numero,
            "fecha_emision": self.fecha_emision.isoformat() if self.fecha_emision else None,
            "emisor": self.emisor,
            "receptor": self.receptor,
            "importe_total": str(self.importe_total),
            "firma": self.firma,
            "clave_valida": self.clave_valida,
            "clave_coincide": self.clave_coincide,
            "fecha_en_rango": self.fecha_en_rango,
            "totales_cuadran": self.totales_cuadran,
            "version_esperada": self.version_esperada,
            "certificado": self.certificado.a_dict() if self.certificado else None,
            "problemas": list(self.problemas),
            "avisos": list(self.avisos),
        }


# ------------------------------------------------------------------ parciales


def verificar_clave(comprobante: ComprobanteLeido) -> tuple[bool, bool]:
    """Comprueba la clave de acceso del comprobante.

    Devuelve ``(es_válida, coincide_con_el_documento)``. La clave es válida si su
    dígito verificador cuadra; coincide si sus campos son los del comprobante
    (fecha, tipo, RUC del emisor, serie y secuencial), que es lo que impide que
    alguien «pegue» una clave de otro documento.
    """
    clave = comprobante.clave_acceso
    if not validar_clave_acceso(clave):
        return False, False

    datos = descomponer_clave_acceso(clave)
    fecha = comprobante.fecha_emision
    coincide = (
        datos["tipo_comprobante"] == comprobante.tipo
        and datos["ruc"] == comprobante.emisor.ruc
        and datos["estab"] == comprobante.emisor.estab
        and datos["pto_emi"] == comprobante.emisor.pto_emi
        and datos["secuencial"] == comprobante.secuencial
        and (fecha is None or datos["fecha_emision"] == fecha.strftime("%d%m%Y"))
    )
    return True, coincide


def verificar_totales(comprobante: ComprobanteLeido) -> List[str]:
    """Comprueba que los importes cuadren. Devuelve la lista de problemas."""
    problemas: List[str] = []
    totales = comprobante.totales

    if comprobante.detalles:
        suma_lineas = sum(
            (detalle.precio_total_sin_impuesto for detalle in comprobante.detalles),
            Decimal("0"),
        )
        if abs(suma_lineas - totales.subtotal) > TOLERANCIA:
            problemas.append(
                f"El subtotal ({totales.subtotal}) no coincide con la suma de las "
                f"líneas ({suma_lineas})."
            )

    if comprobante.tipo in ("01", "03", "04"):
        esperado = totales.subtotal + totales.valor_impuestos + totales.propina
        if abs(esperado - totales.importe_total) > TOLERANCIA:
            problemas.append(
                f"El importe total ({totales.importe_total}) no coincide con el "
                f"subtotal más los impuestos ({esperado})."
            )

    for indice, detalle in enumerate(comprobante.detalles, start=1):
        calculado = detalle.cantidad * detalle.precio_unitario - detalle.descuento
        if detalle.precio_total_sin_impuesto and (
            abs(calculado - detalle.precio_total_sin_impuesto) > TOLERANCIA
        ):
            problemas.append(
                f"La línea {indice} ({detalle.descripcion}) tiene un importe que no "
                f"cuadra: {detalle.precio_total_sin_impuesto} frente a {calculado}."
            )

    return problemas


def verificar_en_el_sri(
    clave_acceso: str,
    *,
    ambiente: int = 1,
    cliente: Any = None,
) -> AutorizacionLeida:
    """Consulta en el SRI el estado de un comprobante por su clave de acceso.

    No hace falta tener el comprobante: sirve para comprobar si una clave que le
    dieron (por ejemplo en una factura de proveedor) está autorizada de verdad.
    """
    if cliente is None:
        from .sri.soap import ClienteSRI

        cliente = ClienteSRI(ambiente=int(getattr(ambiente, "value", ambiente)))

    respuesta = cliente.autorizar(str(clave_acceso).strip())
    if not respuesta.autorizaciones:
        return AutorizacionLeida(
            estado="NO ENCONTRADO", clave_acceso=clave_acceso, crudo=respuesta.crudo
        )

    ultima = respuesta.ultima
    autorizacion = None
    if respuesta.crudo:
        try:
            autorizacion = leer_autorizacion(respuesta.crudo)
        except ErrorValidacion:      # respuesta en otro formato: se usa el detalle
            autorizacion = None
    if autorizacion is None:
        autorizacion = AutorizacionLeida(
            estado=getattr(ultima, "estado", ""),
            numero_autorizacion=getattr(ultima, "numero_autorizacion", ""),
            fecha_autorizacion=getattr(ultima, "fecha_autorizacion", None),
            ambiente=getattr(ultima, "ambiente", ""),
            mensajes=[
                {
                    "identificador": getattr(mensaje, "identificador", ""),
                    "mensaje": getattr(mensaje, "mensaje", ""),
                    "informacion_adicional": getattr(mensaje, "informacion_adicional", ""),
                    "tipo": getattr(mensaje, "tipo", ""),
                }
                for mensaje in getattr(ultima, "mensajes", [])
            ],
        )
        contenido = getattr(ultima, "comprobante", "")
        if contenido:
            try:
                autorizacion.comprobante = leer_comprobante(contenido)
            except ErrorValidacion:
                pass

    autorizacion.clave_acceso = autorizacion.clave_acceso or clave_acceso
    return autorizacion


def _como_publico(certificado: Any) -> Optional[CertificadoPublico]:
    """Normaliza el certificado recibido (propio o ajeno) a uno público."""
    if certificado is None:
        return None
    if isinstance(certificado, CertificadoPublico):
        return certificado
    propio = getattr(certificado, "certificado", None)  # ``Certificado`` del paquete
    if propio is not None and not isinstance(propio, CertificadoPublico):
        return CertificadoPublico(certificado=propio)
    return None


# ------------------------------------------------------------------ principal


def verificar_comprobante(
    xml: Union[str, bytes],
    *,
    certificado: Optional[Any] = None,
    hoy: Optional[date] = None,
    exigir_firma: bool = True,
    dias: Optional[int] = None,
) -> InformeVerificacion:
    """Verifica un comprobante electrónico del SRI.

    Con ``exigir_firma=False`` se salta la firma (útil para comprobantes sin
    firmar, como los borradores propios). ``certificado`` permite verificar con un
    certificado concreto en lugar del que trae el XML.
    """
    informe = InformeVerificacion()

    try:
        comprobante = leer_comprobante(xml)
    except ErrorValidacion as error:
        informe.añadir_problema(str(error))
        return informe

    informe.comprobante = comprobante
    informe.tipo = comprobante.tipo
    informe.clave_acceso = comprobante.clave_acceso
    informe.numero = comprobante.numero
    informe.fecha_emision = comprobante.fecha_emision
    informe.emisor = comprobante.emisor.razon_social or comprobante.emisor.ruc
    informe.receptor = comprobante.receptor.razon_social or comprobante.receptor.identificacion
    informe.importe_total = comprobante.totales.importe_total

    # --- estructura
    if comprobante.tipo not in DESCRIPCION_TIPO_COMPROBANTE:
        informe.añadir_aviso(f"Tipo de comprobante desconocido: {comprobante.tipo!r}.")
    esperada = VERSIONES.get(comprobante.tipo)
    if esperada:
        informe.version_esperada = comprobante.version == esperada
        if not informe.version_esperada:
            informe.añadir_aviso(
                f"La versión del esquema es {comprobante.version or 'desconocida'} y el "
                f"SRI usa la {esperada} para este comprobante."
            )

    # --- clave de acceso
    if comprobante.clave_acceso:
        informe.clave_valida, informe.clave_coincide = verificar_clave(comprobante)
        if not informe.clave_valida:
            informe.añadir_problema(
                f"La clave de acceso {comprobante.clave_acceso} no es válida "
                "(falla el dígito verificador)."
            )
        elif not informe.clave_coincide:
            informe.añadir_problema(
                "La clave de acceso no corresponde a los datos del comprobante "
                "(fecha, tipo, RUC, serie o secuencial)."
            )
    else:
        informe.añadir_problema("El comprobante no tiene clave de acceso.")

    # --- firma
    if exigir_firma:
        try:
            resultado = verificar_firma(xml, certificado)
            informe.firma = bool(resultado["valido"])
        except ErrorFirma as error:
            informe.firma = False
            informe.añadir_problema(f"La firma no es válida: {error}")

        informe.certificado = _como_publico(certificado) or certificado_del_xml(xml)
        if informe.certificado is not None:
            if informe.certificado.vencido():
                informe.añadir_problema(
                    "El certificado con el que se firmó está vencido o aún no era "
                    f"válido (vigente del {informe.certificado.valido_desde:%d/%m/%Y} "
                    f"al {informe.certificado.valido_hasta:%d/%m/%Y})."
                )
            ruc = informe.certificado.ruc
            if ruc and comprobante.emisor.ruc and ruc != comprobante.emisor.ruc:
                informe.añadir_problema(
                    f"El certificado es de otro RUC ({ruc}) distinto del emisor del "
                    f"comprobante ({comprobante.emisor.ruc})."
                )
        else:
            informe.añadir_aviso("El XML no trae el certificado incrustado.")

    # --- fecha de emisión (la ventana que aplica el SRI)
    if comprobante.fecha_emision:
        referencia = hoy or hoy_en_ecuador()
        limite = referencia - timedelta(days=DIAS_TOLERANCIA if dias is None else dias)
        informe.fecha_en_rango = limite <= comprobante.fecha_emision <= referencia
        if not informe.fecha_en_rango:
            informe.añadir_problema(
                f"La fecha de emisión ({comprobante.fecha_emision:%d/%m/%Y}) está fuera "
                f"de la ventana del SRI: como muy antiguo {limite:%d/%m/%Y} y como muy "
                f"nuevo hoy ({referencia:%d/%m/%Y})."
            )
    else:
        informe.añadir_problema("El comprobante no tiene fecha de emisión legible.")

    # --- totales
    problemas_totales = verificar_totales(comprobante)
    informe.totales_cuadran = not problemas_totales
    for problema in problemas_totales:
        informe.añadir_problema(problema)

    return informe
