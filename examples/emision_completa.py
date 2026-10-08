#!/usr/bin/env python
"""Ciclo completo de emisión contra el ambiente de **pruebas** del SRI.

Requiere un certificado ``.p12`` real (o de pruebas) y conexión a Internet:

    SRI_P12=/ruta/firmante.p12 SRI_P12_CLAVE=secreto python examples/emision_completa.py

Con un RUC no registrado en el SRI, la recepción devolverá ``DEVUELTA`` con el
error 35; eso confirma que el XML y la firma llegaron y se procesaron bien.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal

from factec import (
    Detalle,
    Emisor,
    EmisorElectronico,
    Impuesto,
    Receptor,
)
from factec.catalogos import Ambiente, TarifaIva, TipoIdentificacion
from factec.excepciones import ErrorFacturacion


def main() -> int:
    ruta_p12 = os.environ.get("SRI_P12")
    clave_p12 = os.environ.get("SRI_P12_CLAVE", "")
    ruc = os.environ.get("SRI_RUC", "1790012345001")

    if not ruta_p12:
        print(
            "Defina SRI_P12 (y SRI_P12_CLAVE) con la ruta del certificado.\n"
            "Ejemplo: SRI_P12=firmante.p12 SRI_P12_CLAVE=secreto python "
            "examples/emision_completa.py",
            file=sys.stderr,
        )
        return 2

    emisor = EmisorElectronico(
        emisor=Emisor(
            ruc=ruc,
            razon_social=os.environ.get("SRI_RAZON_SOCIAL", "EMPRESA DE EJEMPLO S.A."),
            dir_matriz=os.environ.get("SRI_DIR_MATRIZ", "Av. Amazonas 123, Quito"),
            dir_establecimiento=os.environ.get("SRI_DIR_MATRIZ", "Av. Amazonas 123, Quito"),
        ),
        certificado=ruta_p12,
        clave_certificado=clave_p12,
        ambiente=Ambiente.PRUEBAS,
    )

    factura = emisor.factura(
        receptor=Receptor(
            razon_social="CLIENTE DE EJEMPLO",
            identificacion="0703886697001",
            tipo_identificacion=TipoIdentificacion.RUC,
            direccion="Guayaquil",
        ),
        detalles=[
            Detalle(
                descripcion="Servicio de ejemplo",
                cantidad=1,
                precio_unitario=Decimal("100.00"),
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
            )
        ],
        fecha_emision=date.today(),
    )
    print(f"Clave de acceso: {factura.clave}")

    try:
        resultado = emisor.emitir(factura, intentos=5, espera=3.0)
    except ErrorFacturacion as exc:
        print(f"\nLa emisión no se completó: {type(exc).__name__}: {exc}")
        if getattr(exc, "mensajes", None):
            for mensaje in exc.mensajes:
                print(f"  - {mensaje}")
        return 1

    print(f"\nRecepción   : {resultado.recepcion.estado if resultado.recepcion else '-'}")
    print(f"Autorizada  : {resultado.autorizada}")
    if resultado.numero_autorizacion:
        print(f"N.º autoriz.: {resultado.numero_autorizacion}")
    for mensaje in resultado.mensajes:
        print(f"  - {mensaje}")

    return 0 if resultado.autorizada else 1


if __name__ == "__main__":
    sys.exit(main())
