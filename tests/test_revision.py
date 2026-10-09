"""Pruebas de la revisión previa a la emisión (certificado y datos).

No usan Django: la revisión es del núcleo y solo necesita un comprobante y un
certificado.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from factec import revision
from factec.catalogos import TarifaIva, TipoIdentificacion
from factec.comprobantes import Factura
from factec.emisor import EmisorElectronico
from factec.excepciones import ErrorRevision, ErrorValidacion
from factec.firma import Certificado, firmar_xml
from factec.modelos import Detalle, Emisor, Impuesto, Receptor
from factec.sri import fechas

from conftest import CLAVE_CERTIFICADO, RUC, _crear_p12

HOY = date(2026, 10, 8)


# ------------------------------------------------------------------ utilidades


@pytest.fixture
def certificado() -> Certificado:
    return Certificado.desde_bytes(_crear_p12(RUC), CLAVE_CERTIFICADO)


def _emisor(ruc: str = RUC) -> Emisor:
    return Emisor(ruc=ruc, razon_social="EMPRESA DE PRUEBAS S.A.", dir_matriz="QUITO")


def _factura(emisor: Emisor | None = None, fecha: date = HOY, **extra) -> Factura:
    return Factura(
        emisor=emisor or _emisor(),
        receptor=Receptor(
            razon_social="CLIENTE DE PRUEBA",
            identificacion="1712345678",
            tipo_identificacion=TipoIdentificacion.CEDULA,
            direccion="QUITO",
        ),
        detalles=[
            Detalle(
                descripcion="Servicio",
                cantidad=1,
                precio_unitario=Decimal("100.00"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
        fecha_emision=fecha,
        **extra,
    )


# ---------------------------------------------------------- revisar_certificado


def test_sin_certificado_lo_dice(certificado):
    informe = revision.revisar_certificado(None)

    assert informe.cargado is False
    assert informe.ok is False
    assert "No hay certificado de firma" in informe.problemas[0]
    assert informe.a_dict()["problemas"] == informe.problemas


def test_certificado_correcto_pasa_la_revision(certificado):
    informe = revision.revisar_certificado(certificado, emisor=_emisor())

    assert informe.ok is True
    assert informe.problemas == []
    assert informe.vigente is True
    assert informe.vencido is False
    assert informe.ruc == RUC
    assert informe.dias_restantes is not None and informe.dias_restantes > 30


def test_avisa_cuando_el_certificado_esta_por_vencer(certificado):
    """Con la firma a punto de vencer: aviso, no error (todavía se puede emitir)."""
    casi_vencido = revision.revisar_certificado(
        certificado, emisor=_emisor(), momento=datetime.now(timezone.utc) + timedelta(days=364)
    )

    assert casi_vencido.vigente is True
    assert casi_vencido.problemas == []
    assert any("vence" in aviso for aviso in casi_vencido.avisos)


def test_certificado_vencido_impide_emitir(certificado):
    vencido = revision.revisar_certificado(
        certificado, emisor=_emisor(), momento=datetime.now(timezone.utc) + timedelta(days=400)
    )

    assert vencido.ok is False
    assert vencido.vencido is True
    assert any("está vencido" in problema for problema in vencido.problemas)


def test_certificado_de_otro_contribuyente(certificado):
    """Firmar con el certificado de otro RUC: el SRI lo rechaza."""
    informe = revision.revisar_certificado(certificado, emisor=_emisor("1790012345001"))

    assert informe.ok is False
    assert any("no coincide con el del emisor" in problema for problema in informe.problemas)


# -------------------------------------------------------------- revisar_emision


def test_un_comprobante_correcto_puede_emitirse(certificado):
    informe = revision.revisar_emision(_factura(), certificado)

    assert informe.puede_emitir is True
    assert informe.ok is True
    assert informe.problemas == []
    assert informe.datos_validos is True
    assert informe.fecha_en_rango is True
    assert informe.clave_valida is True
    assert informe.descripcion_tipo == "Factura"
    assert "listo para emitir" in informe.resumen()


def test_sin_certificado_no_se_puede_emitir():
    informe = revision.revisar_emision(_factura(), None)

    assert informe.puede_emitir is False
    assert informe.certificado is not None
    assert any("No hay certificado" in problema for problema in informe.problemas)
    assert "no se puede emitir" in informe.resumen()


def test_la_fecha_futura_impide_emitir(certificado):
    informe = revision.revisar_emision(_factura(fecha=fechas.hoy_en_ecuador() + timedelta(days=1)), certificado)

    assert informe.puede_emitir is False
    assert informe.fecha_en_rango is False
    assert any("FECHA EMISIÓN EXTEMPORANEA" in problema for problema in informe.problemas)


def test_los_datos_invalidos_impiden_emitir(certificado):
    factura = _factura()
    factura.detalles[0].cantidad = Decimal("0")

    informe = revision.revisar_emision(factura, certificado)

    assert informe.puede_emitir is False
    assert informe.datos_validos is False
    assert any("reglas del SRI" in problema for problema in informe.problemas)


def test_avisa_cuando_el_comprobante_no_es_de_hoy(certificado):
    informe = revision.revisar_emision(_factura(fecha=HOY - timedelta(days=5)), certificado)

    assert informe.puede_emitir is True          # está dentro del rango del SRI
    assert any("hoy es el" in aviso for aviso in informe.avisos)


# ------------------------------------------------------------------ revisar_xml


def test_revisar_xml_de_un_comprobante_sin_firmar(certificado):
    xml = _factura().to_xml()

    informe = revision.revisar_xml(xml, certificado=certificado, emisor=_emisor())

    assert informe.puede_emitir is True
    assert informe.tipo == "01"
    assert informe.numero == "001-001-000000001"
    assert informe.clave_valida is True
    assert informe.clave_coincide is True
    assert informe.totales_cuadran is True


def test_revisar_xml_detecta_totales_que_no_cuadran(certificado):
    xml = _factura().to_xml().replace(
        "<importeTotal>115.00</importeTotal>", "<importeTotal>915.00</importeTotal>"
    )

    informe = revision.revisar_xml(xml, certificado=certificado, emisor=_emisor())

    assert informe.puede_emitir is False
    assert informe.totales_cuadran is False
    assert any("no coincide con el subtotal" in problema for problema in informe.problemas)


def test_revisar_xml_de_algo_que_no_es_un_comprobante(certificado):
    informe = revision.revisar_xml("<hola/>", certificado=certificado)

    assert informe.puede_emitir is False
    assert any("no se pudo leer" in problema for problema in informe.problemas)


def test_el_informe_se_puede_pasar_a_diccionario(certificado):
    informe = revision.revisar_xml(_factura().to_xml(), certificado=certificado, emisor=_emisor())
    datos = informe.a_dict()

    assert datos["puede_emitir"] is True
    assert datos["certificado"]["ruc"] == RUC
    assert datos["problemas"] == []
    assert datos["fecha_emision"] == HOY.isoformat()


# ---------------------------------------------------- el emisor revisa al emitir


def test_el_emisor_no_emite_sin_certificado():
    """Ni firma ni envía: se avisa antes, sin dejar el comprobante a medias."""
    emisor = EmisorElectronico(emisor=_emisor())      # sin certificado
    factura = emisor.factura(
        receptor=Receptor(identificacion="1712345678", razon_social="CLIENTE"),
        detalles=[
            Detalle(
                descripcion="Servicio", cantidad=1, precio_unitario=Decimal("100"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
    )

    with pytest.raises(ErrorRevision) as error:
        emisor.emitir(factura, enviar=False)

    assert "No hay certificado de firma" in str(error.value)
    assert error.value.informe is not None
    assert error.value.informe.puede_emitir is False


def test_el_emisor_puede_omitir_la_revision():
    """``revisar=False`` mantiene el comportamiento anterior (sin comprobación)."""
    emisor = EmisorElectronico(emisor=_emisor())
    factura = emisor.factura(
        receptor=Receptor(identificacion="1712345678", razon_social="CLIENTE"),
        detalles=[
            Detalle(
                descripcion="Servicio", cantidad=1, precio_unitario=Decimal("100"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
    )

    with pytest.raises(ErrorValidacion):     # al firmar, sin certificado
        emisor.emitir(factura, enviar=False, revisar=False)


def test_la_revision_del_emisor_se_puede_pedir_aparte(certificado):
    emisor = EmisorElectronico(emisor=_emisor(), certificado=certificado)
    factura = _factura()

    informe = emisor.revisar(factura)

    assert informe.puede_emitir is True
    assert isinstance(informe.a_dict(), dict)
    assert firmar_xml(factura.to_xml(), certificado)  # el XML es firmable
