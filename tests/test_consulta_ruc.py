"""Pruebas de la consulta del catastro de RUC (sin contactar la red)."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from factec.excepciones import ErrorSRI
from factec.sri import consultar_ruc, existe_ruc
from factec.sri.consulta_ruc import URL_CATASTRO, URL_EXISTE

RESPUESTA = {
    "contribuyentes": [
        {
            "numeroRuc": "0703886697001",
            "razonSocial": "URDIN GONZALEZ JOHNNY EDGAR",
            "estadoContribuyenteRuc": "ACTIVO",
            "actividadEconomicaPrincipal": "ACTIVIDADES DE DISEÑO DE SOFTWARE",
            "tipoContribuyente": "PERSONA NATURAL",
            "regimen": "RIMPE",
            "categoria": "NEGOCIO POPULAR",
            "obligadoLlevarContabilidad": "NO",
            "agenteRetencion": "NO",
            "contribuyenteEspecial": "NO",
            "contribuyenteFantasma": "NO",
            "transaccionesInexistente": "NO",
            "motivoCancelacionSuspension": None,
            "informacionFechasContribuyente": {
                "fechaInicioActividades": "2015-04-15 00:00:00.0",
                "fechaCese": "2019-01-31 00:00:00.0",
                "fechaReinicioActividades": "2021-06-27 00:00:00.0",
                "fechaActualizacion": "2026-07-21 12:00:00.0",
            },
        }
    ]
}


class RespuestaFalsa:
    def __init__(self, cuerpo: Any, text: str = "true", status_code: int = 200):
        self._cuerpo = cuerpo
        self.text = text
        self.status_code = status_code

    def json(self):
        if isinstance(self._cuerpo, Exception):
            raise self._cuerpo
        return self._cuerpo

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


class SesionFalsa:
    def __init__(self, respuesta: RespuestaFalsa, error: Exception | None = None):
        self.headers: Dict[str, str] = {}
        self.respuesta = respuesta
        self.error = error
        self.peticiones: List[Dict[str, Any]] = []

    def get(self, url, params=None, timeout=None):
        self.peticiones.append({"url": url, "params": params, "timeout": timeout})
        if self.error:
            raise self.error
        return self.respuesta

    @property
    def cabeceras(self) -> Dict[str, str]:
        return self.headers


def test_parsea_la_respuesta_del_sri():
    sesion = SesionFalsa(RespuestaFalsa(RESPUESTA))
    datos = consultar_ruc("0703886697001", session=sesion)

    assert datos.encontrado is True
    assert datos.ruc == "0703886697001"
    assert datos.razon_social == "URDIN GONZALEZ JOHNNY EDGAR"
    assert datos.activo is True
    assert datos.tipo_contribuyente == "PERSONA NATURAL"
    assert datos.es_rimpe is True
    assert datos.es_negocio_popular is True
    assert datos.obligado_contabilidad is False
    assert datos.agente_retencion is False
    assert datos.contribuyente_especial is False
    assert datos.fecha_inicio_actividades == "2015-04-15"
    assert datos.fecha_actualizacion == "2026-07-21"
    assert datos.crudo["numeroRuc"] == "0703886697001"


def test_usa_las_cabeceras_de_navegador():
    """El SRI bloquea a los clientes sin User-Agent de navegador."""
    sesion = SesionFalsa(RespuestaFalsa(RESPUESTA))
    consultar_ruc("0703886697001", session=sesion)
    assert "Mozilla" in sesion.headers["User-Agent"]
    assert sesion.peticiones[0]["url"] == URL_CATASTRO
    assert sesion.peticiones[0]["params"] == {"ruc": "0703886697001"}


def test_regimen_rimpe_texto():
    sesion = SesionFalsa(RespuestaFalsa(RESPUESTA))
    datos = consultar_ruc("0703886697001", session=sesion)
    assert datos.regimen_rimpe_texto == "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"


def test_regimen_rimpe_general():
    respuesta = {
        "contribuyentes": [dict(RESPUESTA["contribuyentes"][0], categoria="RIMPE EMPRENDEDOR")]
    }
    datos = consultar_ruc("0703886697001", session=SesionFalsa(RespuestaFalsa(respuesta)))
    assert datos.es_rimpe is True
    assert datos.es_negocio_popular is False
    assert datos.regimen_rimpe_texto == "CONTRIBUYENTE RÉGIMEN RIMPE"


def test_no_rimpe():
    respuesta = {"contribuyentes": [dict(RESPUESTA["contribuyentes"][0], regimen="GENERAL")]}
    datos = consultar_ruc("0703886697001", session=SesionFalsa(RespuestaFalsa(respuesta)))
    assert datos.es_rimpe is False
    assert datos.regimen_rimpe_texto is None


def test_ruc_no_encontrado():
    datos = consultar_ruc("1790012345001",
                          session=SesionFalsa(RespuestaFalsa({"contribuyentes": []})))
    assert datos.encontrado is False
    assert datos.razon_social == ""


def test_como_config():
    datos = consultar_ruc("0703886697001", session=SesionFalsa(RespuestaFalsa(RESPUESTA)))
    config = datos.como_config(estab="001")
    assert config["ruc"] == "0703886697001"
    assert config["obligado_contabilidad"] is False
    assert config["contribuyente_rimpe"] is True
    assert config["regimen"] == "NEGOCIO POPULAR"
    assert config["estab"] == "001"


def test_normaliza_el_ruc():
    sesion = SesionFalsa(RespuestaFalsa(RESPUESTA))
    consultar_ruc("070-3886697001", session=sesion)
    assert sesion.peticiones[0]["params"] == {"ruc": "0703886697001"}


@pytest.mark.parametrize("invalido", ["123", "", None, "070388669700"])
def test_ruc_invalido(invalido):
    with pytest.raises(ErrorSRI, match="13 dígitos"):
        consultar_ruc(invalido, session=SesionFalsa(RespuestaFalsa(RESPUESTA)))


def test_error_de_red():
    import requests

    with pytest.raises(ErrorSRI, match="No se pudo consultar"):
        consultar_ruc("0703886697001",
                      session=SesionFalsa(None, error=requests.ConnectionError("sin red")))  # type: ignore[arg-type]


def test_respuesta_no_json():
    with pytest.raises(ErrorSRI, match="no es JSON"):
        consultar_ruc("0703886697001",
                      session=SesionFalsa(RespuestaFalsa(ValueError("boom"))))


def test_http_error():
    import requests

    with pytest.raises(ErrorSRI):
        consultar_ruc("0703886697001", session=SesionFalsa(RespuestaFalsa({}, status_code=500)))


class TestExisteRuc:
    def test_existe(self):
        sesion = SesionFalsa(RespuestaFalsa({}, text="true"))
        assert existe_ruc("0703886697001", session=sesion) is True
        assert sesion.peticiones[0]["url"] == URL_EXISTE

    def test_no_existe(self):
        sesion = SesionFalsa(RespuestaFalsa({}, text="false"))
        assert existe_ruc("0703886697001", session=sesion) is False

    def test_ruc_invalido_no_consulta(self):
        sesion = SesionFalsa(RespuestaFalsa({}, text="true"))
        assert existe_ruc("123", session=sesion) is False
        assert sesion.peticiones == []
