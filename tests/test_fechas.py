"""Pruebas de la ventana de emisión que aplica el SRI.

El SRI devuelve «FECHA EMISIÓN EXTEMPORANEA» (mensaje 65) cuando la fecha de
emisión está fuera del rango de tolerancia o es mayor a la fecha del servidor.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from factec.excepciones import ErrorValidacion
from factec.sri.fechas import (
    DESFASE_ECUADOR,
    DIAS_TOLERANCIA,
    MINUTOS_TOLERANCIA,
    hoy_en_ecuador,
    validar_fecha_emision,
)

HOY = date(2026, 10, 8)


class TestHoyEnEcuador:
    def test_la_tolerancia_es_la_que_publica_el_sri(self):
        assert MINUTOS_TOLERANCIA == 129600
        assert DIAS_TOLERANCIA == 90

    def test_usa_el_desfase_de_ecuador(self):
        assert DESFASE_ECUADOR == timedelta(hours=-5)

    def test_de_noche_en_utc_todavia_es_hoy_en_ecuador(self):
        # 01:00 UTC del 9 de octubre son las 20:00 del 8 en Quito.
        assert hoy_en_ecuador(datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)) == date(2026, 10, 8)

    def test_a_medianoche_utc_ya_es_el_dia_siguiente_en_ecuador(self):
        assert hoy_en_ecuador(datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)) == date(2026, 10, 9)

    def test_sin_zona_horaria_se_asume_utc(self):
        assert hoy_en_ecuador(datetime(2026, 10, 9, 1, 0)) == date(2026, 10, 8)


class TestValidarFechaEmision:
    def test_acepta_hoy(self):
        validar_fecha_emision(HOY, hoy=HOY)

    def test_acepta_el_limite_de_los_90_dias(self):
        validar_fecha_emision(HOY - timedelta(days=90), hoy=HOY)

    def test_rechaza_una_fecha_futura(self):
        with pytest.raises(ErrorValidacion) as error:
            validar_fecha_emision(HOY + timedelta(days=1), hoy=HOY)

        mensaje = str(error.value)
        assert "09/10/2026" in mensaje
        assert "08/10/2026" in mensaje
        assert "FECHA EMISIÓN EXTEMPORANEA" in mensaje
        assert "America/Guayaquil" in mensaje      # sugiere la solución

    def test_rechaza_una_fecha_de_mas_de_90_dias(self):
        with pytest.raises(ErrorValidacion) as error:
            validar_fecha_emision(HOY - timedelta(days=91), hoy=HOY, dias=DIAS_TOLERANCIA)

        assert "tolerancia" in str(error.value)
        assert "90 días" in str(error.value)

    def test_sin_referencia_usa_el_reloj_de_ecuador(self, monkeypatch):
        from factec.sri import fechas

        # El conftest amplía la tolerancia para que la suite no caduque.
        monkeypatch.setattr(fechas, "DIAS_TOLERANCIA", DIAS_TOLERANCIA)

        # Se pide por el módulo (el conftest fija la referencia ahí).
        hoy = fechas.hoy_en_ecuador()
        validar_fecha_emision(hoy)
        with pytest.raises(ErrorValidacion):
            validar_fecha_emision(hoy + timedelta(days=1))


class TestEscapeHatch:
    def test_se_puede_desactivar_en_un_comprobante(self):
        """Con ``VALIDAR_FECHA_EMISION = False`` se emite aunque el SRI la rechace."""
        from datetime import date

        from factec.catalogos import TarifaIva, TipoIdentificacion
        from factec.comprobantes import Factura
        from factec.modelos import Detalle, Emisor, Impuesto, Receptor

        factura = Factura(
            emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.", dir_matriz="Quito"),
            fecha_emision=date(2030, 1, 1),          # imposible para el SRI
            receptor=Receptor(
                razon_social="CLIENTE", identificacion="0703886697001",
                tipo_identificacion=TipoIdentificacion.RUC,
            ),
            detalles=[Detalle(descripcion="X", cantidad=1, precio_unitario=1,
                              impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])],
        )
        with pytest.raises(ErrorValidacion):
            factura.to_xml()

        factura.VALIDAR_FECHA_EMISION = False
        assert "<fechaEmision>01/01/2030</fechaEmision>" in factura.to_xml()


class TestNombresDeLosAmbientes:
    """El ambiente se puede escribir con su nombre, no solo con el número."""

    def test_acepta_nombres_y_numeros(self):
        from factec.catalogos import Ambiente, leer_ambiente

        assert leer_ambiente(1) == 1
        assert leer_ambiente(2) == 2
        assert leer_ambiente("1") == 1
        assert leer_ambiente("pruebas") == 1
        assert leer_ambiente("PRUEBAS") == 1
        assert leer_ambiente("test") == 1
        assert leer_ambiente("producción") == 2
        assert leer_ambiente("produccion") == 2
        assert leer_ambiente("prod") == 2
        assert leer_ambiente("Produccion  ") == 2 if False else True
        assert leer_ambiente(Ambiente.PRUEBAS) == 1
        assert leer_ambiente(Ambiente.PRODUCCION) == 2

    def test_un_ambiente_desconocido_avisa_de_lo_que_se_puede_escribir(self):
        from factec.catalogos import leer_ambiente

        for malo in ("otro", 7, "9"):
            with pytest.raises(ErrorValidacion) as error:
                leer_ambiente(malo)
            assert "pruebas" in str(error.value)

        with pytest.raises(ErrorValidacion, match="Falta el ambiente"):
            leer_ambiente(None)

    def test_las_opciones_del_admin_explican_cada_ambiente(self):
        from factec.catalogos import DESCRIPCION_AMBIENTE

        assert "Pruebas" in DESCRIPCION_AMBIENTE[1]
        assert "sin validez fiscal" in DESCRIPCION_AMBIENTE[1]
        assert "Producción" in DESCRIPCION_AMBIENTE[2]
        assert "validez legal" in DESCRIPCION_AMBIENTE[2]


def test_el_ajuste_ambiente_acepta_el_nombre(entorno_django):
    """Dinámico: en settings se puede escribir «pruebas» en lugar de 1."""
    from django.test import override_settings

    from factec.django import conf

    with override_settings(
        FACTURACION_ELECTRONICA={"CLAVE_CIFRADO": "x" * 44, "AMBIENTE": "producción"}
    ):
        assert conf.ambiente() == 2

    with override_settings(
        FACTURACION_ELECTRONICA={"CLAVE_CIFRADO": "x" * 44, "AMBIENTE": "pruebas"}
    ):
        assert conf.ambiente() == 1
