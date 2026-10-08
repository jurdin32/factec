"""Pruebas de la consulta automática al SRI desde la configuración del emisor.

Sin red: se sustituye ``consultar_ruc`` por una respuesta prefabricada.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from factec.django import sri_datos
from factec.excepciones import ErrorFacturacion
from factec.sri.consulta_ruc import DatosRuc

RUC = "0703886697001"


def _datos(**cambios: Any) -> DatosRuc:
    base = dict(
        ruc=RUC,
        razon_social="URDIN GONZALEZ JOHNNY EDGAR",
        estado="ACTIVO",
        tipo_contribuyente="PERSONA NATURAL",
        regimen="RIMPE",
        categoria="NEGOCIO POPULAR",
        obligado_contabilidad=False,
        agente_retencion=False,
        contribuyente_especial=False,
        encontrado=True,
    )
    base.update(cambios)
    return DatosRuc(**base)  # type: ignore[arg-type]


class TestMapeo:
    def test_mapea_los_campos_que_publica_el_sri(self):
        campos = sri_datos.mapear_datos(_datos())
        assert campos == {
            "razon_social": "URDIN GONZALEZ JOHNNY EDGAR",
            "regimen": "RIMPE",
            "categoria": "NEGOCIO POPULAR",
            "obligado_contabilidad": False,
        }

    def test_los_campos_manuales_no_se_mapean(self):
        campos = sri_datos.mapear_datos(_datos())
        for campo in sri_datos.CAMPOS_MANUALES:
            if campo == "clave_certificado":
                continue
            assert campo not in campos

    def test_obligado_a_contabilidad_si(self):
        assert sri_datos.mapear_datos(_datos(obligado_contabilidad=True))[
            "obligado_contabilidad"
        ] is True


class TestAvisos:
    def test_rimpe_informa_del_texto_del_xml(self):
        avisos = sri_datos.avisos_de_datos(_datos())
        assert any("RIMPE" in a and "CONTRIBUYENTE NEGOCIO POPULAR" in a for a in avisos)

    def test_contribuyente_especial_pide_el_numero(self):
        avisos = sri_datos.avisos_de_datos(_datos(contribuyente_especial=True))
        assert any("contribuyente especial" in a and "número" in a for a in avisos)

    def test_agente_de_retencion_pide_el_numero(self):
        avisos = sri_datos.avisos_de_datos(_datos(agente_retencion=True))
        assert any("agente de retención" in a for a in avisos)

    def test_contribuyente_no_activo(self):
        avisos = sri_datos.avisos_de_datos(_datos(estado="SUSPENDIDO"))
        assert any("no está ACTIVO" in a for a in avisos)
