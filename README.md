# factec

**Facturación electrónica de Ecuador (SRI)** para Python y Django: genera, firma y
envía los seis comprobantes electrónicos que exige el Servicio de Rentas Internas.

- **Clave de acceso** de 49 dígitos (algoritmo módulo 11 del SRI).
- **XML** validado contra los XSD oficiales de cada comprobante.
- **Firma XAdES-BES** con el certificado `.p12` del contribuyente.
- **Envío SOAP** a los webservices de recepción y autorización (pruebas y producción).
- **App de Django** con las tablas, el admin y tareas de Celery listas para usar.

| Comprobante | `codDoc` | Esquema |
|---|---|---|
| Factura | `01` | 1.1.0 |
| Liquidación de compra | `03` | 1.1.0 |
| Nota de crédito | `04` | 1.1.0 |
| Nota de débito | `05` | 1.0.0 |
| Guía de remisión | `06` | 1.1.0 |
| Comprobante de retención | `07` | 2.0.0 |

---

## Instalación

```bash
# solo el núcleo (sin Django)
pip install "factec @ git+https://github.com/jurdin32/factec.git"

# con la app de Django (recomendado si va a facturar desde el admin)
pip install "factec[django] @ git+https://github.com/jurdin32/factec.git"

# una versión concreta
pip install "factec[django] @ git+https://github.com/jurdin32/factec.git@v1.1.0"
```

**Requisitos:** Python 3.9 o superior. Dependencias: `cryptography`, `lxml` y
`requests` (el extra `django` añade `Django` y `celery`). El certificado debe ser
**RSA**: el SRI no admite otro tipo de clave.

Celery **no es obligatorio**: si no lo instala (o no configura un broker), la
emisión se hace en el momento, sin cola ni worker.

---

## Puesta en marcha con Django

Son cinco pasos. El **3 es obligatorio**: sin migrar no existen las tablas y el
admin dará error al abrir cualquier comprobante.

### 1. Instale el paquete

```bash
python -m venv venv
source venv/bin/activate
pip install "factec[django] @ git+https://github.com/jurdin32/factec.git"
```

### 2. Añada la app y los ajustes

En `settings.py` del proyecto:

```python
INSTALLED_APPS = [
    # ... las de Django ...
    "factec.django",          # tablas, admin, tareas y comandos del paquete
]

# El certificado .p12 se guarda como archivo subido: estas dos son necesarias.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

FACTURACION_ELECTRONICA = {
    # Cifra la contraseña del .p12. Genere la suya:
    #   python -c "from factec.django.crypto import generar_clave; print(generar_clave())"
    "CLAVE_CIFRADO": "ponga-aquí-su-clave",

    # El ambiente se puede escribir con su nombre (o con el número):
    #   "pruebas"     → 1: comprobantes sin validez fiscal, para ensayar
    #   "producción"  → 2: comprobantes con validez legal ante el SRI
    # Es solo el respaldo: manda el ambiente de la configuración del emisor.
    "AMBIENTE": "pruebas",

    "GUARDAR_XML": True,       # guarda los XML en la base de datos
    "GUARDAR_ARCHIVOS": True,  # además, los XML y las respuestas como archivos
}

# El SRI está en Ecuador y compara la fecha de emisión con la suya. Si deja el
# valor por omisión de Django (UTC), a partir de las 19:00 el admin muestra el día
# siguiente y es fácil emitir con fecha de mañana (el SRI lo devuelve).
LANGUAGE_CODE = "es-ec"
TIME_ZONE = "America/Guayaquil"
```

### 3. Cree las tablas (obligatorio)

```bash
python manage.py migrate
```

Crea las tablas `sri_fe_*` (configuración del emisor, clientes, productos y los
seis comprobantes). Las migraciones vienen dentro del paquete: **no hay que
generar ninguna** en el proyecto.

### 4. Cree su usuario del admin

```bash
python manage.py createsuperuser
```

### 5. Levante el servidor y entre al admin

```bash
python manage.py runserver
```

Admin: <http://127.0.0.1:8000/admin/> → **Facturación electrónica (SRI)**

1. **Configuraciones del emisor** → escriba el **RUC** y guarde: la razón social,
   el régimen y las obligaciones se traen solos del catastro del SRI. Adjunte el
   **archivo de firma (.p12)** y su **contraseña** (se guarda cifrada).
2. **Catálogos** → cree sus **Productos o servicios** (tipo, código, precio, IVA y,
   si quiere, sus **detalles adicionales**) y sus **Clientes** (o use
   `CONSUMIDOR FINAL` para probar).
3. **Comprobantes** → cree una **factura**, añada líneas *eligiendo el producto o
   servicio* (la descripción, el precio, el IVA, la unidad y sus detalles
   adicionales se completan solos) y pulse
   **«Emitir: firmar, enviar al SRI y esperar autorización»**.

Cada comprobante deja sus **XML y las respuestas del SRI** en una carpeta por año,
mes y día dentro de `MEDIA_ROOT`, y se descargan desde el propio admin
(**Emisión → Comprobantes emitidos → Archivos y respuestas del SRI**):

```
media/sri/comprobantes/2026/10/08/001-001-000000012_<clave>/
├── sin_firma.xml
├── firmado.xml
├── autorizado.xml
├── respuesta_recepcion.xml
├── respuesta_autorizacion.xml
└── error.txt                    # si el SRI lo devolvió o falló el envío
```

Se guarda **todo, también lo rechazado**: es la evidencia de lo que se envió y de
lo que contestó el SRI. Para los comprobantes emitidos antes de activar esta
opción, o si se perdió la carpeta, existe `python manage.py archivar_comprobantes`.

### Si el SRI devuelve «FECHA EMISIÓN EXTEMPORANEA» (mensaje 65)

La fecha de emisión **no puede ser futura** ni tener **más de 90 días** (la
tolerancia de 129600 minutos que publica el SRI). Se compara con la fecha del
servidor del SRI, que está en Ecuador. El paquete lo comprueba antes de enviar y
el admin no deja guardar un comprobante fuera de esa ventana.

Si un comprobante quedó en **Devuelto**, corrija el dato y vuelva a pulsar
**«Emitir»**: se genera uno nuevo con los datos corregidos (el rechazado se
conserva como historial) y **no** se gasta otro secuencial.

```python
from factec.sri.fechas import DIAS_TOLERANCIA, hoy_en_ecuador, validar_fecha_emision

hoy_en_ecuador()                 # la fecha con la que compara el SRI
validar_fecha_emision(fecha)     # lanza ErrorValidacion si el SRI la rechazaría
```

### Productos y servicios con sus detalles adicionales

En **Catálogos → Productos o servicios** se elige si lo que se vende es un
**producto (bien)** o un **servicio**: sirve para filtrar y buscar, y propone la
unidad de medida si se deja vacía (`UNIDAD` o `SERVICIO`).

Los **detalles adicionales** del producto (hasta 3, en formato `NOMBRE=VALOR`
separados por `;`, como `MARCA=ACME; GARANTIA=12 MESES`) se copian solos en cada
línea del comprobante que use ese producto: se escriben **una sola vez** hasta el
SRI, en `detallesAdicionales` de la línea. Si una venta concreta necesita otros
(por ejemplo, el lote), se cambian en la propia línea.

```python
from factec.django.documentos import Producto, TipoProducto

servicio = Producto.objects.create(
    tipo=TipoProducto.SERVICIO, codigo_principal="ASES", descripcion="Asesoría mensual",
    precio_unitario=200, datos_adicionales="MODALIDAD=REMOTA",
)
linea = factura.detalles.create(producto=servicio, cantidad=1)
linea.datos_adicionales          # 'MODALIDAD=REMOTA'  (heredado)
```

### La fecha de emisión es la del día en que se firma

Un comprobante se firma **el día en que se emite**, no cuando se preparó: el SRI
valida esa fecha contra su propio reloj. Por eso, al emitir, el paquete pone la
fecha del día (hora de Ecuador) en lugar de la fecha del documento, y un borrador
que se quedó de otro día se refecha antes de firmarlo (se le recalcula la clave de
acceso) en vez de reenviarse con la fecha vieja.

```python
# settings.py — comportamiento por omisión (la fecha del día de la firma)
FACTURACION_ELECTRONICA = {
    "FECHA_EMISION_AL_EMITIR": True,
}

# ¿Necesita emitir con otra fecha (dentro de los 90 días que admite el SRI)?
facturacion.emitir(mi_factura, fecha_emision=date(2026, 10, 1))
```

Como la fecha la pone la firma, un documento guardado con una fecha rara (por
ejemplo, futura porque el servidor estaba en UTC) no deja el comprobante
inservible: se emite con la de hoy. Y si prefiere que mande la fecha del documento,
ponga `FECHA_EMISION_AL_EMITIR: False`.

Desde el admin, **«Actualizar la fecha de emisión al día de hoy»** hace lo mismo
sobre los comprobantes seleccionados, y el filtro **«Sin enviar, de otro día (hay
que refechar)»** los localiza de un vistazo.

---

## Servicios que hay que levantar

| Servicio | ¿Obligatorio? | Comando |
|---|---|---|
| Django | Sí | `python manage.py runserver` (desarrollo) o `gunicorn mi_proyecto.wsgi` (producción) |
| Redis | Solo si usa Celery | `redis-server` (o `docker run -p 6379:6379 redis`) |
| Celery worker | Solo si usa Celery | `celery -A mi_proyecto worker -l info -c 4 -E` |
| Celery beat | Opcional | `celery -A mi_proyecto beat -l info` (revisa la firma a las 7:00 y reintenta los pendientes cada 10 min) |
| Flower (panel) | Opcional | `celery -A mi_proyecto flower` → <http://127.0.0.1:5555> (necesita el worker con `-E`) |
| Archivos estáticos | Al desplegar | `python manage.py collectstatic` (con `DEBUG = False`) |

**¿Hace falta Celery?** No. Sin Celery, `emitir()` firma, envía y espera la
autorización dentro de la misma llamada: más lento, pero sin nada más que
levantar (y el aviso diario de la firma se pone en el cron). Con Celery la emisión
va a la cola y el worker la resuelve; para eso necesita **Redis**, el **cliente**
(`pip install redis`), un **`celery.py`** en el proyecto y el **worker** corriendo
(está detallado en [docs/django.md](docs/django.md#celery)). Si falta cualquiera de
las tres cosas, el paquete lo dice en el log y emite de forma síncrona:
«*No hay broker de Celery configurado: se emite de forma síncrona*».

Dos tropiezos habituales, ya resueltos en la documentación: el worker no arranca
sin el cliente del broker (`pip install redis`), y en macOS y Windows las tareas
fallan con `ValueError: not enough values to unpack` si no se pone
`FORKED_BY_MULTIPROCESSING=1` en el `celery.py` (el pool usa «spawn»).

El SRI es un servicio remoto: no hay nada que instalar ni levantar en su máquina.

### Monitorizar con Flower (el panel de Celery)

Flower es la interfaz web de Celery: ahí se ve qué tareas van llegando
(`sri_fe.emitir_comprobante`, `sri_fe.revisar_certificado`…), cuáles fallan, los
workers conectados y sus colas. El paquete lo trae como extra:

```bash
pip install "factec[django,flower]"

# el worker tiene que enviar los eventos de las tareas, si no Flower no las ve
# (o ponga CELERY_WORKER_SEND_TASK_EVENTS = True en settings.py y olvídese del -E)
celery -A mi_proyecto worker -l info -c 4 -E

celery -A mi_proyecto flower --address=127.0.0.1 --port=5555    # panel
```

Si el servidor es remoto, no abra el puerto al mundo: tráigalo por SSH.

```bash
ssh -L 5555:127.0.0.1:5555 usuario@servidor     # y abra http://127.0.0.1:5555
```

Y para dejarlo expuesto, que sea con usuario y clave:
`celery -A mi_proyecto flower --address=0.0.0.0 --basic_auth=juan:secreta`.

En los ejemplos, `mi_proyecto` es el nombre **de su** proyecto (el de la carpeta con
`settings.py`): si un comando le responde `The module mi_proyecto was not found`,
sustitúyalo. Para no equivocarse:

```bash
python manage.py servicios_celery --comandos     # worker, beat y Flower, con sus nombres
```

En [docs/flower.md](docs/flower.md) está el día a día del panel: qué significa cada
estado para una factura, cómo buscar una emisión concreta, el API para monitorizar
y qué mirar cuando algo no aparece.

### Servicios de Linux (systemd)

Un comando del paquete crea los servicios del worker, del beat y de Flower con
las rutas del proyecto ya resueltas:

```bash
sudo python manage.py servicios_celery                 # crea y arranca los tres
python manage.py servicios_celery --comandos           # los comandos, ya con sus nombres
python manage.py servicios_celery --dry-run            # enseña lo que haría
python manage.py servicios_celery --estado             # ¿están funcionando?
sudo python manage.py servicios_celery --reiniciar     # tras desplegar
sudo python manage.py servicios_celery --quitar        # los elimina

sudo python manage.py servicios_celery --sin-flower                       # sin panel
sudo python manage.py servicios_celery --concurrencia 2 --usuario www-data
sudo python manage.py servicios_celery --direccion 0.0.0.0 \
     --flower-auth juan:secreta                                           # panel expuesto
```

Deja las unidades en `/etc/systemd/system` (`<proyecto>-celery-worker.service`,
`…-celery-beat.service` y `…-flower.service`), las habilita al arranque, las
arranca y comprueba que Redis responde. Los logs salen por journald:
`journalctl -u <proyecto>-celery-worker -f`. El script que hace el trabajo viaja
dentro del paquete: `python manage.py servicios_celery --ruta` dice dónde está
(se puede copiar a otro servidor y ejecutar a mano).

### Comandos que vienen con el paquete

```bash
# crea la configuración del emisor con lo que publica el SRI (y lo confirma)
python manage.py importar_ruc_sri --ruc 0703886697001 --dir-matriz "PANAMERICANA Y CARCHI"

# sin consultar al SRI y sin preguntar (para instalaciones desatendidas)
python manage.py importar_ruc_sri --ruc 0703886697001 --sin-consultar \
    --dir-matriz "PANAMERICANA Y CARCHI" --sin-confirmar

python manage.py archivar_comprobantes            # reescribe los archivos de los ya emitidos
python manage.py archivar_comprobantes --desde 2026-10-01 --estado DEVUELTO --simular

python manage.py revisar_firma                   # ¿está la firma para emitir? (avisa si no)
python manage.py revisar_firma --correo          # además, avisa por correo

sri-fe --help                # línea de comandos del núcleo: clave, firmar, enviar…
```

---

## Uso desde Python

```python
from factec.django import documentos

cliente = documentos.Cliente.consumidor_final()
producto = documentos.Producto.objects.create(
    codigo_principal="SRV001", descripcion="Servicio de desarrollo",
    unidad_medida="hora", precio_unitario=100, codigo_porcentaje_iva="4",
)

factura = documentos.Factura.objects.create(receptor=cliente, forma_pago="19")
factura.detalles.create(producto=producto, cantidad=2)   # el resto se completa solo

registro = factura.emitir()          # XML → firma → SRI → autorización
print(factura.estado)                # AUTORIZADO
print(factura.clave_acceso)          # 49 dígitos
print(factura.total)                 # el importeTotal del XML
```

Sin Django, el núcleo se usa igual para armar el XML, firmarlo o enviarlo:

```python
from factec import Emisor, EmisorElectronico, Receptor, Detalle

emisor = EmisorElectronico(emisor=Emisor(ruc="1790012345001", razon_social="ACME S.A.",
                                         dir_matriz="Quito"),
                           certificado="firma.p12", clave_certificado="secreto", ambiente=1)
factura = emisor.factura(receptor=Receptor(identificacion="9999999999999",
                                           razon_social="CONSUMIDOR FINAL"),
                         detalles=[Detalle(descripcion="Servicio", cantidad=1,
                                           precio_unitario=100)])
resultado = emisor.emitir(factura)
```

---

## Filtros y búsquedas en el admin

**Todos** los listados del paquete traen filtros y buscador: los comprobantes (por
estado ante el SRI, ambiente, forma de pago, rango de fechas y de importes),
los catálogos (tipo de identificación, IVA, unidad, activo…), los destinatarios
de guía, los documentos sustento, las retenciones y hasta las **líneas**, para
responder «¿en qué comprobantes vendí este producto?».

Y se puede ampliar sin tocar el paquete, desde `settings`:

```python
FACTURACION_ELECTRONICA = {
    "ADMIN": {
        # para todos los modelos
        "_todos": {"filtros": ["mi_app.filtros.PorSucursal"]},
        "factura": {
            "filtros": ["receptor__tipo_identificacion", "mi_app.filtros.PorSucursal"],
            "busqueda": ["receptor__direccion", "detalles__descripcion"],
            "columnas": ["mi_app.admin.columna_sucursal"],
            "solo_lectura": ["observaciones"],
        },
        # «solo» reemplaza los filtros y búsquedas del paquete en ese modelo
        "producto": {"solo": True, "filtros": ["activo"], "busqueda": ["descripcion"]},
    },
}
```

---

## Revisar antes de emitir (la firma, primero)

Si el certificado está vencido, o es de otro contribuyente, el SRI devuelve el
comprobante y el documento queda en un estado incómodo (secuencial gastado, factura
«devuelta»). Para que eso no pase, **antes de firmar y enviar** el paquete revisa:

* el **certificado**: que se pueda abrir, que esté vigente y que su RUC sea el del
  emisor (y avisa con antelación de que va a vencer);
* la **fecha de emisión**: dentro de la ventana que admite el SRI;
* los **datos**: totales, clave de acceso y el resto de reglas del SRI.

Si algo falla **no se emite**: se explica el motivo y no se gasta secuencial.

```python
from factec.django import services

informe = services.revisar_comprobante(comprobante)     # o registro.revisar()
informe.puede_emitir          # False si hay algo que el SRI rechazaría
informe.problemas             # ["El certificado de firma está vencido desde el…"]
informe.avisos                # ["El certificado de firma vence el 14/10/2026 (en 6 día(s))…"]
informe.certificado.ruc, informe.certificado.dias_restantes
```

Y al emitir, la revisión es automática: si no pasa, `emitir()` lanza
`ErrorRevision` (con el informe dentro) en lugar de enviar algo que va a volver mal.

```python
from factec.excepciones import ErrorRevision

try:
    registro = facturacion.emitir(mi_factura)
except ErrorRevision as error:
    return JsonResponse({"problemas": error.informe.problemas}, status=400)
```

### La revisión programada (que el aviso llegue antes)

El aviso sirve si llega **antes** de que falle una emisión. El paquete trae la
revisión lista para programarla: revisa la firma una vez al día y avisa por correo
cuando está vencida o a punto de vencer.

```python
# settings.py
from factec.django.conf import planificador

CELERY_BEAT_SCHEDULE = {**planificador()}      # revisa la firma a diario
                                               # y reintenta los pendientes

FACTURACION_ELECTRONICA = {
    "CORREOS_AVISO": ["administracion@mitienda.ec"],   # a quién avisar
    "DIAS_AVISO_CERTIFICADO": 30,                      # con cuánta antelación
}
```

Sin Celery, programe el comando en el cron (o en el Programador de tareas):

```bash
# todos los días a las 7 de la mañana
0 7 * * * cd /ruta/del/proyecto && ./venv/bin/python manage.py revisar_firma --correo
```

En el admin, además, aparece un **aviso en el listado** mientras la firma impida
emitir, y hay dos acciones sobre los comprobantes: **«Revisar antes de emitir
(certificado y datos)»** y **«Actualizar la fecha de emisión al día de hoy»**.

---

## Leer y verificar comprobantes

Sirve para sus comprobantes y para los que le entreguen (por ejemplo, las
facturas de sus proveedores). Todo son funciones normales: se usan en una vista,
en una tarea o en un comando.

```python
from factec.django import consulta

# 1) Una vista que devuelve el comprobante en JSON
def detalle(request, clave):
    datos = consulta.datos_por_clave(clave)          # estado + datos leídos + archivos
    if datos is None:
        return JsonResponse({"error": "no encontrado"}, status=404)
    return JsonResponse(datos)

# 2) Verificar una factura que llega en XML antes de guardarla
informe = consulta.verificar_xml(xml_del_proveedor, exigir_firma=False)
if not informe.ok:
    return JsonResponse({"problemas": informe.problemas}, status=400)
informe.emisor, informe.importe_total, informe.a_dict()

# 3) Preguntar al SRI por una clave (actualiza el comprobante si es suyo)
autorizacion = consulta.verificar_en_el_sri(clave)
autorizacion.autorizada
```

Y desde el propio comprobante:

```python
registro = documentos.Factura.objects.get(pk=1).comprobante

registro.leer()                  # datos separados: emisor, receptor, totales, líneas
registro.verificar()             # firma, clave, fecha y totales: .ok, .problemas
registro.verificar_en_el_sri()   # estado en el SRI (y lo guarda)
registro.archivos()              # XML y respuestas guardados
registro.a_dict()                # todo listo para JSON
```

Sin Django, el núcleo lee y verifica igual (también XML de terceros, usando el
certificado que viene dentro de la firma):

```python
from factec import leer_comprobante, verificar_comprobante

comprobante = leer_comprobante(xml)
comprobante.emisor.ruc, comprobante.totales.importe_total
[detalle.descripcion for detalle in comprobante.detalles]

informe = verificar_comprobante(xml)     # ok, problemas, firma, certificado…
informe.certificado.nombre, informe.certificado.vencido()
```

---

## Documentación

| Documento | Contenido |
|---|---|
| [docs/nucleo.md](docs/nucleo.md) | Uso sin Django: clave de acceso, XML, firma, envío, lectura y verificación, CLI y API |
| [docs/django.md](docs/django.md) | La app de Django: modelos, admin, adaptadores, Celery y ajustes |
| [docs/flower.md](docs/flower.md) | El panel de Celery: pantallas, cómo seguir una factura y qué mirar cuando algo falla |
| [docs/prueba-real.md](docs/prueba-real.md) | Emitir de verdad contra el ambiente de pruebas del SRI |
| [examples/](examples/) | Scripts listos para ejecutar (incluye `celery.py` para copiar) |
| [tests/](tests/) | 515 pruebas, incluida la validación contra los XSD oficiales |

---

## Licencia

MIT. Consulte [LICENSE](LICENSE).
