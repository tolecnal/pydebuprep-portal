"""The spec's role table, row by row: who may do what."""

from __future__ import annotations

from pydebuprep_portal import access
from pydebuprep_portal.access import Viewer

T = "web"
ADMIN = Viewer("a", "Admin", frozenset({"admin"}))
AUDITOR = Viewer("u", "Auditor", frozenset({"auditor"}))
LEAD = Viewer("l", "Lead", frozenset(), teams=frozenset({T}), leads=frozenset({T}))
MEMBER = Viewer("m", "Member", frozenset(), teams=frozenset({T}))
OTHER_LEAD = Viewer("o", "Other", frozenset(), teams=frozenset({"db"}), leads=frozenset({"db"}))
NOBODY = Viewer("n", "Nobody", frozenset())
EVERYONE = {
    "admin": ADMIN,
    "auditor": AUDITOR,
    "lead": LEAD,
    "member": MEMBER,
    "other-lead": OTHER_LEAD,
    "nobody": NOBODY,
}


def allowed(check: object) -> set[str]:
    return {name for name, viewer in EVERYONE.items() if check(viewer)}  # type: ignore[operator]


def test_read_own_team_reports() -> None:
    assert allowed(lambda v: access.may_read_team(v, T, False)) == {
        "admin",
        "auditor",
        "lead",
        "member",
    }


def test_read_other_teams_public_reports() -> None:
    assert allowed(lambda v: access.may_read_team(v, T, True)) == set(EVERYONE)


def test_read_other_teams_private_reports() -> None:
    assert allowed(lambda v: access.may_read_team(v, "db", False)) == {
        "admin",
        "auditor",
        "other-lead",
    }


def test_read_unassigned_queue() -> None:
    assert allowed(access.may_read_queue) == {"admin", "auditor"}
    assert allowed(lambda v: access.may_read_team(v, None, True)) == {"admin", "auditor"}
    assert allowed(access.may_see_queue_hosts) == {"admin", "auditor", "lead", "other-lead"}


def test_map_hosts() -> None:
    def may(mapped: bool, only_admins: bool) -> set[str]:
        return allowed(
            lambda v: access.may_map(v, T, host_mapped=mapped, only_admins_map=only_admins)
        )

    assert may(False, False) == {"admin", "lead"}
    assert may(True, False) == {"admin"}  # leads map only from the queue
    assert may(False, True) == {"admin"}


def test_create_and_revoke_tokens() -> None:
    assert allowed(lambda v: access.may_manage_tokens(v, "global", None)) == {"admin"}
    for kind in ("team", "host"):
        assert allowed(lambda v, k=kind: access.may_manage_tokens(v, k, T)) == {"admin", "lead"}
    assert allowed(lambda v: access.may_read_tokens(v, T)) == {"admin", "auditor", "lead"}
    assert allowed(lambda v: access.may_read_tokens(v, None)) == {"admin", "auditor"}


def test_set_visibility() -> None:
    assert allowed(lambda v: access.may_set_visibility(v, T)) == {"admin", "lead"}


def test_delete_reports() -> None:
    assert allowed(lambda v: access.may_delete_reports(v, T)) == {"admin", "lead"}
    assert allowed(lambda v: access.may_delete_reports(v, None)) == {"admin"}


def test_retention_settings_audit_log() -> None:
    assert allowed(access.may_set_retention) == {"admin"}
    assert allowed(access.may_change_settings) == {"admin"}
    assert allowed(access.may_read_audit) == {"admin", "auditor"}


def test_configs() -> None:
    assert allowed(lambda v: access.may_read_configs(v, T)) == {
        "admin",
        "auditor",
        "lead",
        "member",
    }
    assert allowed(lambda v: access.may_edit_configs(v, T)) == {"admin", "lead", "member"}
    assert allowed(lambda v: access.may_read_configs(v, "db")) == {"admin", "auditor", "other-lead"}
    assert allowed(lambda v: access.may_edit_configs(v, "db")) == {"admin", "other-lead"}
