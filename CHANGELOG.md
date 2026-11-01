# Changelog

## Unreleased

- The local preview's Keycloak is 26.8.1 (was 26.0.7).
- Dependabot keeps the GitHub Actions, the pinned Python dependencies, the image's
  base and the compose files' images current, a week after each release.
- The smoke test also checks what reports from pydebuprep 0.8.0 leave behind: the
  machine ID of a report made on the spot (against `systemd-id128`), a host name two
  machines report, and findings found by their evidence.
- README: hosts and search, the order to upgrade in, and a code block that swallowed
  the paragraph after it.

## 0.3.0 (2026-11-01)

- Built on pydebuprep 0.8.0. **Upgrade the portal before its hosts:** pydebuprep 0.8.0
  sends a machine ID, which a portal built on 0.7.0 (0.2.0 and older) refuses. This
  portal takes reports with and without one.
- Machine IDs: reports keep the machine ID pydebuprep 0.8.0 sends (a hash of
  `/etc/machine-id`, never the ID itself). A host name that more than one machine
  reports is flagged in the hosts list, and its page says so and shows each report's
  machine.
- Hosts by release, kernel and silence: filter the hosts list by the release or kernel
  of each host's newest report, or by no report in a number of days; the list counts
  hosts per release. The API's `/api/v1/hosts` takes `release`, `kernel` and
  `silent_days`, and returns each host's release, kernel and number of machines.
- Findings by more than their title: the search's words also match a finding's detail
  and evidence (existing findings are filled in from their reports when the database
  is upgraded), and `from`/`to` limit the reports by date (`since`/`until` in the API).
- CSV: the reports, hosts and search pages download what they list as CSV (every
  match, up to 10,000 rows). Cells that would start a spreadsheet formula are escaped.
- The demo data has a host name two machines report, a host left on bookworm, and
  findings with details.

## 0.2.0 (2026-11-01)

- A new look: the navigation moves to a sidebar on the left, grouped by what you do there,
  with the current page marked; on a phone it folds into a menu button. Severities show as
  coloured badges, a report's counts as tiles, and tables, forms and notices are cards.
  Light and dark follow the system setting.
- After the host and config forms, pages redirect to the names as stored, and the warning
  for a served config that no longer passes logs them, never what the request said
  (CodeQL; neither was reachable).
- `make preview` tags its build `:preview`, not as the released version.

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
