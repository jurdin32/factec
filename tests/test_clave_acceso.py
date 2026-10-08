"""Pruebas de la clave de acceso (módulo 11)."""

from __future__ import annotations

from datetime import date

import pytest

from factec.catalogos import Ambiente, TipoComprobante, TipoEmision
from factec.clave_acceso import (
    LARGO_CLAVE_ACCESO,
    calcular_digito_verificador,
    descomponer_clave_acceso,
    generar_clave_acceso,
    normalizar_serie,
    validar_clave_acceso,
)
from factec.excepciones import ErrorValidacion

# Clave verificada contra el webservice del SRI y contra la estructura oficial.
CLAVE_CONOCIDA = "0810202601179001234500110010010000000011234567819"


class TestDigitoVerificador:
    def test_cero_cuando_el_resto_da_once(self):
        assert calcular_digito_verificador("0000000000") == "0"

    @pytest.mark.parametrize("cadena", ["0" * 48, "9" * 48, "123456789012345678901234567890123456789012345678"])
    def test_siempre_devuelve_un_digito(self, cadena):
        assert calcular_digito_verificador(cadena) in "0123456789"

    def test_rechaza_cadenas_no_numericas(self):
        with pytest.raises(ErrorValidacion):
            calcular_digito_verificador("12AB")


class TestGeneracion:
    def test_estructura_de_la_clave_conocida(self):
        clave = generar_clave_acceso(
            fecha_emision=date(2026, 10, 8),
            tipo_comprobante=TipoComprobante.FACTURA,
            ruc="1790012345001",
            ambiente=Ambiente.PRUEBAS,
            serie="001001",
            secuencial="1",
            codigo_numerico="12345678",
        )
        assert clave == CLAVE_CONOCIDA

    def test_largo_y_campos(self):
        clave = generar_clave_acceso(
            fecha_emision=date(2026, 10, 8),
            tipo_comprobante="01",
            ruc="1790012345001",
            ambiente=1,
            serie="001001",
            secuencial="42",
            codigo_numerico="87654321",
        )
        assert len(clave) == LARGO_CLAVE_ACCESO
        assert clave[:8] == "08102026"
        assert clave[8:10] == "01"
        assert clave[10:23] == "1790012345001"
        assert clave[23] == "1"
        assert clave[24:30] == "001001"
        assert clave[30:39] == "000000042"
        assert clave[39:47] == "87654321"
        assert clave[47] == TipoEmision.NORMAL.value
        assert validar_clave_acceso(clave)

    def test_acepta_varios_formatos_de_fecha(self):
        esperado = generar_clave_acceso(
            fecha_emision=date(2026, 3, 7), tipo_comprobante="01", ruc="1790012345001",
            ambiente=1, serie="001001", secuencial="1", codigo_numerico="1",
        )
        for formato in ("07/03/2026", "07-03-2026", "2026-03-07", "07032026"):
            assert generar_clave_acceso(
                fecha_emision=formato, tipo_comprobante="01", ruc="1790012345001",
                ambiente=1, serie="001001", secuencial="1", codigo_numerico="1",
            ) == esperado

    @pytest.mark.parametrize(
        "campo, valor",
        [
            ("ruc", "123"),
            ("ambiente", 3),
            ("tipo_comprobante", "99x"),
            ("secuencial", ""),
        ],
    )
    def test_rechaza_campos_invalidos(self, campo, valor):
        argumentos = dict(
            fecha_emision=date(2026, 10, 8), tipo_comprobante="01", ruc="1790012345001",
            ambiente=1, serie="001001", secuencial="1", codigo_numerico="12345678",
        )
        argumentos[campo] = valor
        with pytest.raises(ErrorValidacion):
            generar_clave_acceso(**argumentos)


class TestNormalizacion:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [("001001", "001001"), ("1", "000001"), ("001-001", "001001"), (1, "000001")],
    )
    def test_serie(self, entrada, esperado):
        assert normalizar_serie(entrada) == esperado

    def test_serie_demasiado_larga(self):
        with pytest.raises(ErrorValidacion):
            normalizar_serie("1234567890")


class TestValidacion:
    def test_clave_conocida_es_valida(self):
        assert validar_clave_acceso(CLAVE_CONOCIDA)

    @pytest.mark.parametrize("invalida", ["", "123", None, CLAVE_CONOCIDA[:-1] + "0"])
    def test_claves_invalidas(self, invalida):
        assert not validar_clave_acceso(invalida)

    def test_cuarenta_y_nueve_ceros_tiene_verificador_correcto(self):
        """El algoritmo acepta 49 ceros: el verificador de 48 ceros es 0."""
        assert validar_clave_acceso("0" * 49)

    def test_descomposicion(self):
        datos = descomponer_clave_acceso(CLAVE_CONOCIDA)
        assert datos["ruc"] == "1790012345001"
        assert datos["tipo_comprobante"] == "01"
        assert datos["ambiente"] == 1
        assert datos["estab"] == "001"
        assert datos["pto_emi"] == "001"
        assert datos["secuencial"] == "000000001"
        assert datos["codigo_numerico"] == "12345678"

    def test_descomposicion_rechaza_clave_invalida(self):
        with pytest.raises(ErrorValidacion):
            descomponer_clave_acceso("123")
