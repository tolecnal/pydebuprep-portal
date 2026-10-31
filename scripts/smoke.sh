#!/bin/sh
# Build and start the portal with compose, then upload a report with the pydebuprep
# CLI (PYDEBUPREP, default: pydebuprep) and check the portal answers. Cleans up after.
set -eu
cd "$(dirname "$0")/.."
PYDEBUPREP=${PYDEBUPREP:-pydebuprep}
work=$(mktemp -d)
project=portal-smoke
export POSTGRES_PASSWORD="smoke-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
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
for attempt in first again; do
  out=$(PYDEBUPREP_TOKEN=$token PYDEBUPREP_STATE_DIR="$work" \
        "$PYDEBUPREP" upload --allow-http --config "$work/pydebuprep.conf" \
        tests/data/sample-report.json)
  echo "$attempt: $out"
  case $out in *"uploaded to $base/reports/"*) ;; *) echo "smoke: upload failed" >&2; exit 1 ;; esac
done

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
