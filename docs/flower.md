# Flower: el panel de Celery

Flower es la interfaz web de Celery: sirve para **ver qué está pasando** con las
emisiones en segundo plano —qué tareas entran, cuáles terminan, cuáles fallan y
por qué— sin tener que leer los logs del worker.

No hace falta para facturar: es una herramienta de trabajo del día a día (y de
socorro cuando una factura «no sale»).

---

## 1. Arrancarlo

Flower se sirve **como subcomando de Celery**:

```bash
pip install "factec[django,flower]"

# el worker tiene que enviar los eventos de las tareas (si no, Flower no las ve)
celery -A mi_proyecto worker -l info -c 4 -E

celery -A mi_proyecto flower --address=127.0.0.1 --port=5555
# → ábralo en http://127.0.0.1:5555
```

Con el comando de servicios del paquete queda como servicio de systemd, sin
escribir nada a mano:

```bash
sudo python manage.py servicios_celery            # worker + beat + Flower
python manage.py servicios_celery --estado        # ¿está respondiendo?
```

| Opción | Para qué |
|---|---|
| `--address=127.0.0.1` | Solo escucha en la propia máquina (por omisión). Para un servidor remoto, tráigalo por SSH |
| `--port=5555` | Puerto del panel |
| `--basic_auth=usuario:clave` | Usuario y clave. **Obligatorio** si lo expone fuera de localhost |
| `--url_prefix=/flower` | Si lo publica detrás de un proxy (nginx, Caddy) en un subdirectorio |
| `--persistent=True --db=/var/lib/facturero/flower` | Guarda el histórico de tareas entre reinicios (si no, se pierde) |
| `--max_tasks=10000` | Cuántas tareas recuerda |

### Verlo desde su ordenador (servidor remoto)

No hace falta abrir el puerto al mundo:

```bash
ssh -L 5555:127.0.0.1:5555 usuario@servidor
# y en su navegador: http://127.0.0.1:5555
```

Si tiene que quedar expuesto, mínimo con usuario y clave:

```bash
celery -A mi_proyecto flower --address=0.0.0.0 --basic_auth=juan:secreta
```

> El panel **no es de solo lectura**: desde ahí se pueden revocar tareas y
> cambiar el tamaño del pool. Trátelo como el admin de Django.

---

## 2. Las pantallas

**Workers** (`/`) — el resumen del clúster: *Online workers*, *Active tasks*,
*Succeeded* y *Failed*, con la lista de workers conectados. Es lo primero que se
mira: si no hay ningún worker en línea, nada de lo que se encole se va a ejecutar.

**Tasks** (`/tasks`) — el histórico: cada tarea con su nombre (`sri_fe.…`), UUID,
estado, cuándo llegó, cuánto tardó y qué worker la hizo. Arriba se filtra por
estado (**All / Started / Succeeded / Failed / Retried**) y se busca por nombre.
En el detalle de una tarea (`/task/<uuid>`) está todo:

| Campo | Para qué sirve aquí |
|---|---|
| **Status** | `SUCCESS`, `FAILURE`, `RETRY`… |
| **Arguments / Keyword arguments** | Los argumentos con los que se llamó (las tareas del paquete reciben el `pk` del comprobante) |
| **Result** | Lo que devolvió. En `sri_fe.emitir_comprobante` es el resumen del comprobante: `clave_acceso`, `estado`, `numero_autorizacion`, `error` |
| **Retries**, **Worker**, **Runtime** | Cuántas veces se reintentó, quién la ejecutó y cuánto tardó |
| **Children / Root** | La cadena de reintentos (una emisión que quedó `EN_PROCESO` se reprograma sola) |

**Broker** (`/broker`) — las colas y los mensajes pendientes en Redis. Si aquí
aparece backlog y el worker no lo consume, el worker está parado o escuchando otra
cola (`CELERY_QUEUE`).

**Worker → pestañas** (`/worker/<nombre>`) — el detalle de un worker:

| Pestaña | Qué muestra |
|---|---|
| **Pool** | Concurrencia, procesos, prefetch, y los controles **Grow / Shrink** para subir o bajar procesos sin reiniciar |
| **Broker** | Conexión al broker de ese worker |
| **Queues** | Colas que atiende y cuántos mensajes hay en cada una |
| **Tasks** | Tareas de ese worker, con sus tiempos |
| **Limits** | Límites de ejecución (rate limits) |
| **Config** | Configuración efectiva de Celery: aquí se comprueba que el broker, la zona horaria y `task_send_events` son los que uno cree |
| **System** | Load average, disco, memoria, versión de Celery y de las librerías |
| **Other** | Lo que no encaja en las demás |

---

## 3. Seguir una factura por el panel

Las cuatro tareas del paquete (nombre configurable con `CELERY_PREFIX`, por
omisión `sri_fe`):

| Tarea | Cuándo aparece | Resultado que devuelve |
|---|---|---|
| `sri_fe.emitir_comprobante` | Al pulsar **Emitir** en el admin, o al llamar `facturacion.emitir()` | Resumen del comprobante: `clave_acceso`, `estado`, `numero_autorizacion`, `intentos`, `error` |
| `sri_fe.consultar_autorizacion` | Al consultar el estado de un comprobante recibido | El mismo resumen |
| `sri_fe.reintentar_pendientes` | Cada 10 minutos (Celery Beat) | Lista de resúmenes de lo que recuperó |
| `sri_fe.revisar_certificado` | A las 7:00 (Celery Beat), o a mano | El informe de la firma: días para vencer, problemas y avisos |

Estados que verá y qué significan para una factura:

| Estado en Flower | Qué significa | Qué hacer |
|---|---|---|
| `PENDING` | Encolada, el worker aún no la tomó | Si se queda así, no hay worker en línea (o escucha otra cola) |
| `STARTED` | Firmando y enviando al SRI | Normal: el SRI puede tardar unos segundos |
| `RETRY` | El SRI respondió `EN PROCESO`: la tarea se reprograma sola | Nada; vuelve a aparecer al rato |
| `SUCCESS` | Terminó. **Ojo**: puede terminar bien y el comprobante estar devuelto (el detalle está en `Result` y en `registro.error`) | Mirar `estado` y `error` del resultado |
| `FAILURE` | Excepción al ejecutar (no del SRI, sino del propio código) | Abrir la tarea y leer la traza; ver «Cuando algo no cuadra» |
| `REVOKED` | Se canceló desde el panel | El comprobante queda a medias; se recupera con `servicios.emitir_ahora()` o con `sri_fe.reintentar_pendientes` |

Dos atajos útiles:

```bash
# ¿qué está corriendo ahora mismo?
curl -s -u juan:secreta http://127.0.0.1:5555/api/tasks?state=STARTED

# ¿cuántas han fallado hoy? (para un monitor o un aviso)
curl -s -u juan:secreta "http://127.0.0.1:5555/api/tasks?state=FAILURE"
```

El API necesita credenciales (`--basic_auth`) o, si prefiere dejarlo abierto solo
en local, la variable `FLOWER_UNAUTHENTICATED_API=1` (así es como lo sirve el
servicio generado por `python manage.py servicios_celery`).

---

## 4. Cuando algo no cuadra

| Síntoma | Causa habitual | Solución |
|---|---|---|
| Flower no muestra **ninguna tarea** | El worker no arrancó con **`-E`** (no envía eventos) | Reinicie el worker con `-E` (el servicio del paquete ya lo lleva) |
| No aparece **ningún worker** | El worker está parado, o el broker es otro | `python manage.py servicios_celery --estado`, `journalctl -u <proyecto>-celery-worker -f` |
| La tarea se queda en `PENDING` | Se encoló en una cola que nadie escucha (`CELERY_QUEUE`) | Iguale la cola del worker a la del proyecto, o quite `CELERY_QUEUE` |
| `FAILURE` con `ValueError: not enough values to unpack` | En macOS/Windows: el pool de procesos usa «spawn» y no preparó las tareas | Ponga `FORKED_BY_MULTIPROCESSING=1` en el `celery.py` o arranque con `--pool=threads` |
| `FAILURE` con `No hay certificado de firma` o «certificado vencido» | La revisión previa impidió emitir (es a propósito) | Corrija la firma: el comprobante quedó en `ERROR` y la tarea periódica lo recupera |
| `SUCCESS` pero la factura no está autorizada | El SRI la devolvió o sigue `EN_PROCESO` | Vea `Result` en el panel, y el estado real en **Comprobantes emitidos** (el panel es de Celery, no del SRI) |

Recuerde que el panel es el espejo de **Celery**, no del SRI: el estado de verdad
de un comprobante vive en la base de datos (`registro.estado`, `registro.mensajes`,
`registro.error`) y en sus archivos (`registro.archivos()`).

---

## 5. Cuándo no hace falta Flower

Si prefiere no mantener otro servicio, todo lo que se ve aquí se consulta también
desde la consola:

```bash
python manage.py servicios_celery --estado             # worker, beat, Flower y Redis
python manage.py revisar_firma                         # informe de la firma
celery -A mi_proyecto inspect active                   # tareas en ejecución
celery -A mi_proyecto inspect registered               # tareas que el worker conoce
celery -A mi_proyecto inspect stats                    # configuración y estado del worker
journalctl -u <proyecto>-celery-worker -f              # los logs del worker
```
