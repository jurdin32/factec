"""Firma electrónica **XAdES-BES** (XMLDSig enveloped) para el SRI.

Implementa el perfil que exige el SRI para los comprobantes electrónicos:

* firma *enveloped* sobre el elemento raíz (``id="comprobante"``);
* canonicalización inclusiva ``xml-c14n``;
* referencia a ``SignedProperties`` con el ``Type`` de ETSI;
* ``KeyInfo`` con el certificado X.509 en base64 y la clave pública RSA;
* ``SignedProperties`` con ``SigningTime`` y ``SigningCertificate``.

Por omisión usa RSA-SHA1 con digests SHA1, que es lo que el SRI valida de forma
más amplia; puede cambiarse con ``algoritmo="sha256"``.

Ejemplo::

    certificado = Certificado.desde_archivo("firmante.p12", "clave")
    xml_firmado = firmar_xml(xml_sin_firma, certificado)
"""

from __future__ import annotations

import base64
import copy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

from ..excepciones import ErrorCertificado, ErrorFirma

__all__ = [
    "Certificado",
    "firmar_xml",
    "verificar_firma",
    "ALGORITMOS",
    "NS_DS",
    "NS_XADES",
    "C14N_INCLUSIVO",
]

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
NS_ETSI_TYPE = "http://uri.etsi.org/01903#SignedProperties"
NS_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
C14N_INCLUSIVO = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"

DECLARACION_XML = '<?xml version="1.0" encoding="UTF-8"?>'

#: URIs de los algoritmos soportados (digest y firma).
ALGORITMOS: Dict[str, Dict[str, str]] = {
    "sha1": {
        "digest": "http://www.w3.org/2000/09/xmldsig#sha1",
        "firma": "http://www.w3.org/2000/09/xmldsig#rsa-sha1",
    },
    "sha256": {
        "digest": "http://www.w3.org/2001/04/xmlenc#sha256",
        "firma": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
    },
    "sha512": {
        "digest": "http://www.w3.org/2001/04/xmlenc#sha512",
        "firma": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512",
    },
}

_HASHES = {"sha1": hashes.SHA1, "sha256": hashes.SHA256, "sha512": hashes.SHA512}


def _digest(datos: bytes, algoritmo: str) -> bytes:
    return hashlib.new(algoritmo, datos).digest()


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def _c14n(elemento: etree._Element) -> bytes:
    """Canonicaliza en modo inclusivo (1.0), como espera el SRI."""
    return etree.tostring(elemento, method="c14n", exclusive=False, with_comments=False)


def _fecha_certificado(certificado: x509.Certificate, atributo: str) -> datetime:
    """``not_valid_before``/``after`` con o sin sufijo ``_utc`` según versión."""
    valor = getattr(certificado, f"{atributo}_utc", None)
    if valor is not None:
        return valor
    return getattr(certificado, atributo).replace(tzinfo=timezone.utc)


@dataclass
class Certificado:
    """Certificado de firma electrónica (archivo ``.p12``/``.pfx``)."""

    clave_privada: rsa.RSAPrivateKey
    certificado: x509.Certificate
    cadena: List[x509.Certificate] = field(default_factory=list)

    # ----------------------------------------------------------- constructores

    @classmethod
    def desde_archivo(
        cls,
        ruta: Union[str, Path],
        clave: str,
        *,
        ruta_ca: Optional[Union[str, Path]] = None,
    ) -> "Certificado":
        """Carga un ``.p12`` desde disco."""
        ruta = Path(ruta)
        if not ruta.exists():
            raise ErrorCertificado(f"No existe el archivo de certificado: {ruta}")
        return cls.desde_bytes(ruta.read_bytes(), clave, ruta_ca=ruta_ca)

    @classmethod
    def desde_bytes(
        cls,
        datos: bytes,
        clave: str,
        *,
        ruta_ca: Optional[Union[str, Path]] = None,
    ) -> "Certificado":
        """Carga un ``.p12`` desde memoria."""
        try:
            clave_privada, certificado, adicionales = pkcs12.load_key_and_certificates(
                datos, (clave or "").encode("utf-8")
            )
        except Exception as exc:  # noqa: BLE001 - se reexpresa como error propio
            raise ErrorCertificado(
                "No se pudo abrir el certificado .p12: verifique la contraseña y "
                "que el archivo sea un PKCS#12 válido."
            ) from exc

        if clave_privada is None or certificado is None:
            raise ErrorCertificado("El .p12 no contiene una clave privada y un certificado.")
        if not isinstance(clave_privada, rsa.RSAPrivateKey):
            raise ErrorCertificado("La clave privada no es RSA; el SRI exige certificados RSA.")

        cadena = list(adicionales or [])
        if ruta_ca:
            cadena.extend(_cargar_cadenas(Path(ruta_ca)))

        return cls(clave_privada=clave_privada, certificado=certificado, cadena=cadena)

    # ------------------------------------------------------------ propiedades

    @property
    def numero_serie(self) -> int:
        return self.certificado.serial_number

    @property
    def emisor(self) -> str:
        return self.certificado.issuer.rfc4514_string()

    @property
    def titular(self) -> str:
        return self.certificado.subject.rfc4514_string()

    @property
    def der(self) -> bytes:
        return self.certificado.public_bytes(serialization.Encoding.DER)

    def certificado_base64(self) -> str:
        return _b64(self.der)

    def huella(self, algoritmo: str = "sha1") -> bytes:
        """Digest del certificado (DER) con el algoritmo indicado."""
        return _digest(self.der, algoritmo)

    def vencido(self, momento: Optional[datetime] = None) -> bool:
        momento = momento or datetime.now(timezone.utc)
        return not (
            _fecha_certificado(self.certificado, "not_valid_before")
            <= momento
            <= _fecha_certificado(self.certificado, "not_valid_after")
        )

    def validar_vigencia(self, momento: Optional[datetime] = None) -> None:
        """Lanza :class:`ErrorCertificado` si el certificado no está vigente."""
        if self.vencido(momento):
            raise ErrorCertificado(
                "El certificado está vencido o aún no es válido: "
                f"{_fecha_certificado(self.certificado, 'not_valid_before')} - "
                f"{_fecha_certificado(self.certificado, 'not_valid_after')}"
            )


def _cargar_cadenas(ruta: Path) -> List[x509.Certificate]:
    datos = ruta.read_bytes()
    if datos.lstrip().startswith(b"-----BEGIN"):
        return list(x509.load_pem_x509_certificates(datos))
    return [x509.load_der_x509_certificate(datos)]


# ---------------------------------------------------------------- construcción


def _nodo(padre: etree._Element, etiqueta: str, **atributos: Any) -> etree._Element:
    return etree.SubElement(
        padre, etiqueta, {k: str(v) for k, v in atributos.items() if v is not None}
    )


def _ds(padre: etree._Element, nombre: str, **atributos: Any) -> etree._Element:
    return _nodo(padre, f"{{{NS_DS}}}{nombre}", **atributos)


def _xades(padre: etree._Element, nombre: str, **atributos: Any) -> etree._Element:
    return _nodo(padre, f"{{{NS_XADES}}}{nombre}", **atributos)


def _entero_a_base64(valor: int) -> str:
    return _b64(valor.to_bytes((valor.bit_length() + 7) // 8, "big"))


def _construir_signed_properties(
    firma: etree._Element,
    certificado: Certificado,
    algoritmo: str,
    momento: datetime,
    sello: str,
) -> tuple:
    """Crea ``ds:Object`` con las ``QualifyingProperties``.

    Devuelve ``(objeto, signed_props)``: el ``ds:Object`` que hay que colocar al
    final del documento y el nodo ``SignedProperties`` que hay que digerir.
    """
    objeto = _ds(firma, "Object", Id=f"Signature-{sello}-Object")
    qualifying = etree.SubElement(
        objeto,
        f"{{{NS_XADES}}}QualifyingProperties",
        nsmap={"xades": NS_XADES},
        attrib={"Target": f"#Signature-{sello}"},
    )
    signed_props = _xades(qualifying, "SignedProperties", Id=f"SignedProperties-{sello}")

    firmante = _xades(signed_props, "SignedSignatureProperties")
    _xades(firmante, "SigningTime").text = momento.isoformat()

    signing_certificate = _xades(firmante, "SigningCertificate")
    cert = _xades(signing_certificate, "Cert")
    cert_digest = _xades(cert, "CertDigest")
    _ds(cert_digest, "DigestMethod", Algorithm=ALGORITMOS[algoritmo]["digest"])
    _ds(cert_digest, "DigestValue").text = _b64(certificado.huella(algoritmo))
    issuer_serial = _xades(cert, "IssuerSerial")
    _ds(issuer_serial, "X509IssuerName").text = certificado.emisor
    _ds(issuer_serial, "X509SerialNumber").text = str(certificado.numero_serie)

    _xades(signed_props, "SignedDataObjectProperties")
    return objeto, signed_props


def _construir_key_info(firma: etree._Element, certificado: Certificado, sello: str) -> etree._Element:
    key_info = _ds(firma, "KeyInfo", Id=f"Certificate-{sello}")
    x509_data = _ds(key_info, "X509Data")
    _ds(x509_data, "X509Certificate").text = certificado.certificado_base64()

    publica = certificado.certificado.public_key()
    if isinstance(publica, rsa.RSAPublicKey):
        numeros = publica.public_numbers()
        rsa_key = _ds(_ds(key_info, "KeyValue"), "RSAKeyValue")
        _ds(rsa_key, "Modulus").text = _entero_a_base64(numeros.n)
        _ds(rsa_key, "Exponent").text = _entero_a_base64(numeros.e)
    return key_info


def firmar_xml(
    xml: Union[str, bytes],
    certificado: Certificado,
    *,
    algoritmo: str = "sha1",
    id_comprobante: str = "comprobante",
    fecha_firma: Optional[datetime] = None,
    validar_vigencia: bool = False,
) -> str:
    """Firma ``xml`` con XAdES-BES y devuelve el documento firmado.

    ``xml`` debe ser el comprobante sin firmar y su raíz tener
    ``id="comprobante"``. Si ya estaba firmado, la firma anterior se reemplaza.
    """
    if algoritmo not in ALGORITMOS:
        raise ErrorFirma(
            f"Algoritmo no soportado: {algoritmo!r}. Use uno de {sorted(ALGORITMOS)}."
        )
    if validar_vigencia:
        certificado.validar_vigencia()

    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    try:
        raiz = etree.fromstring(xml)
    except etree.XMLSyntaxError as exc:
        raise ErrorFirma(f"El XML no es válido y no se puede firmar: {exc}") from exc

    if raiz.get("id") != id_comprobante:
        raise ErrorFirma(
            f"El elemento raíz debe tener id={id_comprobante!r} para poder firmarlo "
            f"(se encontró id={raiz.get('id')!r})."
        )

    # Una firma previa invalidaría el documento: se elimina antes de firmar.
    for previa in raiz.findall(f"{{{NS_DS}}}Signature"):
        raiz.remove(previa)

    uris = ALGORITMOS[algoritmo]
    momento = fecha_firma or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    sello = uuid4().hex[:16]

    firma = etree.SubElement(raiz, f"{{{NS_DS}}}Signature", nsmap={"ds": NS_DS})
    firma.set("Id", f"Signature-{sello}")

    # --- SignedInfo con la referencia al comprobante -------------------------
    signed_info = _ds(firma, "SignedInfo")
    _ds(signed_info, "CanonicalizationMethod", Algorithm=C14N_INCLUSIVO)
    _ds(signed_info, "SignatureMethod", Algorithm=uris["firma"])

    referencia_doc = _ds(signed_info, "Reference", Id=f"Reference-{sello}", URI=f"#{id_comprobante}")
    transformadas = _ds(referencia_doc, "Transforms")
    _ds(transformadas, "Transform", Algorithm=NS_ENVELOPED)
    _ds(referencia_doc, "DigestMethod", Algorithm=uris["digest"])
    digest_doc = _ds(referencia_doc, "DigestValue")

    # --- SignedProperties (debe colgar del documento para el C14N) -----------
    objeto, signing_props = _construir_signed_properties(
        firma, certificado, algoritmo, momento, sello
    )

    # 1) Digest del comprobante: la transformada enveloped quita la firma.
    copia = copy.deepcopy(raiz)
    for previa in copia.findall(f"{{{NS_DS}}}Signature"):
        copia.remove(previa)
    digest_doc.text = _b64(_digest(_c14n(copia), algoritmo))

    # 2) Referencia a SignedProperties (va después de la del comprobante).
    referencia_props = _ds(
        signed_info, "Reference", Type=NS_ETSI_TYPE, URI=f"#SignedProperties-{sello}"
    )
    transformadas_props = _ds(referencia_props, "Transforms")
    _ds(transformadas_props, "Transform", Algorithm=C14N_INCLUSIVO)
    _ds(referencia_props, "DigestMethod", Algorithm=uris["digest"])
    _ds(referencia_props, "DigestValue").text = _b64(_digest(_c14n(signing_props), algoritmo))

    # --- Ensamblado en el orden que exige el esquema XMLDSig: ---------------
    #     SignedInfo, SignatureValue, KeyInfo, Object
    firma.remove(objeto)
    signature_value = _ds(firma, "SignatureValue")
    _construir_key_info(firma, certificado, sello)
    firma.append(objeto)

    # 3) Firma RSA sobre el C14N de SignedInfo (ya final).
    signature_value.text = _b64(
        certificado.clave_privada.sign(
            _c14n(signed_info), padding.PKCS1v15(), _HASHES[algoritmo]()
        )
    )

    return DECLARACION_XML + etree.tostring(raiz, encoding="unicode")


# ---------------------------------------------------------------- verificación


def _algoritmo_desde_uri(uri: str) -> str:
    for nombre, valores in ALGORITMOS.items():
        if valores["firma"] == uri:
            return nombre
    raise ErrorFirma(f"Algoritmo de firma no reconocido: {uri!r}")


def verificar_firma(
    xml: Union[str, bytes],
    certificado: Optional[Certificado] = None,
) -> Dict[str, Any]:
    """Verifica las firmas XAdES-BES del documento.

    Comprueba, por cada ``Signature``: el digest del comprobante, el digest de
    ``SignedProperties`` y la firma RSA de ``SignedInfo``. Devuelve el detalle y
    lanza :class:`ErrorFirma` si algo no cuadra.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    raiz = etree.fromstring(xml)

    firmas = raiz.findall(f"{{{NS_DS}}}Signature")
    if not firmas:
        raise ErrorFirma("El documento no contiene ninguna firma ds:Signature.")
    if certificado is None:
        raise ErrorFirma("Se requiere el certificado para verificar la firma RSA.")
    clave_publica = certificado.certificado.public_key()

    resultados: List[Dict[str, bool]] = []
    for indice, firma in enumerate(firmas, start=1):
        signed_info = firma.find(f"{{{NS_DS}}}SignedInfo")
        if signed_info is None:
            raise ErrorFirma("La firma no contiene SignedInfo.")
        metodo_firma = signed_info.find(f"{{{NS_DS}}}SignatureMethod").get("Algorithm")
        algoritmo = _algoritmo_desde_uri(metodo_firma)

        referencia_doc = referencia_props = None
        for referencia in signed_info.findall(f"{{{NS_DS}}}Reference"):
            if referencia.get("Type") == NS_ETSI_TYPE:
                referencia_props = referencia
            else:
                referencia_doc = referencia
        if referencia_doc is None:
            raise ErrorFirma("La firma no referencia el comprobante.")

        uri = referencia_doc.get("URI", "")
        id_objetivo = uri.lstrip("#")
        objetivo = raiz if raiz.get("id") == id_objetivo else raiz.find(f".//*[@id='{id_objetivo}']")
        if objetivo is None:
            raise ErrorFirma(f"No se encontró el elemento referenciado {uri!r}.")

        copia = copy.deepcopy(objetivo)
        for previa in copia.findall(f"{{{NS_DS}}}Signature"):
            copia.remove(previa)
        if _b64(_digest(_c14n(copia), algoritmo)) != referencia_doc.find(
            f"{{{NS_DS}}}DigestValue"
        ).text:
            raise ErrorFirma("El digest del comprobante no coincide: el XML fue alterado.")

        props_ok = True
        if referencia_props is not None:
            id_props = referencia_props.get("URI", "").lstrip("#")
            nodo_props = raiz.find(f".//*[@Id='{id_props}']")
            if nodo_props is None:
                raise ErrorFirma("No se encontró el elemento SignedProperties referenciado.")
            props_ok = _b64(_digest(_c14n(nodo_props), algoritmo)) == referencia_props.find(
                f"{{{NS_DS}}}DigestValue"
            ).text
            if not props_ok:
                raise ErrorFirma("El digest de SignedProperties no coincide.")

        valor = firma.find(f"{{{NS_DS}}}SignatureValue").text
        try:
            clave_publica.verify(
                base64.b64decode(valor), _c14n(signed_info), padding.PKCS1v15(), _HASHES[algoritmo]()
            )
        except Exception as exc:  # noqa: BLE001
            raise ErrorFirma(f"La firma RSA no es válida: {exc}") from exc

        resultados.append(
            {"indice": indice, "documento": True, "signed_properties": props_ok, "rsa": True}
        )

    return {"valido": True, "firmas": resultados}
