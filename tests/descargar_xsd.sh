#!/usr/bin/env bash
#
# Descarga los XSD oficiales del SRI para las pruebas que validan el XML contra
# el esquema (tests/test_comprobantes_xsd.py).
#
# Los esquemas son del SRI y **no** se distribuyen con el paquete: se bajan al
# ejecutar las pruebas. Para que la validación no cambie por debajo, la descarga
# está fijada a un commit concreto y se comprueba la huella sha256 de cada
# archivo: si el origen no coincide, esto falla en lugar de validar contra otra
# cosa. La integración continua usa este mismo script, así que las pruebas de los
# XSD no se saltan nunca en silencio.
#
#   ./tests/descargar_xsd.sh                    # en /tmp/sri_xsd
#   ./tests/descargar_xsd.sh /ruta/a/los/xsd    # donde usted quiera
#   SRI_XSD_DIR=/tmp/sri_xsd pytest             # y a probar
#
set -euo pipefail

DESTINO="${1:-${SRI_XSD_DIR:-/tmp/sri_xsd}}"

#: Commit del repositorio donde están los seis esquemas (odoo-l10n-ecuador).
COMMIT="9a61aec865780692eac47fb6c76ebbda1421953b"
BASE="https://raw.githubusercontent.com/joguenco/odoo-l10n-ecuador/$COMMIT/l10n_ec_account_edi/data/xsd"

#: Archivo y huella sha256 del original del SRI.
ARCHIVOS=(
  "factura_V1.1.0.xsd:bd7d795f44cbbfecef9957444d3cc288e67cd28329454fff5540875c7d21288b"
  "NotaCredito_V1.1.0.xsd:cd06578781a06c53d95546c340f1c222243162d1c4833e1de68495fa133e1c0c"
  "NotaDebito_V1.0.0.xsd:c8b7042e4d7302771f1b0e8f73611262f4856740e2e3565e33bc4f9c05a02f9a"
  "ComprobanteRetencion_V2.0.0.xsd:a34b1ce4914913457e9d2ced71a6bde0c114df052d00051d5b991e26cf920445"
  "GuiaRemision_V1.1.0.xsd:69f09cd42208ed6b1cdccbcd7689b3bb2b80b07e175025a9ffbc4a5562c7c3ed"
  "LiquidacionCompra_V1.1.0.xsd:4ebf0d2a4efed3c5b64ad02c17a374f02f43b4dec31dce4bc7dc0dfb580e12a6"
)

huella() {
  # macOS trae shasum; Linux, sha256sum.
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

mkdir -p "$DESTINO"
bajados=0

for entrada in "${ARCHIVOS[@]}"; do
  nombre="${entrada%%:*}"
  esperada="${entrada##*:}"
  ruta="$DESTINO/$nombre"

  if [ -f "$ruta" ] && [ "$(huella "$ruta")" = "$esperada" ]; then
    printf '  ✓ %s (ya estaba)\n' "$nombre"
    continue
  fi

  curl -fsSL -o "$ruta" "$BASE/$nombre"

  obtenida="$(huella "$ruta")"
  if [ "$obtenida" != "$esperada" ]; then
    printf '  ✗ %s no coincide con la huella esperada\n' "$nombre" >&2
    printf '    esperada: %s\n    obtenida: %s\n' "$esperada" "$obtenida" >&2
    exit 1
  fi
  printf '  ✓ %s\n' "$nombre"
  bajados=$((bajados + 1))
done

printf 'XSD del SRI en %s (%s de %s descargados).\n' \
  "$DESTINO" "$bajados" "${#ARCHIVOS[@]}"
printf 'Para las pruebas:  SRI_XSD_DIR=%s pytest\n' "$DESTINO"
