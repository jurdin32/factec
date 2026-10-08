"""Pruebas de los modelos de datos y del cálculo de totales."""

from __future__ import annotations

from decimal import Decimal

import pytest

from factec.catalogos import CodigoImpuesto, TarifaIva
from factec.comprobantes._comun import calcular_totales
from factec.comprobantes.base import formatear_decimal, formatear_fecha
from factec.excepciones import ErrorValidacion
from factec.modelos import (
    Detalle,
    Emisor,
    Impuesto,
    InfoAdicional,
    a_decimal,
    cuantizar,
)
from datetime import date


class TestConversionDecimal:
    def test_float_se_convierte_sin_error_binario(self):
        assert a_decimal(0.1) == Decimal("0.1")
        assert a_decimal(1.1) + a_decimal(2.2) == Decimal("3.3")

    def test_none_es_cero(self):
        assert a_decimal(None) == Decimal("0")

    @pytest.mark.parametrize("valor", ["10.5", Decimal("10.5")])
    def test_acepta_varios_tipos(self, valor):
        assert a_decimal(valor) == Decimal("10.5")

    def test_entero(self):
        assert a_decimal(10) == Decimal("10")

    def test_cuantizar_redondea_a_la_mitad_hacia_arriba(self):
        assert cuantizar(Decimal("2.345"), 2) == Decimal("2.35")
        assert cuantizar(Decimal("2.344"), 2) == Decimal("2.34")


class TestFormato:
    @pytest.mark.parametrize(
        "valor, decimales, esperado",
        [(Decimal("10"), 2, "10.00"), (Decimal("10.005"), 2, "10.01"), (1, 6, "1.000000")],
    )
    def test_formatear_decimal(self, valor, decimales, esperado):
        assert formatear_decimal(valor, decimales) == esperado

    def test_formatear_fecha_usa_barras(self):
        assert formatear_fecha(date(2026, 10, 8)) == "08/10/2026"
        assert formatear_fecha("2026-10-08") == "08/10/2026"


class TestImpuesto:
    def test_resolver_usa_la_tarifa_del_catalogo(self):
        impuesto = Impuesto(codigo_porcentaje=TarifaIva.IVA_15, base_imponible=Decimal("100"))
        impuesto.resolver()
        assert impuesto.tarifa == Decimal("15.00")
        assert impuesto.valor == Decimal("15.00")

    def test_resolver_no_pisa_valores_explicitos(self):
        impuesto = Impuesto(
            codigo_porcentaje=TarifaIva.IVA_15,
            base_imponible=Decimal("100"),
            tarifa=Decimal("15"),
            valor=Decimal("99.99"),
        )
        impuesto.resolver()
        assert impuesto.valor == Decimal("99.99")

    def test_tarifa_cero_para_exento(self):
        impuesto = Impuesto(codigo_porcentaje=TarifaIva.EXENTO, base_imponible=Decimal("50"))
        impuesto.resolver()
        assert impuesto.valor == Decimal("0.00")

    def test_codigo_valor_acepta_enum_y_texto(self):
        assert Impuesto(codigo=CodigoImpuesto.IVA).codigo_valor() == "2"
        assert Impuesto(codigo="3").codigo_valor() == "3"


class TestDetalle:
    def test_precio_total_sin_impuesto(self):
        detalle = Detalle(descripcion="X", cantidad=2, precio_unitario=Decimal("100"),
                          descuento=Decimal("5"))
        assert detalle.precio_total_sin_impuesto == Decimal("195.00")

    def test_impuestos_se_calculan_sobre_el_neto(self):
        detalle = Detalle(descripcion="X", cantidad=2, precio_unitario=Decimal("100"),
                          descuento=Decimal("5"), impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])
        impuesto = detalle.impuestos_resueltos()[0]
        assert impuesto.base_imponible == Decimal("195.00")
        assert impuesto.valor == Decimal("29.25")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"descripcion": "", "cantidad": 1, "precio_unitario": 1},
            {"descripcion": "X", "cantidad": 0, "precio_unitario": 1},
            {"descripcion": "X", "cantidad": 1, "precio_unitario": 1, "descuento": 5},
        ],
    )
    def test_validaciones(self, kwargs):
        detalle = Detalle(impuestos=[Impuesto()], **kwargs)
        with pytest.raises(ErrorValidacion):
            detalle.validar()

    def test_exige_al_menos_un_impuesto(self):
        with pytest.raises(ErrorValidacion):
            Detalle(descripcion="X", cantidad=1, precio_unitario=1).validar()


class TestCalcularTotales:
    def test_agrupa_impuestos_por_codigo_y_porcentaje(self):
        detalles = [
            Detalle(descripcion="A", cantidad=1, precio_unitario=Decimal("100"),
                    impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
            Detalle(descripcion="B", cantidad=1, precio_unitario=Decimal("50"),
                    impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
            Detalle(descripcion="C", cantidad=1, precio_unitario=Decimal("30"),
                    impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_0)]),
        ]
        totales = calcular_totales(detalles)
        assert totales["total_sin_impuestos"] == Decimal("180.00")
        assert totales["total_impuestos"] == Decimal("22.50")
        assert totales["importe_total"] == Decimal("202.50")
        codigos = [(t.codigo, t.codigo_porcentaje) for t in totales["total_con_impuestos"]]
        assert codigos == [("2", "0"), ("2", "4")]
        por_porcentaje = {t.codigo_porcentaje: t for t in totales["total_con_impuestos"]}
        assert por_porcentaje["4"].base_imponible == Decimal("150.00")
        assert por_porcentaje["4"].valor == Decimal("22.50")
        assert por_porcentaje["0"].valor == Decimal("0.00")

    def test_acumula_descuentos(self):
        detalles = [
            Detalle(descripcion="A", cantidad=1, precio_unitario=Decimal("100"),
                    descuento=Decimal("10"), impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
            Detalle(descripcion="B", cantidad=1, precio_unitario=Decimal("100"),
                    descuento=Decimal("5.5"), impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
        ]
        totales = calcular_totales(detalles)
        assert totales["total_descuento"] == Decimal("15.50")
        assert totales["total_sin_impuestos"] == Decimal("184.50")

    def test_error_indica_el_detalle(self):
        detalles = [Detalle(descripcion="A", cantidad=1, precio_unitario=1,
                            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)]),
                    Detalle(descripcion="", cantidad=1, precio_unitario=1,
                            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])]
        with pytest.raises(ErrorValidacion, match="Detalle 2"):
            calcular_totales(detalles)


class TestInfoAdicional:
    def test_omite_valores_vacios(self):
        datos = InfoAdicional({"Email": "a@b.com", "Vacio": "", "Nulo": None}).a_lista()
        assert datos == [{"nombre": "Email", "valor": "a@b.com"}]

    def test_limite_de_campos(self):
        with pytest.raises(ErrorValidacion):
            InfoAdicional({f"c{i}": "x" for i in range(20)}).a_lista()


class TestEmisor:
    def test_serie(self):
        assert Emisor(ruc="1790012345001", razon_social="X", dir_matriz="Y",
                      estab="1", pto_emi="2").serie == "001002"

    def test_validaciones(self):
        with pytest.raises(ErrorValidacion):
            Emisor(ruc="123", razon_social="X", dir_matriz="Y").validar()
        with pytest.raises(ErrorValidacion):
            Emisor(ruc="1790012345001", razon_social="", dir_matriz="Y").validar()

    def test_texto_rimpe(self):
        base = dict(ruc="1790012345001", razon_social="X", dir_matriz="Y")
        assert Emisor(**base).rimpe_texto is None
        assert Emisor(**base, contribuyente_rimpe=True).rimpe_texto == "CONTRIBUYENTE RÉGIMEN RIMPE"
        assert Emisor(**base, contribuyente_rimpe=True, regimen="RIMPE").rimpe_texto == (
            "CONTRIBUYENTE RÉGIMEN RIMPE"
        )
        assert Emisor(
            **base, contribuyente_rimpe=True, regimen="NEGOCIO POPULAR"
        ).rimpe_texto == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"
