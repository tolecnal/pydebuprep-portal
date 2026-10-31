# pydebuprep-portal

A portal that keeps [pydebuprep](https://pydebuprep.app/) reports in one place. Hosts
upload their reports with a token; people sign in with OpenID Connect and see the
newest reports first, open any report in every format pydebuprep renders, follow a
host's timeline and search findings.

pydebuprep works without the portal. A host uses it only when its config says so.

> **Status: step 2 of the build order.** Uploads, sign-in, reports, hosts, search and
> global upload tokens work. Teams, team and host tokens, the unassigned-queue mapping,
> retention and the audit log come next; serving configs to hosts
> (`pydebuprep --config https://…`) comes after that. Until teams exist, reports are
> visible to admins and auditors only.

## Running it

The portal runs as a Docker image (Debian 13, `debian:trixie-slim`) next to PostgreSQL,
behind your own reverse proxy, which terminates TLS:

```sh
cp .env.example .env      # then fill it in: see "Settings"
docker compose up -d
```

`docker compose` builds the image, starts PostgreSQL, brings the database schema up to
date and serves the portal on `127.0.0.1:8000`. Point the reverse proxy at it, and set
`PORTAL_BASE_URL` to the URL people and hosts use, for example
`https://portal.example.com`. The proxy must pass `X-Forwarded-Proto` and
`X-Forwarded-For`; `PORTAL_FORWARDED_ALLOW_IPS` lists the proxies to trust for them.

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
   - `pydebuprep-admins` — admins: read every report, manage upload tokens;
   - `pydebuprep-auditors` — auditors: read everything, change nothing.
4. Put the issuer (`https://KEYCLOAK/realms/REALM`), the client id and its secret in
   `.env`.

Losing a group in the provider removes the role at the next sign-in. Another provider
works the same way, provided it puts the groups in a claim of the ID token
(`PORTAL_OIDC_GROUPS_CLAIM`); the group names' prefix is `PORTAL_GROUP_PREFIX`.

## Uploading reports

A host needs an upload token. Admins create them on the **Tokens** page, or, before
anyone has signed in, on the command line:

```sh
docker compose exec portal python -m pydebuprep_portal.manage create-token "web servers"
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
make db-up       # PostgreSQL 17 in Docker on 127.0.0.1:55432
make lint test   # ruff, mypy, bandit; pytest with 100 % coverage
make lock        # after changing requirements*.in
scripts/smoke.sh # build the image, run it with compose, upload with the pydebuprep CLI
```

## License

MIT
