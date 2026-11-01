# pydebuprep-portal

A portal that keeps [pydebuprep](https://pydebuprep.app/) reports in one place. Hosts
upload their reports with a token; people sign in with OpenID Connect and see the
newest reports first, open any report in every format pydebuprep renders, follow a
host's timeline and search findings.

pydebuprep works without the portal. A host uses it only when its config says so.

> **Status: the build order is complete.** Uploads, sign-in, reports, hosts, search,
> teams and roles, global, team and host tokens, the unassigned queue, retention, the
> audit log, and configs that hosts fetch with `pydebuprep --config https://…`.

## Preview it locally

With Docker, `make preview` builds the portal and starts it with PostgreSQL and a
ready-made Keycloak, then fills it with demo teams, hosts and a few weeks of reports:

```sh
make preview        # then open http://localhost:8400
make preview-down   # stop it and delete its data
```

On a headless machine, give the name or address you'll browse to; the preview's ports
(8400 and Keycloak's 8480) are then published on every interface:

```sh
make preview PREVIEW_HOST=dev-box.example.lan   # then open http://dev-box.example.lan:8400
```

Sign in as `admin`, `lead` (lead of team web), `member` (of team web) or `auditor`;
each password is the user name. Every secret in `compose.preview.yaml` is public: it is
for trying the portal out, never for real reports.

## Running it

The portal runs as a Docker image (Debian 13, `debian:trixie-slim`) next to PostgreSQL 18,
behind your own reverse proxy, which terminates TLS:

```sh
cp .env.example .env      # then fill it in: see "Settings"
docker compose up -d
```

`docker compose` pulls the released image (`ghcr.io/tolecnal/pydebuprep-portal`, the
version in `compose.yaml`, or `PORTAL_VERSION`), starts PostgreSQL, brings the database
schema up to date and serves the portal on `127.0.0.1:8000`. `docker compose up --build`
builds the image from this checkout instead. Every released image carries a signed
build-provenance attestation:

```sh
gh attestation verify oci://ghcr.io/tolecnal/pydebuprep-portal:0.3.0 -R tolecnal/pydebuprep-portal
```

Point the reverse proxy at it, and set
`PORTAL_BASE_URL` to the URL people and hosts use, for example
`https://portal.example.com`. The portal builds its links and redirects from
`PORTAL_BASE_URL`, so the proxy's `X-Forwarded-*` headers only give the logs the client's
address. To trust them, set `PORTAL_FORWARDED_ALLOW_IPS` to the address the proxy
connects from *as the container sees it*: with compose that is the Docker network's
gateway (`docker network inspect <project>_default`), not `127.0.0.1`.

Let the proxy pass request bodies as large as the portal's upload limit (10 MiB by
default; nginx's own default, `client_max_body_size 1m`, is too small), and keep it
buffering them (nginx's default), so slow hosts are dealt with by the proxy.

## Signing in

The portal has no accounts of its own: an OpenID Connect provider is required. With
**Keycloak**:

1. Create a client `pydebuprep-portal` (OpenID Connect, client authentication on,
   standard flow). Valid redirect URI: `https://portal.example.com/auth/callback`;
   valid post-logout redirect URI: `https://portal.example.com/`.
2. Add a **Group Membership** mapper to the client (token claim name `groups`; full
   group path on or off, both work) with **Add to ID token** on. The portal reads the
   groups from the ID token at every sign-in; without the mapper nobody gets a role.
3. Create the groups and add people:
   - `pydebuprep-admins` — admins: everything;
   - `pydebuprep-auditors` — auditors: read everything, change nothing;
   - `pydebuprep-team-<team>` — members of a team, for example `pydebuprep-team-web`;
   - `pydebuprep-team-<team>-lead` — the team's leads.
4. Put the issuer (`https://KEYCLOAK/realms/REALM`), the client id and its secret in
   `.env`.

Losing a group in the provider removes the role at the next sign-in. Another provider
works the same way, provided it puts the groups in a claim of the ID token
(`PORTAL_OIDC_GROUPS_CLAIM`); the group names' prefix is `PORTAL_GROUP_PREFIX`.

## Teams and roles

A team appears the first time someone signs in with its group (admins can also create
one ahead of time, on the **Teams** page). Team names are lower-case letters, digits and
dashes, and can't end in `-lead`. A person may be in several teams, with different roles
in each.

Each team is **private** (its members, admins and auditors read its reports) or
**public** (everyone signed in reads them). New teams are private; admins and the
team's leads switch it.

| | Admin | Team lead | Team member | Auditor |
|---|---|---|---|---|
| Read own team's reports | ✓ | ✓ | ✓ | ✓ |
| Read other teams' public reports | ✓ | ✓ | ✓ | ✓ |
| Read other teams' private reports | ✓ | – | – | ✓ |
| Read the unassigned queue | ✓ | host names only | – | ✓ |
| Map hosts from the queue | any team | own team, unless only admins map hosts | – | – |
| Create/revoke tokens | global, team and host | team and host, own team | – | – |
| Set team private/public | ✓ | own team | – | – |
| Delete reports | ✓ | own team | – | – |
| Retention, global settings | ✓ | – | – | – |
| Audit log | read | – | – | read |

## Which team a report belongs to

A host belongs to at most one team, and its reports go to that team whatever token sent
them. A report sent with another team's token is stored with the host's team and
flagged; admins find those with the "Sent with another team's token" filter.

A host that isn't in a team yet:

- with a **team token** or a **host token**, joins that token's team at its first upload;
- with a **global token**, waits in the **unassigned** queue, where admins map it to any
  team and team leads into their own. Mapping a host moves its earlier reports too.
  The setting **only admins map hosts** leaves mapping to admins.

Admins can move a host to another team (with all its reports) or send it back to the
queue, from the host's page.

## Uploading reports

A host needs an upload token, of one of three kinds:

- a **global token** (admins, on the **Tokens** page): any host; new hosts wait in the
  unassigned queue;
- a **team token** (admins and the team's leads, on the team's page): any host, which
  joins the team;
- a **host token** (the same people): only the one host name it was made for; reports
  of any other host are refused.

Before anyone has signed in, create one on the command line:

```sh
docker compose exec portal python -m pydebuprep_portal.manage create-token "web servers"
docker compose exec portal python -m pydebuprep_portal.manage create-token web --team web
docker compose exec portal python -m pydebuprep_portal.manage create-token web1 --team web --host web1
```

The token is shown once. A token can upload reports and nothing else: it can't read
them. Store it on each host so only root can read it:

```sh
sudo install -d -m 0700 /etc/pydebuprep
sudo install -m 0600 /dev/stdin /etc/pydebuprep/token   # paste the token, then Ctrl-D
```

and add the portal to the host's `/etc/pydebuprep.conf`:

```yaml
upload:
  url: https://portal.example.com
```

Each `pydebuprep report` now uploads its report. A report that couldn't be sent is
kept on the host and sent later with `sudo pydebuprep upload`; sending one twice is
harmless. See [Using a portal](https://pydebuprep.app/guide/portal/) for the details.

## Configs

A team's members and leads (and admins) build pydebuprep configs on the **Configs**
page. The form is built from pydebuprep's own checks and options, so it offers only what
the CLI knows, and every save is checked by pydebuprep's own rules for a config fetched
from a URL. Every save is a new **version**; hosts get the newest, and earlier versions
stay viewable. Auditors read every team's configs.

A host runs with a config through its URL and the token it uploads with:

```sh
sudo pydebuprep --config https://portal.example.com/api/v1/configs/web/default
```

A global token fetches any team's configs; a team or host token, its own team's. The
portal fills in its own upload URL, and an unchanged config isn't downloaded again
(ETag). pydebuprep keeps a copy, and runs with it when the portal can't be reached,
saying so in the report.

A served config only steers pydebuprep's read-only checks: it never names a file to
write or read, a directory to walk or a URL to probe (`report.output_dir`,
`report.snapshot_dir`, the `upload` section, `credentials_file`, `search_paths`,
`http_probes`), and its baseline is `auto` or `none`.

Before anyone has signed in, create a config with pydebuprep's defaults on the command
line; it prints the URL:

```sh
docker compose exec portal python -m pydebuprep_portal.manage create-config web default
```

## Retention

Reports are kept 365 days by default; admins change that on the **Settings** page, or
choose to keep them forever. A team or a host can keep its reports longer or shorter, or
keep forever: a host's setting beats its team's, which beats the global one. The newest
report of every host is always kept, so a host that stopped reporting doesn't vanish.
The portal deletes expired reports once a day (`manage expire` does it at once).

The largest upload accepted is also on the **Settings** page (10 MiB by default).

## The audit log

Every change to tokens, mappings, team visibility, retention and settings, and every
deleted report, is in the **audit log** with who and when. Admins and auditors read it.

## Hosts and search

- **Hosts** lists every host with its newest report. Filter by the release or kernel of
  that report (who is still on bookworm?), by no report in a number of days, or by a
  newest report worse than the one before; the list counts hosts per release.
- **Machines**: pydebuprep 0.8.0 and newer send a machine ID (a hash of
  `/etc/machine-id`, never the ID itself). A host name that more than one machine
  reports is flagged ("2 machines"), and the host's page shows which report came from
  which machine.
- **Search** finds findings by host, check, severity, words in the title, detail or
  evidence, and the dates of their reports.
- **CSV**: the reports, hosts and search pages download what they list (up to 10,000
  rows). Cells that a spreadsheet would run as a formula are escaped.

## Upgrading

Each release lists in `CHANGELOG.md` what to know before upgrading. Upgrade the portal
before its hosts' pydebuprep: a portal validates reports against the pydebuprep it is
built on, and refuses fields a newer pydebuprep adds (portal 0.2.0 and older refuse the
machine ID of pydebuprep 0.8.0). A newer portal takes reports from older pydebuprep.
The portal migrates its database when it starts.

### From PostgreSQL 17 (portals before 0.4.0)

`compose.yaml` runs PostgreSQL 18, with its data in a new volume (`postgres18`);
portals from before 0.4.0 ran PostgreSQL 17 in the volume `db`. A major version can't
open the other's files, so move the database over once, where you run
`docker compose`, after putting the new `compose.yaml` in place:

```sh
scripts/upgrade-postgres-18.sh     # PROJECT=<name> if you use docker compose -p
```

It stops the portal, dumps the database with a temporary PostgreSQL 17, restores it into
PostgreSQL 18, compares the number of reports and the schema revision, and starts the
portal again. It never touches the old volume: once the portal works, remove it with
`docker volume rm <project>_db`. Run again, it says there is nothing to do; if the new
release was started first (an empty PostgreSQL 18 database), it says how to start over.

## The API

Everything is also available as an HTTP API under `/api/v1`, documented at `/docs`
(OpenAPI). Uploads use an upload token; everything else uses an OIDC access token from
the same provider, whose audience is `PORTAL_OIDC_AUDIENCE` (by default the client id),
with the same roles as signing in.

## Settings

All settings are environment variables; `.env.example` lists them with their
defaults. The required ones are `PORTAL_BASE_URL`, `PORTAL_SESSION_SECRET` (at least
32 random characters), `PORTAL_OIDC_ISSUER`, `PORTAL_OIDC_CLIENT_ID`,
`PORTAL_OIDC_CLIENT_SECRET` and `PORTAL_DATABASE_URL` (set by `compose.yaml`). A
secret may be read from a file instead: `PORTAL_SESSION_SECRET_FILE` and so on.

## Developing

```sh
make venv        # .venv with the locked dependencies
make db-up       # PostgreSQL 18 in Docker on 127.0.0.1:55432
make lint test   # ruff, mypy, bandit; pytest with 100 % coverage
make lock        # after changing requirements*.in
PYTHON=.venv/bin/python PYDEBUPREP=.venv/bin/pydebuprep scripts/smoke.sh
```

The smoke test builds the image, runs it with compose and uploads reports with the
pinned pydebuprep CLI: one it makes on the spot, with the machine's ID, and two of one
host name from different machines with nginx and Apache findings. It then checks inside
the container that the machine IDs, the clash and the findings' evidence were stored
and can be found, and that the portal answers. CI runs it on every pull request.

## License

MIT
