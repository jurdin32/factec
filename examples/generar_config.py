#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Genera ``prueba_config.json`` a partir del catastro del SRI y de tu certificado.

Obtiene del SRI los datos del contribuyente (razón social, régimen, obligaciones)
y del ``.p12`` el titular, y escribe la configuración que usa ``prueba_real.py``.

    python examples/generar_config.py --ruc 0703886697001 \
        --certificado "0703886697-141024143934.p12" \
        --dir-matriz "TU DIRECCION" \
        --salida prueba_config.json

La contraseña del certificado es **opcional**: solo se usa para comprobar que el
RUC del titular coincide con el del emisor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from factec.excepciones import ErrorFacturacion
from factec.sri import consultar_ruc

OK = "✅"
AVISO = "⚠️ "
ERROR = "❌"


def ruc_del_certificado(certificado: Any) -> Optional[str]:
    from cryptography import x509

    for atributo in certificado.certificado.subject:
        if atributo.oid == x509.oid.NameOID.SERIAL_NUMBER:
            encontrado = re.search(r"\d{13}", str(atributo.value))
            if encontrado:
                return encontrado.group(0)
    for atributo in certificado.certificado.subject:
        encontrado = re.search(r"\d{13}", str(atributo.value))
        if encontrado:
            return encontrado.group(0)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera prueba_config.json desde el SRI.")
    parser.add_argument("--ruc", required=True, help="RUC del emisor (13 dígitos)")
    parser.add_argument("--certificado", help="Ruta del .p12 (para leer el titular)")
    parser.add_argument("--clave-archivo", dest="clave_archivo",
                        help="Archivo con la contraseña del .p12")
    parser.add_argument("--dir-matriz", dest="dir_matriz",
                        help="Dirección de la matriz (el SRI no la publica)")
    parser.add_argument("--estab", default="001")
    parser.add_argument("--pto-emi", dest="pto_emi", default="001")
    parser.add_argument("--iva", default="4",
                        help="Código de tarifa de IVA (por omisión '4', que es el 15 por ciento)")
    parser.add_argument("--salida", default="prueba_config.json")
    args = parser.parse_args()

    print("── Consultando el catastro del SRI ───────────────────")
    try:
        datos = consultar_ruc(args.ruc)
    except ErrorFacturacion as exc:
        print(f"{ERROR} {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not datos.encontrado:
        print(f"{ERROR} El SRI no devolvió datos para el RUC {args.ruc}")
        return 1

    campos: List[tuple] = [
        ("RUC", datos.ruc),
        ("Razón social", datos.razon_social),
        ("Estado", datos.estado),
        ("Tipo", datos.tipo_contribuyente),
        ("Régimen", datos.regimen),
        ("Categoría", datos.categoria),
        ("Obligado a llevar contabilidad", "SI" if datos.obligado_contabilidad else "NO"),
        ("Agente de retención", "SI" if datos.agente_retencion else "NO"),
        ("Contribuyente especial", "SI" if datos.contribuyente_especial else "NO"),
        ("Inicio de actividades", datos.fecha_inicio_actividades),
        ("Actualizado", datos.fecha_actualizacion),
    ]
    for etiqueta, valor in campos:
        print(f"   {etiqueta:32}: {valor}")

    if datos.es_rimpe:
        print(f"   contribuyenteRimpe             : {datos.regimen_rimpe_texto}")
    if not datos.activo:
        print(f"{AVISO} el contribuyente no está ACTIVO (estado: {datos.estado})")

    problemas: List[str] = []

    print("\n── Certificado ──────────────────────────────────────")
    titular = None
    if args.certificado:
        try:
            from factec.firma import Certificado

            clave = ""
            if args.clave_archivo:
                clave = Path(args.clave_archivo).read_text(encoding="utf-8").strip()
            import os

            clave = clave or os.environ.get("SRI_P12_CLAVE", "")
            if not clave:
                import getpass

                clave = getpass.getpass("Contraseña del certificado (Enter para omitir): ")

            if clave:
                certificado = Certificado.desde_archivo(args.certificado, clave)
                titular = certificado.titular
                ruc_cert = ruc_del_certificado(certificado)
                print(f"   Titular          : {titular}")
                print(f"   RUC del certificado: {ruc_cert or '(no detectado)'}")
                print(f"   Vigente hasta    : "
                      f"{getattr(certificado.certificado, 'not_valid_after_utc', None)}")
                if certificado.vencido():
                    problemas.append("El certificado está vencido.")
                    print(f"{ERROR} certificado no vigente")
                if ruc_cert and ruc_cert != datos.ruc:
                    problemas.append(
                        f"El RUC del certificado ({ruc_cert}) no coincide con el del "
                        f"emisor ({datos.ruc})."
                    )
                    print(f"{ERROR} el RUC no coincide")
                else:
                    print(f"{OK} el RUC del certificado coincide con el del emisor")
            else:
                print("   (omitido: sin contraseña)")
        except ErrorFacturacion as exc:
            problemas.append(f"No se pudo leer el certificado: {exc}")
            print(f"{ERROR} {type(exc).__name__}: {exc}")
    else:
        print("   (omitido: no se indicó --certificado)")

    config: Dict[str, Any] = {
        "_generado": "por examples/generar_config.py a partir del catastro del SRI",
        "_leeme": "El SRI NO publica la dirección de matriz: revísala. La contraseña "
                  "del .p12 no va aquí, se pide por teclado o con --clave-archivo.",
        "certificado": args.certificado or "PON_AQUI_LA_RUTA_DE_TU_FIRMA.p12",
        "dir_matriz": args.dir_matriz or "PON_AQUI_TU_DIRECCION_DE_MATRIZ",
        "estab": args.estab,
        "pto_emi": args.pto_emi,
        "secuencial_inicial": 1,
        "codigo_porcentaje_iva": args.iva,
        "descripcion": "Servicio de prueba",
        "receptor": {
            "razon_social": "CONSUMIDOR FINAL",
            "identificacion": "9999999999999",
            "tipo_identificacion": "07",
            "direccion": "QUITO",
        },
    }
    config.update(datos.como_config())

    salida = Path(args.salida)
    salida.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{OK} Configuración escrita en {salida}")

    if not args.dir_matriz:
        problemas.append(
            "Falta la dirección de matriz ('dir_matriz'): el SRI no la publica. "
            "Pásala con --dir-matriz."
        )

    if problemas:
        print("\n── Pendiente ────────────────────────────────────────")
        for problema in problemas:
            print(f"{AVISO} {problema}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        sys.exit(130)
