#!/bin/sh
# Build and start the portal with compose, then upload reports with the pydebuprep
# CLI (PYDEBUPREP, default: pydebuprep) and check what the portal stored and answers.
# PYTHON (default: python3) must have the pinned pydebuprep installed. Cleans up after.
set -eu
cd "$(dirname "$0")/.."
PYDEBUPREP=${PYDEBUPREP:-pydebuprep}
PYTHON=${PYTHON:-python3}
work=$(mktemp -d)
project=portal-smoke
POSTGRES_PASSWORD="smoke-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
export POSTGRES_PASSWORD
export PORTAL_ENV_FILE="$work/env"
PORTAL_PORT=$(python3 -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])")
export PORTAL_PORT
base=http://127.0.0.1:$PORTAL_PORT
cleanup() {
  docker compose -p "$project" down -v >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

cat > "$PORTAL_ENV_FILE" <<ENV
PORTAL_BASE_URL=$base
PORTAL_ALLOW_HTTP=1
PORTAL_SESSION_SECRET=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
PORTAL_OIDC_ISSUER=https://idp.invalid/realms/smoke
PORTAL_OIDC_CLIENT_ID=portal
PORTAL_OIDC_CLIENT_SECRET=unused
ENV

docker compose -p "$project" up -d --build --wait
token=$(docker compose -p "$project" exec -T portal \
  python -m pydebuprep_portal.manage create-token smoke --team smoke)

cat > "$work/pydebuprep.conf" <<CONF
upload:
  url: $base
  pending_dir: null
CONF
upload() {  # NAME FILE
  out=$(PYDEBUPREP_TOKEN=$token PYDEBUPREP_STATE_DIR="$work" \
        "$PYDEBUPREP" upload --allow-http --config "$work/pydebuprep.conf" "$2")
  echo "$1: $out"
  case $out in *"uploaded to $base/reports/"*) ;; *) echo "smoke: upload failed" >&2; exit 1 ;; esac
}
upload first tests/data/sample-report.json
upload again tests/data/sample-report.json

# A report the CLI makes here and now, with this machine's ID; and two reports of one
# host name from two machines, with nginx and Apache findings.
"$PYDEBUPREP" report --no-baseline --checks os-release --format json -o "$work/fresh.json" || true
upload fresh "$work/fresh.json"
"$PYTHON" scripts/smoke_reports.py "$work"
upload clash-1 "$work/smoke-web-1.json"
upload clash-2 "$work/smoke-web-2.json"
fresh_host=$("$PYTHON" -c "import json, sys; print(json.load(open(sys.argv[1]))['host']['hostname'])" "$work/fresh.json")
# systemd's own app-specific ID where it is available, else pydebuprep's.
if command -v systemd-id128 >/dev/null && [ -s /etc/machine-id ]; then
  fresh_machine=$(systemd-id128 machine-id -a e44561703c184c26a2c15fb541fc276a)
else
  fresh_machine=$("$PYTHON" -c "from pydebuprep.identity import machine_id; from pydebuprep.system import System; print(machine_id(System()) or '')")
fi
docker compose -p "$project" exec -T portal python - "$fresh_host" "$fresh_machine" \
  < scripts/smoke_check.py

url=$(docker compose -p "$project" exec -T portal \
  python -m pydebuprep_portal.manage create-config smoke default)
mkdir -m 0700 "$work/configs"  # as the package creates under /var/lib/pydebuprep
out=$(PYDEBUPREP_TOKEN=$token PYDEBUPREP_STATE_DIR="$work" \
      "$PYDEBUPREP" configtest --allow-http --config "$url" 2>&1)
echo "configtest: $out"
case $out in *warning*) echo "smoke: config fetch warned" >&2; exit 1 ;; esac
case $out in *"fetched now"*) ;; *) echo "smoke: config fetch failed" >&2; exit 1 ;; esac
ls "$work/configs"/*.json >/dev/null || { echo "smoke: no saved copy of the config" >&2; exit 1; }

for path in /healthz /docs /static/swagger/swagger-ui-bundle.js; do
  python3 -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5)" "$base$path"
done
code=$(python3 -c "
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen(sys.argv[1], timeout=5)
except urllib.error.HTTPError as e:
    print(e.code)" "$base/api/v1/reports")
[ "$code" = 401 ] || { echo "smoke: reading reports without signing in answered $code" >&2; exit 1; }
echo "smoke: ok"
