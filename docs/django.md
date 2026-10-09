# Integración con Django

La app `factec.django`: migraciones, modelos, admin, adaptadores para sus propios modelos, tareas de Celery y todos los ajustes.

> Documentación de [**factec**](../README.md) · volver al README

---

El paquete trae una app de Django lista para usar, con las tablas, el admin y las
tareas de Celery. Se instala con el extra:

```bash
pip install "factec[django]"
```

### Añadir la app y migrar

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

#### El ambiente: «pruebas» o «producción»

El ajuste `AMBIENTE` es solo un respaldo (el que manda es el ambiente de la
configuración del emisor, que se elige en el admin). Se puede escribir el nombre
en lugar del número, que es más difícil de equivocar:

```python
FACTURACION_ELECTRONICA = {
    "AMBIENTE": "pruebas",     # 1: comprobantes sin validez fiscal
    # "AMBIENTE": "producción",  # 2: comprobantes con validez legal
}
```

Se admiten `pruebas` (o `prueba`, `test`, `testing`, `sandbox`, `certificación`, y
el número `1`) y `producción` (o `produccion`, `prod`, `production`, `real`, y el
número `2`). Con cualquier otra cosa, el paquete lo dice al arrancar:

```
factec.excepciones.ErrorValidacion: Ambiente desconocido: 'producion'.
Use «pruebas» o «producción» (o 1 y 2).
```

Lo mismo vale en la línea de comandos: `sri-fe autorizar <clave> --ambiente producción`.

#### Ponga la zona horaria de Ecuador

```python
LANGUAGE_CODE = "es-ec"
TIME_ZONE = "America/Guayaquil"
USE_TZ = True
```

El SRI compara la ``fechaEmision`` con la fecha de **su** servidor, que está en
Ecuador. Con el valor por omisión de Django (`TIME_ZONE = "UTC"`, `en-us`) el admin
muestra el día siguiente a partir de las 19:00 hora de Ecuador, así que es fácil
emitir con fecha de mañana y recibir un «Devuelto» por `FECHA EMISIÓN
EXTEMPORANEA` (mensaje 65). El paquete valida la fecha igualmente —no se puede
guardar ni emitir un comprobante con fecha futura o de más de 90 días—, pero más
vale no ver el error.

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

### Los modelos del SRI (clientes, productos y comprobantes)

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
    tipo=documentos.TipoProducto.SERVICIO,    # o PRODUCTO (un bien)
    codigo_principal="SRV001",                # máximo 25 caracteres
    descripcion="Servicio de desarrollo",     # máximo 300
    unidad_medida="hora",                     # si se omite: UNIDAD o SERVICIO, según el tipo
    precio_unitario=100,
    codigo_porcentaje_iva="4",                # 4 = IVA 15 %
    datos_adicionales="MODALIDAD=REMOTA",     # van a cada línea que use el producto
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

`Producto` se clasifica con **`tipo`**: `TipoProducto.PRODUCTO` (un bien) o
`TipoProducto.SERVICIO`. Es una clasificación de la tienda —el SRI no la distingue
en la factura— que sirve para filtrar y buscar en el admin, y para proponer la
unidad de medida (`UNIDAD_POR_TIPO`). Al elegir un producto en la línea, esta
hereda todo lo que el producto tenga: descripción, códigos, unidad, precio, IVA y
sus **detalles adicionales** (`Producto.datos_adicionales`).

#### Campos adicionales a la medida de la tienda

El SRI admite dos secciones libres y no hay que tocar el paquete para usarlas: se
escriben en el admin como pares `NOMBRE=VALOR` separados por `;` (o por renglones).

| Dónde | Campo | Va al XML | Máximo | Ejemplo |
|---|---|---|---|---|
| **Producto o servicio** (`Producto`) | `datos_adicionales` | los hereda cada línea que lo use | 3 | `MARCA=ACME; GARANTIA=12 MESES` |
| Línea (comprobante de venta) | `datos_adicionales` | `<detallesAdicionales>/<detAdicional>` | 3 | `MARCA=ACME; LOTE=2026-01` |
| Bien transportado (`GuiaDetalle`) | `datos_adicionales` | igual | 3 | `BULTO=12` |
| Comprobante (los seis) | `informacion_adicional` | `<infoAdicional>/<campoAdicional>` | 15 | `ORDEN=OC-0001; VENDEDOR=MARÍA` |
| Configuración del emisor | `campos_adicionales` | como el anterior, en **todos** los comprobantes | 15 | `SUCURSAL=MATRIZ` |

Los campos de la tienda se añaden primero y los del comprobante tienen prioridad,
así que una factura concreta puede sobrescribir el vendedor o la sucursal. Las
`observaciones` viajan además como el campo adicional «Observaciones».

Los **detalles adicionales de la línea** se configuran una sola vez, en el producto o
servicio, y la línea los hereda al elegirlo (en el admin se rellenan solos, como el
resto de los datos del producto). Lo que se escriba en la línea tiene prioridad, y
también se heredan si usa sus propios modelos: el adaptador mira los de la línea y,
si están vacíos, los del producto al que apunta.

```python
from factec.django import documentos
from factec.django.documentos import Producto, TipoProducto

producto = Producto.objects.create(                    # producto o servicio
    tipo=TipoProducto.SERVICIO, codigo_principal="ASES",
    descripcion="Asesoría mensual", precio_unitario=200,
    datos_adicionales="MODALIDAD=REMOTA",               # viaja en cada línea
)

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

#### Comprobantes devueltos por el SRI

Si el SRI devuelve un comprobante (`DEVUELTO`) o lo rechaza en la autorización
(`NO_AUTORIZADO`), el XML guardado ya no sirve: reenviarlo daría el mismo error.
Al corregir el documento y volver a pulsar **Emitir** (o `emitir()`), el paquete:

* construye un comprobante **nuevo** con los datos actuales y otra clave de acceso;
* reutiliza el **mismo secuencial** del documento, sin gastar otro;
* conserva el comprobante rechazado como historial (con sus `mensajes` del SRI).

`reintentar()` hace lo mismo: con un estado rechazado rehace el comprobante en
lugar de reenviar el XML devuelto.

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

### Configurar los datos desde el admin

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

### Avisos automáticos (`manage.py check`)

Al añadir la app, Django avisa de lo que falta, con la indicación de dónde
completarlo:

| Código | Nivel | Cuándo |
|---|---|---|
| `sri_fe.W001` | Aviso | No hay ninguna configuración del emisor activa |
| `sri_fe.W002` | Aviso | Falta el archivo de firma `.p12` |
| `sri_fe.W003` | Aviso | Falta la contraseña del certificado |
| `sri_fe.E004` | Error | La contraseña cifrada no se puede descifrar (falta `SRI_CLAVE_CIFRADO`) |
| `sri_fe.E005` | Error | El certificado no se abre (contraseña incorrecta o archivo dañado) |
| `sri_fe.E006` | Error | El certificado está vencido (no se puede emitir nada con él) |
| `sri_fe.E007` | Error | El RUC del certificado no coincide con el del emisor |
| `sri_fe.W008` | Aviso | La base de datos no está al día: ejecute `python manage.py migrate --skip-checks` |
| `sri_fe.E009` | Error | Hay configuraciones del emisor, pero ninguna activa |
| `sri_fe.W010` | Aviso | El certificado vence en pocos días (`DIAS_AVISO_CERTIFICADO`) |

Son avisos para no impedir el arranque: primero se levanta el servidor y se
rellena la configuración en el admin. **Un certificado vencido sí es un error**:
con él el SRI devuelve todo lo que se emita.

### Revisar antes de emitir (certificado y datos)

Antes de firmar y enviar se revisa lo que haría que el SRI devolviera el
comprobante: el certificado (abrible, vigente y del mismo RUC) y los datos (fecha
de emisión, totales y clave de acceso). Si algo falla **no se emite**: se explica
el motivo y no se gasta secuencial.

| Punto | Dónde se ve |
|---|---|
| Aviso en los listados del admin | Al abrir **Comprobantes emitidos** o **Configuraciones del emisor**, si la firma impide emitir (o está por vencer) |
| Acción **«Revisar antes de emitir (certificado y datos)»** | Sobre los comprobantes seleccionados: informa sin firmar ni enviar |
| Columna **«Firma»** | `válido`, `vence en N día(s)`, `vencido — no se puede emitir` |
| `manage.py check` | Al arrancar (ver los avisos de arriba) |
| `manage.py revisar_firma` | A mano o programado (ver más abajo) |

```python
from factec.django import services

informe = services.revisar_comprobante(comprobante)   # antes de registrarlo
informe = services.revisar(registro)                  # el ya guardado (su XML)
informe = registro.revisar()                          # lo mismo, desde el modelo
informe = consulta.revisar(registro)                  # lo mismo, desde una vista

informe.puede_emitir, informe.problemas, informe.avisos
informe.certificado.dias_restantes, informe.certificado.ruc
informe.a_dict()
```

`facturacion.emitir()` hace esa revisión por su cuenta (`REVISAR_ANTES_DE_EMITIR`) y
lanza `ErrorRevision` —con el informe dentro— en lugar de emitir algo que va a
volver mal:

```python
from factec.django import facturacion
from factec.excepciones import ErrorRevision

try:
    registro = facturacion.emitir(mi_factura)
except ErrorRevision as error:
    return JsonResponse({"problemas": error.informe.problemas}, status=400)

facturacion.emitir(mi_factura, revisar=False)          # omitir la revisión
```

En la emisión por tareas (Celery o `services.procesar`) el comprobante no se envía
y queda en `ERROR` con el motivo (también en `error.txt`), listo para reintentar
cuando se arregle.

#### La fecha de emisión: la del día en que se firma

El SRI valida la fecha de emisión contra su propio reloj, así que un comprobante se
firma **el día en que se emite**: con `FECHA_EMISION_AL_EMITIR` (activo por
omisión) la fecha del comprobante es la de hoy en Ecuador, no la del documento. Un
borrador que quedó de otro día se **refecha** antes de firmarlo (nueva fecha, nueva
clave de acceso) en vez de reenviarse con la fecha vieja.

```python
from factec.django import facturacion

# La fecha del documento se respeta (dentro de la ventana del SRI)
facturacion.emitir(mi_factura, fecha_emision=date(2026, 10, 1))

# Refechar un comprobante ya guardado (antes de firmarlo)
services.actualizar_fecha(registro)          # a hoy; devuelve el registro actualizado
registro.fecha_desactualizada                # True si quedó sin enviar de otro día

# Ver qué XML se va a firmar, sin emitir nada
comprobante = facturacion.comprobante_de(mi_factura)   # fecha del documento
facturacion.fijar_fecha_de_emision(comprobante)        # la del día de la firma
comprobante.fecha_emision, comprobante.clave
```

Como la fecha la pone la firma, un documento con una fecha mal puesta (futura o
de hace meses) no inutiliza el comprobante: se emite con la de hoy, que es la que el
SRI acepta.

Solo se puede refechar un comprobante **que no se haya enviado** (`intentos == 0`):
una vez que el SRI lo recibió, la clave está registrada allí. En el admin está la
acción **«Actualizar la fecha de emisión al día de hoy»**, el filtro **«Sin enviar,
de otro día (hay que refechar)»**, y `FECHA_EMISION_AL_EMITIR = False` desactiva el
comportamiento (cada emisión usa la fecha del documento).

#### Que el aviso llegue antes: la revisión programada

```bash
python manage.py revisar_firma            # ¿se puede emitir? (código 1 si no)
python manage.py revisar_firma --correo   # además, avisa a CORREOS_AVISO
python manage.py revisar_firma --sin-pendientes --dias 15
```

```python
# settings.py — con Celery Beat, programada desde el propio paquete
from factec.django.conf import planificador

CELERY_BEAT_SCHEDULE = {**planificador()}     # revisa la firma a las 7:00
                                              # y reintenta los pendientes cada 10 min
CELERY_TIMEZONE = TIME_ZONE                   # la hora se interpreta en esa zona
# o elija la hora: planificador(a_las=6, minuto=30)

FACTURACION_ELECTRONICA = {
    "CORREOS_AVISO": ["administracion@mitienda.ec"],   # si no, se usan los ADMINS
    "DIAS_AVISO_CERTIFICADO": 30,
}
```

La tarea `sri_fe.revisar_certificado` envía el informe por correo y lo deja en el
log. Sin Celery, programe el comando en el cron:

```bash
0 7 * * * cd /ruta/del/proyecto && ./venv/bin/python manage.py revisar_firma --correo
```

### Archivos del comprobante

Además de los XML en la base de datos (`GUARDAR_XML`), cada comprobante deja sus
archivos en disco (`GUARDAR_ARCHIVOS`, activado por omisión) dentro de
`MEDIA_ROOT`, ordenados por año, mes y día::

    media/sri/comprobantes/2026/10/08/001-001-000000012_<clave de acceso>/
        sin_firma.xml                # el XML tal como se construyó
        firmado.xml                  # con la firma XAdES-BES
        autorizado.xml               # el que devuelve el SRI al autorizar
        respuesta_recepcion.xml      # respuesta SOAP de recepción, sin retocar
        respuesta_autorizacion.xml   # respuesta SOAP de autorización
        error.txt                    # último error, si lo hubo

Se escribe en cada paso, así que **también queda lo que salió mal** (devuelto,
no autorizado o error de red): es la evidencia de lo enviado y de lo respondido.
Las respuestas crudas se guardan además en los campos `respuesta_recepcion` y
`respuesta_autorizacion` del comprobante.

| Ajuste | Para qué |
|---|---|
| `GUARDAR_XML` | Guardar los XML en la base de datos (por omisión `True`) |
| `GUARDAR_ARCHIVOS` | Guardar los XML y las respuestas como archivos (por omisión `True`) |

En el admin, cada comprobante tiene la sección **Archivos y respuestas del SRI**
con la carpeta y un enlace de descarga por archivo. La descarga pasa por el admin
(hace falta ser del *staff*), así que **no hay que publicar `MEDIA_URL`** ni
`collectstatic` para verlos; aun así el archivo está en `MEDIA_ROOT` para
copiarlo con un `rsync`, subirlo a S3 o hacer copias de seguridad.

```python
from factec.django import archivos

archivos.carpeta_de(registro)          # "sri/comprobantes/2026/10/08/001-001-…_<clave>"
archivos.ruta_de(registro, "firmado.xml")
archivos.archivos_del_registro(registro)   # [{nombre, ruta, relativa, bytes}]
```

#### Comprobantes anteriores

Si los comprobantes se emitieron antes de activar los archivos, o si se perdió la
carpeta, se rehacen desde lo guardado en la base de datos:

```bash
python manage.py archivar_comprobantes                        # todos
python manage.py archivar_comprobantes --desde 2026-10-01     # desde una fecha
python manage.py archivar_comprobantes --estado DEVUELTO      # solo los devueltos
python manage.py archivar_comprobantes --simular              # ver sin escribir
```

### Filtros y búsquedas

Todos los listados del admin vienen con filtros y buscador, y se pueden ampliar
desde `settings` sin tocar el paquete: en cada petición se leen los ajustes
`ADMIN` y se añaden (o se reemplazan, con `solo`) los filtros, búsquedas,
columnas y campos de solo lectura que indique.

| Modelo | Filtros de serie | Búsqueda por |
|---|---|---|
| `Factura`, `LiquidacionCompra`, `NotaCredito`, `NotaDebito` | Estado ante el SRI, emitidos/sin emitir, ambiente, fecha de emisión, fecha de autorización, importe, forma de pago, tipo de identificación de la contraparte… | Serie y secuencial, clave de acceso, número de autorización, razón social e identificación, correo, dirección, motivo, número del documento modificado… |
| `GuiaRemision` | Estado, fechas de emisión e inicio del traslado, placa, tipo de identificación del transportista | Transportista, placa, punto de partida, destinatarios |
| `Retencion` | Estado, período fiscal, parte relacionada, tipo de sujeto retenido | Sujeto retenido, documentos sustento, códigos de retención |
| `GuiaDestinatario` | Motivo de traslado, tipo de identificación, documento que sustenta, fecha del sustento | Destinatario, ruta, número y autorización del sustento, bienes |
| `RetencionDocSustento` | Documento y código de sustento, pago local/exterior, convenio de doble tributación, rango de fechas e importes | Números de documento y autorización, sujeto retenido, retenciones e impuestos |
| `Cliente` | Tipo de identificación, fecha de alta | Razón social, identificación, correo, teléfono, dirección |
| `Producto` | **Producto o servicio**, activo, IVA, unidad de medida, fecha de alta | Código principal y auxiliar, descripción, unidad, detalles adicionales |
| `ComprobanteEmitido` | Estado, tipo, ambiente, tipo de emisión, configuración, rangos de fechas e importes, **sin enviar de otro día** | Clave, autorización, receptor, secuencial, carpeta, mensajes y error |
| `ConfiguracionEmisor` | Activo, ambiente, obligado a contabilidad, régimen, categoría, firma cargada, fecha de alta | RUC, razón social, direcciones, serie, certificado, resoluciones |
| **Líneas** (`FacturaDetalle`, `LiquidacionCompraDetalle`, `NotaCreditoDetalle`, `GuiaDetalle`) | Fecha y estado del comprobante, IVA, importe, tipo de identificación de la contraparte | Descripción, códigos, datos adicionales, producto, cliente/proveedor, secuencial y clave del comprobante |
| `RetencionImpuesto`, `RetencionDocSustentoImpuesto`, `NotaDebitoMotivo` | Código y porcentaje, estado y fecha de la nota | Códigos de retención, base, documento sustento, sujeto retenido |

Las líneas se registran aparte justamente para eso: buscar en un solo sitio en qué
comprobantes aparece un producto, un código o un cliente.

```python
FACTURACION_ELECTRONICA = {
    "ADMIN": {
        "_todos": {"filtros": ["mi_app.filtros.PorSucursal"]},   # para todos
        "factura": {
            "filtros": ["receptor__tipo_identificacion", "mi_app.filtros.PorSucursal"],
            "busqueda": ["receptor__direccion", "detalles__descripcion"],
            "columnas": ["mi_app.admin.columna_sucursal"],
            "solo_lectura": ["observaciones"],
        },
        "producto": {"solo": True, "filtros": ["activo"], "busqueda": ["descripcion"]},
    },
}
```

Las entradas son el nombre de un campo, la ruta a un filtro del proyecto
(`"mi_app.filtros.MiFiltro"`, una subclase de `SimpleListFilter`) o, en las
columnas, la ruta a una función. El paquete trae filtros reutilizables por si
quiere usarlos en sus propios admins:

```python
from factec.django.admin_filtros import (
    AdminConAjustes, FiltroConCertificado, filtro_emitido, filtro_por_fecha,
    filtro_por_importe,
)

class MiFacturaAdmin(AdminConAjustes):
    list_filter = (
        filtro_por_fecha("fecha_emision", "Emitidas"),
        filtro_por_importe("importe_total", "Importe"),
        filtro_emitido("comprobante"),
    )
    search_fields = ("secuencial", "receptor__razon_social")
```

### Leer y verificar desde una vista

Todo lo del comprobante se puede consultar desde código, sin pasar por el admin:
`factec.django.consulta` reúne las operaciones que suele necesitar una vista.

| Función | Para qué |
|---|---|
| `consulta.datos(registro_o_pk_o_clave, verificar_comprobante_=True)` | Todo en un diccionario listo para JSON |
| `consulta.datos_por_clave(clave)` | Igual, buscando por clave (``None`` si no existe) |
| `consulta.leer(...)` | Datos del comprobante ya separados |
| `consulta.verificar(...)` | Firma, clave de acceso, fecha y totales |
| `consulta.verificar_xml(xml)` | Verifica un XML suelto (factura de un proveedor) |
| `consulta.verificar_en_el_sri(clave_o_registro)` | Pregunta al SRI y guarda el resultado |
| `consulta.archivos_de(...)` | XML y respuestas guardados en disco |

```python
import json

from django.http import JsonResponse

from factec.django import consulta


def comprobante_json(request, clave):
    """Vista de ejemplo: devuelve el comprobante con su estado y su verificación."""
    datos = consulta.datos_por_clave(clave, verificar_comprobante_=True)
    if datos is None:
        return JsonResponse({"error": "No existe ese comprobante."}, status=404)
    return JsonResponse(datos)


def comprobante_del_sri(request, clave):
    """Vista de ejemplo: comprueba en el SRI si la clave está autorizada."""
    autorizacion = consulta.verificar_en_el_sri(clave)      # guarda si es nuestro
    return JsonResponse(autorizacion.a_dict())
```

#### Comprobantes de proveedores (compras)

Un XML que le entregan se puede leer y verificar antes de registrarlo. La
verificación usa el certificado que va **dentro** de la firma, así que no hace
falta pedir el `.p12` al proveedor:

```python
from factec.django import consulta
from factec.lectura import leer_comprobante

def recibir_factura(request):
    xml = request.FILES["xml"].read()

    informe = consulta.verificar_xml(xml)          # firma, clave, totales, fecha
    if not informe.ok:
        return JsonResponse({"problemas": informe.problemas}, status=400)

    leido = leer_comprobante(xml)
    compra = MiCompra.objects.create(
        proveedor=leido.emisor.razon_social,
        ruc=leido.emisor.ruc,
        numero=leido.numero,
        fecha=leido.fecha_emision,
        total=leido.totales.importe_total,
        clave_acceso=leido.clave_acceso,
    )
    for detalle in leido.detalles:
        compra.lineas.create(descripcion=detalle.descripcion,
                             cantidad=detalle.cantidad,
                             precio=detalle.precio_unitario)

    # Y, si quiere confirmar con el SRI que esa clave está autorizada:
    autorizacion = consulta.verificar_en_el_sri(leido.clave_acceso)
    return JsonResponse({"guardada": True, "autorizada": autorizacion.autorizada})
```

Los métodos del propio comprobante hacen lo mismo sin importar nada más:

```python
registro.leer()                  # ComprobanteLeido
registro.verificar()             # InformeVerificacion
registro.verificar_en_el_sri()   # AutorizacionLeida (guarda estado, respuesta y archivos)
registro.archivos()              # [{nombre, ruta, relativa, bytes}]
registro.a_dict(verificar=True)  # diccionario para JSON
```

### Emitir desde el código

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

### Emitir a partir de su propio modelo

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

### Celery

Celery es **opcional**:

* **Sin Celery** (instalando `pip install factec` sin el extra, o sin broker
  configurado) `emitir()` firma, envía y espera la autorización **dentro de la
  misma llamada**. Es lo más cómodo para probar, pero bloquea mientras el SRI
  responde.
* **Con Celery** la emisión va a la cola y la respuesta es inmediata; el
  comprobante queda en `BORRADOR` y el worker lo va actualizando.

#### Configurarlo paso a paso

> **`mi_proyecto` es un marcador**: ahí va el nombre de su proyecto (el de la
> carpeta con `settings.py`, p. ej. `facturero`). Si copia un comando y le sale
> `Unable to load celery application. The module mi_proyecto was not found`, es
> justo eso. Para no equivocarse, pida los comandos ya resueltos:
>
> ```bash
> python manage.py servicios_celery --comandos
> ```

Cuatro pasos, y todos los nombres se sustituyen por los de su proyecto
(«`mi_proyecto`»). Hay una copia lista para empezar en
[examples/celery.py](../examples/celery.py).

**1. El archivo `celery.py`** (el único que no viene hecho):

```python
# mi_proyecto/celery.py
import os

# En macOS y Windows el pool usa «spawn» y sin esta variable las tareas fallan al
# ejecutarse («ValueError: not enough values to unpack»).
os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

from celery import Celery  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mi_proyecto.settings")

app = Celery("mi_proyecto")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()      # registra las tareas de factec.django
```

**2. Cargarlo al arrancar el proyecto** (`mi_proyecto/__init__.py`), para que
`celery -A mi_proyecto` lo encuentre:

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

**3. Los ajustes** (`settings.py`):

```python
from factec.django.conf import planificador

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TIMEZONE = TIME_ZONE                        # la hora de las tareas periódicas
CELERY_WORKER_SEND_TASK_EVENTS = True              # alimenta el panel de Flower
CELERY_TASK_SEND_SENT_EVENT = True                 # que se vean como PENDING
CELERY_BEAT_SCHEDULE = {**planificador()}          # revisión diaria y reintentos
```

**4. Los servicios** (Linux: `sudo python manage.py servicios_celery`):

```bash
redis-server
celery -A mi_proyecto worker -l info -c 4          # mi_proyecto → su proyecto
celery -A mi_proyecto beat -l info                 # tareas periódicas
celery -A mi_proyecto flower                       # panel
```

> Ejecute `celery` **desde su entorno virtual** (actívelo o llame a
> `./venv/bin/celery`): con un `celery` de otro entorno, Django y factec no
> existen para él.

| Ajuste de Django | Para qué | Por omisión |
|---|---|---|
| `CELERY_BROKER_URL` | El broker (Redis). Con clave: `redis://:clave@127.0.0.1:6379/0` | sin definir: el paquete emite en síncrono |
| `CELERY_TIMEZONE` | Zona de las tareas periódicas; póngala igual que `TIME_ZONE` | la del sistema |
| `CELERY_WORKER_SEND_TASK_EVENTS` | Envía los eventos de las tareas (lo que ve Flower); equivale a `-E` | `False` |
| `CELERY_TASK_SEND_SENT_EVENT` | Que las tareas se vean como `PENDING` en cuanto entran | `False` |
| `CELERY_BEAT_SCHEDULE` | Tareas periódicas: `planificador()` trae la revisión de la firma y el reintento | vacío: nada periódico |
| `CELERY_QUEUE` (del paquete) | Cola donde encolar (si el worker escucha una concreta) | la del proyecto |
| `CELERY_PREFIX` (del paquete) | Prefijo de los nombres de tarea (`sri_fe.…`) | `sri_fe` |
| `EMITIR_CON_CELERY` (del paquete) | `True`: `emitir()` encola. `False`: emite en síncrono | `True` |

El paquete no impone una app de Celery: hay que crear la del proyecto, como en
cualquier Django. Es el único paso que no viene hecho.

```python
# mi_proyecto/celery.py
import os

# En macOS y Windows el pool de procesos usa «spawn» (no «fork») y, sin esta
# variable, Celery no prepara el registro de tareas en los procesos hijos: la
# tarea llega al worker y falla con «ValueError: not enough values to unpack
# (expected 3, got 0)». Con ella, cada hijo prepara las tareas igual que si el
# pool hubiera hecho execv.
os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")

from celery import Celery  # noqa: E402  (después de la variable, a propósito)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mi_proyecto.settings")

app = Celery("mi_proyecto")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()      # encuentra factec.django.tasks: no hay que registrar nada
```

> **Si las tareas fallan al ejecutarse**, con el error
> ``ValueError: not enough values to unpack (expected 3, got 0)`` en
> ``celery/app/trace.py``, es ese caso de «spawn»: ponga la variable de arriba en
> el ``celery.py`` (o arranque el worker con ``--pool=threads``, o con
> ``FORKED_BY_MULTIPROCESSING=1`` delante del comando). En Linux con «fork» no
> hace falta.

```python
# mi_proyecto/__init__.py
from .celery import app as celery_app

__all__ = ("celery_app",)
```

```python
# settings.py
from factec.django.conf import planificador

CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    **planificador(),                                  # la firma, a las 7:00;
                                                       # los pendientes, cada 10 min
    "sri-consultar-en-proceso": {                      # o a mano, con su horario
        "task": "sri_fe.consultar_autorizacion",
        "schedule": crontab(minute="*/10"),
    },
}
```

> Para emitir en segundo plano hacen falta **dos cosas además de Celery**: el
> cliente del broker (`pip install redis`, si usa Redis) y el `celery.py` de
> arriba. Sin el cliente verá un error de `kombu` al arrancar el worker
> (``'NoneType' object has no attribute 'Redis'``).

| Tarea | Para qué |
|---|---|
| `sri_fe.emitir_comprobante` | Firma, envía y espera la autorización |
| `sri_fe.consultar_autorizacion` | Consulta el estado de un comprobante ya recibido |
| `sri_fe.reintentar_pendientes` | Recupera los que no llegaron a estado final |
| `sri_fe.revisar_certificado` | Revisa la firma electrónica y avisa antes de que falle una emisión |

```bash
celery -A mi_proyecto worker -l info -c 4 -E   # -c: procesos; -E: envía eventos
celery -A mi_proyecto beat -l info             # opcional: revisión y reintentos

# Comprobar que el worker tiene las tareas del paquete:
celery -A mi_proyecto inspect registered | grep sri_fe

# Lanzar una tarea a mano (útil para ver que todo el circuito funciona):
celery -A mi_proyecto call sri_fe.revisar_certificado
```

#### Flower: el panel de monitorización

Flower es la interfaz web de Celery: ahí se ve cada tarea que entra
(`sri_fe.emitir_comprobante`, `sri_fe.revisar_certificado`…), su estado, los
argumentos, el resultado, las que fallan y los workers conectados.

```bash
pip install "factec[django,flower]"
celery -A mi_proyecto flower --address=127.0.0.1 --port=5555
# y abra http://127.0.0.1:5555
```

| Detalle | Por qué |
|---|---|
| El worker debe arrancar con **`-E`** | Sin eventos de tarea, Flower ve el worker pero **ninguna tarea** |
| `--address=127.0.0.1` (por omisión) | Para un servidor remoto, tráigalo por SSH: `ssh -L 5555:127.0.0.1:5555 usuario@servidor` |
| `--basic_auth=usuario:clave` | Obligatorio si lo expone (`--address=0.0.0.0`): el panel deja **ejecutar y revocar tareas** |
| `FLOWER_UNAUTHENTICATED_API=1` | Solo si necesita consultar su API (`/api/tasks`) sin credenciales |
| `--url_prefix=/flower` | Si lo publica detrás de un proxy, en un subdirectorio |

El panel muestra las tareas con su nombre real, así que se ve el ciclo completo de
una factura: `sri_fe.emitir_comprobante` → `RECIBIDO`/`AUTORIZADO`. Para saber si
el circuito funciona, en el script de servicios hay un atajo:

```bash
python manage.py servicios_celery --estado
```

La guía del panel (pantallas, estados, API para monitorizar y qué mirar cuando algo
falla) está en [flower.md](flower.md).

#### Servicios de systemd (Linux)

El comando `servicios_celery` crea los tres servicios (worker, beat y Flower)
con las rutas del proyecto ya resueltas, los habilita al arranque y comprueba que
Redis responde:

```bash
sudo python manage.py servicios_celery                      # crea y arranca
python manage.py servicios_celery --comandos                # los comandos, ya con sus nombres
python manage.py servicios_celery --plantillas              # los modelos .service, para editarlos a mano
python manage.py servicios_celery --dry-run                 # enseña y no toca nada
python manage.py servicios_celery --estado                  # Redis, worker, beat y Flower
sudo python manage.py servicios_celery --reiniciar           # tras desplegar
sudo python manage.py servicios_celery --quitar              # los elimina

sudo python manage.py servicios_celery --sin-flower          # sin el panel
sudo python manage.py servicios_celery --concurrencia 2 --usuario www-data
sudo python manage.py servicios_celery --direccion 0.0.0.0 --flower-auth juan:secreta
python manage.py servicios_celery --ruta                     # dónde está el script
```

Detrás está `instalar_servicios_celery.sh`, que viaja dentro del paquete (el
comando le pasa el módulo de ajustes, el entorno virtual y la carpeta del
proyecto). Se puede copiar a otro servidor y ejecutar a mano; con
`--solo-archivos` deja las unidades donde le diga sin tocar systemctl.

#### Los archivos, para tenerlos en el proyecto

Si prefiere los `.service` dentro de su repositorio, para revisarlos o
versionarlos:

```bash
python manage.py servicios_celery --plantillas --destino deploy/systemd
```

Copia los modelos con el nombre de su proyecto, el módulo y la concurrencia ya
puestos (`<proyecto>-celery-worker.service`, `…-celery-beat.service`,
`…-flower.service` y un `env.ejemplo`), y deja cuatro marcadores que dependen del
servidor: `__USUARIO__`, `__GRUPO__`, `__PROYECTO__` y `__VENV__`. El comando
imprime el `sed` que los sustituye y los `systemctl` para dejarlos instalados.
Cada archivo lleva arriba su explicación y lo mismo el `env.ejemplo` (para qué
sirve cada variable), así que se pueden editar a mano sin la documentación al
lado.

Las unidades quedan en `/etc/systemd/system/<proyecto>-celery-worker.service`,
`…-celery-beat.service` y `…-flower.service`, con `Restart=always`, `journald`
para los logs (`journalctl -u <proyecto>-celery-worker -f`) y un
`EnvironmentFile` opcional en `<proyecto>/.env` para los secretos
(`SRI_CLAVE_CIFRADO`, `CELERY_BROKER_URL`…).

Las cuatro tareas quedan registradas solas porque `factec.django` está en
`INSTALLED_APPS` y `autodiscover_tasks()` importa su `tasks`. Si arranca el worker
y ve `Received unregistered task of type 'sri_fe.emitir_comprobante'`, es que
falta el `celery.py` de arriba (o el `app.autodiscover_tasks()`).

Si el SRI responde `EN PROCESO`, la tarea se reprograma sola en lugar de
mantener el worker ocupado consultando en bucle.

### Estados del comprobante

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
| `ERROR` | Fallo de comunicación o la revisión previa no pasó (el motivo queda en `error`); se puede reintentar |

Los mensajes del SRI se guardan en `mensajes` (JSON) y el último fallo en `error`.

### Ajustes disponibles

```python
FACTURACION_ELECTRONICA = {
    "AMBIENTE": 2,                    # respaldo si no hay configuración activa
    "CLAVE_CIFRADO": "...",           # o SRI_CLAVE_CIFRADO (recomendado)
    "REINTENTOS_AUTORIZACION": 6,
    "ESPERA_AUTORIZACION": 4.0,
    "GUARDAR_XML": True,              # guardar los XML en la base de datos
    "CELERY_QUEUE": "facturacion",     # cola para las tareas (ver «Celery»)
    "CELERY_PREFIX": "sri_fe",         # prefijo de los nombres de tarea
    "TIMEOUT": 30.0,
    "TIMEOUT_CONSULTA_SRI": 15.0,      # espera máxima al consultar el RUC
    "CONSULTAR_SRI_AUTOMATICAMENTE": True,
    "EMITIR_CON_CELERY": True,         # emitir() encola; False = síncrono
    "VALIDAR_VIGENCIA": True,
    "ALGORITMO_FIRMA": "sha1",
    "REVISAR_ANTES_DE_EMITIR": True,  # revisa certificado y datos antes de firmar
    "DIAS_AVISO_CERTIFICADO": 30,     # antelación con la que se avisa del vencimiento
    "FECHA_EMISION_AL_EMITIR": True,  # emitir con la fecha del día de la firma
    "CORREOS_AVISO": [],              # a quién avisar de los problemas de la firma
}
```

Los ajustes de Celery (`CELERY_BROKER_URL`, `CELERY_BEAT_SCHEDULE`,
`CELERY_TIMEZONE`, `CELERY_WORKER_SEND_TASK_EVENTS`…) son de Django, no del
paquete: están explicados en [Configurarlo paso a paso](#configurarlo-paso-a-paso).

También se puede usar el paquete **sin** base de datos, definiendo `EMISOR` y
`CERTIFICADO` en los ajustes (útil en pruebas).
