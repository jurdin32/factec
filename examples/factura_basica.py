#!/usr/bin/env python
"""Genera el XML de los seis comprobantes, sin certificado ni acceso a la red.

    python examples/factura_basica.py
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from factec import (
    ComprobanteRetencion,
    Destinatario,
    Detalle,
    DetalleGuia,
    DocSustento,
    Emisor,
    Factura,
    GuiaRemision,
    Impuesto,
    ImpuestoDocSustento,
    ImpuestoRetencion,
    LiquidacionCompra,
    Motivo,
    NotaCredito,
    NotaDebito,
    PagoRetencion,
    Receptor,
)
from factec.catalogos import (
    FormaPago,
    MotivoTraslado,
    TarifaIva,
    TipoIdentificacion,
)

FECHA = date(2026, 10, 8)
SALIDA = Path(__file__).parent / "salida"


def main() -> None:
    emisor = Emisor(
        ruc="1790012345001",
        razon_social="EMPRESA DE EJEMPLO S.A.",
        nombre_comercial="EJEMPLO",
        dir_matriz="Av. Amazonas 123, Quito",
        dir_establecimiento="Av. Amazonas 123, Quito",
    )
    receptor = Receptor(
        razon_social="CLIENTE DE EJEMPLO",
        identificacion="0703886697001",
        tipo_identificacion=TipoIdentificacion.RUC,
        direccion="Guayaquil",
    )
    comunes = {"emisor": emisor, "fecha_emision": FECHA}
    detalle = Detalle(
        descripcion="Servicio de ejemplo",
        cantidad=2,
        precio_unitario=Decimal("100.00"),
        descuento=Decimal("5.00"),
        codigo_principal="SRV001",
        impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
    )

    comprobantes = [
        Factura(receptor=receptor, detalles=[detalle], secuencial="1", **comunes),
        LiquidacionCompra(proveedor=receptor, detalles=[detalle], secuencial="2", **comunes),
        NotaCredito(
            receptor=receptor, detalles=[detalle], motivo="Devolución de mercadería",
            cod_doc_modificado="01", num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), secuencial="3", **comunes,
        ),
        NotaDebito(
            receptor=receptor, motivos=[Motivo(razon="Intereses por mora", valor=Decimal("50.00"))],
            impuestos=[
                Impuesto(codigo_porcentaje=TarifaIva.IVA_15, base_imponible=Decimal("50.00"))
            ],
            total_sin_impuestos=Decimal("50.00"), cod_doc_modificado="01",
            num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), secuencial="4", **comunes,
        ),
        ComprobanteRetencion(
            sujeto_retenido=receptor, periodo_fiscal=date(2026, 9, 1), secuencial="5", **comunes,
            docs_sustento=[
                DocSustento(
                    cod_sustento="01", cod_doc_sustento="01",
                    num_doc_sustento="001001000000001",  # 15 dígitos, sin guiones
                    fecha_emision=date(2026, 9, 15), total_sin_impuestos=Decimal("100.00"),
                    importe_total=Decimal("115.00"),
                    impuestos=[
                        ImpuestoDocSustento(
                            codigo="2", codigo_porcentaje="4", base_imponible=Decimal("100.00"),
                            tarifa=Decimal("15.00"), valor=Decimal("15.00"),
                        )
                    ],
                    retenciones=[
                        ImpuestoRetencion(
                            codigo="1", codigo_retencion="312", base_imponible=Decimal("100.00"),
                            porcentaje_retener=Decimal("1.75"), valor_retenido=Decimal("1.75"),
                        )
                    ],
                    pagos=[
                        PagoRetencion(
                            forma_pago=FormaPago.SIN_SISTEMA_FINANCIERO, total=Decimal("115.00")
                        )
                    ],
                )
            ],
        ),
        GuiaRemision(
            dir_partida="Quito", razon_social_transportista="TRANSPORTES DE EJEMPLO",
            ruc_transportista="1790012345001", placa="ABC1234",
            fecha_ini_transporte=FECHA, fecha_fin_transporte=date(2026, 10, 9), secuencial="6",
            **comunes,
            destinatarios=[
                Destinatario(
                    razon_social="DESTINO DE EJEMPLO", identificacion="0703886697001",
                    direccion="Guayaquil", motivo_traslado=MotivoTraslado.VENTA,
                    detalles=[DetalleGuia(descripcion="Caja de ejemplo", cantidad=1)],
                )
            ],
        ),
    ]

    SALIDA.mkdir(parents=True, exist_ok=True)
    for comprobante in comprobantes:
        ruta = SALIDA / f"{comprobante.ETIQUETA}.xml"
        ruta.write_text(comprobante.to_xml(pretty=True), encoding="utf-8")
        print(f"{comprobante.ETIQUETA:22} clave={comprobante.clave}  ->  {ruta}")

    factura = comprobantes[0]
    print(f"\nTotales de la factura: {factura.calcular()['importe_total']}")
    print(f"XML en {SALIDA}")


if __name__ == "__main__":
    main()
