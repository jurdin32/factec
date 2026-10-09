#!/usr/bin/env bash
#
# Servicios de systemd para Celery en Linux: worker, beat y Flower (el panel).
#
# Pensado para un proyecto Django que usa el paquete factec (facturación
# electrónica del SRI): crea las unidades, las habilita y las arranca.
#
#   sudo ./instalar_servicios_celery.sh                     # worker + beat + Flower
#   sudo ./instalar_servicios_celery.sh --sin-flower        # sin el panel
#   ./instalar_servicios_celery.sh --dry-run                # enseña lo que haría
#   ./instalar_servicios_celery.sh --estado                 # ¿están funcionando?
#   ./instalar_servicios_celery.sh --reiniciar              # tras desplegar
#   sudo ./instalar_servicios_celery.sh --quitar            # los elimina
#
# Normalmente no hace falta llamarlo a mano: el paquete trae el comando
#   python manage.py servicios_celery --dry-run
#   python manage.py servicios_celery --estado
# que ya le pasa el módulo de ajustes y el entorno virtual correctos.
#
# Se ejecuta desde la carpeta del proyecto (donde está manage.py); detecta solo
# el módulo de ajustes, el entorno virtual y la ruta de celery(1).
#
# Flower es el panel de monitorización: permite ver las tareas que van llegando
# (sri_fe.emitir_comprobante, sri_fe.revisar_certificado…), las que fallan y los
# workers conectados. Se abre en http://127.0.0.1:5555 (por SSH:
# ssh -L 5555:127.0.0.1:5555 usuario@servidor).
#
set -euo pipefail

# ------------------------------------------------------------------ valores

PROYECTO_DIR="$PWD"
NOMBRE="${NOMBRE:-}"                       # nombre de los servicios (por omisión, el del proyecto)
MODULO=""                                  # módulo de settings (se detecta)
VENV="${VENV:-}"                           # entorno virtual (se detecta)
USUARIO="${USUARIO_SERVICIO:-$(id -un)}"
GRUPO="${GRUPO_SERVICIO:-$(id -gn)}"
DESTINO="/etc/systemd/system"
CONCURRENCIA="${CONCURRENCIA:-4}"
LOGLEVEL="info"
ACCION="instalar"                          # instalar | quitar | estado | reiniciar
CREAR_WORKER=1
CREAR_BEAT=1
CREAR_FLOWER=1
PUERTO_FLOWER="${PUERTO_FLOWER:-5555}"
DIRECCION_FLOWER="${DIRECCION_FLOWER:-127.0.0.1}"
AUTH_FLOWER="${FLOWER_BASIC_AUTH:-}"       # usuario:clave (recomendado si no es local)
DRY_RUN=0
SOLO_ARCHIVOS=0
SUDO=""

# ------------------------------------------------------------------ salida

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  ROJO=$'\033[31m'; VERDE=$'\033[32m'; AMARILLO=$'\033[33m'
  AZUL=$'\033[34m'; NEGRITA=$'\033[1m'; APAGADO=$'\033[0m'
else
  ROJO=""; VERDE=""; AMARILLO=""; AZUL=""; NEGRITA=""; APAGADO=""
fi

info()  { printf '%s\n' "${AZUL}•${APAGADO} $*"; }
ok()    { printf '%s\n' "${VERDE}✓${APAGADO} $*"; }
aviso() { printf '%s\n' "${AMARILLO}!${APAGADO} $*"; }
error() { printf '%s\n' "${ROJO}✗${APAGADO} $*" >&2; }
manda() { printf '%s\n' "    ${APAGADO}\$ $*${APAGADO}"; }

uso() {
  cat <<'AYUDA'
Servicios de systemd para Celery (worker y beat) de un proyecto Django.

Uso: instalar_servicios_celery.sh [opciones]

Acciones (una sola):
  (ninguna)          Crea las unidades, recarga systemd y las arranca
  --dry-run          Enseña las unidades y los comandos, sin tocar nada
  --estado           Muestra si Redis, el worker, el beat y Flower funcionan
  --comandos         Enseña los comandos para este proyecto (con sus nombres)
  --reiniciar        Reinicia los servicios ya instalados
  --quitar           Para, deshabilita y borra las unidades

Opciones:
  --proyecto-dir DIR Carpeta del proyecto (por omisión, la actual)
  --nombre NOMBRE    Nombre base de los servicios (por omisión, el del proyecto)
  --modulo MODULO    Módulo de ajustes de Django (p. ej. facturero). Se detecta solo
  --venv DIR         Entorno virtual con celery (se detecta solo: ./.venv, ./venv…)
  --usuario USUARIO  Usuario con el que corre el servicio (por omisión, usted)
  --grupo GRUPO      Grupo del servicio (por omisión, el suyo)
  --concurrencia N   Procesos del worker (por omisión, 4)
  --loglevel NIVEL   info, debug, warning (por omisión, info)
  --destino DIR      Carpeta donde escribir las unidades
                     (por omisión, /etc/systemd/system)
  --solo-archivos    Escribe las unidades y no toca systemctl
                     (útil para revisarlas o copiarlas a otro servidor)
  --solo-worker      No crear los servicios del beat ni de Flower
  --solo-beat        Solo el beat (sin worker ni Flower)
  --sin-flower       No crear el panel (Flower)
  --puerto N         Puerto de Flower (por omisión, 5555)
  --direccion IP     Dirección donde escucha Flower (por omisión, 127.0.0.1)
  --flower-auth U:C  Usuario y clave del panel (por omisión, sin clave)
  -h, --help         Esta ayuda

Ejemplos:
  sudo ./instalar_servicios_celery.sh
  sudo ./instalar_servicios_celery.sh --concurrencia 2 --usuario www-data
  sudo ./instalar_servicios_celery.sh --direccion 0.0.0.0 --flower-auth juan:secreta
  ./instalar_servicios_celery.sh --dry-run --destino /tmp/unidades

Para ver el panel desde su ordenador (sin abrir el puerto al mundo):
  ssh -L 5555:127.0.0.1:5555 usuario@servidor   y abra http://127.0.0.1:5555
  (necesita el paquete con el extra: pip install "factec[django,flower]")
AYUDA
}

# --------------------------------------------------------------- argumentos

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --estado) ACCION="estado" ;;
    --comandos) ACCION="comandos" ;;
    --reiniciar) ACCION="reiniciar" ;;
    --quitar) ACCION="quitar" ;;
    --proyecto-dir) PROYECTO_DIR="${2:?falta la carpeta}"; shift ;;
    --nombre) NOMBRE="${2:?falta el nombre}"; shift ;;
    --modulo) MODULO="${2:?falta el módulo}"; shift ;;
    --venv) VENV="${2:?falta la carpeta del entorno}"; shift ;;
    --usuario) USUARIO="${2:?falta el usuario}"; shift ;;
    --grupo) GRUPO="${2:?falta el grupo}"; shift ;;
    --concurrencia) CONCURRENCIA="${2:?falta el número}"; shift ;;
    --loglevel) LOGLEVEL="${2:?falta el nivel}"; shift ;;
    --destino) DESTINO="${2:?falta la carpeta}"; shift ;;
    --solo-archivos) SOLO_ARCHIVOS=1 ;;
    --solo-worker) CREAR_BEAT=0; CREAR_FLOWER=0 ;;
    --solo-beat) CREAR_WORKER=0; CREAR_FLOWER=0 ;;
    --sin-flower) CREAR_FLOWER=0 ;;
    --puerto) PUERTO_FLOWER="${2:?falta el puerto}"; shift ;;
    --direccion) DIRECCION_FLOWER="${2:?falta la dirección}"; shift ;;
    --flower-auth) AUTH_FLOWER="${2:?falta usuario:clave}"; shift ;;
    -h|--help) uso; exit 0 ;;
    *) error "Opción desconocida: $1"; echo; uso; exit 2 ;;
  esac
  shift
done

# ------------------------------------------------------------ comprobaciones

comprobar_entorno() {
  if [ "$(uname -s)" != "Linux" ]; then
    error "Este script instala servicios de systemd: solo sirve en Linux."
    info  "En macOS use 'brew services' o arranque celery a mano:"
    manda "$VENV/bin/celery -A $MODULO worker -l info"
    exit 2
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    error "No hay systemctl: este servidor no usa systemd."
    exit 2
  fi
}

detectar_proyecto() {
  # Sube hasta encontrar manage.py.
  local dir="$PROYECTO_DIR"
  while [ "$dir" != "/" ] && [ ! -f "$dir/manage.py" ]; do
    dir="$(dirname "$dir")"
  done
  if [ ! -f "$dir/manage.py" ]; then
    error "No encuentro manage.py desde $PROYECTO_DIR (use --proyecto-dir)."
    exit 2
  fi
  PROYECTO_DIR="$(cd "$dir" && pwd)"
  [ -n "$NOMBRE" ] || NOMBRE="$(basename "$PROYECTO_DIR")"
  [ -n "$NOMBRE" ] || NOMBRE="facturacion"

  if [ -z "$MODULO" ]; then
    # El módulo de ajustes es la carpeta (dentro del proyecto) con settings.py.
    local encontrado
    encontrado="$(cd "$PROYECTO_DIR" && find . -maxdepth 2 -name settings.py -not -path "*/.*" 2>/dev/null | head -1)"
    if [ -z "$encontrado" ]; then
      error "No encuentro el módulo de ajustes (un settings.py). Use --modulo."
      exit 2
    fi
    MODULO="$(basename "$(dirname "$encontrado")")"
  fi
}

detectar_venv() {
  if [ -n "$VENV" ]; then
    VENV="$(cd "$VENV" && pwd)"
  else
    local candidato
    for candidato in "$PROYECTO_DIR/.venv" "$PROYECTO_DIR/venv" \
                     "$(dirname "$PROYECTO_DIR")/.venv" "$(dirname "$PROYECTO_DIR")/venv" \
                     "${VIRTUAL_ENV:-}"; do
      if [ -n "$candidato" ] && [ -x "$candidato/bin/celery" ]; then
        VENV="$(cd "$candidato" && pwd)"
        break
      fi
    done
  fi
  if [ -z "$VENV" ] || [ ! -x "$VENV/bin/celery" ]; then
    error "No encuentro celery(1) en ningún entorno virtual."
    info  "Instale el paquete con su extra de Django:"
    manda "pip install \"factec[django]\""
    manda "pip install redis        # el cliente del broker (si usa Redis)"
    info  "Y si usa un entorno en otro sitio, indíquelo: --venv /ruta/al/venv"
    exit 2
  fi
}

# Flower se instala con el extra del paquete: pip install "factec[django,flower]".
flower_instalado() {
  [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import flower" >/dev/null 2>&1
}

comprobar_flower() {
  if [ "$CREAR_FLOWER" -eq 0 ] || flower_instalado; then
    return 0
  fi
  aviso "Flower no está instalado en $VENV: no creo su servicio."
  info  "Para el panel:  $VENV/bin/pip install \"factec[django,flower]\""
  info  "Y vuelva a ejecutar este script."
  CREAR_FLOWER=0
}

preparar_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
  elif [ -w "$(dirname "$DESTINO")" ] 2>/dev/null && [ -w "$DESTINO" ] 2>/dev/null; then
    SUDO=""
  else
    if ! command -v sudo >/dev/null 2>&1; then
      error "Hace falta root para escribir en $DESTINO (use --destino o sudo)."
      exit 2
    fi
    SUDO="sudo"
    info "Se pedirá la contraseña de sudo para escribir en $DESTINO."
  fi
}

# ---------------------------------------------------------------- unidades

# Rutas y variables comunes a las dos unidades.
unidad_comun() {
  local descripcion="$1"
  cat <<COMUN
[Unit]
Description=$descripcion
Documentation=https://github.com/jurdin32/factec
After=network-online.target
Wants=network-online.target
COMUN
  # Si Redis está instalado como servicio local, se arranca antes.
  if command -v systemctl >/dev/null 2>&1 &&
     systemctl list-unit-files --type=service 2>/dev/null | grep -q '^redis-server\.service'; then
    printf 'After=redis-server.service\nWants=redis-server.service\n'
  fi
}

unidad_servicio() {
  local descripcion="$1" extra="$2"
  cat <<SERVICIO

[Service]
Type=simple
User=$USUARIO
Group=$GRUPO
WorkingDirectory=$PROYECTO_DIR
Environment="DJANGO_SETTINGS_MODULE=$MODULO.settings"
Environment="PYTHONUNBUFFERED=1"
Environment="FORKED_BY_MULTIPROCESSING=1"
EnvironmentFile=-$PROYECTO_DIR/.env
$extra
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=60
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICIO
}

unidad_worker() {
  printf '# Worker de Celery de %s (%s).\n' "$NOMBRE" "$PROYECTO_DIR"
  printf '# Generado por el script de servicios del paquete factec.\n'
  unidad_comun "Celery worker de $NOMBRE (facturación electrónica SRI)"
  # -E envía los eventos de las tareas: sin eso Flower no las vería.
  unidad_servicio "$NOMBRE" "ExecStart=$VENV/bin/celery -A $MODULO worker -l $LOGLEVEL -c $CONCURRENCIA -E --without-mingle --without-gossip"
}

unidad_beat() {
  printf '# Scheduler de Celery (tareas periódicas) de %s.\n' "$NOMBRE"
  printf '# Generado por el script de servicios del paquete factec.\n'
  unidad_comun "Celery beat de $NOMBRE (revisa la firma y reintenta los pendientes)"
  unidad_servicio "$NOMBRE" "StateDirectory=$NOMBRE
ExecStart=$VENV/bin/celery -A $MODULO beat -l $LOGLEVEL --schedule=/var/lib/$NOMBRE/celerybeat-schedule"
}

unidad_flower() {
  printf '# Panel Flower (monitorización de Celery) de %s.\n' "$NOMBRE"
  printf '# Generado por el script de servicios del paquete factec.\n'
  unidad_comun "Flower (panel de Celery) de $NOMBRE"
  local opciones="--address=$DIRECCION_FLOWER --port=$PUERTO_FLOWER"
  if [ -n "$AUTH_FLOWER" ]; then
    opciones="$opciones --basic_auth=$AUTH_FLOWER"
  fi
  unidad_servicio "$NOMBRE" "StateDirectory=$NOMBRE
Environment=\"FLOWER_UNAUTHENTICATED_API=1\"
ExecStart=$VENV/bin/celery -A $MODULO flower $opciones"
}

nombre_worker() { printf '%s-celery-worker.service' "$NOMBRE"; }
nombre_beat()   { printf '%s-celery-beat.service' "$NOMBRE"; }
nombre_flower() { printf '%s-flower.service' "$NOMBRE"; }
ruta_unidad()   { printf '%s/%s' "$DESTINO" "$1"; }

escribir_unidad() {
  local archivo="$1"; shift
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n%s\n' "${NEGRITA}── $DESTINO/$archivo ──${APAGADO}"
    "$@"
    return 0
  fi
  if [ "$SOLO_ARCHIVOS" -eq 1 ] && [ -w "$DESTINO" ]; then
    mkdir -p "$DESTINO"
    "$@" > "$(ruta_unidad "$archivo")"
  else
    $SUDO mkdir -p "$DESTINO"
    "$@" | $SUDO tee "$(ruta_unidad "$archivo")" >/dev/null
  fi
  ok "Unidad escrita: $(ruta_unidad "$archivo")"
}

# ---------------------------------------------------------------- acciones

instalar() {
  info "Proyecto : $PROYECTO_DIR (módulo $MODULO)"
  info "Entorno  : $VENV"
  info "Usuario  : $USUARIO:$GRUPO"
  info "Destino  : $DESTINO"
  if [ "$CREAR_FLOWER" -eq 1 ]; then
    info "Flower   : http://$DIRECCION_FLOWER:$PUERTO_FLOWER${AUTH_FLOWER:+ (con usuario y clave)}"
  fi

  if [ "$CREAR_WORKER" -eq 1 ]; then
    escribir_unidad "$(nombre_worker)" unidad_worker
  fi
  if [ "$CREAR_BEAT" -eq 1 ]; then
    escribir_unidad "$(nombre_beat)" unidad_beat
  fi
  if [ "$CREAR_FLOWER" -eq 1 ]; then
    escribir_unidad "$(nombre_flower)" unidad_flower
  fi

  if [ "$SOLO_ARCHIVOS" -eq 1 ]; then
    aviso "Unidades escritas en $DESTINO. No se toca systemctl (--solo-archivos)."
    info  "En el servidor:  sudo cp $DESTINO/*.service /etc/systemd/system/ && sudo systemctl daemon-reload"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n%s\n' "${NEGRITA}Comandos que ejecutaría:${APAGADO}"
    manda "systemctl daemon-reload"
    [ "$CREAR_WORKER" -eq 1 ] && manda "systemctl enable --now $(nombre_worker)"
    [ "$CREAR_BEAT" -eq 1 ] && manda "systemctl enable --now $(nombre_beat)"
    [ "$CREAR_FLOWER" -eq 1 ] && manda "systemctl enable --now $(nombre_flower)"
    manda "systemctl status $(nombre_worker)"
    aviso "Nada se ha tocado (--dry-run)."
    return 0
  fi

  preparar_sudo
  $SUDO systemctl daemon-reload
  if [ "$CREAR_WORKER" -eq 1 ]; then
    $SUDO systemctl enable --now "$(nombre_worker)" >/dev/null
    ok "Worker habilitado y arrancado."
  fi
  if [ "$CREAR_BEAT" -eq 1 ]; then
    $SUDO systemctl enable --now "$(nombre_beat)" >/dev/null
    ok "Beat habilitado y arrancado."
  fi
  if [ "$CREAR_FLOWER" -eq 1 ]; then
    $SUDO systemctl enable --now "$(nombre_flower)" >/dev/null
    ok "Flower habilitado y arrancado: panel en http://$DIRECCION_FLOWER:$PUERTO_FLOWER"
  fi

  sleep 3
  estado
  comprobar_redis
}

quitar() {
  preparar_sudo
  local unidad
  for unidad in "$(nombre_worker)" "$(nombre_beat)" "$(nombre_flower)"; do
    if [ -f "$(ruta_unidad "$unidad")" ]; then
      $SUDO systemctl disable --now "$unidad" >/dev/null 2>&1 || true
      $SUDO rm -f "$(ruta_unidad "$unidad")"
      ok "Eliminada $unidad"
    fi
  done
  $SUDO systemctl daemon-reload
  $SUDO systemctl reset-failed >/dev/null 2>&1 || true
  ok "Servicios de Celery eliminados."
}

reiniciar() {
  preparar_sudo
  local unidad
  for unidad in "$(nombre_worker)" "$(nombre_beat)" "$(nombre_flower)"; do
    [ -f "$(ruta_unidad "$unidad")" ] || continue
    $SUDO systemctl restart "$unidad"
    ok "Reiniciada $unidad"
  done
  sleep 3
  estado
}

comandos() {
  # Los comandos exactos de ESTE proyecto: el módulo y el entorno virtual ya
  # resueltos, para no tener que sustituir «mi_proyecto» a mano.
  local python="$VENV/bin/python"
  local celery="$VENV/bin/celery"
  local flower="--address=$DIRECCION_FLOWER --port=$PUERTO_FLOWER"
  [ -n "$AUTH_FLOWER" ] && flower="$flower --basic_auth=$AUTH_FLOWER"

  printf '\n%s\n\n' "${NEGRITA}Comandos para $NOMBRE${APAGADO}"

  printf '%s\n' "${NEGRITA}El broker${APAGADO} (Redis; el paquete lo busca en CELERY_BROKER_URL)"
  manda "redis-server"
  printf '\n'

  printf '%s\n' "${NEGRITA}El worker${APAGADO} (ejecuta las tareas)"
  manda "$celery -A $MODULO worker -l info -c $CONCURRENCIA"
  printf '\n'

  printf '%s\n' "${NEGRITA}Las tareas periódicas${APAGADO} (firma a las 7:00 y reintentos)"
  manda "$celery -A $MODULO beat -l info"
  printf '\n'

  printf '%s\n' "${NEGRITA}El panel${APAGADO} (http://$DIRECCION_FLOWER:$PUERTO_FLOWER)"
  manda "$celery -A $MODULO flower $flower"
  printf '\n'

  printf '%s\n' "${NEGRITA}Como servicios de Linux${APAGADO} (arrancan solos y se reinician)"
  manda "sudo $python manage.py servicios_celery"
  printf '\n'

  printf '%s\n' "${NEGRITA}Comprobar, sin arrancar nada${APAGADO}"
  manda "$python manage.py servicios_celery --estado"
  manda "$celery -A $MODULO inspect registered"
  printf '\n%s\n' "${AMARILLO}!${APAGADO} Use el celery del entorno virtual ($VENV): active el venv o llámelo por su ruta."
}

estado_procesos_sin_systemd() {
  # Sin systemd (macOS, contenedores…): se mira si los procesos están vivos.
  local patron
  for patron in "celery -A .* worker" "celery -A .* beat" "celery -A .* flower"; do
    if pgrep -f "$patron" >/dev/null 2>&1; then
      printf '  %s %-28s %s\n' "${VERDE}✓${APAGADO}" "$(printf '%s' "$patron" | sed 's/celery -A .\* //')" "en marcha"
    else
      printf '  %s %-28s %s\n' "${AMARILLO}·${APAGADO}" "$(printf '%s' "$patron" | sed 's/celery -A .\* //')" "parado"
    fi
  done
}

estado() {
  local unidad estado
  printf '\n%s\n' "${NEGRITA}Estado de los servicios${APAGADO}"
  if ! command -v systemctl >/dev/null 2>&1; then
    info "Sin systemd en este equipo: se comprueban los procesos."
    estado_procesos_sin_systemd
  fi

  if command -v systemctl >/dev/null 2>&1; then
    for unidad in redis-server "$(nombre_worker)" "$(nombre_beat)" "$(nombre_flower)"; do
      estado="$(systemctl is-active "$unidad" 2>/dev/null || true)"
      if [ "$estado" = "active" ]; then
        printf '  %s %-28s %s\n' "${VERDE}✓${APAGADO}" "$unidad" "funcionando"
      elif [ "$estado" = "inactive" ] || [ -z "$estado" ]; then
        printf '  %s %-28s %s\n' "${AMARILLO}·${APAGADO}" "$unidad" "parado o no instalado"
      else
        printf '  %s %-28s %s\n' "${ROJO}✗${APAGADO}" "$unidad" "$estado"
      fi
    done
  fi

  if command -v redis-cli >/dev/null 2>&1; then
    if redis-cli ping >/dev/null 2>&1; then
      printf '  %s %-28s %s\n' "${VERDE}✓${APAGADO}" "redis (broker)" "responde PONG"
    else
      printf '  %s %-28s %s\n' "${ROJO}✗${APAGADO}" "redis (broker)" "no responde"
    fi
  fi

  # El panel: responde en su puerto y, si el worker envía eventos, se ven las tareas.
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$(nombre_flower)"; then
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS -o /dev/null "http://$DIRECCION_FLOWER:$PUERTO_FLOWER/"; then
        ok "Flower responde en http://$DIRECCION_FLOWER:$PUERTO_FLOWER"
      else
        aviso "Flower está activo pero no responde en el puerto $PUERTO_FLOWER."
      fi
    fi
    if ! grep -q ' -E ' "$(ruta_unidad "$(nombre_worker)")" 2>/dev/null; then
      aviso "El worker no envía eventos (-E): Flower verá el worker pero no las tareas."
      info  "Vuelva a generar las unidades con este script para añadirlo."
    fi
  fi

  # Las tareas registradas: es la prueba de que el worker ve el paquete.
  if [ -x "$VENV/bin/celery" ] && command -v systemctl >/dev/null 2>&1 &&
     systemctl is-active --quiet "$(nombre_worker)"; then
    local tareas
    tareas="$(cd "$PROYECTO_DIR" && "$VENV/bin/celery" -A "$MODULO" inspect registered 2>/dev/null | grep -c 'sri_fe\.' || true)"
    if [ "${tareas:-0}" -gt 0 ]; then
      ok "El worker tiene ${tareas} tareas sri_fe.* registradas."
    else
      aviso "El worker no lista tareas sri_fe.*: revise el celery.py (autodiscover_tasks)."
    fi
  fi

  local unidad
  for unidad in "$(nombre_worker)" "$(nombre_beat)" "$(nombre_flower)"; do
    if command -v systemctl >/dev/null 2>&1 && systemctl is-failed --quiet "$unidad" 2>/dev/null; then
      aviso "Últimas líneas de $unidad:"
      $SUDO journalctl -u "$unidad" -n 15 --no-pager 2>/dev/null | sed 's/^/    /'
    fi
  done
  printf '\n  %s\n' "Panel:         http://$DIRECCION_FLOWER:$PUERTO_FLOWER (por SSH: ssh -L $PUERTO_FLOWER:127.0.0.1:$PUERTO_FLOWER usuario@servidor)"
  printf '  %s\n' "Logs en vivo:  journalctl -u $(nombre_worker) -f"
}

comprobar_redis() {
  if command -v redis-cli >/dev/null 2>&1 && ! redis-cli ping >/dev/null 2>&1; then
    aviso "Redis no responde: el worker enviará las tareas pero no las recibirá."
    info  "Arranque Redis:  sudo systemctl enable --now redis-server"
    info  "O instálelo:     sudo apt install redis-server"
  fi
  if ! grep -q "CELERY_BROKER_URL" "$PROYECTO_DIR/$MODULO/settings.py" 2>/dev/null; then
    aviso "No veo CELERY_BROKER_URL en $MODULO/settings.py: sin broker, el paquete emite de forma síncrona."
  fi
}

# -------------------------------------------------------------------- main

case "$ACCION" in
  comandos)
    # Sirve en cualquier sistema: solo imprime los comandos del proyecto.
    detectar_proyecto
    detectar_venv
    comandos
    ;;
  estado)
    # El estado se puede consultar también en macOS o en un contenedor, y no
    # necesita permisos: solo mira y cuenta.
    detectar_proyecto
    detectar_venv
    estado
    comprobar_redis
    ;;
  quitar)
    comprobar_entorno
    detectar_proyecto
    preparar_sudo
    quitar
    ;;
  reiniciar)
    comprobar_entorno
    detectar_proyecto
    detectar_venv
    preparar_sudo
    reiniciar
    ;;
  *)
    detectar_proyecto
    detectar_venv
    comprobar_flower
    if [ "$DRY_RUN" -eq 1 ] || [ "$SOLO_ARCHIVOS" -eq 1 ]; then
      if [ "$(uname -s)" = "Linux" ]; then comprobar_entorno; fi
      if [ "$SOLO_ARCHIVOS" -eq 1 ] && [ "$(uname -s)" != "Linux" ]; then
        aviso "No es Linux: solo se generan los archivos (--solo-archivos)."
      fi
    else
      comprobar_entorno
      preparar_sudo
    fi
    instalar
    ;;
esac
