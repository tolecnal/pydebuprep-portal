#!/bin/sh
# Move a portal's database from PostgreSQL 17 (pydebuprep-portal 0.3.0 and older) to
# PostgreSQL 18. Run it where you run `docker compose`, after putting this release's
# compose.yaml in place and before starting it; PROJECT names the compose project if
# you give one with -p:
#   scripts/upgrade-postgres-18.sh
# It stops the portal, dumps the database from the old volume "db" with a temporary
# PostgreSQL 17, restores it into the new volume "postgres18", compares the two, and
# starts the portal again. The old volume is left as it was; remove it yourself once
# the portal works (the script prints how).
set -eu
compose() { docker compose ${PROJECT:+-p "$PROJECT"} "$@"; }
project=$(compose config | sed -n 's/^name: //p')
old="${project}_db"
new="${project}_postgres18"
temp="${project}-postgres17-upgrade"
sql() { compose exec -T db psql -U portal -d portal -tAc "$1"; }

if ! docker volume inspect "$old" >/dev/null 2>&1; then
  echo "upgrade: no volume $old, so nothing to move; start the portal as usual."
  exit 0
fi

# Already on PostgreSQL 18? Look before stopping anything.
if docker volume inspect "$new" >/dev/null 2>&1; then
  compose up -d --wait db
  if [ "$(sql "SELECT to_regclass('public.reports') IS NOT NULL")" = t ]; then
    reports=$(sql "SELECT count(*) FROM reports")
    if [ "$reports" != 0 ]; then
      echo "upgrade: already on PostgreSQL 18 ($reports reports); nothing to do."
      echo "upgrade: the old volume $old is still there: docker volume rm $old"
      exit 0
    fi
    echo "upgrade: PostgreSQL 18 has a database without reports: the new release was" >&2
    echo "started before upgrading. To move the old database into it, run:" >&2
    echo "  docker compose down && docker volume rm $new && $0" >&2
    exit 1
  fi
fi

echo "upgrade: stopping $project (its volumes are kept)"
compose down
trap 'docker rm -f "$temp" >/dev/null 2>&1 || true' EXIT

echo "upgrade: starting PostgreSQL 18 on the new volume"
compose up -d --wait db

echo "upgrade: reading the old database from $old with PostgreSQL 17"
docker run -d --name "$temp" -v "$old:/var/lib/postgresql/data" postgres:17 >/dev/null
tries=0
until docker exec "$temp" pg_isready -q -U portal -d portal; do
  tries=$((tries + 1))
  [ "$tries" -lt 120 ] || { echo "upgrade: PostgreSQL 17 didn't start" >&2; exit 1; }
  sleep 1
done

echo "upgrade: copying the database"
docker exec "$temp" pg_dump -U portal -d portal -Fc --no-owner \
  | compose exec -T db pg_restore -U portal -d portal --no-owner --exit-on-error

count() {  # CONTAINER-COMMAND...: the number of reports and the schema revision
  "$@" psql -U portal -d portal -tAc \
    "SELECT (SELECT count(*) FROM reports) || ' reports, revision ' || (SELECT version_num FROM alembic_version)"
}
before=$(count docker exec "$temp")
after=$(count compose exec -T db)
echo "upgrade: PostgreSQL 17 had $before; PostgreSQL 18 has $after"
[ "$before" = "$after" ] || { echo "upgrade: they differ; the portal is left stopped." >&2; exit 1; }
docker rm -f "$temp" >/dev/null

compose up -d --wait
echo "upgrade: done. Once the portal works, remove the old volume with: docker volume rm $old"
