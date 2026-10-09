"""Verificación de comprobantes: firma, clave, fecha, totales y estado en el SRI."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from factec import verificacion
from factec.catalogos import TarifaIva, TipoIdentificacion
from factec.comprobantes import Factura
from factec.excepciones import ErrorFirma
from factec.firma import Certificado, certificado_del_xml, firmar_xml
from factec.lectura import leer_comprobante
from factec.modelos import Detalle, Emisor, Impuesto, Receptor
from factec.verificacion import (
    InformeVerificacion,
    verificar_clave,
    verificar_comprobante,
    verificar_totales,
)

from conftest import CLAVE_CERTIFICADO, RUC, _crear_p12

FECHA = date(2026, 9, 15)


@pytest.fixture
def certificado():
    from datetime import datetime, timezone

    return Certificado.desde_bytes(
        _crear_p12(RUC, CLAVE_CERTIFICADO), CLAVE_CERTIFICADO
    )


def _factura(**cambios):
    datos = dict(
        emisor=Emisor(ruc=RUC, razon_social="ACME S.A.", dir_matriz="Quito"),
        ambiente=1,
        fecha_emision=FECHA,
        secuencial="42",
        receptor=Receptor(
            identificacion="0703886697001",
            razon_social="CLIENTE EJEMPLO",
            tipo_identificacion=TipoIdentificacion.RUC,
        ),
        detalles=[
            Detalle(
                descripcion="Servicio",
                cantidad=Decimal("2"),
                precio_unitario=Decimal("100.00"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
    )
    datos.update(cambios)
    return Factura(**datos)


def _firmada(certificado) -> str:
    return firmar_xml(_factura().to_xml(), certificado)


class TestVerificarComprobante:
    def test_una_factura_correcta_pasa_todo(self, certificado):
        informe = verificar_comprobante(_firmada(certificado), hoy=FECHA)

        assert informe.ok is True
        assert informe.problemas == []
        assert informe.firma is True
        assert informe.clave_valida is True
        assert informe.clave_coincide is True
        assert informe.fecha_en_rango is True
        assert informe.totales_cuadran is True
        assert informe.version_esperada is True
        assert informe.tipo == "01"
        assert informe.descripcion_tipo == "Factura"
        assert informe.importe_total == Decimal("230.00")

    def test_usa_el_certificado_que_viene_en_el_xml(self, certificado):
        xml = _firmada(certificado)

        informe = verificar_comprobante(xml, hoy=FECHA)

        assert informe.certificado is not None
        assert informe.certificado.certificado == certificado.certificado
        assert informe.certificado.ruc == RUC
        assert informe.certificado.vencido() is False

    def test_detecta_un_xml_alterado(self, certificado):
        xml = _firmada(certificado).replace(
            "<importeTotal>230.00</importeTotal>", "<importeTotal>930.00</importeTotal>"
        )

        informe = verificar_comprobante(xml, hoy=FECHA)

        assert informe.ok is False
        assert informe.firma is False
        assert any("alterado" in problema for problema in informe.problemas)
        assert any("no coincide con el subtotal" in problema for problema in informe.problemas)

    def test_detecta_una_clave_de_otro_comprobante(self, certificado):
        """Una clave válida pero de otro documento: el dígito cuadra, los datos no."""
        leido = leer_comprobante(_firmada(certificado))
        otra = _factura(secuencial="43")           # otra factura, otra clave válida

        assert verificar_clave(leido) == (True, True)

        leido.clave_acceso = otra.clave            # se «pega» una clave ajena
        valida, coincide = verificar_clave(leido)

        assert valida is True          # el dígito verificador está bien…
        assert coincide is False       # …pero no es de este comprobante

    def test_detecta_una_clave_con_el_digito_mal(self, certificado):
        leido = leer_comprobante(_firmada(certificado))
        leido.clave_acceso = leido.clave_acceso[:-1] + (
            "0" if leido.clave_acceso[-1] != "0" else "1"
        )

        assert verificar_clave(leido) == (False, False)

    def test_detecta_una_fecha_fuera_de_la_ventana(self, certificado):
        informe = verificar_comprobante(_firmada(certificado), hoy=FECHA + timedelta(days=120))

        assert informe.fecha_en_rango is False
        assert any("ventana del SRI" in problema for problema in informe.problemas)

    def test_sin_exigir_firma_avisa(self):
        """Un borrador propio (sin firmar) se puede revisar igual."""
        informe = verificar_comprobante(_factura().to_xml(), hoy=FECHA, exigir_firma=False)

        assert informe.firma is None
        assert informe.ok is True

    def test_un_comprobante_sin_firma_no_sirve(self):
        informe = verificar_comprobante(_factura().to_xml(), hoy=FECHA)

        assert informe.ok is False
        assert any("firma" in problema.lower() for problema in informe.problemas)

    def test_un_xml_que_no_es_comprobante(self):
        informe = verificar_comprobante("<hola/>", hoy=FECHA)

        assert informe.ok is False
        assert informe.comprobante is None
        assert informe.problemas

    def test_a_dict_es_serializable(self, certificado):
        import json

        datos = verificar_comprobante(_firmada(certificado), hoy=FECHA).a_dict()

        json.dumps(datos)
        assert datos["ok"] is True
        assert datos["certificado"]["ruc"] == RUC
        assert datos["importe_total"] == "230.00"


class TestVerificarTotales:
    def test_detecta_lineas_que_no_cuadran(self, certificado):
        leido = leer_comprobante(_firmada(certificado))
        leido.detalles[0].precio_total_sin_impuesto = Decimal("999.00")

        problemas = verificar_totales(leido)

        assert any("no coincide con la suma de las líneas" in p for p in problemas)

    def test_detecta_un_total_mal_sumado(self, certificado):
        leido = leer_comprobante(_firmada(certificado))
        leido.totales.importe_total = Decimal("230.50")

        assert any("no coincide" in p for p in verificar_totales(leido))


class TestCertificadoIncrustado:
    def test_sin_certificado_incrustado_no_se_puede_comprobar_el_rsa(self, certificado):
        import re

        xml = re.sub(
            r"<ds:X509Certificate>.*?</ds:X509Certificate>", "", _firmada(certificado), flags=re.S
        )

        with pytest.raises(ErrorFirma, match="certificado"):
            from factec.firma import verificar_firma

            verificar_firma(xml, None)

    def test_un_xml_sin_firma_no_tiene_certificado(self):
        assert certificado_del_xml(_factura().to_xml()) is None


class TestVerificarEnElSRI:
    def test_consulta_por_clave(self):
        from factec.sri.soap import Autorizacion, RespuestaAutorizacion

        class Cliente:
            def __init__(self):
                self.consultadas = []

            def autorizar(self, clave):
                self.consultadas.append(clave)
                return RespuestaAutorizacion(
                    clave_acceso_consultada=clave,
                    numero_comprobantes=1,
                    autorizaciones=[
                        Autorizacion(
                            estado="AUTORIZADO",
                            numero_autorizacion=clave,
                            ambiente="PRUEBAS",
                            comprobante=_factura().to_xml(),
                        )
                    ],
                    crudo="<respuesta/>",
                )

        cliente = Cliente()
        autorizacion = verificacion.verificar_en_el_sri("0810202601" + "0" * 39, cliente=cliente)

        assert autorizacion.autorizada is True
        assert cliente.consultadas

    def test_una_clave_sin_autorizacion(self):
        from factec.sri.soap import RespuestaAutorizacion

        class Cliente:
            def autorizar(self, clave):
                return RespuestaAutorizacion(clave_acceso_consultada=clave, crudo="<respuesta/>")

        autorizacion = verificacion.verificar_en_el_sri("0810202601" + "0" * 39, cliente=Cliente())

        assert autorizacion.autorizada is False
        assert autorizacion.estado == "NO ENCONTRADO"
