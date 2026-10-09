# Uso sin Django

El núcleo del paquete: clave de acceso, XML de los seis comprobantes, firma XAdES-BES, envío al SRI, catálogos, línea de comandos y referencia de la API.

> Documentación de [**factec**](../README.md) · volver al README

---

## Inicio rápido

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

## Clave de acceso

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

## Los ambientes: «pruebas» y «producción»

El SRI tiene dos ambientes, y en el paquete se pueden escribir por su nombre en
cualquier sitio donde se espere el número:

| Para escribir | Número | Qué es |
|---|---|---|
| `"pruebas"` (o `prueba`, `test`, `testing`, `sandbox`, `certificación`) | `1` | Comprobantes **sin validez fiscal**, para ensayar. Es el valor por omisión |
| `"producción"` (o `produccion`, `prod`, `production`, `real`) | `2` | Comprobantes con **validez legal** ante el SRI |

```python
from factec import EmisorElectronico
from factec.catalogos import Ambiente, leer_ambiente

EmisorElectronico(..., ambiente="pruebas")      # igual que ambiente=1
leer_ambiente("producción")                     # 2
leer_ambiente(Ambiente.PRUEBAS)                 # 1
leer_ambiente("producion")                      # ErrorValidacion: use «pruebas» o «producción»

ClienteSRI(ambiente="pruebas")                  # el cliente SOAP, igual
```

Y en la línea de comandos:

```bash
sri-fe autorizar <clave> --ambiente pruebas
sri-fe enviar factura.xml --ambiente producción
```

## Fecha de emisión (ventana que exige el SRI)

El SRI rechaza el comprobante con el mensaje 65, «FECHA EMISIÓN EXTEMPORANEA»,
cuando la fecha de emisión es posterior a la del servidor del SRI o está fuera del
rango de tolerancia (**129600 minutos = 90 días**). Como el servidor del SRI está
en Ecuador (UTC-5), comparar contra la hora local de su servidor puede fallar por
un día: a las 20:00 en Quito ya es el día siguiente en UTC.

El paquete lo comprueba al construir el XML, sin gastar secuencial ni llamar al
SRI:

```python
from factec.sri.fechas import DIAS_TOLERANCIA, hoy_en_ecuador, validar_fecha_emision

hoy_en_ecuador()                          # la fecha con la que compara el SRI
validar_fecha_emision(hoy_en_ecuador())   # sin problema
validar_fecha_emision(hoy_en_ecuador().replace(day=...))   # lanza ErrorValidacion
```

| Ayuda | Para qué |
|---|---|
| `hoy_en_ecuador()` | Hoy según el reloj del SRI (UTC-5 fijo) |
| `validar_fecha_emision(fecha)` | Lanza `ErrorValidacion` si el SRI la rechazaría |
| `DIAS_TOLERANCIA` / `MINUTOS_TOLERANCIA` | 90 días / 129600 minutos |

Es el único «hoy» del paquete: no use `date.today()` ni `timezone.localdate()` para
decidir el día de un comprobante. En un servidor con `TIME_ZONE = "UTC"` (lo habitual)
el día del servidor va por delante desde las 19:00 de Ecuador, así que la fecha del
XML, la del borrador y la de las listas del admin acabarían siendo distintas.

Si prefiere emitir de todos modos (por ejemplo para reproducir un error), ponga
`VALIDAR_FECHA_EMISION = False` en la clase o instancia del comprobante.

### Un comprobante se firma el día en que se emite

La fecha de emisión la valida el SRI contra su propio reloj, así que no puede ser la
fecha en la que se preparó el comprobante: si un XML quedó guardado y se firma días
después, hay que cambiarle la fecha. Al hacerlo **cambia también la clave de
acceso**, porque la clave empieza por la fecha:

```python
from datetime import date

from factec.fechado import cambiar_fecha_de_emision

cambiado = cambiar_fecha_de_emision(xml_sin_firmar, date(2026, 10, 8))
cambiado.fecha_anterior       # date(2026, 10, 1)
cambiado.fecha                # date(2026, 10, 8)
cambiado.clave_anterior       # la clave vieja
cambiado.clave_acceso         # la nueva, ya con la fecha nueva
cambiado.xml                  # listo para firmar

# Admite también «08/10/2026», «2026-10-08» y datetime
cambiar_fecha_de_emision(xml, "08/10/2026")
```

Solo funciona con el XML **sin firmar** (la firma cubre el contenido anterior); si
le pasa uno firmado, lanza `ErrorValidacion` explicándolo. El resto del comprobante
—número, secuencial, código numérico— se conserva.

En la línea de comandos:

```bash
sri-fe fecha factura.xml --fecha 2026-10-08 --salida factura_hoy.xml
```

## Firma electrónica (XAdES-BES)

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

## Envío al SRI

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

## Interfaz de línea de comandos

```bash
sri-fe catalogos                                   # tablas de códigos del SRI
sri-fe catalogos --json

sri-fe clave --tipo 01 --ruc 1790012345001 --serie 001001 \
             --secuencial 1 --codigo 12345678 --fecha 08/10/2026
sri-fe clave --clave 0810202601179001234500110010010000000011234567819

sri-fe ejemplo --salida ./salida                   # XML de los 6 comprobantes
sri-fe firmar salida/factura.xml --certificado firmante.p12 --clave-clave mi-clave

sri-fe leer salida/factura_firmado.xml             # datos del comprobante
sri-fe leer factura_proveedor.xml --json

sri-fe verificar salida/factura_firmado.xml        # firma, clave, fecha y totales
sri-fe verificar salida/factura_firmado.xml --solo-firma   # solo las firmas XAdES-BES

sri-fe revisar salida/factura.xml --certificado firmante.p12 --clave-clave mi-clave
sri-fe revisar --certificado firmante.p12 --clave-clave mi-clave   # solo la firma
sri-fe fecha salida/factura.xml --fecha 2026-10-08 --salida factura_hoy.xml

sri-fe autorizar 0810202601179001234500110010010000000011234567819 --ambiente pruebas
sri-fe enviar salida/factura_firmado.xml --ambiente pruebas

sri-fe actualizacion                               # ¿hay versión nueva? (10 si la hay)
sri-fe actualizacion --forzar --json               # mira ahora, en JSON
```

### Aviso de versión

Al terminar cualquier comando, `sri-fe` cuenta si hay una versión nueva del
paquete (o si se acaba de actualizar), sin salir a la red y sin cambiar el
resultado del comando: solo escribe en la salida de errores.

```
╔═══════════════════════════════════════════════════════════════════════════╗
║ ↑  Hay una versión nueva de factec                                        ║
║                                                                           ║
║ Instalada   1.10.1                                                        ║
║ Disponible  1.11.0                                                        ║
║                                                                           ║
║ Para actualizar:                                                          ║
║   pip install -U "factec @ git+https://github.com/jurdin32/factec.git"    ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

Lo que decide es `factec.actualizacion`, que guarda lo último comprobado en
`~/.cache/factec/actualizacion.json` (24 horas de validez). Los comandos que
sí salen a la red son `sri-fe actualizacion`, `manage.py comprobar_actualizacion`
y la tarea semanal `sri_fe.comprobar_actualizacion`.

```python
from factec.actualizacion import actualizacion_disponible, comprobar, esta_al_dia, ultima_conocida

comprobar()                 # sale a la red (o usa lo guardado, si es de hoy)
esta_al_dia(), actualizacion_disponible(), ultima_conocida()
```

Se apaga con `FACTEC_SIN_AVISOS=1` (no enseñar nada) y con `FACTEC_SIN_COMPROBAR=1`
(no salir a la red). `FACTEC_REPOSITORIO=usuario/repo` mira otro repositorio y
`FACTEC_CACHE_DIR=...` guarda el estado en otro sitio. Nada de esto lanza
excepciones: sin internet, el paquete sigue funcionando igual.

`verificar` usa el certificado que va dentro del XML, así que para comprobantes
de terceros no hace falta indicar nada. El informe completo se imprime en JSON
(la salida se puede canalizar a otro programa) y los mensajes legibles van a la
salida de errores; devuelve código 4 si el comprobante no pasa la verificación.

---

## Catálogos

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

## API principal

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

## Leer comprobantes

`factec.lectura` pasa cualquier XML de comprobante (factura, liquidación de
compra, nota de crédito, nota de débito, guía de remisión o retención) a objetos
de Python. Sirve igual para los suyos y para los de terceros.

```python
from factec import leer_comprobante, leer_autorizacion

comprobante = leer_comprobante(xml)
comprobante.tipo                 # "01"
comprobante.descripcion_tipo     # "Factura"
comprobante.numero               # "001-001-000000013"
comprobante.fecha_emision        # date(2026, 10, 8)
comprobante.clave_acceso
comprobante.emisor.ruc           # datos del emisor, obligado a contabilidad, RIMPE…
comprobante.receptor.razon_social
comprobante.receptor.es_consumidor_final
comprobante.totales.importe_total
comprobante.totales.valor_impuestos
comprobante.detalles[0].descripcion
comprobante.detalles[0].codigo_auxiliar
comprobante.detalles[0].datos_adicionales
comprobante.info_adicional
comprobante.pagos
comprobante.a_dict()             # listo para JSON (fechas y decimales como texto)
```

`extras` lleva lo propio de cada documento: el `motivo` y el documento modificado
de la nota de crédito, el transportista y los destinatarios de la guía, o el
`sujeto_retenido` y los `docs_sustento` de la retención.

De la respuesta del SRI se saca todo lo de la autorización, incluido el
comprobante autorizado:

```python
autorizacion = leer_autorizacion(respuesta_xml)
autorizacion.estado, autorizacion.autorizada
autorizacion.numero_autorizacion, autorizacion.fecha_autorizacion
autorizacion.mensajes
autorizacion.comprobante.totales.importe_total
```

Si lo que hay en el XML viene con prefijos (`soap:`, `ds:`, `ns2:`), se limpian
solos; y si el documento tiene un nombre de campo distinto al habitual, los
buscadores son tolerantes (por ejemplo `codigoPrincipal` o `codigoInterno`).

## Verificar comprobantes

`factec.verificacion` comprueba todo lo que se puede revisar sin salir a la red:

| Comprobación | Detalle |
|---|---|
| **Firma** XAdES-BES | Digest del documento, digest de `SignedProperties` y RSA con el certificado que trae el XML |
| **Certificado** | Vigente y del mismo RUC que el emisor |
| **Clave de acceso** | Dígito verificador y coherencia con el documento (fecha, tipo, RUC, serie, secuencial) |
| **Fecha de emisión** | Dentro de la ventana del SRI (90 días, nunca futura) |
| **Totales** | La suma de las líneas, los impuestos y el importe total |

```python
from factec import verificar_comprobante

informe = verificar_comprobante(xml_de_mi_proveedor)
informe.ok              # True si todo cuadra
informe.problemas       # ["La firma no es válida: el XML fue alterado"]
informe.avisos          # cosas no bloqueantes (versión distinta, sin certificado…)
informe.firma, informe.clave_valida, informe.clave_coincide
informe.fecha_en_rango, informe.totales_cuadran
informe.certificado.nombre, informe.certificado.ruc, informe.certificado.vencido()
informe.a_dict()
```

Con `exigir_firma=False` se revisa un borrador propio (todavía sin firmar). Y si
el certificado no viene incrustado en el XML, se puede pasar el que corresponda
con `certificado=`.

Para el estado en el SRI:

```python
from factec import verificar_en_el_sri

autorizacion = verificar_en_el_sri(clave_acceso, ambiente=1)
autorizacion.autorizada
autorizacion.estado          # "AUTORIZADO", "NO AUTORIZADO", "NO ENCONTRADO"…
autorizacion.mensajes
```

## Revisar antes de emitir

`factec.revision` mira **lo que puede impedir la emisión** sin contactar con el SRI,
para no firmar ni enviar algo que va a volver rechazado (con el secuencial ya
gastado):

| Comprobación | Detalle |
|---|---|
| **Certificado** | Que se pueda abrir, que esté vigente y que su RUC sea el del emisor |
| **Vencimiento próximo** | Aviso (no error) con `DIAS_AVISO_CERTIFICADO` días de antelación |
| **Fecha de emisión** | Dentro de la ventana del SRI |
| **Datos** | Totales, clave de acceso y el resto de reglas del SRI |

```python
from factec.revision import revisar_certificado, revisar_emision, revisar_xml

informe = revisar_emision(comprobante, certificado)     # el comprobante sin firmar
informe.puede_emitir      # False si hay algo que el SRI rechazaría
informe.problemas         # ["El certificado de firma está vencido desde el 14/10/2026…"]
informe.avisos            # ["El certificado de firma vence el 14/10/2026 (en 6 día(s))…"]
informe.certificado.vencido, informe.certificado.dias_restantes, informe.certificado.ruc
informe.fecha_en_rango, informe.datos_validos, informe.clave_valida
informe.resumen()         # una línea para un log o un mensaje
informe.a_dict()          # listo para JSON

# Un comprobante ya construido (el XML guardado)
revisar_xml(xml, certificado=certificado, emisor=emisor)

# Solo el certificado (por ejemplo, al arrancar o en un chequeo programado)
revisar_certificado(certificado, emisor=emisor, dias_aviso=30)
```

El emisor lo revisa solo antes de firmar, así que un certificado vencido no llega
a enviarse:

```python
from factec import EmisorElectronico
from factec.excepciones import ErrorRevision

emisor = EmisorElectronico(emisor=emisor_, certificado="firmante.p12",
                           clave_certificado="mi-clave")

informe = emisor.revisar(factura)        # consultar sin emitir
try:
    resultado = emisor.emitir(factura)   # revisa y, si hay problemas, no emite
except ErrorRevision as error:
    print(error.informe.problemas)       # lo que hay que corregir
    print(error.informe.avisos)          # lo que conviene atender

emisor.emitir(factura, revisar=False)    # omitir la revisión (no recomendado)
```

## Validación contra los XSD

Los esquemas oficiales del SRI **no declaran** el elemento `ds:Signature`, por lo
que la validación XSD aplica al XML **sin firmar**; la firma se valida por
separado (`verificar_firma`). El flujo correcto es: generar → validar → firmar → enviar.

Para ejecutar las pruebas que validan contra los XSD, indique dónde están:

```bash
SRI_XSD_DIR=/ruta/a/los/xsd pytest
```

Las pruebas se saltan solas si no encuentra los esquemas, y **no** se incluyen en
el paquete por no redistribuir material de terceros. Obténgalos de la
documentación de comprobantes electrónicos del SRI: se necesitan seis archivos
(`factura_V1.1.0.xsd`, `NotaCredito_V1.1.0.xsd`, `NotaDebito_V1.0.0.xsd`,
`ComprobanteRetencion_V2.0.0.xsd`, `GuiaRemision_V1.1.0.xsd` y
`LiquidacionCompra_V1.1.0.xsd`). Guárdelos en una carpeta que no se borre sola
—no en `/tmp`, que el sistema limpia— y pase esa ruta en `SRI_XSD_DIR`.

---

## Notas y limitaciones

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

## Estructura del proyecto

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

## Ejemplos y pruebas

```bash
python examples/factura_basica.py          # genera XML (sin certificado ni red)
python examples/prueba_real.py --config prueba_config.json --solo-diagnostico
python examples/prueba_real.py --config prueba_config.json   # emisión real en pruebas
SRI_XSD_DIR=/tmp/sri_xsd pytest            # incluye las que validan contra los XSD
```
