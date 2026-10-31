# Changelog

## 0.1.0 (2026-10-31)

The first release: the whole build order of the portal's design.

- Hosts upload their pydebuprep reports over HTTPS with a token (global, team or host
  token); reports are checked by pydebuprep's own loader and its published JSON Schema,
  deduplicated, size-limited while reading and decompressing.
- Sign-in with OpenID Connect (Keycloak or any provider): admins, auditors, and team
  members and leads from the provider's groups. Teams are private or public.
- The newest reports first, every report in every format pydebuprep renders (its HTML in
  a sandboxed frame), host timelines, finding search.
- The unassigned queue and mapping hosts to teams; flags for reports sent with another
  team's token.
- Retention: a global TTL with team and host overrides, expired daily; a host's newest
  report is always kept.
- A config generator built from pydebuprep's own checks; every save a version; hosts run
  with `pydebuprep --config https://…/api/v1/configs/<team>/<name>`.
- The audit log; an HTTP API under `/api/v1`, documented at `/docs`.
- A Docker image on Debian 13 (`ghcr.io/tolecnal/pydebuprep-portal`), `compose.yaml`,
  and `make preview` for a local look with a ready-made Keycloak and demo data.
- Uses pydebuprep 0.7.0.
