#!/bin/sh
# Bring the database schema up to date, then serve the portal.
set -eu
python -m pydebuprep_portal.manage migrate
exec uvicorn --factory pydebuprep_portal.app:from_environment \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips "${PORTAL_FORWARDED_ALLOW_IPS:-127.0.0.1}"
