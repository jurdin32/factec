# Prueba real contra el SRI

Emitir un comprobante de verdad contra el **ambiente de pruebas** (`1`) del SRI, paso a paso, y qué revisar antes de pasar a producción.

> Documentación de [**factec**](../README.md) · volver al README

---

Procedimiento completo usando tu propia firma electrónica y tu RUC.

### Requisitos previos

Antes de que el SRI acepte un comprobante en el ambiente de pruebas:

1. **El RUC debe estar habilitado como emisor electrónico.** Si nunca hiciste la
   solicitud de habilitación, el SRI responderá con el error de estructura
   indicando que no existe un contribuyente registrado.
2. **El certificado debe ser el del titular del RUC.** El SRI comprueba que quien
   firma sea el contribuyente; el script verifica esto antes de enviar.
3. **El certificado debe estar vigente** y ser de una entidad de certificación
   autorizada (un certificado autofirmado será rechazado por firma inválida).
4. **`estab` y `ptoEmi` deben existir** en el SRI para ese RUC.
5. Los datos del emisor (`obligado_contabilidad`, `contribuyente_rimpe`,
   `agente_retencion`, `contribuyente_especial`) deben **coincidir** con lo
   declarado en el SRI.

### Pasos

```bash
# 1. Genera la configuración desde el catastro del SRI (razón social, régimen,
#    obligaciones) y comprueba que el RUC del certificado coincide.
python examples/generar_config.py --ruc 0703886697001 \
    --certificado "0703886697-141024143934.p12" \
    --dir-matriz "TU DIRECCION" \
    --salida prueba_config.json

# 2. Diagnóstico previo: certificado, vigencia, RUC y datos. No envía nada.
python examples/prueba_real.py --config prueba_config.json --solo-diagnostico

# 3. Emisión real. Pedirá la contraseña del .p12 por teclado.
python examples/prueba_real.py --config prueba_config.json
```

La contraseña se solicita con `getpass` y **no se guarda** en ningún archivo ni
queda en el historial de la consola. Si prefieres no escribirla cada vez, puedes
dejarla en un archivo con permisos restringidos y pasar la ruta:

```bash
printf 'TU_CONTRASENA' > /tmp/sri_clave.txt && chmod 600 /tmp/sri_clave.txt
python examples/prueba_real.py --config prueba_config.json \
    --certificado "0703886697-141024143934.p12" --clave-archivo /tmp/sri_clave.txt
```

> ⚠️ La dirección de matriz **no** la publica el SRI: hay que indicarla con
> `--dir-matriz`. Es texto libre en el esquema y no afecta a que el comprobante
> se acepte, pero forma parte del documento.

### Qué hace el script

1. **Diagnóstico:** vigencia y tipo de clave del certificado, RUC del certificado
   frente al del emisor, campos obligatorios y el valor exacto de
   `contribuyenteRimpe`.
2. **Construcción:** una factura mínima (1 ítem) con el IVA que indiques.
3. **Firma** XAdES-BES con tu certificado.
4. **Recepción** en `celcer.sri.gob.ec` (ambiente de pruebas).
5. **Autorización:** sondea hasta obtener el estado definitivo.
6. **Guarda** en `prueba_salida/`: `factura_sin_firma.xml`, `factura_firmada.xml`
   y, si se autoriza, `factura_autorizada.xml`.

Al final imprime el estado y, para cada mensaje del SRI, una pista de qué revisar.

### Cómo interpretar el resultado

| Resultado | Significado |
|---|---|
| `RECIBIDA` + `AUTORIZADO` | Prueba superada: el comprobante es válido y quedó autorizado |
| `DEVUELTA` | El SRI no aceptó el comprobante; imprime el motivo y una pista |
| `RECIBIDA` + `NO AUTORIZADO` | Pasó la estructura y la firma, pero falló una validación de negocio |
| `RECIBIDA` + `EN PROCESO` | El SRI aún procesa; el script reintenta automáticamente |

Errores frecuentes en pruebas:

- **«No existe un contribuyente registrado con el RUC …»** → el RUC no está
  habilitado para facturación electrónica en ese ambiente.
- **«ARCHIVO NO CUMPLE ESTRUCTURA XML»** → algún campo no cumple el esquema
  (suele ser `contribuyenteRimpe`, un `estab` inexistente o un dato del emisor que
  no coincide con el SRI).
- **Firma inválida** → el certificado no es de una CA autorizada o no corresponde
  al RUC del emisor.
- **Clave de acceso no válida** → revisa serie, secuencial y dígito verificador.

### Errores frecuentes del ambiente de pruebas

| Mensaje del SRI | Qué significa |
|---|---|
| `FECHA EMISIÓN EXTEMPORANEA` (65) | La fecha de emisión es futura o de más de 90 días. El paquete ya lo valida: revise `TIME_ZONE` (debe ser `America/Guayaquil`) y la fecha del comprobante |
| `CLAVE ACCESO REGISTRADA` | Esa clave ya se envió: el paquete es idempotente por documento, no lo reenvía |
| `ARCHIVO NO CUMPLE ESTRUCTURA XML` (35) | El XML no cuadra con el XSD: revise los catálogos del SRI que usó |
| `FIRMA INVÁLIDA` | El certificado no es RSA, la contraseña es incorrecta o el XML se modificó después de firmarlo |
| `CERTIFICADO NO VIGENTE` | La firma electrónica está vencida: el SRI no acepta el comprobante |

### Antes de pasar a producción

- Cambia `--ambiente` a producción (2) y usa un RUC habilitado para producción.
- **Persiste la numeración**: `EmisorElectronico` lleva el secuencial **en
  memoria**; al reiniciar hay que continuar desde el último emitido.
- Conserva el XML autorizado que devuelve el SRI: es el documento con validez
  legal y la base del RIDE.
