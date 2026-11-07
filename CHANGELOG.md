# Changelog

## 0.11.0 (2026-11-07)

- Built on pydebuprep 0.15.1, the release after 0.14.0: 0.15.0 was tagged but never published,
  as its package failed to build. **Upgrade the portal before the hosts:** a report compared with
  a snapshot taken by pydebuprep 0.15.1 carries a new field, which older portals refuse.
- Findings are labelled new, worse or pre-existing against what the snapshot found. The report
  page counts them and lists what's new or worse first; search results, the search API and
  the findings CSV show the label.
- The config generator offers pydebuprep 0.15.1's new checks (`upgrade-log`, `boot-config`,
  `package-config`, `storage-health`, `oom-events`) and options. A host needs 0.15.1 before
  a config it fetches can set `snapshot_findings` or those options; older versions reject them.

## 0.10.0 (2026-11-06)

- Security fixes; see the advisory on GitHub.
- Built on pydebuprep 0.14.0. Its reports are the same format, so hosts may upgrade before
  or after the portal.
- `generated_at` must fall between 2000 and 2100; a snapshot time outside that is shown as
  unknown.
- A report with a number too long to read, or half a character (a lone surrogate), is
  refused with a 422 instead of failing with a 500.
- Pages and downloads are no longer kept in browser caches.
- `compose.yaml` runs the portal with a read-only filesystem, no capabilities and
  `no-new-privileges`.
- **Upgrade note:** a role or team group now counts only at the top level of the provider's
  groups. Before, any path ending in the name did, so whoever could create a subgroup
  could make themselves an admin. If your groups live under one group, set
  `PORTAL_GROUP_PARENT=/org` before upgrading, or their members lose their roles.
- Sessions are kept on the server: signing out ends one for any copy of its cookie. They
  last one hour instead of eight, which also limits how long a lost role lasts. Everyone
  signs in again after the upgrade.
- The API refuses ID tokens from every provider; it only recognised Keycloak's.
- The machine ID's Copy button says when copying needs HTTPS, as the report Copy buttons do,
  rather than doing nothing.

## 0.9.0 (2026-11-05)

- Built on pydebuprep 0.13.0. Its reports are the same format as 0.12.0's, so hosts may
  upgrade before or after the portal. A host needs 0.13.0 before a config it fetches can
  set `format: confluence-legacy` or `confluence-cloud`; older versions reject those.
  `format: confluence` keeps working everywhere.
- Reports can be downloaded as `confluence-cloud`, Markdown for pasting into Confluence
  Cloud, or `confluence-legacy`, the wiki markup for on-prem Confluence.
- The report page shows a read-only TODO list, built from the report's findings, so
  every report has one, old ones included. Downloads can include it, and the API takes
  `?todo=true`.
- The report page's downloads are grouped into Jira, Confluence and Other, and the Jira and
  Confluence formats have a Copy button, so they can be pasted without saving a file.
  Copying needs HTTPS or localhost.
- The config generator no longer offers `jira` and `confluence`, the other names for the
  `-legacy` formats, unless a config already uses one.
- Markdown and HTML report downloads show the labels in the report header in bold.

## 0.8.0 (2026-11-05)

- Built on pydebuprep 0.12.0. Its reports are the same format as 0.11.0's, so hosts
  may upgrade before or after the portal. **Exception:** a host needs pydebuprep 0.12.0
  before a config it fetches can set `format: jira-legacy` or `jira-cloud`; older
  versions reject those. `format: jira` keeps working everywhere.
- Reports can be downloaded as `jira-cloud`, Markdown for pasting into Jira Cloud (whose
  editor shows wiki markup as plain text), or `jira-legacy`, the wiki markup for on-prem
  Jira, pasted with the comment in text mode. The report page links those two; the
  `jira` address and API format still work and give the `jira-legacy` file.
- Jira and Confluence downloads show the labels in the report header in bold.

## 0.7.0 (2026-11-05)

- Built on pydebuprep 0.11.0. Its reports are the same format as 0.10.0's, so hosts
  may upgrade before or after the portal.
- Jira, Confluence and HTML report downloads change with it: a check's status takes the
  colour of its worst finding, so a check with only warnings is no longer red in Jira and
  Confluence, and warnings are orange (yellow in Confluence, whose status macro has no
  orange). The portal's downloads have no TODO list, so pydebuprep's new TODO tables
  don't appear in them.
- Releases now run the lint and test jobs before building the image. CI no longer runs
  on merges to `main`; it runs on pull requests, weekly and by hand. Security and CodeQL
  skip merges that can't change what they check, and pip downloads are cached.

## 0.6.0 (2026-11-03)

- Built on pydebuprep 0.10.0. Its reports are the same format as 0.9.0's, so hosts
  may upgrade before or after the portal. Its new `pydebuprep-cleanup` command is
  configured only on each host (`cleanup:` can't be set in a portal's config).
- **UI and ergonomics**:
  - A light/dark theme toggle in the sidebar header (`localStorage` + `data-theme`) and
    one-click `Copy` buttons on newly created tokens, config CLI commands, rendered YAML
    configs, and report machine IDs.
  - Fleet summary tiles at the top of `/hosts` (total hosts, critical, warning, info, OK,
    and worse than before), plus `customized` badges on checks with non-default options in
    the config generator and status badges for token state and team visibility.
  - Refined card elevation, glowing severity indicator dots, gradient-tinted stat cards,
    and dimmed zero-count cells in report and host timeline tables so non-zero findings
    stand out immediately.
- **Security hardening**:
  - Host names in the unassigned queue HTMX targets are URL-encoded, and post-sign-in
    redirect paths reject control characters (`\r`, `\n`, `\t`, NUL).
  - Host names `.` and `..`, and names with `/` or `\`, are refused on upload; NUL
    characters are looked for in the report exactly as sent.
  - Every request to the identity provider (metadata, tokens, JWKS) times out after 10
    seconds, and sign-in checks the ID token's issuer against `PORTAL_OIDC_ISSUER`.
  - Spreadsheet formula escaping in CSV exports also covers leading spaces before `=`,
    `+`, `-`, `@`, and a leading newline.
  - Search takes at most 20 distinct words and 200 characters, on the page and in the API.
  - A portal whose `PORTAL_BASE_URL` is https sends HSTS
    (`Strict-Transport-Security: max-age=31536000`). Session cookies were already
    `https_only` unless `PORTAL_ALLOW_HTTP` is set.
- **Performance**: report list and host timeline queries defer the full JSON `Report.body`
  column, `worse_only` host filtering is pushed down into SQL, and database connection
  pooling is tuned explicitly.

## 0.5.0 (2026-11-02)

- Built on pydebuprep 0.9.0: the config generator offers its new checks (php,
  certbot) and options (tls_probe, config_test, cert_warning_days,
  cert_critical_days), and search can filter by them. Its reports are the same
  format as 0.8.0's, so hosts may upgrade before or after the portal.
- **Snapshots and upgrades**: the portal keeps what each report was compared with
  (the snapshot's time, release and host) and which findings are changes since it.
  - A report's page says what it was compared with ("an upgrade from 22.04 to
    24.04"), counts and lists the changes since the snapshot apart from the problems
    found now, and warns about a snapshot taken on the same release or another host.
  - A host's page has a Snapshot column, so a multi-step upgrade reads as a chain.
  - Search can keep only changes since the snapshot (`changes` on the page,
    `changes_only` in the API); the search CSV has a `since_snapshot` column.
  - The API's reports have `baseline`, `baseline_at`, `baseline_os_version`,
    `baseline_host`, `changes` and `comparison`, and their `os_version` and
    `machine_id`; findings have `since_baseline`.
- The database migrates when the portal starts (`0005`): reports already stored get
  their snapshot and changes from their own JSON.

## 0.4.0 (2026-11-01)

- **PostgreSQL 18** (was 17), in a new volume `postgres18`. Before starting this
  release, move the database over with `scripts/upgrade-postgres-18.sh` (see
  "Upgrading" in the README); the old volume `db` is left as it was. Started without
  it, the portal comes up with an empty database, and the script says how to recover.
  Tests, CI and `make db-up` use PostgreSQL 18 too.
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
