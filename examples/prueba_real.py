#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prueba real de emisión en el ambiente de PRUEBAS del SRI.

Usa tu propia firma electrónica (.p12) y tu RUC. La contraseña del certificado se
pide por teclado con ``getpass``: nunca se guarda en disco ni en el historial.

Preparación
-----------
1. Copia ``prueba_config.ejemplo.json`` a ``prueba_config.json`` y rellena tus datos.
2. Ejecuta::

       python examples/prueba_real.py --config prueba_config.json

   Solo diagnóstico (sin enviar nada al SRI)::

       python examples/prueba_real.py --config prueba_config.json --solo-diagnostico

La contraseña también se puede tomar de la variable de entorno ``SRI_P12_CLAVE``
si no quieres escribirla cada vez (menos seguro: queda en el entorno del proceso).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from factec import (
    Detalle,
    Emisor,
    EmisorElectronico,
    Impuesto,
    Receptor,
)
from factec.catalogos import (
    Ambiente,
    DESCRIPCION_FORMA_PAGO,
    DESCRIPCION_TARIFA_IVA,
    TarifaIva,
    TipoIdentificacion,
)
from factec.excepciones import ErrorFacturacion
from factec.firma import Certificado

OK = "✅"
AVISO = "⚠️ "
ERROR = "❌"


# --------------------------------------------------------------- utilidades


def ruc_del_certificado(certificado: Certificado) -> Optional[str]:
    """Intenta extraer el RUC del titular del certificado.

    En los certificados ecuatorianos el RUC suele ir en ``serialNumber`` (OID
    2.5.4.5) o dentro del nombre común.
    """
    from cryptography import x509

    sujeto = certificado.certificado.subject
    for atributo in sujeto:
        if atributo.oid == x509.oid.NameOID.SERIAL_NUMBER:
            encontrado = re.search(r"\d{13}", str(atributo.value))
            if encontrado:
                return encontrado.group(0)

    candidatos: List[str] = [str(a.value) for a in sujeto]
    candidatos.append(certificado.titular)
    try:
        extensiones = certificado.certificado.extensions
        try:
            san = extensiones.get_extension_for_class(x509.SubjectAlternativeName)
            candidatos.extend(str(v) for v in san.value.get_values_for_type(x509.DNSName))
            candidatos.extend(str(v) for v in san.value.get_values_for_type(x509.OtherName))
        except x509.ExtensionNotFound:
            pass
    except Exception:  # noqa: BLE001 - la extracción es solo informativa
        pass

    for valor in candidatos:
        encontrado = re.search(r"\d{13}", valor)
        if encontrado:
            return encontrado.group(0)
    return None


def explicar_mensaje(texto: str) -> List[str]:
    """Devuelve pistas para los mensajes más frecuentes del SRI."""
    pistas: List[str] = []
    mayusculas = texto.upper()

    if "NO CUMPLE ESTRUCTURA" in mayusculas or "ESTRUCTURA XML" in mayusculas:
        pistas.append(
            "El SRI no pudo validar el XML. Revisa que el RUC esté registrado en el "
            "ambiente de pruebas, que 'estab'/'ptoEmi' existan y que los datos del "
            "emisor (obligado a llevar contabilidad, RIMPE, agente de retención) "
            "coincidan con los declarados en el SRI."
        )
    if "CONTRIBUYENTE REGISTRADO" in mayusculas or "NÚMERO DE RUC" in mayusculas:
        pistas.append(
            "El RUC no está registrado (o no está habilitado para facturación "
            "electrónica) en este ambiente del SRI."
        )
    if "FIRMA" in mayusculas:
        pistas.append(
            "Comprueba que el certificado sea de una entidad de certificación "
            "autorizada y que corresponda al RUC del emisor."
        )
    if "CLAVE" in mayusculas and "ACCESO" in mayusculas:
        pistas.append(
            "Revisa la clave de acceso: tipo de comprobante, RUC, ambiente, serie, "
            "secuencial y dígito verificador."
        )
    if "ESTABLECIMIENTO" in mayusculas:
        pistas.append("El establecimiento o punto de emisión no existe en el SRI.")
    if "FECHA" in mayusculas:
        pistas.append(
            "Revisa la fecha de emisión: el SRI no admite fechas muy alejadas del "
            "presente ni anteriores al inicio de actividades."
        )
    return pistas


def _cargar_config(ruta: Path) -> Dict[str, Any]:
    if not ruta.exists():
        print(f"{ERROR} No existe el archivo de configuración: {ruta}", file=sys.stderr)
        print(f"   Copia examples/prueba_config.ejemplo.json a {ruta} y rellénalo.")
        raise SystemExit(2)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{ERROR} El archivo {ruta} no es JSON válido: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return datos


def _clave_certificado(
    config: Dict[str, Any], clave_archivo: Optional[str] = None
) -> str:
    """Obtiene la contraseña del .p12 sin dejarla nunca en la línea de comandos.

    Orden de búsqueda: archivo indicado con ``--clave-archivo`` o
    ``SRI_P12_CLAVE_FILE``, variable de entorno ``SRI_P12_CLAVE``, campo
    ``clave_certificado`` de la configuración y, por último, petición interactiva.
    """
    ruta = clave_archivo or os.environ.get("SRI_P12_CLAVE_FILE")
    if ruta:
        archivo = Path(ruta)
        if not archivo.exists():
            print(f"{ERROR} No existe el archivo de contraseña: {archivo}", file=sys.stderr)
            raise SystemExit(2)
        return archivo.read_text(encoding="utf-8").strip()

    clave = os.environ.get("SRI_P12_CLAVE") or config.get("clave_certificado")
    if clave:
        return clave
    try:
        return getpass.getpass("Contraseña del certificado (.p12): ")
    except (EOFError, KeyboardInterrupt):
        print(f"\n{ERROR} No se pudo leer la contraseña.", file=sys.stderr)
        raise SystemExit(2)


# ---------------------------------------------------------------- diagnóstico


def diagnostico(config: Dict[str, Any], certificado: Certificado) -> List[str]:
    """Comprobaciones previas. Devuelve la lista de problemas detectados."""
    problemas: List[str] = []

    print("── Certificado ───────────────────────────────────────")
    print(f"   Titular : {certificado.titular}")
    print(f"   Emisor  : {certificado.emisor}")
    print(f"   Serie   : {certificado.numero_serie}")
    vigente_hasta = getattr(certificado.certificado, "not_valid_after_utc", None) or (
        certificado.certificado.not_valid_after
    )
    print(f"   Válido hasta: {vigente_hasta}")
    if certificado.vencido():
        problemas.append("El certificado está vencido o aún no es válido.")
        print(f"{ERROR} certificado no vigente")
    else:
        print(f"{OK} certificado vigente")

    ruc_cert = ruc_del_certificado(certificado)
    ruc_emisor = str(config.get("ruc", "")).strip()
    print(f"   RUC en el certificado: {ruc_cert or '(no detectado)'}")
    if ruc_cert and ruc_emisor and ruc_cert != ruc_emisor:
        problemas.append(
            f"El RUC del certificado ({ruc_cert}) no coincide con el del emisor "
            f"({ruc_emisor}). El SRI exige que el comprobante lo firme el titular del RUC."
        )
        print(f"{ERROR} el RUC no coincide con el del emisor")
    elif ruc_cert and ruc_emisor:
        print(f"{OK} el RUC del certificado coincide con el del emisor")

    print("\n── Datos del emisor ─────────────────────────────────")
    for campo in ("ruc", "razon_social", "dir_matriz", "estab", "pto_emi"):
        valor = config.get(campo)
        print(f"   {campo:18}: {valor!r}")
        if not valor:
            problemas.append(f"Falta el campo obligatorio '{campo}' en la configuración.")
    if config.get("contribuyente_rimpe"):
        regimen = (config.get("regimen") or "").upper()
        esperado = (
            "CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE"
            if "NEGOCIO POPULAR" in regimen
            else "CONTRIBUYENTE RÉGIMEN RIMPE"
        )
        print(f"   contribuyenteRimpe : {esperado!r}")

    return problemas


# ------------------------------------------------------------------- emisión


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba real de emisión en el ambiente de pruebas del SRI."
    )
    parser.add_argument("--config", default="prueba_config.json",
                        help="Archivo JSON con los datos del contribuyente")
    parser.add_argument("--clave-archivo", dest="clave_archivo", default=None,
                        help="Archivo de texto que contiene la contraseña del .p12 "
                             "(evita escribirla en la línea de comandos)")
    parser.add_argument("--solo-diagnostico", action="store_true",
                        help="Comprueba certificado y datos, sin enviar nada al SRI")
    parser.add_argument("--certificado", default=None,
                        help="Ruta del .p12; tiene prioridad sobre la configuración")
    parser.add_argument("--salida", default="prueba_salida",
                        help="Directorio donde guardar los XML")
    parser.add_argument("--intentos", type=int, default=6, help="Consultas de autorización")
    parser.add_argument("--espera", type=float, default=4.0, help="Segundos entre consultas")
    args = parser.parse_args()

    config = _cargar_config(Path(args.config))
    ruta_p12 = args.certificado or config.get("certificado")
    if ruta_p12:
        config["certificado"] = ruta_p12
    if not ruta_p12:
        print(f"{ERROR} Falta 'certificado' (ruta del .p12) en la configuración.", file=sys.stderr)
        return 2

    # Sin búfer: así los mensajes de error no se adelantan a la salida normal
    # cuando el script se ejecuta canalizado o redirigido a un archivo.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    print("══════════════════════════════════════════════════════")
    print(" PRUEBA DE FACTURACIÓN ELECTRÓNICA — AMBIENTE DE PRUEBAS")
    print("══════════════════════════════════════════════════════\n")

    try:
        certificado = Certificado.desde_archivo(ruta_p12, _clave_certificado(config, args.clave_archivo))
    except ErrorFacturacion as exc:
        print(f"\n{ERROR} {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    problemas = diagnostico(config, certificado)
    if problemas:
        print("\n── Problemas detectados ─────────────────────────────")
        for problema in problemas:
            print(f"{AVISO} {problema}")
    else:
        print(f"\n{OK} Diagnóstico previo correcto")

    if args.solo_diagnostico:
        print("\n(--solo-diagnostico: no se envía nada al SRI)")
        return 1 if problemas else 0

    if problemas:
        print(f"\n{AVISO} Corrige los problemas antes de enviar al SRI.")
        return 1

    # ------------------------------------------------------------ comprobante
    emisor = Emisor(
        ruc=str(config["ruc"]),
        razon_social=config["razon_social"],
        nombre_comercial=config.get("nombre_comercial"),
        dir_matriz=config["dir_matriz"],
        dir_establecimiento=config.get("dir_establecimiento") or config["dir_matriz"],
        estab=str(config.get("estab", "001")),
        pto_emi=str(config.get("pto_emi", "001")),
        obligado_contabilidad=bool(config.get("obligado_contabilidad", True)),
        contribuyente_especial=config.get("contribuyente_especial"),
        agente_retencion=config.get("agente_retencion"),
        contribuyente_rimpe=bool(config.get("contribuyente_rimpe", False)),
        regimen=config.get("regimen"),
    )

    datos_receptor = config.get("receptor") or {}
    receptor = Receptor(
        razon_social=datos_receptor.get("razon_social", "CONSUMIDOR FINAL"),
        identificacion=datos_receptor.get("identificacion", "9999999999999"),
        tipo_identificacion=datos_receptor.get("tipo_identificacion",
                                                TipoIdentificacion.CONSUMIDOR_FINAL),
        direccion=datos_receptor.get("direccion"),
    )

    emisor_electronico = EmisorElectronico(
        emisor=emisor,
        certificado=certificado,
        ambiente=Ambiente.PRUEBAS,
        secuencial_inicial=int(config.get("secuencial_inicial", 1)),
    )

    impuestos = [
        Impuesto(codigo_porcentaje=config.get("codigo_porcentaje_iva", TarifaIva.IVA_15))
    ]
    factura = emisor_electronico.factura(
        receptor=receptor,
        detalles=[
            Detalle(
                descripcion=config.get("descripcion", "Servicio de prueba"),
                cantidad=Decimal("1"),
                precio_unitario=Decimal("10.00"),
                codigo_principal="PRUEBA01",
                impuestos=impuestos,
            )
        ],
        fecha_emision=date.today(),
    )

    codigo_iva = str(getattr(impuestos[0].codigo_porcentaje, "value",
                             impuestos[0].codigo_porcentaje))
    print("\n── Comprobante ──────────────────────────────────────")
    print(f"   Clave de acceso : {factura.clave}")
    print(f"   Ambiente        : 1 (pruebas)")
    print(f"   Secuencial      : {factura.secuencial_normalizado}")
    print(f"   IVA             : {DESCRIPCION_TARIFA_IVA.get(codigo_iva, codigo_iva)}")
    print(f"   Total           : {factura.calcular()['importe_total']}")

    xml_sin_firma = factura.to_xml()
    xml_firmado = emisor_electronico.firmar(factura)
    print(f"   XML firmado     : {len(xml_firmado)} caracteres")

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "factura_sin_firma.xml").write_text(factura.to_xml(pretty=True), encoding="utf-8")
    (salida / "factura_firmada.xml").write_text(xml_firmado, encoding="utf-8")
    print(f"   Guardado en     : {salida}")

    # --------------------------------------------------------------- envío
    print("\n── Recepción en el SRI ──────────────────────────────")
    try:
        recepcion = emisor_electronico.cliente.validar_comprobante(xml_firmado)
    except ErrorFacturacion as exc:
        print(f"{ERROR} {type(exc).__name__}: {exc}")
        return 1

    print(f"   estado: {recepcion.estado}")
    for mensaje in recepcion.mensajes:
        print(f"   • {mensaje}")
        for pista in explicar_mensaje(f"{mensaje.identificador} {mensaje.mensaje} "
                                      f"{mensaje.informacion_adicional}"):
            print(f"     → {pista}")

    if not recepcion.recibida:
        print(f"\n{ERROR} El SRI devolvió el comprobante. Corrige lo anterior y reintenta.")
        return 1
    print(f"{OK} comprobante RECIBIDO por el SRI")

    print("\n── Autorización ─────────────────────────────────────")
    autorizacion = emisor_electronico.cliente.esperar_autorizacion(
        recepcion.clave_acceso or factura.clave, intentos=args.intentos, espera=args.espera
    )
    ultima = autorizacion.ultima
    if ultima is None:
        print(f"{ERROR} El SRI no devolvió ninguna autorización.")
        return 1

    print(f"   estado            : {ultima.estado}")
    print(f"   número autorización: {ultima.numero_autorizacion}")
    print(f"   fecha             : {ultima.fecha_autorizacion}")
    for mensaje in ultima.mensajes:
        print(f"   • {mensaje}")
        for pista in explicar_mensaje(f"{mensaje.identificador} {mensaje.mensaje}"):
            print(f"     → {pista}")

    if ultima.comprobante:
        ruta_autorizado = salida / "factura_autorizada.xml"
        ruta_autorizado.write_text(ultima.comprobante, encoding="utf-8")
        print(f"   XML autorizado en : {ruta_autorizado}")
        ruta_autorizacion = salida / "factura_autorizacion.xml"
        ruta_autorizacion.write_text(ultima.a_xml(), encoding="utf-8")
        print(f"   Autorización en   : {ruta_autorizacion}")
        print(f"   (guarda el número y la fecha de autorización junto al comprobante)")

    if autorizacion.autorizada:
        print(f"\n{OK} ¡COMPROBANTE AUTORIZADO! La prueba fue exitosa.")
        return 0

    print(f"\n{ERROR} El comprobante no quedó autorizado.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        sys.exit(130)
