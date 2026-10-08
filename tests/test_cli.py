"""Pruebas de la interfaz de línea de comandos."""

from __future__ import annotations

import json

import pytest

from factec.__main__ import main
from factec.clave_acceso import validar_clave_acceso

CLAVE = "0810202601179001234500110010010000000011234567819"


class TestCatalogos:
    def test_json(self, capsys):
        assert main(["catalogos", "--json"]) == 0
        datos = json.loads(capsys.readouterr().out)
        assert datos["tipos_comprobante"]["01"] == "Factura"
        assert datos["formas_pago"]["19"].startswith("Tarjeta de crédito")
        assert datos["tarifas_iva"]["4"] == "IVA 15%"

    def test_texto(self, capsys):
        assert main(["catalogos"]) == 0
        salida = capsys.readouterr().out
        assert "== tipos_comprobante ==" in salida
        assert "Comprobante de retención" in salida


class TestClave:
    def test_genera_49_digitos(self, capsys):
        codigo = main(["clave", "--tipo", "01", "--ruc", "1790012345001",
                       "--serie", "001001", "--secuencial", "1", "--codigo", "12345678",
                       "--fecha", "08/10/2026"])
        assert codigo == 0
        clave = capsys.readouterr().out.strip()
        assert clave == CLAVE
        assert validar_clave_acceso(clave)

    def test_analiza(self, capsys):
        assert main(["clave", "--clave", CLAVE]) == 0
        datos = json.loads(capsys.readouterr().out)
        assert datos["ruc"] == "1790012345001"
        assert datos["tipo_comprobante"] == "01"

    def test_clave_invalida(self, capsys):
        assert main(["clave", "--clave", "123"]) == 2
        assert "inválida" in capsys.readouterr().err

    def test_faltan_argumentos(self, capsys):
        assert main(["clave", "--tipo", "01"]) == 2
        assert "Faltan argumentos" in capsys.readouterr().err


class TestEjemplo:
    def test_genera_los_seis(self, tmp_path, capsys):
        destino = tmp_path / "salida"
        assert main(["ejemplo", "--salida", str(destino)]) == 0
        archivos = sorted(p.name for p in destino.iterdir())
        assert archivos == [
            "comprobanteRetencion.xml", "factura.xml", "guiaRemision.xml",
            "liquidacionCompra.xml", "notaCredito.xml", "notaDebito.xml",
        ]
        assert "6 comprobantes" in capsys.readouterr().out

    def test_los_xml_tienen_su_clave(self, tmp_path):
        from lxml import etree

        destino = tmp_path / "salida"
        main(["ejemplo", "--salida", str(destino)])
        for archivo in destino.glob("*.xml"):
            raiz = etree.parse(str(archivo)).getroot()
            clave = raiz.findtext("infoTributaria/claveAcceso")
            assert validar_clave_acceso(clave), archivo.name


class TestFirmarYVerificar:
    def test_ida_y_vuelta(self, tmp_path, ruta_certificado, capsys):
        salida = tmp_path / "salida"
        main(["ejemplo", "--salida", str(salida)])
        original = salida / "factura.xml"
        firmado = tmp_path / "factura_firmada.xml"

        assert main(["firmar", str(original), "--certificado", str(ruta_certificado),
                     "--clave-clave", "clave-de-pruebas", "--salida", str(firmado)]) == 0
        assert "✅ Firmado" in capsys.readouterr().out
        assert firmado.exists()
        assert "Signature" in firmado.read_text(encoding="utf-8")

        assert main(["verificar", str(firmado), "--certificado", str(ruta_certificado),
                     "--clave-clave", "clave-de-pruebas"]) == 0
        resultado = json.loads(capsys.readouterr().out)
        assert resultado["valido"] is True

    def test_firmar_archivo_inexistente(self, tmp_path, ruta_certificado, capsys):
        assert main(["firmar", str(tmp_path / "no.xml"), "--certificado",
                     str(ruta_certificado), "--clave-clave", "clave-de-pruebas"]) == 2
        assert "No existe el archivo" in capsys.readouterr().err

    def test_clave_de_certificado_incorrecta(self, tmp_path, ruta_certificado, capsys):
        """Un error controlado se informa y devuelve 1, no traza hacia el usuario."""
        salida = tmp_path / "salida"
        main(["ejemplo", "--salida", str(salida)])
        assert main(["firmar", str(salida / "factura.xml"), "--certificado",
                     str(ruta_certificado), "--clave-clave", "mala"]) == 1
        assert "ErrorCertificado" in capsys.readouterr().err


class TestParser:
    def test_sin_comando(self, capsys):
        with pytest.raises(SystemExit):
            main([])

    def test_version(self, capsys):
        from factec import __version__

        with pytest.raises(SystemExit):
            main(["--version"])
        assert __version__ in capsys.readouterr().out

    def test_ambiente_invalido(self, capsys):
        with pytest.raises(SystemExit):
            main(["autorizar", CLAVE, "--ambiente", "otro"])
