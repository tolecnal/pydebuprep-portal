"""Who is looking, and what they may read."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROLES = {"admins": "admin", "auditors": "auditor"}
TEAM_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
LEAD_SUFFIX = "-lead"


@dataclass(frozen=True)
class Viewer:
    subject: str
    name: str
    roles: frozenset[str]
    teams: frozenset[str] = field(default_factory=frozenset)
    leads: frozenset[str] = field(default_factory=frozenset)  # a lead is also in teams

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def reads_all(self) -> bool:
        """Admins and auditors read every report, including the unassigned queue."""
        return bool(self.roles & {"admin", "auditor"})


def roles_from_groups(groups: object, prefix: str) -> frozenset[str]:
    """Roles from the provider's group claim; Keycloak's `/a/b/group` paths count by name."""
    if not isinstance(groups, list):
        return frozenset()
    roles = set()
    prefix = prefix.lower()
    for group in groups:
        if isinstance(group, str):
            name = group.rsplit("/", 1)[-1].lower()
            if name.startswith(prefix) and name[len(prefix) :] in ROLES:
                roles.add(ROLES[name[len(prefix) :]])
    return frozenset(roles)


def valid_team_name(name: str) -> bool:
    """Lower-case letters, digits and inner dashes, at most 63; never ending in -lead,
    which marks a team's lead group."""
    return TEAM_NAME.fullmatch(name) is not None and not name.endswith(LEAD_SUFFIX)


def teams_from_groups(groups: object, prefix: str) -> tuple[frozenset[str], frozenset[str]]:
    """(teams, leads) from `<prefix>team-<team>` and `<prefix>team-<team>-lead` groups."""
    if not isinstance(groups, list):
        return frozenset(), frozenset()
    start = f"{prefix}team-".lower()
    teams, leads = set(), set()
    for group in groups:
        if not isinstance(group, str):
            continue
        name = group.rsplit("/", 1)[-1].lower()
        if not name.startswith(start):
            continue
        team = name[len(start) :]
        lead = team.endswith(LEAD_SUFFIX)
        if lead:
            team = team[: -len(LEAD_SUFFIX)]
        if valid_team_name(team):
            teams.add(team)
            if lead:
                leads.add(team)
    return frozenset(teams), frozenset(leads)


# --- the spec's role table -----------------------------------------------------------
# `team` is a team's name, or None for the unassigned queue.


def may_read_team(viewer: Viewer, team: str | None, public: bool) -> bool:
    if viewer.reads_all:
        return True
    return team is not None and (team in viewer.teams or public)


def may_read_queue(viewer: Viewer) -> bool:
    return viewer.reads_all


def may_see_queue_hosts(viewer: Viewer) -> bool:
    """Leads see the queue's host names (to map them), never its reports."""
    return viewer.reads_all or bool(viewer.leads)


def may_map(viewer: Viewer, team: str, *, host_mapped: bool, only_admins_map: bool) -> bool:
    if viewer.is_admin:
        return True
    return team in viewer.leads and not host_mapped and not only_admins_map


def may_manage_tokens(viewer: Viewer, kind: str, team: str | None) -> bool:
    if viewer.is_admin:
        return True
    return kind in ("team", "host") and team is not None and team in viewer.leads


def may_read_tokens(viewer: Viewer, team: str | None) -> bool:
    return viewer.reads_all or (team is not None and team in viewer.leads)


def may_set_visibility(viewer: Viewer, team: str) -> bool:
    return viewer.is_admin or team in viewer.leads


def may_delete_reports(viewer: Viewer, team: str | None) -> bool:
    return viewer.is_admin or (team is not None and team in viewer.leads)


def may_set_retention(viewer: Viewer) -> bool:
    return viewer.is_admin


def may_change_settings(viewer: Viewer) -> bool:
    return viewer.is_admin


def may_read_audit(viewer: Viewer) -> bool:
    return viewer.reads_all


def may_read_configs(viewer: Viewer, team: str) -> bool:
    return viewer.reads_all or team in viewer.teams


def may_edit_configs(viewer: Viewer, team: str) -> bool:
    """Admins, and the team's members and leads (the spec gives members their team's)."""
    return viewer.is_admin or team in viewer.teams
