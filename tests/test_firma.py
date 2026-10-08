"""Pruebas de la firma XAdES-BES."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from lxml import etree

from factec.comprobantes.factura import Factura
from factec.excepciones import ErrorCertificado, ErrorFirma
from factec.firma import (
    ALGORITMOS,
    NS_DS,
    NS_XADES,
    Certificado,
    firmar_xml,
    verificar_firma,
)
from factec.firma.xades import NS_ETSI_TYPE


@pytest.fixture
def factura_armada(emisor, receptor, detalle) -> Factura:
    return Factura(emisor=emisor, receptor=receptor, detalles=[detalle],
                   fecha_emision=date(2026, 10, 8), secuencial="1")


@pytest.fixture
def firmado(factura_armada, certificado) -> str:
    return firmar_xml(factura_armada.to_xml(), certificado)


class TestCertificado:
    def test_carga_desde_archivo(self, ruta_certificado):
        cert = Certificado.desde_archivo(ruta_certificado, "clave-de-pruebas")
        assert cert.numero_serie > 0
        assert "EMPRESA DE PRUEBAS" in cert.titular
        assert cert.certificado_base64()

    def test_clave_incorrecta(self, ruta_certificado):
        with pytest.raises(ErrorCertificado, match="No se pudo abrir"):
            Certificado.desde_archivo(ruta_certificado, "incorrecta")

    def test_archivo_inexistente(self):
        with pytest.raises(ErrorCertificado, match="No existe"):
            Certificado.desde_archivo("/ruta/que/no/existe.p12", "x")

    def test_vigencia(self, certificado):
        assert not certificado.vencido()
        certificado.validar_vigencia()
        assert certificado.vencido(datetime(2050, 1, 1, tzinfo=timezone.utc))
        with pytest.raises(ErrorCertificado, match="vencido"):
            certificado.validar_vigencia(datetime(2050, 1, 1, tzinfo=timezone.utc))


class TestFirma:
    def test_firma_y_verifica(self, firmado, certificado):
        resultado = verificar_firma(firmado, certificado)
        assert resultado["valido"] is True
        assert resultado["firmas"][0]["documento"] is True
        assert resultado["firmas"][0]["signed_properties"] is True
        assert resultado["firmas"][0]["rsa"] is True

    @pytest.mark.parametrize("algoritmo", sorted(ALGORITMOS))
    def test_todos_los_algoritmos(self, factura_armada, certificado, algoritmo):
        xml = firmar_xml(factura_armada.to_xml(), certificado, algoritmo=algoritmo)
        assert verificar_firma(xml, certificado)["valido"] is True
        assert ALGORITMOS[algoritmo]["digest"] in xml

    def test_algoritmo_desconocido(self, factura_armada, certificado):
        with pytest.raises(ErrorFirma, match="no soportado"):
            firmar_xml(factura_armada.to_xml(), certificado, algoritmo="md5")

    def test_orden_de_hijos_segun_xmldsig(self, firmado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        firma = raiz.find(f"{{{NS_DS}}}Signature")
        assert [etree.QName(h).localname for h in firma] == [
            "SignedInfo", "SignatureValue", "KeyInfo", "Object",
        ]

    def test_usa_los_prefijos_estandar(self, firmado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        firma = raiz.find(f"{{{NS_DS}}}Signature")
        assert firma.prefix == "ds"
        qualifying = firma.find(f".//{{{NS_XADES}}}QualifyingProperties")
        assert qualifying.prefix == "xades"
        # El atributo nsmap no debe colarse como atributo real.
        assert "nsmap" not in firma.attrib
        assert "nsmap" not in etree.tostring(firma).decode()

    def test_estructura_xades(self, firmado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        props = raiz.find(f".//{{{NS_XADES}}}SignedProperties")
        assert props is not None
        assert props.find(f"{{{NS_XADES}}}SignedSignatureProperties") is not None
        assert props.find(f".//{{{NS_XADES}}}SigningTime") is not None
        assert props.find(f".//{{{NS_XADES}}}CertDigest") is not None
        assert props.find(f".//{{{NS_XADES}}}IssuerSerial") is not None

    def test_dos_referencias_con_type_etsi(self, firmado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        referencias = raiz.findall(f".//{{{NS_DS}}}Reference")
        assert len(referencias) == 2
        tipos = [r.get("Type") for r in referencias]
        assert NS_ETSI_TYPE in tipos
        assert referencias[0].get("URI") == "#comprobante"

    def test_firma_es_el_ultimo_elemento(self, firmado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        assert etree.QName(raiz[-1]).localname == "Signature"

    def test_detecta_manipulacion(self, firmado, certificado):
        manipulado = firmado.replace("100.00", "999.00")
        assert manipulado != firmado
        with pytest.raises(ErrorFirma, match="digest del comprobante"):
            verificar_firma(manipulado, certificado)

    def test_detecta_manipulacion_de_signed_properties(self, firmado, certificado):
        raiz = etree.fromstring(firmado.encode("utf-8"))
        tiempo = raiz.find(f".//{{{NS_XADES}}}SigningTime")
        tiempo.text = "2000-01-01T00:00:00+00:00"
        manipulado = etree.tostring(raiz, encoding="unicode")
        with pytest.raises(ErrorFirma, match="SignedProperties"):
            verificar_firma(manipulado, certificado)

    def test_exige_id_comprobante(self, certificado):
        with pytest.raises(ErrorFirma, match="id="):
            firmar_xml('<?xml version="1.0"?><otro id="x"><a/></otro>', certificado)

    def test_reemplaza_firma_previa(self, factura_armada, certificado):
        una = firmar_xml(factura_armada.to_xml(), certificado)
        dos = firmar_xml(una, certificado)
        raiz = etree.fromstring(dos.encode("utf-8"))
        assert len(raiz.findall(f"{{{NS_DS}}}Signature")) == 1
        assert verificar_firma(dos, certificado)["valido"] is True

    def test_xml_invalido(self, certificado):
        with pytest.raises(ErrorFirma, match="no es válido"):
            firmar_xml("<factura id='comprobante'>", certificado)

    def test_sin_firma(self, factura_armada, certificado):
        with pytest.raises(ErrorFirma, match="ninguna firma"):
            verificar_firma(factura_armada.to_xml(), certificado)

    def test_verificar_requiere_certificado(self, firmado):
        with pytest.raises(ErrorFirma, match="certificado"):
            verificar_firma(firmado, None)

    def test_fecha_de_firma_explicita(self, factura_armada, certificado):
        momento = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
        xml = firmar_xml(factura_armada.to_xml(), certificado, fecha_firma=momento)
        raiz = etree.fromstring(xml.encode("utf-8"))
        assert raiz.find(f".//{{{NS_XADES}}}SigningTime").text == momento.isoformat()

    def test_firmar_desde_el_comprobante(self, factura_armada, certificado):
        xml = factura_armada.firmar(certificado)
        assert verificar_firma(xml, certificado)["valido"] is True


class TestVerificacionIndependiente:
    def test_signxml_verifica_la_firma(self, factura_armada, certificado):
        """Comprobación con una librería externa (signxml), si está instalada."""
        signxml = pytest.importorskip("signxml")
        from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod

        xml = firmar_xml(factura_armada.to_xml(), certificado, algoritmo="sha1")
        configuracion = SignatureConfiguration(
            require_x509=True,
            expect_references=2,
            signature_methods={SignatureMethod.RSA_SHA1},
            digest_algorithms={DigestAlgorithm.SHA1},
        )
        signxml.XMLVerifier().verify(
            xml.encode("utf-8"),
            x509_cert=certificado.certificado,
            expect_config=configuracion,
        )
