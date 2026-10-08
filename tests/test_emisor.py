"""Pruebas de la fachada :class:`EmisorElectronico`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from factec.catalogos import Ambiente, MotivoTraslado, TarifaIva
from factec.emisor import EmisorElectronico
from factec.excepciones import ErrorValidacion
from factec.modelos import (
    Destinatario,
    Detalle,
    DetalleGuia,
    Impuesto,
    Motivo,
)
from factec.sri.soap import RespuestaAutorizacion, Autorizacion

FECHA = date(2026, 10, 8)


@pytest.fixture
def emisor_electronico(emisor, ruta_certificado):
    return EmisorElectronico(
        emisor=emisor,
        certificado=ruta_certificado,
        clave_certificado="clave-de-pruebas",
        ambiente=Ambiente.PRUEBAS,
    )


@pytest.fixture
def detalle_simple() -> Detalle:
    return Detalle(descripcion="Servicio", cantidad=1, precio_unitario=Decimal("100.00"),
                   impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])


class TestConstruccion:
    def test_factura(self, emisor_electronico, receptor, detalle_simple):
        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                            fecha_emision=FECHA)
        assert factura.TIPO == "01"
        assert len(factura.clave) == 49
        assert factura.fecha_emision == FECHA
        assert factura.ambiente == int(Ambiente.PRUEBAS)

    def test_secuencial_autoincremental(self, emisor_electronico, receptor, detalle_simple):
        uno = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple], fecha_emision=FECHA)
        dos = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple], fecha_emision=FECHA)
        assert uno.secuencial_normalizado == "000000001"
        assert dos.secuencial_normalizado == "000000002"

    def test_secuencial_explicito(self, emisor_electronico, receptor, detalle_simple):
        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                             fecha_emision=FECHA, secuencial="77")
        assert factura.secuencial_normalizado == "000000077"

    def test_liquidacion_compra(self, emisor_electronico, receptor, detalle_simple):
        lc = emisor_electronico.liquidacion_compra(proveedor=receptor, detalles=[detalle_simple],
                                                   fecha_emision=FECHA)
        assert lc.TIPO == "03"

    def test_nota_credito(self, emisor_electronico, receptor, detalle_simple):
        nc = emisor_electronico.nota_credito(
            receptor=receptor, detalles=[detalle_simple], motivo="Devolución",
            cod_doc_modificado="01", num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), fecha_emision=FECHA,
        )
        assert nc.TIPO == "04"

    def test_nota_debito(self, emisor_electronico, receptor):
        nd = emisor_electronico.nota_debito(
            receptor=receptor, motivos=[Motivo(razon="Intereses", valor=Decimal("10.00"))],
            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15, base_imponible=Decimal("10.00"))],
            total_sin_impuestos=Decimal("10.00"), cod_doc_modificado="01",
            num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), fecha_emision=FECHA,
        )
        assert nd.TIPO == "05"

    def test_retencion(self, emisor_electronico, receptor, doc_sustento):
        ret = emisor_electronico.retencion(sujeto_retenido=receptor, docs_sustento=[doc_sustento],
                                           periodo_fiscal=date(2026, 9, 1), fecha_emision=FECHA)
        assert ret.TIPO == "07"

    def test_guia_remision(self, emisor_electronico):
        guia = emisor_electronico.guia_remision(
            dir_partida="Quito", razon_social_transportista="TRANSPORTES",
            ruc_transportista="1790012345001", placa="ABC1234",
            fecha_ini_transporte=FECHA, fecha_fin_transporte=date(2026, 10, 9),
            destinatarios=[Destinatario(
                razon_social="DESTINO", identificacion="0703886697001", direccion="Guayaquil",
                motivo_traslado=MotivoTraslado.VENTA,
                detalles=[DetalleGuia(descripcion="Caja", cantidad=1)])],
            fecha_emision=FECHA,
        )
        assert guia.TIPO == "06"


class TestFirmaYEnvio:
    def test_firmar(self, emisor_electronico, receptor, detalle_simple, certificado):
        from factec.firma import verificar_firma

        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                            fecha_emision=FECHA)
        xml = emisor_electronico.firmar(factura)
        assert verificar_firma(xml, certificado)["valido"] is True

    def test_emitir_sin_enviar(self, emisor_electronico, receptor, detalle_simple, certificado):
        from factec.firma import verificar_firma

        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                            fecha_emision=FECHA)
        resultado = emisor_electronico.emitir(factura, enviar=False)
        assert resultado.clave_acceso == factura.clave
        assert resultado.xml_firmado
        assert verificar_firma(resultado.xml_firmado, certificado)["valido"] is True
        assert resultado.recepcion is None
        assert resultado.autorizada is False

    def test_enviar_usa_el_cliente(self, emisor_electronico, receptor, detalle_simple, monkeypatch):
        from factec.sri.soap import RespuestaRecepcion

        llamadas = {}

        class ClienteFalso:
            def validar_comprobante(self, xml):
                llamadas["con_firma"] = "Signature" in xml
                return RespuestaRecepcion(estado="RECIBIDA", clave_acceso=llamadas.get("clave", ""))

            def esperar_autorizacion(self, clave, intentos=5, espera=3.0):
                llamadas["autorizada"] = clave
                autorizacion = Autorizacion(estado="AUTORIZADO", numero_autorizacion=clave)
                return RespuestaAutorizacion(
                    clave_acceso_consultada=clave, numero_comprobantes=1,
                    autorizaciones=[autorizacion],
                )

        emisor_electronico._cliente = ClienteFalso()
        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                            fecha_emision=FECHA)
        llamadas["clave"] = factura.clave
        resultado = emisor_electronico.emitir(factura)

        assert resultado.autorizada is True
        assert resultado.recepcionada is True
        assert llamadas["con_firma"] is True
        assert llamadas["autorizada"] == factura.clave
        resultado.lanzar_si_fallo()

    def test_emitir_detecta_recepcion_devuelta(self, emisor_electronico, receptor,
                                              detalle_simple, monkeypatch):
        from factec.excepciones import ErrorRecepcion
        from factec.sri.soap import Mensaje, RespuestaRecepcion

        class ClienteFalso:
            def validar_comprobante(self, xml):
                return RespuestaRecepcion(estado="DEVUELTA", clave_acceso="N/A",
                                          mensajes=[Mensaje("35", "Estructura inválida")])

            def esperar_autorizacion(self, *args, **kwargs):
                raise AssertionError("no debería llegar a autorizar")

        emisor_electronico._cliente = ClienteFalso()
        factura = emisor_electronico.factura(receptor=receptor, detalles=[detalle_simple],
                                            fecha_emision=FECHA)
        with pytest.raises(ErrorRecepcion):
            emisor_electronico.emitir(factura)


class TestCertificadoRequerido:
    def test_sin_certificado(self, emisor, receptor, detalle_simple):
        electronico = EmisorElectronico(emisor=emisor)
        factura = electronico.factura(receptor=receptor, detalles=[detalle_simple], fecha_emision=FECHA)
        with pytest.raises(ErrorValidacion, match="certificado"):
            electronico.firmar(factura)
        assert electronico._certificado is None

    def test_certificado_vencido_no_se_acepta_al_construir(self, emisor, ruta_certificado, monkeypatch):
        from factec import firma

        original = firma.Certificado.validar_vigencia

        def explotar(self, momento=None):
            from factec.excepciones import ErrorCertificado

            raise ErrorCertificado("El certificado está vencido")

        monkeypatch.setattr(firma.Certificado, "validar_vigencia", explotar)
        with pytest.raises(Exception, match="vencido"):
            EmisorElectronico(emisor=emisor, certificado=ruta_certificado,
                              clave_certificado="clave-de-pruebas")
        monkeypatch.setattr(firma.Certificado, "validar_vigencia", original)
