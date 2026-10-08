# factec

Paquete de **facturación electrónica de Ecuador (SRI)**: genera, firma y envía
los seis comprobantes electrónicos que exige el Servicio de Rentas Internas.

- **Clave de acceso** de 49 dígitos con el algoritmo módulo 11 del SRI.
- **XML** de los seis comprobantes, validado contra los XSD oficiales.
- **Firma XAdES-BES** enveloped con el certificado `.p12` del contribuyente.
- **Envío SOAP** a los webservices de recepción y autorización (pruebas y producción).

| Comprobante | `codDoc` | Esquema |
|---|---|---|
| Factura | `01` | 1.1.0 |
| Liquidación de compra | `03` | 1.1.0 |
| Nota de crédito | `04` | 1.1.0 |
| Nota de débito | `05` | 1.0.0 |
| Guía de remisión | `06` | 1.1.0 |
| Comprobante de retención | `07` | 2.0.0 |

---

## 1. Instalación

```bash
pip install factec
```

Desde GitHub (última versión de la rama `main`):

```bash
pip install "factec @ git+https://github.com/jurdin32/factec.git"
# con la app de Django:
pip install "factec[django] @ git+https://github.com/jurdin32/factec.git"
```

Desde el código fuente (esta carpeta):

```bash
pip install .
# o, para desarrollo:
pip install -e ".[dev]"
```

**Requisitos:** Python 3.9 o superior. Dependencias: `cryptography`, `lxml` y
`requests`. El certificado debe ser **RSA** (el SRI no admite otro tipo de clave).

Al instalar se añade el comando de consola `sri-fe`.

---

## 2. Inicio rápido

```python
from datetime import date
from decimal import Decimal

from factec import (
    Detalle, Emisor, EmisorElectronico, Impuesto, Receptor,
)
from factec.catalogos import TarifaIva, TipoIdentificacion

# 1. Configure el emisor y su certificado
emisor = EmisorElectronico(
    emisor=Emisor(
        ruc="1790012345001",
        razon_social="ACME S.A.",
        nombre_comercial="ACME",
        dir_matriz="Av. Amazonas 123, Quito",
        dir_establecimiento="Av. Amazonas 123, Quito",
    ),
    certificado="firmante.p12",
    clave_certificado="mi-clave",
    ambiente=1,          # 1 = pruebas, 2 = producción
)

# 2. Arme la factura
factura = emisor.factura(
    receptor=Receptor(
        identificacion="0703886697001",
        razon_social="CLIENTE EJEMPLO",
        tipo_identificacion=TipoIdentificacion.RUC,
        direccion="Guayaquil",
    ),
    detalles=[
        Detalle(
            descripcion="Servicio de desarrollo",
            cantidad=Decimal("2"),
            precio_unitario=Decimal("100.00"),
            impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
        )
    ],
    fecha_emision=date(2026, 10, 8),
)

# 3. Clave de acceso y XML
print(factura.clave)             # 0810202601179001234500110010010000000011234567819
print(factura.to_xml())          # XML sin firmar
print(factura.to_xml(pretty=True))

# 4. Firmar y enviar
resultado = emisor.emitir(factura)   # firma + recepción + autorización
print(resultado.autorizada, resultado.numero_autorizacion)
print(resultado.xml_autorizado)      # el comprobante tal como lo devuelve el SRI
```

Para una liquidación de compra, nota de crédito, nota de débito, retención o
guía de remisión, la fachada expone `emisor.liquidacion_compra(...)`,
`emisor.nota_credito(...)`, `emisor.nota_debito(...)`, `emisor.retencion(...)` y
`emisor.guia_remision(...)`.

---

## 3. Clave de acceso

```python
from datetime import date
from factec import (
    generar_clave_acceso, validar_clave_acceso, descomponer_clave_acceso,
)

clave = generar_clave_acceso(
    fecha_emision=date(2026, 10, 8),
    tipo_comprobante="01",      # tabla 1 del SRI
    ruc="1790012345001",
    ambiente=1,
    serie="001001",             # establecimiento (3) + punto de emisión (3)
    secuencial="1",
    codigo_numerico="12345678", # 8 dígitos elegidos por el emisor
)
assert validar_clave_acceso(clave)
descomponer_clave_acceso(clave)
```

La clave tiene **49 dígitos**: fecha (8) + tipo (2) + RUC (13) + ambiente (1) +
serie (6) + secuencial (9) + código numérico (8) + tipo de emisión (1) +
verificador módulo 11 (1). Las funciones aceptan fechas como `date` o texto
(`dd/mm/aaaa`, `dd-mm-aaaa`, `aaaa-mm-dd`).

Cada comprobante genera su clave automáticamente la primera vez que se pide
(`comprobante.clave`), usando un código numérico aleatorio si no se indica otro.

---

## 4. Firma electrónica (XAdES-BES)

```python
from factec import Certificado, firmar_xml, verificar_firma

certificado = Certificado.desde_archivo("firmante.p12", "mi-clave")
certificado.validar_vigencia()

xml_firmado = firmar_xml(factura.to_xml(), certificado)          # SHA-1, el clásico del SRI
xml_firmado = firmar_xml(factura.to_xml(), certificado, algoritmo="sha256")

verificar_firma(xml_firmado, certificado)   # {'valido': True, 'firmas': [...]}
```

O directamente desde el comprobante:

```python
xml_firmado = factura.firmar(certificado)
```

Qué produce la firma:

- firma *enveloped* sobre el elemento raíz (`id="comprobante"`);
- canonicalización inclusiva `xml-c14n`;
- dos referencias: el comprobante y `SignedProperties` (con el `Type` de ETSI);
- `KeyInfo` con el certificado X.509 y la clave pública RSA;
- `SignedProperties` con `SigningTime`, `CertDigest` e `IssuerSerial`;
- los elementos `ds:Signature` en el orden que exige XMLDSig:
  `SignedInfo`, `SignatureValue`, `KeyInfo`, `Object`.

**Algoritmos disponibles:** `sha1` (por omisión), `sha256` y `sha512`.
El SRI valida de forma más amplia SHA-1, por eso es el valor por omisión.

⚠️ Firme **después** de tener el XML definitivo: la firma cubre el contenido, así
que cualquier cambio posterior la invalida.

---

## 5. Envío al SRI

| Ambiente | Valor | Host |
|---|---|---|
| Pruebas | `1` | `celcer.sri.gob.ec` |
| Producción | `2` | `cel.sri.gob.ec` |

### Flujo de una sola llamada

```python
resultado = emisor.emitir(factura)     # firma + validarComprobante + autorizacionComprobante
if resultado.autorizada:
    print(resultado.numero_autorizacion)
```

### Flujo por pasos

```python
xml = emisor.firmar(factura)

recepcion = emisor.cliente.validar_comprobante(xml)
recepcion.lanzar_si_devuelta()          # lanza ErrorRecepcion si el SRI la devuelve
print(recepcion.estado)                 # RECIBIDA / DEVUELTA

autorizacion = emisor.cliente.esperar_autorizacion(recepcion.clave_acceso, intentos=5, espera=3)
autorizacion.lanzar_si_no_autorizada()  # lanza ErrorAutorizacion si no se autoriza
print(autorizacion.ultima.estado)       # AUTORIZADO / NO AUTORIZADO / EN PROCESO

print(autorizacion.ultima.comprobante)  # XML autorizado que devuelve el SRI
```

### Estados

| Servicio | Estados posibles |
|---|---|
| `validarComprobante` | `RECIBIDA`, `DEVUELTA` |
| `autorizacionComprobante` | `AUTORIZADO`, `NO AUTORIZADO`, `EN PROCESO` |

La autorización es asíncrona: `esperar_autorizacion()` reintenta mientras el
estado sea `EN PROCESO`.

### Errores

Todos heredan de `ErrorFacturacion`:

| Excepción | Cuándo |
|---|---|
| `ErrorValidacion` | Los datos del comprobante no cumplen las reglas |
| `ErrorCertificado` | El `.p12` no se abre, no es RSA o está vencido |
| `ErrorFirma` | No se pudo firmar o la verificación falla |
| `ErrorSRI` | Fallo de comunicación (red, HTTP 5xx, SOAP Fault) |
| `ErrorRecepcion` | El SRI devolvió el comprobante |
| `ErrorAutorizacion` | El SRI no autorizó el comprobante |

---

## 6. Interfaz de línea de comandos

```bash
sri-fe catalogos                                   # tablas de códigos del SRI
sri-fe catalogos --json

sri-fe clave --tipo 01 --ruc 1790012345001 --serie 001001 \
             --secuencial 1 --codigo 12345678 --fecha 08/10/2026
sri-fe clave --clave 0810202601179001234500110010010000000011234567819

sri-fe ejemplo --salida ./salida                   # XML de los 6 comprobantes
sri-fe firmar salida/factura.xml --certificado firmante.p12 --clave-clave mi-clave
sri-fe verificar salida/factura_firmado.xml --certificado firmante.p12 --clave-clave mi-clave

sri-fe autorizar 0810202601179001234500110010010000000011234567819 --ambiente pruebas
sri-fe enviar salida/factura_firmado.xml --ambiente pruebas
```

---

## 7. Catálogos

`factec.catalogos` expone las tablas del SRI como `Enum` de
cadenas (se pueden pasar directamente a los modelos) y como diccionarios
`{codigo: descripción}`.

```python
from factec.catalogos import (
    Ambiente, TipoEmision, TipoComprobante, TipoIdentificacion, FormaPago,
    CodigoImpuesto, CodigoRetencion, TarifaIva, TarifaRetencionIva,
    MotivoTraslado, TipoSujetoRetenido, PagoLocExt, Moneda,
    DESCRIPCION_FORMA_PAGO, PORCENTAJE_IVA,
)
```

| Tabla | Contenido |
|---|---|
| `TipoComprobante` | 01 factura, 03 liquidación, 04 nota de crédito, 05 nota de débito, 06 guía de remisión, 07 retención |
| `TipoIdentificacion` | 04 RUC, 05 cédula, 06 pasaporte, 07 consumidor final, 08 exterior |
| `FormaPago` | 01, 15, 16, 17, 18, 19, 20, 21 |
| `TarifaIva` | 0 (0%), 2 (12%), 3 (14%), 4 (15%), 5 (5%), 6 (no objeto), 7 (exento), 8 (diferenciado) |
| `CodigoImpuesto` | 2 IVA, 3 ICE, 5 IRBPNR |
| `CodigoRetencion` | 1 renta, 2 IVA, 6 ISD |
| `MotivoTraslado` | 01 … 10 |
| `Ambiente` / `TipoEmision` | 1 pruebas / 2 producción · 1 normal / 2 contingencia |

---

## 8. API principal

### Fachada

| Miembro | Descripción |
|---|---|
| `EmisorElectronico(emisor, certificado=..., clave_certificado=..., ambiente=1)` | Configura emisor, certificado y ambiente |
| `.factura(...)`, `.liquidacion_compra(...)`, `.nota_credito(...)`, `.nota_debito(...)`, `.retencion(...)`, `.guia_remision(...)` | Construyen cada comprobante |
| `.firmar(comprobante, algoritmo="sha1")` | Devuelve el XML firmado |
| `.emitir(comprobante, enviar=True)` | Ciclo completo; `enviar=False` solo construye y firma |
| `.enviar(...)`, `.consultar_autorizacion(clave)` | Envío y consulta puntual |
| `.siguiente_secuencial(tipo)` | Contador en memoria por tipo de comprobante |

### Consulta del catastro de RUC

```python
from factec import consultar_ruc, existe_ruc

datos = consultar_ruc("0703886697001")
datos.razon_social            # 'URDIN GONZALEZ JOHNNY EDGAR'
datos.obligado_contabilidad   # False
datos.es_rimpe                # True
datos.regimen_rimpe_texto     # 'CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE'
datos.como_config()           # dict listo para prueba_config.json

existe_ruc("0703886697001")   # True / False
```

### Objetos de datos

`Emisor`, `Receptor`, `Detalle`, `Impuesto`, `Pago`, `Compensacion`, `Motivo`,
`Reembolso`, `DetalleReembolso`, `ImpuestoReembolso`, `DocSustento`,
`ImpuestoDocSustento`, `ImpuestoRetencion`, `PagoRetencion`, `Destinatario`,
`DetalleGuia`.

Los importes usan `Decimal`; pueden pasarse como `int`, `float`, `str` o
`Decimal` y se convierten sin arrastrar el error binario del `float`.

`Impuesto(codigo_porcentaje=TarifaIva.IVA_15)` calcula la tarifa y el valor solo:
la tarifa sale del catálogo y el valor es `base × tarifa / 100` redondeado a 2
decimales (con `base` = `cantidad × precioUnitario − descuento`).

### Resultado de emisión

| Atributo | Descripción |
|---|---|
| `clave_acceso` | Clave de 49 dígitos |
| `xml_sin_firma`, `xml_firmado` | XML en cada etapa |
| `recepcion`, `autorizacion` | Respuestas del SRI |
| `recepcionada`, `autorizada` | Atajos booleanos |
| `numero_autorizacion`, `xml_autorizado`, `mensajes` | Datos de la autorización |
| `lanzar_si_fallo()` | Lanza la excepción adecuada si algo falló |

---

## 9. Validación contra los XSD

Los esquemas oficiales del SRI **no declaran** el elemento `ds:Signature`, por lo
que la validación XSD aplica al XML **sin firmar**; la firma se valida por
separado (`verificar_firma`). El flujo correcto es: generar → validar → firmar → enviar.

Para ejecutar las pruebas que validan contra los XSD, indique dónde están:

```bash
SRI_XSD_DIR=/ruta/a/los/xsd pytest
```

Las pruebas se saltan solas si no encuentra los esquemas, y **no** se incluyen en
el paquete por no redistribuir material de terceros. Obténgalos de la
documentación de comprobantes electrónicos del SRI.

---

## 10. Notas y limitaciones

- **Secuenciales:** `EmisorElectronico` lleva un contador **en memoria**. Si la
  aplicación se reinicia hay que fijarlo con `secuencial_inicial` o pasar
  `secuencial` explícito. Persistir la numeración es responsabilidad de la
  aplicación.
- **Certificado:** se exige RSA. El paquete no comprueba la cadena de confianza
  ni la revocación; para eso use `cryptography` o su proveedor de confianza.
- **`numDocSustento`** en el comprobante de retención 2.0.0 son **15 dígitos sin
  guiones** (por ejemplo `001001000000001`).
- **`SOAPAction`**: el SRI exige `SOAPAction` **vacío** en sus webservices; con
  otro valor responde un SOAP Fault. El cliente ya lo envía así.
- **RIDE (PDF):** este paquete **no** genera el RIDE. Cubre la emisión
  electrónica; el RIDE se puede construir con el XML autorizado que devuelve
  `resultado.xml_autorizado`.
- Los catálogos corresponden a las tablas publicadas por el SRI; conviene
  revisarlos si el SRI publica cambios.

---

## 11. Estructura del proyecto


```
factec/
├── pyproject.toml
├── README.md
├── src/factec/
│   ├── __init__.py         # API pública
│   ├── __main__.py         # CLI (sri-fe)
│   ├── catalogos.py        # tablas de códigos del SRI
│   ├── clave_acceso.py     # clave de 49 dígitos (módulo 11)
│   ├── modelos.py          # dataclasses de datos
│   ├── emisor.py           # fachada EmisorElectronico
│   ├── excepciones.py
│   ├── comprobantes/       # un generador de XML por comprobante
│   ├── firma/              # XAdES-BES
│   ├── sri/                # endpoints, cliente SOAP y consulta de RUC
│   └── django/             # app de Django: modelos, admin, servicios y tareas
├── examples/
└── tests/
```

---

## 12. Ejemplos y pruebas

```bash
python examples/factura_basica.py          # genera XML (sin certificado ni red)
python examples/prueba_real.py --config prueba_config.json --solo-diagnostico
python examples/prueba_real.py --config prueba_config.json   # emisión real en pruebas
SRI_XSD_DIR=/tmp/sri_xsd pytest            # 205 pruebas
```

---

## 13. Prueba real en el ambiente de pruebas

Procedimiento completo usando tu propia firma electrónica y tu RUC.

### 13.1 Requisitos previos

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

### 13.2 Pasos

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

### 13.3 Qué hace el script

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

### 13.4 Cómo interpretar el resultado

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

### 13.5 Antes de pasar a producción

- Cambia `--ambiente` a producción (2) y usa un RUC habilitado para producción.
- **Persiste la numeración**: `EmisorElectronico` lleva el secuencial **en
  memoria**; al reiniciar hay que continuar desde el último emitido.
- Conserva el XML autorizado que devuelve el SRI: es el documento con validez
  legal y la base del RIDE.

---

## 14. Integración con Django

El paquete trae una app de Django lista para usar, con las tablas, el admin y las
tareas de Celery. Se instala con el extra:

```bash
pip install "factec[django]"
```

### 14.1 Añadir la app y migrar

```python
# settings.py
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ...
    "factec.django",
]
```

```bash
python manage.py migrate
```

Eso crea las tablas de la app (usa el *label* `sri_fe`):

| Tabla | Contenido |
|---|---|
| `sri_fe_configuracionemisor` | Datos del contribuyente, archivo `.p12` y su contraseña |
| `sri_fe_comprobanteemitido` | Cada comprobante con sus XML y el estado ante el SRI |
| `sri_fe_secuencial` | Contador persistente de secuenciales |
| `sri_fe_cliente`, `sri_fe_producto` | Catálogos de clientes y productos |
| `sri_fe_factura`, `sri_fe_facturadetalle` | Facturas y sus líneas |
| `sri_fe_liquidacioncompra`, `…detalle` | Liquidaciones de compra |
| `sri_fe_notacredito`, `sri_fe_notacreditodetalle` | Notas de crédito |
| `sri_fe_notadebito`, `sri_fe_notadebitomotivo` | Notas de débito y sus motivos |
| `sri_fe_guiaremision`, `…destinatario`, `…detalle` | Guías de remisión y sus bienes |
| `sri_fe_retencion`, `…docsustento`, `…impuesto`, `…retencionimpuesto` | Retenciones |

No hay que generar migraciones en el proyecto: vienen dentro del paquete, así que
`migrate` deja la base de datos lista.

#### Dónde se guarda el certificado `.p12`

El archivo de firma se guarda en un `FileField`, o sea dentro de `MEDIA_ROOT`. Un
proyecto recién creado con `django-admin startproject` **no** define esas
opciones: añádalas o la carga del certificado fallará.

```python
# settings.py
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
```

```python
# en el admin: adjunte el archivo en «Firma electrónica → Archivo de firma».
# o desde código, guardándolo en el campo (no le pase una ruta absoluta):
from pathlib import Path

from django.core.files import File

configuracion.certificado.save("firma.p12", File(open("/ruta/a/mi/firma.p12", "rb")))
configuracion.establecer_clave("contraseña del .p12")   # se guarda cifrada
configuracion.save()
```

Si despliega con `DEBUG = False`, sirva `MEDIA_URL` con su servidor web (o use un
almacenamiento como S3). Para instalarlo desde GitHub:

```bash
pip install "factec[django] @ git+https://github.com/jurdin32/factec.git"
```

### 14.2 Los modelos del SRI (clientes, productos y comprobantes)

La app trae los modelos con **los campos exactos del SRI**: los mismos nombres,
las mismas longitudes del esquema oficial y los catálogos (tablas 1, 2, 5, 16, 21
y 24). Sirven tanto desde el admin como desde Python.

```python
from factec.django import documentos

cliente = documentos.Cliente.objects.create(
    razon_social="DISTRIBUIDORA ANDINA CÍA. LTDA.",
    identificacion="1790012345001",          # RUC (13), cédula (10), pasaporte…
    tipo_identificacion="04",                 # 04 RUC, 05 cédula, 06 pasaporte, 07 consumidor final, 08 exterior
    direccion="AV. AMAZONAS 123, QUITO",
)
producto = documentos.Producto.objects.create(
    codigo_principal="SRV001",                # máximo 25 caracteres
    descripcion="Servicio de desarrollo",     # máximo 300
    unidad_medida="hora",
    precio_unitario=100,
    codigo_porcentaje_iva="4",                # 4 = IVA 15 %
)
```

| Modelo | Comprobante | Campos propios (además de fecha, secuencial, observaciones y campos adicionales) |
|---|---|---|
| `Factura` | `01` | `receptor`, `forma_pago`, `plazo`, `unidad_tiempo`, `propina`, `placa`, `guia_remision`, `valor_ret_iva`, `valor_ret_renta` |
| `LiquidacionCompra` | `03` | `proveedor`, `forma_pago`, `plazo`, `unidad_tiempo`, `correo` |
| `NotaCredito` | `04` | `receptor`, `motivo`, `cod_doc_modificado`, `num_doc_modificado`, `fecha_emision_doc_sustento`, `rise` |
| `NotaDebito` | `05` | `receptor`, `codigo_porcentaje_iva`, `base_imponible`, `forma_pago`, `cod_doc_modificado`, `num_doc_modificado` y sus `motivos` |
| `GuiaRemision` | `06` | `dir_partida`, `razon_social_transportista`, `ruc_transportista`, `placa`, `fecha_ini_transporte`, `fecha_fin_transporte` y sus `destinatarios` |
| `Retencion` | `07` | `sujeto_retenido`, `periodo_fiscal`, `parte_rel`, `tipo_sujeto_retenido` y sus `docs_sustento` |

Las líneas de factura, liquidación y nota de crédito (`FacturaDetalle`,
`LiquidacionCompraDetalle` y `NotaCreditoDetalle`) tienen los campos del `detalle`
del SRI: `codigo_principal` (25), `codigo_auxiliar` (25), `descripcion` (300),
`unidad_medida` (50), `cantidad` y `precio_unitario` con 6 decimales, `descuento`
con 2, `codigo_porcentaje_iva` y `datos_adicionales`.

#### Campos adicionales a la medida de la tienda

El SRI admite dos secciones libres y no hay que tocar el paquete para usarlas: se
escriben en el admin como pares `NOMBRE=VALOR` separados por `;` (o por renglones).

| Dónde | Campo | Va al XML | Máximo | Ejemplo |
|---|---|---|---|---|
| Línea (comprobante de venta) | `datos_adicionales` | `<detallesAdicionales>/<detAdicional>` | 3 | `MARCA=ACME; LOTE=2026-01` |
| Bien transportado (`GuiaDetalle`) | `datos_adicionales` | igual | 3 | `BULTO=12` |
| Comprobante (los seis) | `informacion_adicional` | `<infoAdicional>/<campoAdicional>` | 15 | `ORDEN=OC-0001; VENDEDOR=MARÍA` |
| Configuración del emisor | `campos_adicionales` | como el anterior, en **todos** los comprobantes | 15 | `SUCURSAL=MATRIZ` |

Los campos de la tienda se añaden primero y los del comprobante tienen prioridad,
así que una factura concreta puede sobrescribir el vendedor o la sucursal. Las
`observaciones` viajan además como el campo adicional «Observaciones».

```python
from factec.django import documentos

configuracion.campos_adicionales = "SUCURSAL=MATRIZ; VENDEDOR=JOHNNY"   # una sola vez

factura = documentos.Factura.objects.create(
    receptor=cliente,
    informacion_adicional="ORDEN=OC-0001; VENDEDOR=MARÍA",   # pisa el de la tienda
)
factura.detalles.create(producto=producto, cantidad=1,
                        datos_adicionales="MARCA=ACME; LOTE=2026-01")
```

El formato se valida al guardar (nombre y valor de hasta 300 caracteres, como el
esquema) y el error sale en el propio campo del admin. Si su proyecto ya guarda
estos datos en un `JSONField`, también se admite un diccionario:

```python
from factec.django.documentos import (
    leer_campos_adicionales, escribir_campos_adicionales,
)

leer_campos_adicionales("MARCA=ACME; LOTE=1")      # {'MARCA': 'ACME', 'LOTE': '1'}
leer_campos_adicionales({"MARCA": "ACME"})         # {'MARCA': 'ACME'}
escribir_campos_adicionales({"MARCA": "ACME"})     # 'MARCA=ACME'
```

#### Lo que se completa solo

En las líneas **basta con elegir el producto**: la descripción, el código, la
unidad de medida, el precio y el IVA se traen del catálogo. En el admin se
rellenan al instante al elegirlo (y el servidor hace lo mismo al guardar, así que
también funciona sin JavaScript).

Los campos que puede aportar el producto son **opcionales**: solo hay que
escribirlos cuando la línea va sin producto. El precio y el IVA empiezan vacíos a
propósito —el desplegable del IVA ofrece «— el del producto —»—, para no facturar
un precio 0 ni un IVA que no corresponda; si se escribe algo a mano, se respeta.

| Al… | Se calcula o completa |
|---|---|
| Elegir `producto` en una línea | Descripción, código, unidad de medida, precio e IVA |
| Guardar una línea con `producto` | Lo mismo, en el servidor (sin depender del navegador) |
| Guardar un documento | `secuencial` al emitir, con el contador `sri_fe_secuencial` |
| Emitir | El XML, la firma, el envío, la autorización y el vínculo con el comprobante |
| Emitir | Los totales (`subtotal`, `valor_iva`, `total`) son los mismos que van al XML |
| Guardar un impuesto de retención | La `tarifa` (según el IVA elegido), la base y el valor |
| Guardar una retención | El `valor_retenido` a partir de la base y el porcentaje |
| Guardar una línea de retención | El impuesto del documento sustento: base y valor |
| Guardar un comprobante | `num_doc_modificado` con guiones y `num_doc_sustento` sin guiones |
| No indicar el IVA de una línea | Se usa el 15 % (o el que tenga el producto) |

Los importes del modelo se calculan con el mismo código que el XML (no hay dos
verdades): `factura.total` es el `importeTotal` que se envía al SRI.

#### Cómo se emite

```python
from factec.django import documentos

cliente = documentos.Cliente.consumidor_final()
producto = documentos.Producto.objects.get(codigo_principal="SRV001")

factura = documentos.Factura.objects.create(receptor=cliente, forma_pago="19")
factura.detalles.create(producto=producto, cantidad=2)
factura.detalles.create(descripcion="Soporte mensual", cantidad=1,
                        precio_unitario=50, codigo_porcentaje_iva="0")

registro = factura.emitir()          # XML → firma → SRI → autorización

print(factura.estado)                # AUTORIZADO
print(factura.clave_acceso)          # 49 dígitos
print(factura.numero_autorizacion)
print(factura.xml())                 # XML sin firmar
print(factura.firmar())              # XML firmado, sin enviar
factura.reintentar()                 # retoma lo que quedó sin autorizar
```

| Método | Qué hace |
|---|---|
| `documento.emitir(encolar=, forzar=)` | Todo el ciclo; idempotente por documento |
| `documento.firmar()` | Devuelve el XML firmado sin enviarlo |
| `documento.xml()` | Devuelve el XML sin firmar |
| `documento.reintentar()` | Reintenta el envío del comprobante ya registrado |
| `documento.estado` | Estado ante el SRI (`BORRADOR` si aún no se emitió) |
| `documento.autorizado` | `True` si el SRI ya lo autorizó |
| `documento.total`, `.subtotal`, `.valor_iva` | Importes calculados como en el XML |

#### En el admin

El menú queda repartido en cuatro secciones, para no mezclar la configuración con
la facturación:

| Sección | Contenido |
|---|---|
| **Configuración del SRI** | Configuración del emisor y secuenciales |
| **Catálogos** | Clientes y productos |
| **Comprobantes** | Facturas, notas de crédito y débito, liquidaciones, guías de remisión, retenciones y sus detalles |
| **Emisión** | Comprobantes emitidos: clave de acceso, estado, autorización y XML |

Si el proyecto usa un ``AdminSite`` propio, hay que activar la agrupación a mano:

```python
from factec.django import admin_agrupado

admin_agrupado.organizar_el_indice(mi_site)
```

Además, en el listado de cada comprobante se puede **filtrar por el estado ante el
SRI** (autorizado, devuelto, en proceso, error…) y, desde el comprobante emitido,
el campo **Documento de origen** lleva al documento que lo generó. Cada ficha muestra los importes calculados, el estado ante el SRI y
el enlace al comprobante emitido, con dos acciones:

- **Emitir: firmar, enviar al SRI y esperar autorización**
- **Reintentar la emisión de los que quedaron pendientes**

Las líneas se editan en la misma pantalla (inline): elija el producto y el resto
se completa solo. El listado de comprobantes muestra el estado con color.

> Si despliega con `DEBUG = False`, ejecute `collectstatic`: el script que rellena
> las líneas va en los estáticos del paquete (`sri_fe/js/lineas.js`). Si no lo
> sirve, todo sigue funcionando: el servidor completa la línea al guardar.

#### Lo que no está modelado

`reembolsos` y `compensaciones` son secciones opcionales del XML que no usa la
mayoría de contribuyentes, así que no tienen modelo. Si las necesita, indíquelas
con un adaptador propio:

```python
from factec.django import adaptadores, facturacion
from factec.modelos import Compensacion, DetalleReembolso

@adaptadores.registrar_para(documentos.Factura)
class AdaptadorConReembolsos(adaptadores.AdaptadorFactura):
    def parametros(self, obj):
        parametros = super().parametros(obj)
        parametros["compensaciones"] = [
            Compensacion(codigo=c.codigo, tarifa=c.tarifa, valor=c.valor)
            for c in obj.compensaciones.all()
        ]
        return parametros

facturacion.emitir(factura)
```

### 14.3 Configurar los datos desde el admin

Al añadir la app aparece **Admin → Facturación electrónica (SRI) →
Configuraciones del emisor**. Escriba el RUC y guarde: **el resto de datos del
contribuyente se consultan automáticamente en el SRI**.

| Lo trae el SRI | Lo completa usted |
|---|---|
| Razón social | Nombre comercial |
| Régimen (`RIMPE`, `GENERAL`…) | Dirección de la matriz |
| Categoría (`NEGOCIO POPULAR`…) | Dirección del establecimiento |
| Obligado a llevar contabilidad | Establecimiento y punto de emisión |
| Si es contribuyente especial / agente de retención | El **n.º de resolución** de esos dos (el SRI solo dice si lo es) |
|  | Ambiente (pruebas/producción) |
|  | El archivo **`.p12`** y su **contraseña** |

La consulta no pisa lo que usted escriba: solo rellena los campos vacíos. Hay una
casilla **«Consultar los datos del RUC en el SRI al guardar»** para desactivarla
(si la desactiva, esos campos pasan a ser obligatorios a mano). También hay una
acción **«Actualizar los datos desde el SRI»** para refrescar varias
configuraciones de una vez.

Si el SRI no responde, el formulario **avisa pero no bloquea** el guardado,
siempre que los datos ya estén informados.

> El SRI **no publica direcciones**: `dir_matriz` y `dir_establecimiento` hay que
> escribirlas. Es texto libre en el esquema y no impide que el comprobante se
> acepte, pero forma parte del documento.

Al guardar el certificado, el formulario lo abre para comprobar que la contraseña
es correcta, que está vigente y que **su RUC coincide con el del emisor** (el SRI
rechaza los comprobantes firmados por otro contribuyente).

La contraseña **nunca se guarda en claro**: se cifra con Fernet. Hace falta una
clave de cifrado:

```bash
python -c "from factec.django.crypto import generar_clave; print(generar_clave())"
```

```bash
# .env — fuera del repositorio
SRI_CLAVE_CIFRADO=<la clave generada>
```

> Si la clave de cifrado cambia, las contraseñas guardadas dejan de poder
> descifrarse: hay que volver a escribirlas en el admin.

Solo puede haber **una configuración activa por ambiente**; al activar otra, la
anterior se desactiva automáticamente.

#### Desde la línea de comandos

El paquete incluye el comando, disponible en cualquier proyecto con la app
instalada (no hay que copiarlo):

```bash
python manage.py importar_ruc_sri --ruc 0703886697001 \
    --dir-matriz "PANAMERICANA Y CARCHI"

# Otro establecimiento o producción
python manage.py importar_ruc_sri --ruc 0703886697001 \
    --estab 001 --pto-emi 002 --ambiente 2 --dir-matriz "..."

# Sin consultar el SRI (solo datos indicados)
python manage.py importar_ruc_sri --ruc 0703886697001 --sin-consultar --sin-confirmar
```

#### Desde Python

```python
from factec.django import sri_datos

# Consultar y ver qué campos se pueden rellenar
resultado = sri_datos.consultar_datos("0703886697001")
resultado.campos    # {'razon_social': ..., 'regimen': ..., 'categoria': ..., ...}
resultado.avisos    # ['El SRI lo registra como contribuyente especial: ...']

# Rellenar una configuración (sin guardar)
sri_datos.completar_configuracion(configuracion, forzar=False)
sri_datos.faltantes_manuales(configuracion)   # lo que falta por escribir a mano
```

También está disponible en el núcleo, sin Django:

```python
from factec import consultar_ruc

datos = consultar_ruc("0703886697001")
datos.razon_social          # 'URDIN GONZALEZ JOHNNY EDGAR'
datos.regimen_rimpe_texto   # 'CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE'
```

### 14.4 Avisos automáticos (`manage.py check`)

Al añadir la app, Django avisa de lo que falta, con la indicación de dónde
completarlo:

| Código | Nivel | Cuándo |
|---|---|---|
| `sri_fe.W001` | Aviso | No hay ninguna configuración del emisor activa |
| `sri_fe.W002` | Aviso | Falta el archivo de firma `.p12` |
| `sri_fe.W003` | Aviso | Falta la contraseña del certificado |
| `sri_fe.E004` | Error | La contraseña cifrada no se puede descifrar (falta `SRI_CLAVE_CIFRADO`) |
| `sri_fe.E005` | Error | El certificado no se abre (contraseña incorrecta o archivo dañado) |
| `sri_fe.W006` | Aviso | El certificado está vencido |
| `sri_fe.E007` | Error | El RUC del certificado no coincide con el del emisor |
| `sri_fe.W008` | Aviso | La base de datos no está al día: ejecute `python manage.py migrate --skip-checks` |

Son avisos para no impedir el arranque: primero se levanta el servidor y se
rellena la configuración en el admin.

### 14.5 Emitir desde el código

Los servicios leen la configuración de la base de datos:

```python
from factec.django import services
from factec.modelos import Detalle, Impuesto, Receptor
from factec.catalogos import TarifaIva, TipoIdentificacion

registro = services.crear_factura(
    receptor=Receptor(
        razon_social="CONSUMIDOR FINAL",
        identificacion="9999999999999",
        tipo_identificacion=TipoIdentificacion.CONSUMIDOR_FINAL,
    ),
    detalles=[
        Detalle(descripcion="Servicio", cantidad=1, precio_unitario=100,
                impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)])
    ],
)
print(registro.clave_acceso, registro.estado)   # BORRADOR

services.encolar(registro)                      # a Celery (segundo plano)
```

Hay un constructor por comprobante: `crear_factura`, `crear_liquidacion_compra`,
`crear_nota_credito`, `crear_nota_debito`, `crear_retencion` y
`crear_guia_remision`. Cada uno reserva su **secuencial persistente** de forma
atómica.

Para pruebas o scripts:

```python
services.procesar(registro)          # firma + recepción + autorización (síncrono)
services.firmar(registro)
services.enviar(registro)
services.autorizar(registro)
services.emitir_ahora(comprobante)   # registrar + procesar
```

### 14.6 Emitir a partir de su propio modelo

La forma más corta: pase **su** objeto (un modelo de Django o cualquier objeto
de Python) y el paquete arma el XML, lo firma, lo envía al SRI y espera la
autorización.

```python
from factec.django import facturacion

venta = Venta.objects.get(pk=1)
registro = facturacion.emitir(venta)          # o emitir(venta, encolar=False)
print(registro.estado, registro.xml_autorizado)
```

`emitir()` es **idempotente por documento**: si esa venta ya tiene un
comprobante, se reutiliza y solo se reintenta el envío; así no se duplican
comprobantes ni se consumen secuenciales de más. Con `forzar=True` se crea uno
nuevo desde cero (por ejemplo tras corregir los datos).

El vínculo (y por tanto la idempotencia) necesita un **modelo de Django
guardado**. Con un objeto cualquiera —un diccionario, un `dataclass`— el
comprobante se emite igual, pero sin enlace: cada llamada genera uno nuevo.

``` python
facturacion.comprobante_de(venta)             # comprobante sin firmar
facturacion.firmar_modelo(venta)              # XML firmado (cadena)
facturacion.emitir(venta)                     # XML + firma + envío + autorización
facturacion.emitir(venta, forzar=True)        # reemite aunque ya exista
facturacion.emitir_lote(ventas)               # varios; devuelve una lista
facturacion.registro_de(venta)                # ComprobanteEmitido enlazado o None
facturacion.ya_emitido(venta)                 # True si ya está AUTORIZADO
facturacion.reintentar(venta)                 # retoma uno sin autorizar
```

`emitir_lote()` devuelve un registro por documento y, por omisión, **sigue con
el resto** si uno falla (la excepción viaja en la lista); con
`detener_en_error=True` se propaga en el primer fallo.

#### Cómo encuentra los datos: convención

El adaptador busca cada dato por varios nombres (`getattr` o claves de
diccionario, y también acepta métodos sin argumentos, listas y `QuerySet`):

| Dato del comprobante | Nombres que se prueban |
|---|---|
| Receptor | `receptor`, `cliente`, `comprador`, `adquiriente`, `proveedor`, `sujeto_retenido` |
| Fecha | `fecha_emision`, `fecha`, `fecha_factura`, `creado` |
| Secuencial | `secuencial`, `numero`, `numero_comprobante` |
| Líneas | `detalles`, `lineas`, `items`, `productos`, `renglones`, `detalle_set` |
| Descripción | `descripcion`, `concepto`, `nombre`, `producto`, `detalle` |
| Cantidad / precio | `cantidad` / `precio_unitario`, `precio`, `valor_unitario`, `valor` |
| IVA de la línea | `codigo_porcentaje_iva`, `codigo_porcentaje`, `tarifa_iva`, `iva_codigo` |
| Código de la línea | `codigo_principal`, `codigo`, `codigo_interno`, `sku` |
| Unidad de medida | `unidad_medida`, `unidad`, `medida` |
| Descuento | `descuento`, `descuento_unitario` |
| Formas de pago | `pagos`, `forma_pago`, `formas_pago`, `tipo_pago` |
| Emisor | `emisor`, `emisor_electronico`, `contribuyente` o la configuración activa |
| Ambiente | `ambiente` o la configuración activa |

Basta con que su modelo exponga los nombres de la primera columna; lo demás se
deduce:

```python
class Venta(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    fecha = models.DateField()
    numero = models.CharField(max_length=9)

    @property
    def detalles(self):                 # DetalleVenta.objects.filter(...)
        return self.detalleventa_set.all()

    @property
    def ambiente(self):
        return 1                        # 1 pruebas, 2 producción
```

El tipo de identificación del receptor se deduce del propio número (13 dígitos
→ cédula, 10 → cédula, `...001` → RUC, si no → pasaporte), y los totales se
calculan. Los impuestos de cada línea se indican con el **código del SRI**:

| Código | Significado |
|---|---|
| `0` | IVA 0 % |
| `2` | IVA 5 % |
| `3` | IVA 12 % |
| `4` | IVA 15 % |
| `5` | IVA 5 % (obsoleto) |
| `6` | No objeto de IVA |
| `7` | Exento de IVA |
| `8` | IVA diferenciado |
| `10` | IVA 13 % |

#### Cómo forzar un comprobante concreto

Si el objeto no sigue la convención, o quiere fijar el tipo, pase un adaptador:

```python
from factec.django import adaptadores, facturacion

facturacion.emitir(venta, adaptador=adaptadores.AdaptadorNotaCredito)
facturacion.emitir(venta, tipo="05")            # '01'..'07'
```

| Clase | `codDoc` | Comprobante |
|---|---|---|
| `AdaptadorFactura` | `01` | Factura |
| `AdaptadorLiquidacionCompra` | `03` | Liquidación de compra |
| `AdaptadorNotaCredito` | `04` | Nota de crédito |
| `AdaptadorNotaDebito` | `05` | Nota de débito |
| `AdaptadorRetencion` | `07` | Comprobante de retención |
| `AdaptadorGuiaRemision` | `06` | Guía de remisión |

Y para su propia convención, registre un adaptador una sola vez:

```python
from factec.django import adaptadores
from factec.modelos import Detalle

@adaptadores.registrar_para(MiVenta)
class AdaptadorFacturaInterna(adaptadores.AdaptadorFactura):
    """Se reescriben solo los datos que no siguen la convención."""

    def receptor(self, venta):
        return adaptadores.Receptor(
            razon_social=venta.tercero.nombre,
            identificacion=venta.tercero.documento,
            direccion=venta.tercero.domicilio,
        )

    def detalles(self, venta):
        base = adaptadores.AdaptadorFactura()
        return [base.detalle_desde_linea(linea, venta) for linea in venta.renglones]

facturacion.emitir(venta)          # ya encuentra el adaptador por el modelo
```

Para el caso en que el modelo **no** determine el tipo de comprobante (por
ejemplo, un mismo documento que puede emitirse como factura o nota de crédito),
indique el tipo en la llamada:

```python
facturacion.emitir(nota, tipo="04")     # usa AdaptadorNotaCredito
```

#### Vínculo con el documento

El `ComprobanteEmitido` guarda el enlace con su documento (`content_type`,
`object_id`), así que puede consultarlo en cualquier momento:

```python
facturacion.registro_de(venta)          # ComprobanteEmitido o None
```

Y, si quiere la relación inversa desde su modelo, añada un `GenericRelation`:

```python
from django.contrib.contenttypes.fields import GenericRelation

class Venta(models.Model):
    comprobantes_sri = GenericRelation("sri_fe.ComprobanteEmitido")

venta.comprobantes_sri.filter(estado="AUTORIZADO").exists()
```

`emitir_lote()` y `reintentar()` cubren la operación normal: emita en lote lo
pendiente y reintente lo que quedó en `ERROR` o `EN_PROCESO` sin volver a
construir el XML ni pedir otro secuencial.

### 14.7 Celery

```python
# settings.py
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_BEAT_SCHEDULE = {
    "sri-reintentar-pendientes": {
        "task": "sri_fe.reintentar_pendientes",
        "schedule": crontab(minute="*/10"),
    },
}
```

| Tarea | Para qué |
|---|---|
| `sri_fe.emitir_comprobante` | Firma, envía y espera la autorización |
| `sri_fe.consultar_autorizacion` | Consulta el estado de un comprobante ya recibido |
| `sri_fe.reintentar_pendientes` | Recupera los que no llegaron a estado final |

```bash
celery -A mi_proyecto worker -l info
celery -A mi_proyecto beat -l info     # opcional, para los reintentos
```

Si el SRI responde `EN PROCESO`, la tarea se reprograma sola en lugar de
mantener el worker ocupado consultando en bucle.

### 14.8 Estados del comprobante

```
BORRADOR ──firmar──► FIRMADO ──enviar──► RECIBIDO ──autorizar──► AUTORIZADO
                        │                  │                        ▲
                        └──► DEVUELTO      └──► EN_PROCESO ─────────┘
                                           └──► NO_AUTORIZADO
                        ERROR (fallo de red o de comunicación con el SRI)
```

| Estado | Significado |
|---|---|
| `BORRADOR` | Registrado, sin firmar |
| `FIRMADO` | XML firmado con XAdES-BES |
| `RECIBIDO` | El SRI aceptó el comprobante |
| `DEVUELTO` | El SRI lo rechazó (los motivos quedan en `mensajes`) |
| `EN_PROCESO` | El SRI aún no lo autoriza |
| `AUTORIZADO` | Autorizado; `xml_autorizado` es el documento con validez legal |
| `NO_AUTORIZADO` | Rechazado en la autorización |
| `ERROR` | Fallo de comunicación; se puede reintentar |

Los mensajes del SRI se guardan en `mensajes` (JSON) y el último fallo en `error`.

### 14.9 Ajustes disponibles

```python
FACTURACION_ELECTRONICA = {
    "AMBIENTE": 2,                    # respaldo si no hay configuración activa
    "CLAVE_CIFRADO": "...",           # o SRI_CLAVE_CIFRADO (recomendado)
    "REINTENTOS_AUTORIZACION": 6,
    "ESPERA_AUTORIZACION": 4.0,
    "GUARDAR_XML": True,              # guardar los XML en la base de datos
    "CELERY_QUEUE": "facturacion",     # cola para las tareas
    "CELERY_PREFIX": "sri_fe",
    "TIMEOUT": 30.0,
    "TIMEOUT_CONSULTA_SRI": 15.0,      # espera máxima al consultar el RUC
    "CONSULTAR_SRI_AUTOMATICAMENTE": True,
    "EMITIR_CON_CELERY": True,         # emitir() encola; False = síncrono
    "VALIDAR_VIGENCIA": True,
    "ALGORITMO_FIRMA": "sha1",
}
```

También se puede usar el paquete **sin** base de datos, definiendo `EMISOR` y
`CERTIFICADO` en los ajustes (útil en pruebas).

---

## Licencia

MIT. Consulte [LICENSE](./LICENSE).
