"""Interfaz de línea de comandos del paquete (``sri-fe``).

Subcomandos disponibles::

    sri-fe catalogos                       # tablas de códigos del SRI
    sri-fe clave --tipo 01 --ruc ... ...   # genera/analiza una clave de acceso
    sri-fe firmar entrada.xml --certificado f.p12 --clave-clave s
    sri-fe verificar firmado.xml --certificado f.p12 --clave-clave s
    sri-fe enviar firmado.xml --ambiente pruebas
    sri-fe autorizar 0810...               # consulta el estado en el SRI
    sri-fe ejemplo --salida ./salida       # XML de ejemplo de los 6 comprobantes
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .catalogos import (
    DESCRIPCION_FORMA_PAGO,
    DESCRIPCION_MOTIVO_TRASLADO,
    DESCRIPCION_TARIFA_IVA,
    DESCRIPCION_TIPO_COMPROBANTE,
    DESCRIPCION_TIPO_IDENTIFICACION,
    Ambiente,
    ETIQUETA_RAIZ,
    FormaPago,
    Moneda,
    MotivoTraslado,
    TarifaIva,
    TipoComprobante,
    TipoIdentificacion,
    VERSIONES,
)
from .clave_acceso import (
    descomponer_clave_acceso,
    generar_clave_acceso,
    validar_clave_acceso,
)
from .emisor import EmisorElectronico
from .excepciones import ErrorFacturacion
from .firma import Certificado, firmar_xml, verificar_firma
from .lectura import leer_comprobante
from .verificacion import verificar_comprobante
from .modelos import (
    Destinatario,
    Detalle,
    DetalleGuia,
    Emisor,
    Impuesto,
    Receptor,
)
from .sri.soap import ClienteSRI


def _ambiente(valor: str) -> int:
    mapa = {"pruebas": 1, "1": 1, "produccion": 2, "producción": 2, "2": 2}
    if valor.lower() not in mapa:
        raise argparse.ArgumentTypeError("El ambiente debe ser 'pruebas' o 'produccion'.")
    return mapa[valor.lower()]


def _emisor_de_ejemplo() -> Emisor:
    return Emisor(
        ruc="1790012345001",
        razon_social="EMPRESA DE EJEMPLO S.A.",
        nombre_comercial="EJEMPLO",
        dir_matriz="Av. Amazonas 123, Quito",
        dir_establecimiento="Av. Amazonas 123, Quito",
        estab="001",
        pto_emi="001",
        obligado_contabilidad=True,
    )


def _receptor_de_ejemplo() -> Receptor:
    return Receptor(
        razon_social="CLIENTE DE EJEMPLO",
        identificacion="0703886697001",
        tipo_identificacion=TipoIdentificacion.RUC,
        direccion="Guayaquil",
        email="cliente@example.com",
    )


# ---------------------------------------------------------------- subcomandos


def cmd_catalogos(args: argparse.Namespace) -> int:
    tablas: Dict[str, Dict[str, str]] = {
        "tipos_comprobante": DESCRIPCION_TIPO_COMPROBANTE,
        "tipos_identificacion": DESCRIPCION_TIPO_IDENTIFICACION,
        "formas_pago": DESCRIPCION_FORMA_PAGO,
        "tarifas_iva": DESCRIPCION_TARIFA_IVA,
        "motivos_traslado": DESCRIPCION_MOTIVO_TRASLADO,
        "versiones": VERSIONES,
        "etiquetas_raiz": ETIQUETA_RAIZ,
    }
    if args.json:
        print(json.dumps(tablas, ensure_ascii=False, indent=2))
        return 0
    for nombre, tabla in tablas.items():
        print(f"\n== {nombre} ==")
        for codigo, descripcion in tabla.items():
            print(f"  {codigo}  {descripcion}")
    return 0


def cmd_clave(args: argparse.Namespace) -> int:
    if args.clave:
        if not validar_clave_acceso(args.clave):
            print(f"❌ Clave inválida: {args.clave}", file=sys.stderr)
            return 2
        datos = descomponer_clave_acceso(args.clave)
        print(json.dumps(datos, ensure_ascii=False, indent=2))
        return 0

    faltantes = [n for n in ("tipo", "ruc", "serie", "secuencial", "codigo") if not getattr(args, n)]
    if faltantes:
        print(f"❌ Faltan argumentos: {', '.join(faltantes)}", file=sys.stderr)
        return 2
    clave = generar_clave_acceso(
        fecha_emision=args.fecha or date.today(),
        tipo_comprobante=args.tipo,
        ruc=args.ruc,
        ambiente=args.ambiente,
        serie=args.serie,
        secuencial=args.secuencial,
        codigo_numerico=args.codigo,
    )
    print(clave)
    return 0


def cmd_firmar(args: argparse.Namespace) -> int:
    certificado = Certificado.desde_archivo(args.certificado, args.clave_clave or "")
    origen = Path(args.archivo)
    if not origen.exists():
        print(f"❌ No existe el archivo: {origen}", file=sys.stderr)
        return 2
    firmado = firmar_xml(origen.read_text(encoding="utf-8"), certificado, algoritmo=args.algoritmo)
    destino = Path(args.salida) if args.salida else origen.with_name(origen.stem + "_firmado.xml")
    destino.write_text(firmado, encoding="utf-8")
    print(f"✅ Firmado: {destino}")
    return 0


def cmd_verificar(args: argparse.Namespace) -> int:
    """Comprueba firma, clave de acceso, fecha y totales del comprobante."""
    certificado = None
    if args.certificado:
        certificado = Certificado.desde_archivo(args.certificado, args.clave_clave or "")
    xml = Path(args.archivo).read_text(encoding="utf-8")

    if args.solo_firma:
        print(json.dumps(verificar_firma(xml, certificado), ensure_ascii=False, indent=2, default=str))
        return 0

    informe = verificar_comprobante(xml, certificado=certificado)
    # El JSON va a la salida estándar (se puede canalizar a otro programa) y los
    # mensajes para leerlos, a la de errores.
    print(json.dumps(informe.a_dict(), ensure_ascii=False, indent=2))
    if informe.ok:
        print(f"✅ {informe.descripcion_tipo} {informe.numero} verificada.", file=sys.stderr)
        return 0
    for problema in informe.problemas:
        print(f"❌ {problema}", file=sys.stderr)
    return 4


def cmd_leer(args: argparse.Namespace) -> int:
    """Muestra los datos del comprobante que hay en un XML."""
    xml = Path(args.archivo).read_text(encoding="utf-8")
    comprobante = leer_comprobante(xml)

    if args.json:
        print(json.dumps(comprobante.a_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"{comprobante.descripcion_tipo} {comprobante.numero}  ({comprobante.version})")
    print(f"clave        : {comprobante.clave_acceso}")
    print(f"fecha        : {comprobante.fecha_emision}   ambiente: {comprobante.ambiente}")
    print(f"emisor       : {comprobante.emisor.ruc}  {comprobante.emisor.razon_social}")
    print(f"receptor     : {comprobante.receptor.identificacion}  {comprobante.receptor.razon_social}")
    print(f"subtotal     : {comprobante.totales.subtotal}")
    print(f"impuestos    : {comprobante.totales.valor_impuestos}")
    print(f"importe total: {comprobante.totales.importe_total}")
    for numero, detalle in enumerate(comprobante.detalles, start=1):
        print(
            f"  {numero}. {detalle.cantidad} × {detalle.precio_unitario}  "
            f"{detalle.descripcion}  [{detalle.codigo_principal}]"
        )
    for nombre, valor in comprobante.info_adicional.items():
        print(f"  {nombre}: {valor}")
    return 0


def cmd_autorizar(args: argparse.Namespace) -> int:
    cliente = ClienteSRI(ambiente=args.ambiente, timeout=args.timeout)
    respuesta = cliente.autorizar(args.clave)
    print(f"clave consultada : {respuesta.clave_acceso_consultada}")
    print(f"comprobantes     : {respuesta.numero_comprobantes}")
    print(f"autorizada       : {respuesta.autorizada}")
    for autorizacion in respuesta.autorizaciones:
        print(f"  estado={autorizacion.estado} numero={autorizacion.numero_autorizacion}")
        for mensaje in autorizacion.mensajes:
            print(f"    {mensaje}")
    return 0 if respuesta.autorizada else 3


def cmd_enviar(args: argparse.Namespace) -> int:
    cliente = ClienteSRI(ambiente=args.ambiente, timeout=args.timeout)
    xml = Path(args.archivo).read_text(encoding="utf-8")
    recepcion = cliente.validar_comprobante(xml)
    print(f"recepción: {recepcion.estado}")
    for mensaje in recepcion.mensajes:
        print(f"  {mensaje}")
    recepcion.lanzar_si_devuelta()
    autorizacion = cliente.esperar_autorizacion(
        recepcion.clave_acceso, intentos=args.intentos, espera=args.espera
    )
    ultima = autorizacion.ultima
    print(f"autorización: {ultima.estado if ultima else 'sin respuesta'}")
    return 0 if autorizacion.autorizada else 3


def cmd_ejemplo(args: argparse.Namespace) -> int:
    """Genera el XML de los seis comprobantes con datos ficticios."""
    from .comprobantes.factura import Factura
    from .comprobantes.guia_remision import GuiaRemision
    from .comprobantes.liquidacion_compra import LiquidacionCompra
    from .comprobantes.nota_credito import NotaCredito
    from .comprobantes.nota_debito import NotaDebito
    from .comprobantes.retencion import ComprobanteRetencion
    from .modelos import DocSustento, ImpuestoDocSustento, ImpuestoRetencion, Motivo, PagoRetencion

    emisor, receptor = _emisor_de_ejemplo(), _receptor_de_ejemplo()
    comunes = dict(emisor=emisor, fecha_emision=date(2026, 10, 8), ambiente=args.ambiente)
    detalle = Detalle(
        descripcion="Servicio de ejemplo",
        cantidad=1,
        precio_unitario=Decimal("100.00"),
        codigo_principal="SRV001",
        impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
    )

    comprobantes: List[Any] = [
        Factura(receptor=receptor, detalles=[detalle], secuencial="1", **comunes),
        LiquidacionCompra(proveedor=receptor, detalles=[detalle], secuencial="2", **comunes),
        NotaCredito(
            receptor=receptor, detalles=[detalle], motivo="Devolución", cod_doc_modificado="01",
            num_doc_modificado="001-001-000000001", fecha_emision_doc_sustento=date(2026, 10, 1),
            secuencial="3", **comunes,
        ),
        NotaDebito(
            receptor=receptor, motivos=[Motivo(razon="Intereses", valor=Decimal("50.00"))],
            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15, base_imponible=Decimal("50.00"))],
            total_sin_impuestos=Decimal("50.00"), cod_doc_modificado="01",
            num_doc_modificado="001-001-000000001",
            fecha_emision_doc_sustento=date(2026, 10, 1), secuencial="4", **comunes,
        ),
        ComprobanteRetencion(
            sujeto_retenido=receptor, periodo_fiscal=date(2026, 9, 1),
            docs_sustento=[
                DocSustento(
                    cod_sustento="01", cod_doc_sustento="01", num_doc_sustento="001001000000001",
                    fecha_emision=date(2026, 9, 15), total_sin_impuestos=Decimal("100.00"),
                    importe_total=Decimal("115.00"),
                    impuestos=[ImpuestoDocSustento(
                        codigo="2", codigo_porcentaje="4", base_imponible=Decimal("100.00"),
                        tarifa=Decimal("15.00"), valor=Decimal("15.00"))],
                    retenciones=[ImpuestoRetencion(
                        codigo="1", codigo_retencion="312", base_imponible=Decimal("100.00"),
                        porcentaje_retener=Decimal("1.75"), valor_retenido=Decimal("1.75"))],
                    pagos=[PagoRetencion(forma_pago=FormaPago.SIN_SISTEMA_FINANCIERO, total=Decimal("115.00"))],
                )
            ],
            secuencial="5", **comunes,
        ),
        GuiaRemision(
            dir_partida="Quito", razon_social_transportista="TRANSPORTES DE EJEMPLO",
            ruc_transportista="1790012345001", placa="ABC1234",
            fecha_ini_transporte=date(2026, 10, 8), fecha_fin_transporte=date(2026, 10, 9),
            destinatarios=[Destinatario(
                razon_social="DESTINO DE EJEMPLO", identificacion="0703886697001",
                direccion="Guayaquil", motivo_traslado=MotivoTraslado.VENTA,
                detalles=[DetalleGuia(descripcion="Caja de ejemplo", cantidad=1)])],
            secuencial="6", **comunes,
        ),
    ]

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    for comprobante in comprobantes:
        ruta = salida / f"{comprobante.ETIQUETA}.xml"
        ruta.write_text(comprobante.to_xml(pretty=True), encoding="utf-8")
        print(f"✅ {ruta}  (clave {comprobante.clave})")
    print(f"\n{len(comprobantes)} comprobantes de ejemplo en {salida}")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sri-fe",
        description="Facturación electrónica de Ecuador (SRI): clave de acceso, XML, firma y envío.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("catalogos", help="Muestra las tablas de códigos del SRI")
    p.add_argument("--json", action="store_true", help="Salida en JSON")
    p.set_defaults(func=cmd_catalogos)

    p = sub.add_parser("clave", help="Genera o analiza una clave de acceso")
    p.add_argument("--clave", help="Clave de 49 dígitos a analizar")
    p.add_argument("--tipo", help="Tipo de comprobante (01, 03, 04, 05, 06, 07)")
    p.add_argument("--ruc", help="RUC del emisor (13 dígitos)")
    p.add_argument("--serie", help="Serie estab+ptoEmi (6 dígitos)")
    p.add_argument("--secuencial", help="Secuencial (hasta 9 dígitos)")
    p.add_argument("--codigo", help="Código numérico (8 dígitos)")
    p.add_argument("--fecha", help="Fecha de emisión (dd/mm/aaaa); por defecto hoy")
    p.add_argument("--ambiente", type=_ambiente, default=Ambiente.PRUEBAS, help="pruebas o produccion")
    p.set_defaults(func=cmd_clave)

    p = sub.add_parser("firmar", help="Firma un XML con XAdES-BES")
    p.add_argument("archivo", help="XML sin firmar")
    p.add_argument("--certificado", required=True, help="Archivo .p12")
    p.add_argument("--clave-clave", dest="clave_clave", help="Contraseña del .p12")
    p.add_argument("--salida", help="Ruta del XML firmado")
    p.add_argument("--algoritmo", default="sha1", choices=["sha1", "sha256", "sha512"])
    p.set_defaults(func=cmd_firmar)

    p = sub.add_parser(
        "verificar",
        help="Verifica un comprobante: firma, clave de acceso, fecha y totales",
    )
    p.add_argument("archivo", help="XML del comprobante")
    p.add_argument("--certificado", help="Archivo .p12 del firmante (si no, el del XML)")
    p.add_argument("--clave-clave", dest="clave_clave", help="Contraseña del .p12")
    p.add_argument(
        "--solo-firma",
        dest="solo_firma",
        action="store_true",
        help="Comprobar únicamente las firmas XAdES-BES",
    )
    p.set_defaults(func=cmd_verificar)

    p = sub.add_parser("leer", help="Muestra los datos del comprobante que hay en un XML")
    p.add_argument("archivo", help="XML del comprobante (propio o de un proveedor)")
    p.add_argument("--json", action="store_true", help="Imprime todo en JSON")
    p.set_defaults(func=cmd_leer)

    p = sub.add_parser("autorizar", help="Consulta en el SRI el estado de una clave")
    p.add_argument("clave", help="Clave de acceso de 49 dígitos")
    p.add_argument("--ambiente", type=_ambiente, default=Ambiente.PRUEBAS)
    p.add_argument("--timeout", type=float, default=30.0)
    p.set_defaults(func=cmd_autorizar)

    p = sub.add_parser("enviar", help="Envía un XML firmado y espera la autorización")
    p.add_argument("archivo", help="XML firmado")
    p.add_argument("--ambiente", type=_ambiente, default=Ambiente.PRUEBAS)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--intentos", type=int, default=5)
    p.add_argument("--espera", type=float, default=3.0)
    p.set_defaults(func=cmd_enviar)

    p = sub.add_parser("ejemplo", help="Genera XML de ejemplo de los seis comprobantes")
    p.add_argument("--salida", default="salida", help="Directorio de destino")
    p.add_argument("--ambiente", type=_ambiente, default=Ambiente.PRUEBAS)
    p.set_defaults(func=cmd_ejemplo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Punto de entrada de la CLI."""
    parser = construir_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ErrorFacturacion as exc:
        print(f"❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
