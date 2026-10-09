"""Revisión previa a la emisión: comprobar antes de firmar y enviar.

Un comprobante emitido con el certificado vencido, con el certificado de otro
contribuyente o con la fecha fuera del rango del SRI se rechaza y deja el
documento inconsistente (secuencial consumido, estado «devuelto»). Para evitarlo,
antes de firmar se revisa todo lo que puede fallar::

    from factec.revision import revisar_emision

    informe = revisar_emision(comprobante, certificado)
    informe.puede_emitir     # False si algo impide emitir
    informe.problemas        # lo que hay que corregir
    informe.avisos           # lo que conviene atender (p. ej. vence en 10 días)
    informe.a_dict()         # listo para una vista o para guardarlo

Y cuando el comprobante ya está construido (por ejemplo el XML guardado en la
base de datos) se revisa el propio XML::

    informe = revisar_xml(registro.xml_sin_firma, certificado=certificado, emisor=emisor)

La revisión **no** contacta con el SRI y no consume secuenciales: es una
comprobación local y rápida.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Union

from .catalogos import DESCRIPCION_TIPO_COMPROBANTE
from .clave_acceso import validar_clave_acceso
from .excepciones import ErrorValidacion
from .firma import ruc_del_certificado
from .lectura import ComprobanteLeido, leer_comprobante
from .sri import fechas
from .verificacion import verificar_clave, verificar_totales

__all__ = [
    "DIAS_AVISO_CERTIFICADO",
    "InformeRevision",
    "RevisionCertificado",
    "revisar_certificado",
    "revisar_emision",
    "revisar_xml",
]

#: Días de antelación con los que se avisa de que el certificado va a vencer.
DIAS_AVISO_CERTIFICADO = 30


# ------------------------------------------------------------- certificado


@dataclass
class RevisionCertificado:
    """Estado del certificado de firma."""

    cargado: bool = False
    vigente: bool = False
    vencido: bool = False
    dias_restantes: Optional[int] = None
    valido_desde: Optional[datetime] = None
    valido_hasta: Optional[datetime] = None
    titular: str = ""
    emisor: str = ""
    ruc: str = ""
    numero_serie: Optional[int] = None
    problemas: List[str] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` si el certificado se puede usar para firmar."""
        return self.cargado and self.vigente and not self.problemas

    def añadir_problema(self, texto: str) -> None:
        self.problemas.append(texto)

    def añadir_aviso(self, texto: str) -> None:
        self.avisos.append(texto)

    def a_dict(self) -> Dict[str, Any]:
        """Diccionario listo para JSON."""
        return {
            "ok": self.ok,
            "cargado": self.cargado,
            "vigente": self.vigente,
            "vencido": self.vencido,
            "dias_restantes": self.dias_restantes,
            "valido_desde": self.valido_desde.isoformat() if self.valido_desde else None,
            "valido_hasta": self.valido_hasta.isoformat() if self.valido_hasta else None,
            "titular": self.titular,
            "emisor": self.emisor,
            "ruc": self.ruc,
            "numero_serie": self.numero_serie,
            "problemas": list(self.problemas),
            "avisos": list(self.avisos),
        }


def _fecha_de(certificado: Any, nombre: str, atributo: str) -> Optional[datetime]:
    """Fecha del certificado, venga del paquete o directamente de ``cryptography``."""
    valor = getattr(certificado, nombre, None)
    if valor is None:
        x509_cert = getattr(certificado, "certificado", None)
        for clave in (f"{atributo}_utc", atributo):
            valor = getattr(x509_cert, clave, None)
            if valor is not None:
                break
    if not isinstance(valor, datetime):
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


def _formato(fecha: Optional[datetime]) -> str:
    return fecha.strftime("%d/%m/%Y") if fecha else "—"


def revisar_certificado(
    certificado: Any = None,
    *,
    emisor: Any = None,
    dias_aviso: int = DIAS_AVISO_CERTIFICADO,
    momento: Optional[datetime] = None,
) -> RevisionCertificado:
    """Revisa el certificado de firma: vigencia, titular y RUC.

    ``certificado`` admite un :class:`factec.firma.Certificado` (el del ``.p12``) o
    un :class:`factec.firma.CertificadoPublico` (el que viaja en un XML firmado).
    ``emisor`` es el emisor del comprobante: si se indica, se comprueba que el RUC
    del certificado sea el suyo.
    """
    informe = RevisionCertificado()
    if certificado is None:
        informe.añadir_problema(
            "No hay certificado de firma: sin él no se puede firmar ni emitir. Cargue "
            "el archivo .p12 (y su contraseña) en la configuración del emisor."
        )
        return informe

    informe.cargado = True
    for atributo, destino in (
        ("titular", "titular"), ("emisor", "emisor"), ("numero_serie", "numero_serie"),
    ):
        try:
            setattr(informe, destino, getattr(certificado, atributo, "") or "")
        except Exception:  # noqa: BLE001 - es informativo: no impide emitir
            pass
    try:
        informe.ruc = ruc_del_certificado(certificado)
    except Exception:  # noqa: BLE001
        informe.ruc = ""

    momento = momento or datetime.now(timezone.utc)
    informe.valido_desde = _fecha_de(certificado, "valido_desde", "not_valid_before")
    informe.valido_hasta = _fecha_de(certificado, "valido_hasta", "not_valid_after")

    if informe.valido_desde is None or informe.valido_hasta is None:
        informe.añadir_aviso(
            "No se pudo leer la vigencia del certificado: compruebe que el archivo "
            "sea un .p12 válido."
        )
    else:
        informe.dias_restantes = (informe.valido_hasta.date() - momento.date()).days
        if momento < informe.valido_desde:
            informe.añadir_problema(
                f"El certificado de firma aún no es válido: empieza a serlo el "
                f"{_formato(informe.valido_desde)}."
            )
        elif momento > informe.valido_hasta:
            informe.vencido = True
            informe.añadir_problema(
                f"El certificado de firma está vencido desde el "
                f"{_formato(informe.valido_hasta)}: el SRI rechaza los comprobantes "
                "firmados con un certificado vencido. Renueve su firma electrónica y "
                "vuelva a cargarla antes de emitir."
            )
        else:
            informe.vigente = True
            if dias_aviso and informe.dias_restantes <= dias_aviso:
                informe.añadir_aviso(
                    f"El certificado de firma vence el "
                    f"{_formato(informe.valido_hasta)} (en "
                    f"{informe.dias_restantes} día(s)): renuévelo antes de esa fecha "
                    "para no quedarse sin poder emitir."
                )

    ruc_emisor = str(getattr(emisor, "ruc", emisor) or "")
    if ruc_emisor and informe.ruc and informe.ruc != ruc_emisor:
        informe.añadir_problema(
            f"El RUC del certificado ({informe.ruc}) no coincide con el del emisor "
            f"({ruc_emisor}): el SRI rechaza los comprobantes firmados por otro "
            "contribuyente. Cargue el certificado correcto."
        )
    elif ruc_emisor and not informe.ruc:
        informe.añadir_aviso(
            "No se pudo leer el RUC del certificado: compruebe que el certificado "
            f"cargado sea el de «{ruc_emisor}»."
        )

    return informe


# ----------------------------------------------------------------- informe


@dataclass
class InformeRevision:
    """Resultado de la revisión previa a la emisión."""

    tipo: str = ""
    clave_acceso: str = ""
    numero: str = ""
    fecha_emision: Optional[date] = None
    fecha_de_hoy: Optional[date] = None
    fecha_en_rango: Optional[bool] = None
    datos_validos: Optional[bool] = None
    clave_valida: Optional[bool] = None
    clave_coincide: Optional[bool] = None
    totales_cuadran: Optional[bool] = None
    ruc_emisor: str = ""
    certificado: Optional[RevisionCertificado] = None
    problemas: List[str] = field(default_factory=list)
    avisos: List[str] = field(default_factory=list)

    @property
    def puede_emitir(self) -> bool:
        """``True`` si nada impide firmar y enviar el comprobante."""
        return not self.problemas

    @property
    def ok(self) -> bool:
        """Igual que :attr:`puede_emitir` (por comodidad al leer el informe)."""
        return self.puede_emitir

    @property
    def descripcion_tipo(self) -> str:
        return DESCRIPCION_TIPO_COMPROBANTE.get(self.tipo, self.tipo)

    def añadir_problema(self, texto: str, *, primero: bool = False) -> None:
        if primero:
            self.problemas.insert(0, texto)
        else:
            self.problemas.append(texto)

    def añadir_aviso(self, texto: str) -> None:
        self.avisos.append(texto)

    def lleva(self, texto: str) -> bool:
        """¿El informe ya incluye ese problema o ese aviso?

        Se compara también por contenido: ``validar()`` del comprobante y la
        comprobación de la fecha cuentan lo mismo con distinto preámbulo.
        """
        return any(
            texto in conocido or conocido in texto
            for conocido in (*self.problemas, *self.avisos)
        )

    def resumen(self) -> str:
        """Una línea para un mensaje del admin, un log o una vista."""
        etiqueta = self.descripcion_tipo or self.tipo or "comprobante"
        if self.numero:
            etiqueta = f"{etiqueta} {self.numero}"
        if self.puede_emitir:
            return f"{etiqueta}: listo para emitir." + (
                f" Avisos: {'; '.join(self.avisos)}" if self.avisos else ""
            )
        return f"{etiqueta}: no se puede emitir — {'; '.join(self.problemas)}"

    def a_dict(self) -> Dict[str, Any]:
        """Diccionario listo para JSON."""
        return {
            "puede_emitir": self.puede_emitir,
            "tipo": self.tipo,
            "descripcion_tipo": self.descripcion_tipo,
            "numero": self.numero,
            "clave_acceso": self.clave_acceso,
            "fecha_emision": self.fecha_emision.isoformat() if self.fecha_emision else None,
            "fecha_de_hoy": self.fecha_de_hoy.isoformat() if self.fecha_de_hoy else None,
            "fecha_en_rango": self.fecha_en_rango,
            "datos_validos": self.datos_validos,
            "clave_valida": self.clave_valida,
            "clave_coincide": self.clave_coincide,
            "totales_cuadran": self.totales_cuadran,
            "ruc_emisor": self.ruc_emisor,
            "certificado": self.certificado.a_dict() if self.certificado else None,
            "problemas": list(self.problemas),
            "avisos": list(self.avisos),
        }

    def sustituir_problema(self, empieza_por: str, texto: str) -> None:
        """Cambia un problema por otro más preciso (o lo añade al principio)."""
        for indice, problema in enumerate(self.problemas):
            if problema.startswith(empieza_por):
                self.problemas[indice] = texto
                return
        self.añadir_problema(texto, primero=True)

    def heredar_del_certificado(self, certificado: RevisionCertificado) -> None:
        """Copia los problemas y avisos del certificado a este informe."""
        self.certificado = certificado
        for texto in certificado.problemas:
            self.añadir_problema(texto)
        for texto in certificado.avisos:
            self.añadir_aviso(texto)


# ------------------------------------------------------------------ revisión


def revisar_emision(
    comprobante: Any,
    certificado: Any = None,
    *,
    emisor: Any = None,
    dias_aviso: int = DIAS_AVISO_CERTIFICADO,
    momento: Optional[datetime] = None,
) -> InformeRevision:
    """Revisa un comprobante (sin firmar) y su certificado antes de emitirlo.

    Devuelve un :class:`InformeRevision`: si ``puede_emitir`` es ``False`` hay que
    corregir lo que aparece en ``problemas`` en lugar de firmar y enviar.
    """
    emisor = emisor if emisor is not None else getattr(comprobante, "emisor", None)
    informe = InformeRevision(
        tipo=str(getattr(comprobante, "TIPO", "") or ""),
        fecha_emision=getattr(comprobante, "fecha_emision", None),
        fecha_de_hoy=fechas.hoy_en_ecuador(momento),
        ruc_emisor=str(getattr(emisor, "ruc", "") or ""),
    )
    informe.heredar_del_certificado(
        revisar_certificado(
            certificado, emisor=emisor, dias_aviso=dias_aviso, momento=momento
        )
    )

    try:
        comprobante.validar()
        informe.datos_validos = True
    except ErrorValidacion as exc:
        informe.datos_validos = False
        informe.añadir_problema(
            f"Los datos del comprobante no cumplen las reglas del SRI: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - se informa en lugar de reventar
        informe.datos_validos = False
        informe.añadir_problema(f"El comprobante no se pudo validar: {exc}")

    _revisar_fecha(informe, momento=momento)

    try:
        informe.clave_acceso = comprobante.clave
        informe.clave_valida = validar_clave_acceso(informe.clave_acceso)
    except Exception as exc:  # noqa: BLE001 - la clave necesita datos válidos
        informe.clave_valida = False
        texto = f"No se pudo calcular la clave de acceso: {exc}"
        if not informe.lleva(texto):
            informe.añadir_problema(texto)

    return informe


def _revisar_fecha(informe: InformeRevision, *, momento: Optional[datetime] = None) -> None:
    """Completa el informe con la comprobación de la fecha de emisión."""
    if informe.fecha_emision is None:
        return
    try:
        fechas.validar_fecha_emision(informe.fecha_emision, hoy=informe.fecha_de_hoy)
        informe.fecha_en_rango = True
    except ErrorValidacion as exc:
        informe.fecha_en_rango = False
        if not informe.lleva(str(exc)):
            informe.añadir_problema(str(exc))
        return

    if informe.fecha_de_hoy and informe.fecha_emision != informe.fecha_de_hoy:
        informe.añadir_aviso(
            f"El comprobante está fechado el {informe.fecha_emision:%d/%m/%Y} y hoy es "
            f"el {informe.fecha_de_hoy:%d/%m/%Y}: el SRI solo admite la fecha del día "
            "de la firma o de los 90 días anteriores."
        )


def revisar_xml(
    xml: Union[str, bytes],
    *,
    certificado: Any = None,
    emisor: Any = None,
    dias_aviso: int = DIAS_AVISO_CERTIFICADO,
    momento: Optional[datetime] = None,
) -> InformeRevision:
    """Revisa un comprobante ya construido (el XML guardado) antes de firmarlo.

    Comprueba el certificado, la fecha de emisión, la clave de acceso y que los
    totales cuadren, sin contactar con el SRI. Es lo que se revisa cuando el
    comprobante está en la base de datos y solo queda firmarlo y enviarlo.
    """
    informe = InformeRevision(fecha_de_hoy=fechas.hoy_en_ecuador(momento))
    informe.heredar_del_certificado(
        revisar_certificado(
            certificado,
            emisor=emisor,
            dias_aviso=dias_aviso,
            momento=momento,
        )
    )

    try:
        leido: ComprobanteLeido = leer_comprobante(xml)
    except ErrorValidacion as exc:
        informe.añadir_problema(f"El XML del comprobante no se pudo leer: {exc}")
        return informe

    informe.tipo = leido.tipo
    informe.numero = leido.numero
    informe.clave_acceso = leido.clave_acceso
    informe.fecha_emision = leido.fecha_emision
    informe.ruc_emisor = informe.ruc_emisor or leido.emisor.ruc

    _revisar_fecha(informe, momento=momento)

    informe.clave_valida, informe.clave_coincide = verificar_clave(leido)
    if informe.clave_valida is False:
        informe.añadir_problema(
            f"La clave de acceso del comprobante no es válida: {leido.clave_acceso}."
        )
    elif informe.clave_coincide is False:
        informe.añadir_problema(
            "La clave de acceso no corresponde al comprobante (fecha, serie o "
            "secuencial distintos)."
        )

    problemas_totales = verificar_totales(leido)
    informe.totales_cuadran = not problemas_totales
    for texto in problemas_totales:
        informe.añadir_problema(texto)

    ruc_certificado = informe.certificado.ruc if informe.certificado else ""
    if ruc_certificado and leido.emisor.ruc and ruc_certificado != leido.emisor.ruc:
        informe.añadir_problema(
            f"El RUC del certificado ({ruc_certificado}) no coincide con el emisor del "
            f"comprobante ({leido.emisor.ruc})."
        )
    return informe
