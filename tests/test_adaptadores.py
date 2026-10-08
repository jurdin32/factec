"""Pruebas de los adaptadores: de un objeto del proyecto a un comprobante del SRI.

No usan Django: el adaptador solo necesita objetos con atributos (o diccionarios),
así que se prueba con objetos sencillos y con ``dataclass``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

import pytest

from factec.catalogos import FormaPago, TarifaIva, TipoComprobante
from factec.django import adaptadores
from factec.excepciones import ErrorValidacion
from factec.modelos import Emisor, Receptor

FECHA = date(2026, 10, 8)
EMISOR = Emisor(ruc="1790012345001", razon_social="EMPRESA DE PRUEBAS S.A.",
                dir_matriz="QUITO", obligado_contabilidad=True)

def _adapt(clase, **kwargs: Any):
    """Adaptador con emisor y ambiente explícitos (así no depende de Django)."""
    return clase(emisor=EMISOR, ambiente=1, **kwargs)


BASE: Dict[str, Any] = {
    "emisor": EMISOR,
    "ambiente": 1,
    "fecha_emision": FECHA,
    "secuencial": "1",
}


# ------------------------------------------------------ objeto por convención


@dataclass
class ClienteSimple:
    nombre: str
    ruc: str
    direccion: str = ""


@dataclass
class LineaSimple:
    concepto: str
    cant: Decimal
    valor: Decimal
    descuento: Decimal = Decimal("0")
    sku: str = ""
    iva_codigo: str = TarifaIva.IVA_15


@dataclass
class VentaSimple:
    cliente: ClienteSimple
    fecha: date
    renglones: List[LineaSimple] = field(default_factory=list)
    observaciones: str = ""
    forma_pago: str = FormaPago.SIN_SISTEMA_FINANCIERO

    @property
    def numero(self) -> str:
        return "42"


def _venta() -> VentaSimple:
    return VentaSimple(
        cliente=ClienteSimple(nombre="CLIENTE DE PRUEBA", ruc="0703886697001",
                              direccion="GUAYAQUIL"),
        fecha=FECHA,
        renglones=[
            LineaSimple(concepto="Servicio", cant=Decimal("2"), valor=Decimal("100"),
                        sku="SRV1"),
            LineaSimple(concepto="Producto exento", cant=Decimal("1"), valor=Decimal("50"),
                        iva_codigo=TarifaIva.EXENTO),
        ],
        observaciones="Nota de prueba",
    )


class TestConvencionFactura:
    def test_encuentra_receptor_y_lineas_con_otros_nombres(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        assert comprobante.receptor.razon_social == "CLIENTE DE PRUEBA"
        assert comprobante.receptor.identificacion == "0703886697001"
        assert len(comprobante.detalles) == 2

    def test_usa_el_ruc_y_deduce_el_tipo_identificacion(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        # 13 dígitos -> RUC (04)
        assert comprobante.receptor.tipo_identificacion == "04"

    def test_deduce_cedula_y_consumidor_final(self):
        deducir = adaptadores.AdaptadorComprobante._deducir_tipo_identificacion
        assert deducir("0703886697") == "05"        # 10 dígitos -> cédula
        assert deducir("") == "07"                  # vacío -> consumidor final
        assert deducir("X1234567") == "06"          # con letras -> pasaporte

    def test_convierte_las_lineas(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        primera, segunda = comprobante.detalles
        assert primera.descripcion == "Servicio"
        assert primera.cantidad == Decimal("2")
        assert primera.precio_unitario == Decimal("100")
        assert primera.codigo_principal == "SRV1"
        assert primera.impuestos[0].porcentaje_valor() == TarifaIva.IVA_15
        assert segunda.impuestos[0].porcentaje_valor() == TarifaIva.EXENTO

    def test_fecha_secuencial_y_observaciones(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        assert comprobante.fecha_emision == FECHA
        assert comprobante.secuencial_normalizado == "000000042"
        assert comprobante.info_adicional == {"Observaciones": "Nota de prueba"}

    def test_pagos_desde_un_codigo(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        assert comprobante.pagos[0].forma_valor() == FormaPago.SIN_SISTEMA_FINANCIERO

    def test_calcula_los_totales(self):
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        totales = comprobante.calcular()
        assert totales["total_sin_impuestos"] == Decimal("250.00")
        assert totales["importe_total"] == Decimal("280.00")

    def test_exige_receptor(self):
        venta = _venta()
        venta.cliente = None
        with pytest.raises(ErrorValidacion, match="receptor"):
            _adapt(adaptadores.AdaptadorFactura).comprobante(venta)


class TestValoresFlexibles:
    def test_acepta_diccionarios(self):
        datos = {
            "cliente": {"razon_social": "CLIENTE", "identificacion": "1790012345001"},
            "fecha": FECHA,
            "secuencial": 1,
            "detalles": [
                {"descripcion": "Servicio", "cantidad": 1, "precio_unitario": 100,
                 "codigo_porcentaje_iva": "4"}
            ],
        }
        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(datos)
        assert comprobante.receptor.razon_social == "CLIENTE"
        assert comprobante.detalles[0].precio_unitario == 100

    def test_acepta_metodos_sin_argumentos(self):
        class ConMetodos:
            emisor = EMISOR
            ambiente = 1
            fecha = FECHA
            secuencial = "7"

            def cliente(self):
                return {"razon_social": "X", "identificacion": "1790012345001"}

            def detalles(self):
                return [{"descripcion": "Y", "cantidad": 1, "precio_unitario": 10}]

        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(ConMetodos())
        assert comprobante.secuencial_normalizado == "000000007"
        assert comprobante.detalles[0].descripcion == "Y"

    def test_acepta_modelos_del_paquete_directamente(self):
        from factec.modelos import Detalle

        class Venta:
            emisor = EMISOR
            ambiente = 1
            fecha_emision = FECHA
            secuencial = "1"
            receptor = Receptor(razon_social="X", identificacion="1790012345001")
            detalles = [Detalle(descripcion="L", cantidad=1, precio_unitario=5,
                                impuestos=[])]

        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(Venta())
        assert isinstance(comprobante.detalles[0], Detalle)

    def test_acepta_administradores_con_all(self):
        class Manager:
            def all(self):
                return [{"descripcion": "L", "cantidad": 1, "precio_unitario": 5}]

        class Venta:
            emisor = EMISOR
            ambiente = 1
            fecha_emision = FECHA
            secuencial = "1"
            receptor = {"razon_social": "X", "identificacion": "1790012345001"}
            detalles = Manager()

        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(Venta())
        assert len(comprobante.detalles) == 1

    def test_fecha_en_texto(self):
        class Venta:
            emisor = EMISOR
            ambiente = 1
            fecha = "08/10/2026"
            secuencial = "1"
            receptor = {"razon_social": "X", "identificacion": "1790012345001"}
            detalles = [{"descripcion": "L", "cantidad": 1, "precio_unitario": 5}]

        assert _adapt(adaptadores.AdaptadorFactura).comprobante(Venta()).fecha_emision == FECHA

    def test_fecha_invalida(self):
        class Venta:
            emisor = EMISOR
            ambiente = 1
            fecha = "no es una fecha"
            secuencial = "1"
            receptor = {"razon_social": "X", "identificacion": "1790012345001"}
            detalles = [{"descripcion": "L", "cantidad": 1, "precio_unitario": 5}]

        with pytest.raises(ErrorValidacion, match="fecha"):
            _adapt(adaptadores.AdaptadorFactura).comprobante(Venta())


class TestImpuestos:
    def test_codigo_como_texto(self):
        impuesto = _adapt(adaptadores.AdaptadorFactura).impuesto_desde("0")
        assert impuesto.porcentaje_valor() == "0"
        impuesto.resolver()
        assert impuesto.valor == Decimal("0.00")

    def test_tarifa_numerica(self):
        impuesto = _adapt(adaptadores.AdaptadorFactura).impuesto_desde(15)
        assert impuesto.porcentaje_valor() == TarifaIva.IVA_15
        assert _adapt(adaptadores.AdaptadorFactura).impuesto_desde(0).porcentaje_valor() == "0"
        assert _adapt(adaptadores.AdaptadorFactura).impuesto_desde(5).porcentaje_valor() == "5"

    def test_objeto_de_impuesto_relacionado(self):
        class ImpuestoBD:
            codigo_porcentaje = "4"

        impuesto = _adapt(adaptadores.AdaptadorFactura).impuesto_desde(ImpuestoBD())
        assert impuesto.porcentaje_valor() == "4"

    def test_por_defecto_iva_15(self):
        assert _adapt(adaptadores.AdaptadorFactura).impuesto_desde(None).porcentaje_valor() == (
            TarifaIva.IVA_15
        )


class TestOtrosTipos:
    def test_liquidacion_compra(self):
        comprobante = _adapt(adaptadores.AdaptadorLiquidacionCompra).comprobante(_venta())
        assert comprobante.TIPO == TipoComprobante.LIQUIDACION_COMPRA.value
        assert comprobante.proveedor.razon_social == "CLIENTE DE PRUEBA"

    def test_nota_credito(self):
        comprobante = _adapt(adaptadores.AdaptadorNotaCredito).comprobante({
            **BASE, "ruc": EMISOR.ruc,
            "receptor": {"razon_social": "X", "identificacion": "1790012345001"},
            "motivo": "Devolución",
            "detalles": [{"descripcion": "L", "cantidad": 1, "precio_unitario": 10}],
            "num_doc_modificado": "001-001-000000001",
        })
        assert comprobante.TIPO == TipoComprobante.NOTA_CREDITO.value
        assert comprobante.motivo == "Devolución"

    def test_nota_credito_exige_motivo(self):
        with pytest.raises(ErrorValidacion, match="motivo"):
            _adapt(adaptadores.AdaptadorNotaCredito).comprobante({
                **BASE,
                "receptor": {"razon_social": "X", "identificacion": "1790012345001"},
                "detalles": [{"descripcion": "L", "cantidad": 1, "precio_unitario": 10}],
                "num_doc_modificado": "001-001-000000001",
            })

    def test_nota_credito_exige_documento_modificado(self):
        with pytest.raises(ErrorValidacion, match="documento que se modifica"):
            _adapt(adaptadores.AdaptadorNotaCredito).comprobante({
                **BASE,
                "receptor": {"razon_social": "X", "identificacion": "1790012345001"},
                "motivo": "Devolución",
                "detalles": [{"descripcion": "L", "cantidad": 1, "precio_unitario": 10}],
            })

    def test_nota_debito(self):
        comprobante = _adapt(adaptadores.AdaptadorNotaDebito).comprobante({
            **BASE,
            "receptor": {"razon_social": "X", "identificacion": "1790012345001"},
            "motivos": [{"razon": "Intereses", "valor": 25}],
            "total_sin_impuestos": 25,
            "num_doc_modificado": "001-001-000000001",
        })
        assert comprobante.TIPO == TipoComprobante.NOTA_DEBITO.value
        assert comprobante.motivos[0].razon == "Intereses"

    def test_retencion(self):
        comprobante = _adapt(adaptadores.AdaptadorRetencion).comprobante({
            **BASE,
            "receptor": {"razon_social": "PROVEEDOR", "identificacion": "1790012345001"},
            "periodo_fiscal": "01/09/2026",
            "docs_sustento": [{
                "num_doc_sustento": "001001000000001",
                "fecha_emision": "15/09/2026",
                "total_sin_impuestos": 100,
                "importe_total": 115,
                "retenciones": [{"codigo": "1", "codigo_retencion": "312",
                                 "base_imponible": 100, "porcentaje_retener": "1.75",
                                 "valor_retenido": "1.75"}],
            }],
        })
        assert comprobante.TIPO == TipoComprobante.COMPROBANTE_RETENCION.value
        assert comprobante.docs_sustento[0].num_doc_sustento == "001001000000001"
        assert comprobante.docs_sustento[0].retenciones[0].codigo_retencion == "312"
        assert comprobante.periodo_fiscal == date(2026, 9, 1)

    def test_guia_remision(self):
        comprobante = _adapt(adaptadores.AdaptadorGuiaRemision).comprobante({
            **BASE,
            "destinatarios": [{
                "razon_social": "DESTINO", "identificacion": "1790012345001",
                "direccion": "GUAYAQUIL", "motivo_traslado": "01",
            }],
            "dir_partida": "QUITO", "razon_social_transportista": "TRANSPORTES",
            "ruc_transportista": "1790012345001", "placa": "ABC1234",
            "fecha_ini_transporte": FECHA, "fecha_fin_transporte": FECHA,
            "detalles": [{"descripcion": "Caja", "cantidad": 1}],
        })
        assert comprobante.TIPO == TipoComprobante.GUIA_REMISION.value
        assert comprobante.destinatarios[0].detalles[0].descripcion == "Caja"


class TestRegistro:
    def test_registra_y_recupera_un_adaptador(self):
        class AdaptadorPropio(adaptadores.AdaptadorFactura):
            pass

        class Modelo:
            pass

        adaptadores.registro.limpiar()
        adaptadores.registrar(Modelo, AdaptadorPropio)
        assert adaptadores.obtener(Modelo) is AdaptadorPropio
        adaptadores.registro.limpiar()

    def test_por_defecto_factura(self):
        adaptadores.registro.limpiar()
        assert adaptadores.obtener(int) is adaptadores.AdaptadorFactura

    def test_por_tipo_de_comprobante(self):
        assert adaptadores.obtener(int, TipoComprobante.NOTA_CREDITO.value) is (
            adaptadores.AdaptadorNotaCredito
        )
        assert adaptadores.obtener(int, TipoComprobante.GUIA_REMISION.value) is (
            adaptadores.AdaptadorGuiaRemision
        )


class TestXmlValido:
    """Los comprobantes generados por convención deben validar contra los XSD."""

    def test_factura_generada_por_convencion(self, esquemas):
        from lxml import etree

        comprobante = _adapt(adaptadores.AdaptadorFactura).comprobante(_venta())
        esquema = esquemas("factura_V1.1.0.xsd")
        documento = etree.fromstring(comprobante.to_xml().encode("utf-8"))
        assert esquema.validate(documento), "\n".join(str(e) for e in esquema.error_log)

    def test_nota_credito_generada_por_convencion(self, esquemas):
        from lxml import etree

        comprobante = _adapt(adaptadores.AdaptadorNotaCredito).comprobante({
            **BASE,
            "receptor": {"razon_social": "X", "identificacion": "1790012345001"},
            "motivo": "Devolución",
            "detalles": [{"descripcion": "L", "cantidad": 1, "precio_unitario": 10,
                          "codigo_porcentaje_iva": "4"}],
            "num_doc_modificado": "001-001-000000001",
        })
        esquema = esquemas("NotaCredito_V1.1.0.xsd")
        documento = etree.fromstring(comprobante.to_xml().encode("utf-8"))
        assert esquema.validate(documento), "\n".join(str(e) for e in esquema.error_log)

    def test_retencion_generada_por_convencion(self, esquemas):
        from lxml import etree

        comprobante = _adapt(adaptadores.AdaptadorRetencion).comprobante({
            **BASE,
            "receptor": {"razon_social": "PROVEEDOR", "identificacion": "1790012345001"},
            "periodo_fiscal": "01/09/2026",
            "docs_sustento": [{
                "num_doc_sustento": "001001000000001",
                "fecha_emision": "15/09/2026",
                "total_sin_impuestos": 100,
                "importe_total": 115,
                "retenciones": [{"codigo": "1", "codigo_retencion": "312",
                                 "base_imponible": 100, "porcentaje_retener": "1.75",
                                 "valor_retenido": "1.75"}],
            }],
        })
        esquema = esquemas("ComprobanteRetencion_V2.0.0.xsd")
        documento = etree.fromstring(comprobante.to_xml().encode("utf-8"))
        assert esquema.validate(documento), "\n".join(str(e) for e in esquema.error_log)


# ------------------------------------------------------ registro de adaptadores


class MiVenta:
    """Documento del proyecto con nombres que no son los de la convención."""

    def __init__(self) -> None:
        self.tercero = {"nombre": "CLIENTE PROPIO", "documento": "1790012345001",
                        "domicilio": "GUAYAQUIL"}
        self.renglones = [
            {"concepto": "Asesoría", "cant": 2, "valor": 50, "iva_codigo": "4"},
        ]
        self.fecha = FECHA
        self.secuencial = "3"


@pytest.fixture
def registro_limpio():
    """Vacía el registro de adaptadores antes y después de cada prueba."""
    adaptadores.registro.limpiar()
    yield adaptadores.registro
    adaptadores.registro.limpiar()


def test_registrar_sirve_como_decorador(registro_limpio):
    @adaptadores.registrar_para(MiVenta)
    class AdaptadorFacturaPropia(adaptadores.AdaptadorFactura):
        def receptor(self, venta):
            return Receptor(
                razon_social=venta.tercero["nombre"],
                identificacion=venta.tercero["documento"],
                direccion=venta.tercero["domicilio"],
            )

        def detalles(self, venta):
            base = adaptadores.AdaptadorFactura()
            return [base.detalle_desde_linea(linea, venta) for linea in venta.renglones]

    # El decorador devuelve la clase (no la pierde).
    assert AdaptadorFacturaPropia is not None
    assert adaptadores.obtener(MiVenta) is AdaptadorFacturaPropia

    comprobante = _adapt(adaptadores.obtener(MiVenta)).comprobante(MiVenta())

    assert comprobante.receptor.razon_social == "CLIENTE PROPIO"
    assert comprobante.receptor.identificacion == "1790012345001"
    assert comprobante.detalles[0].descripcion == "Asesoría"
    assert comprobante.detalles[0].cantidad == 2
    assert comprobante.detalles[0].precio_unitario == 50
    assert comprobante.detalles[0].impuestos[0].codigo_porcentaje == TarifaIva.IVA_15
    assert comprobante.secuencial_normalizado == "000000003"


def test_obtener_cae_al_tipo_de_comprobante_sin_registro(registro_limpio):
    assert adaptadores.obtener(MiVenta, "04") is adaptadores.AdaptadorNotaCredito
    assert adaptadores.obtener(MiVenta, "07") is adaptadores.AdaptadorRetencion
    # Un modelo sin adaptador registrado y sin tipo explícito usa factura.
    assert adaptadores.obtener(MiVenta) is adaptadores.AdaptadorFactura


def test_registrar_por_nombre_del_modelo(registro_limpio):
    class AdaptadorPropio(adaptadores.AdaptadorFactura):
        pass

    registro_limpio.registrar("mi_app.MiVenta", AdaptadorPropio)
    assert adaptadores.obtener("mi_app.MiVenta") is AdaptadorPropio
