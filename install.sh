#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Uso: ./install.sh [--dry-run] [--yes]

  --dry-run  Valida Docker y Compose sin cambiar archivos ni desplegar.
  --yes      Omite la confirmación (útil para ejecución automatizada).
EOF
}

DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'Opción desconocida.\n' >&2; usage >&2; exit 2 ;;
  esac
done

fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }
on_error() {
  local code=$?
  printf 'Error: fallo en la línea %s (código %s). No se inició una nueva actualización.\n' "$1" "$code" >&2
  exit "$code"
}
trap 'on_error $LINENO' ERR

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then fail 'Docker no está instalado o no está en PATH.'; fi
if ! docker compose version >/dev/null 2>&1; then fail 'Se requiere Docker Compose v2 (docker compose).'; fi

COMPOSE_FILE=''
for candidate in compose.yaml compose.yml docker-compose.yaml docker-compose.yml; do
  if [[ -f "$ROOT_DIR/$candidate" ]]; then COMPOSE_FILE=$candidate; break; fi
done
[[ -n "$COMPOSE_FILE" ]] || fail 'No se encontró un archivo Compose en el directorio del instalador.'
COMPOSE=(docker compose -f "$COMPOSE_FILE")

# Keep the fully rendered Compose configuration private and never print it: it
# may contain credentials. Parse it locally and emit only the data mount path.
CONFIG_FILE=$(mktemp "${TMPDIR:-/tmp}/paquito-compose.XXXXXX")
chmod 600 "$CONFIG_FILE"
cleanup() { rm -f -- "$CONFIG_FILE"; }
trap cleanup EXIT
"${COMPOSE[@]}" config --format json >"$CONFIG_FILE" || fail 'Compose no pudo validar la configuración. Revisa el archivo sin compartir sus valores secretos.'

MOUNT_SOURCE=$(python3 - "$CONFIG_FILE" "$ROOT_DIR" <<'PY'
import json, os, sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
root = os.path.realpath(sys.argv[2])
services = config.get("services", {})
service = services.get("mibot")
if not isinstance(service, dict):
    raise SystemExit("El servicio 'mibot' no está definido.")
env = service.get("environment") or {}
required = ("BOT_TOKEN", "ALLOWED_USER_IDS", "ALERT_API_TOKEN")
bad = False
for name in required:
    value = env.get(name)
    if value is None or not str(value).strip():
        print(f"Falta configurar {name}.", file=sys.stderr)
        bad = True
        continue
    value = str(value).strip()
    if name == "BOT_TOKEN" and ("PEGA_AQUI" in value or "REEMPLAZAR" in value):
        print("BOT_TOKEN conserva un valor de ejemplo.", file=sys.stderr); bad = True
    if name == "ALLOWED_USER_IDS" and (not all(part.strip().isdigit() for part in value.split(","))):
        print("ALLOWED_USER_IDS debe contener uno o más ID numéricos separados por comas.", file=sys.stderr); bad = True
    if name == "ALERT_API_TOKEN" and (value.startswith("CAMBIA_") or "CAMBIA_ESTO" in value):
        print("ALERT_API_TOKEN conserva un valor de ejemplo.", file=sys.stderr); bad = True
if bad:
    raise SystemExit(1)
mounts = [m for m in service.get("volumes", []) if m.get("target") == "/app/data" and m.get("type") == "bind"]
if len(mounts) != 1 or not mounts[0].get("source"):
    raise SystemExit("Se requiere exactamente un bind mount de host en /app/data.")
source = mounts[0]["source"]
if not os.path.isabs(source):
    source = os.path.join(root, source)
source = os.path.realpath(source)
if os.path.commonpath((root, source)) != root:
    raise SystemExit("El bind mount de datos debe estar dentro del directorio del proyecto.")
print(source)
PY
) || fail 'Configuración incompleta o inválida. No se muestran credenciales.'

printf 'Validación correcta: Docker, Compose, servicio, variables requeridas y bind mount de datos.\n'
if (( DRY_RUN )); then
  printf 'Dry-run: se prepararía %s, se probaría la escritura como UID/GID 10001:10001 y después se ejecutaría pull y up -d. No se hizo ningún cambio.\n' "$MOUNT_SOURCE"
  exit 0
fi

if (( ! ASSUME_YES )); then
  [[ -t 0 ]] || fail 'La ejecución no interactiva requiere --yes.'
  printf 'Se asignará %s al UID/GID 10001:10001, se descargará la imagen y se recreará el servicio mibot. ¿Continuar? [y/N] '
  IFS= read -r answer
  [[ "$answer" == 'y' || "$answer" == 'Y' || "$answer" == 'yes' || "$answer" == 'YES' ]] || { printf 'Cancelado; no se realizaron cambios.\n'; exit 0; }
fi

mkdir -p "$MOUNT_SOURCE"
chown -R 10001:10001 "$MOUNT_SOURCE" || fail 'No se pudo asignar el propietario 10001:10001. Ejecuta el script con permisos suficientes.'
chmod 755 "$MOUNT_SOURCE" || fail 'No se pudieron establecer permisos en el directorio de datos.'

"${COMPOSE[@]}" pull mibot
IMAGE=$("${COMPOSE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["mibot"]["image"])')
docker run --rm --pull never --user 10001:10001 --volume "$MOUNT_SOURCE:/app/data" --entrypoint python "$IMAGE" -c 'import os, tempfile; p="/app/data"; f=tempfile.NamedTemporaryFile(prefix=".paquito-write-check-", dir=p, delete=False); name=f.name; f.close(); os.unlink(name)'
printf 'Comprobada la escritura en los datos como UID/GID 10001:10001.\n'
"${COMPOSE[@]}" up -d mibot
"${COMPOSE[@]}" ps mibot
