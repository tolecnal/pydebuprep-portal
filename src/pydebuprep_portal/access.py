"""Who is looking, and what they may read."""

from __future__ import annotations

from dataclasses import dataclass

ROLES = {"admins": "admin", "auditors": "auditor"}


@dataclass(frozen=True)
class Viewer:
    subject: str
    name: str
    roles: frozenset[str]

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
    for group in groups:
        if isinstance(group, str):
            name = group.rsplit("/", 1)[-1]
            if name.startswith(prefix) and name[len(prefix) :] in ROLES:
                roles.add(ROLES[name[len(prefix) :]])
    return frozenset(roles)
