"""Roles come from the OIDC provider's groups."""

from __future__ import annotations

import pytest

from pydebuprep_portal.access import Viewer, roles_from_groups, teams_from_groups, valid_team_name


@pytest.mark.parametrize(
    ("groups", "roles"),
    [
        (["pydebuprep-admins"], {"admin"}),
        (["/org/pydebuprep-auditors"], {"auditor"}),
        (["pydebuprep-admins", "pydebuprep-auditors"], {"admin", "auditor"}),
        (["pydebuprep-admins-x", "admins", "pydebuprep-team-web"], set()),
        ("pydebuprep-admins", set()),
        ([1, None], set()),
        (None, set()),
    ],
)
def test_roles_from_groups(groups: object, roles: set[str]) -> None:
    assert roles_from_groups(groups, "pydebuprep-") == roles


def test_custom_prefix() -> None:
    assert roles_from_groups(["pdp-admins", "pydebuprep-admins"], "pdp-") == {"admin"}


def test_viewer_properties() -> None:
    assert Viewer("s", "n", frozenset({"admin"})).is_admin
    assert Viewer("s", "n", frozenset({"admin"})).reads_all
    assert Viewer("s", "n", frozenset({"auditor"})).reads_all
    assert not Viewer("s", "n", frozenset({"auditor"})).is_admin
    assert not Viewer("s", "n", frozenset()).reads_all


@pytest.mark.parametrize(
    ("groups", "teams", "leads"),
    [
        (["pydebuprep-team-web"], {"web"}, set()),
        (["pydebuprep-team-web-lead"], {"web"}, {"web"}),
        (["/org/pydebuprep-team-DB"], {"db"}, set()),
        (
            [
                "pydebuprep-team-",
                "pydebuprep-team--x",
                "pydebuprep-team-a_b",
                "pydebuprep-team-" + "a" * 64,
                "pydebuprep-team-x-",
                "pydebuprep-team-x-lead-lead",
            ],
            set(),
            set(),
        ),
        (["pydebuprep-team-web", "pydebuprep-team-db-lead"], {"web", "db"}, {"db"}),
        (["pydebuprep-team-" + "a" * 63], {"a" * 63}, set()),
        (["pydebuprep-admins", "other-team-web", 7], set(), set()),
        ("pydebuprep-team-web", set(), set()),
    ],
)
def test_teams_from_groups(groups: object, teams: set[str], leads: set[str]) -> None:
    assert teams_from_groups(groups, "pydebuprep-") == (teams, leads)


def test_teams_with_a_custom_prefix() -> None:
    assert teams_from_groups(["pdp-team-web-lead"], "pdp-") == ({"web"}, {"web"})


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("web", True),
        ("web-1", True),
        ("a", True),
        ("Web", False),
        ("web-lead", False),
        ("-web", False),
        ("web-", False),
        ("we_b", False),
        ("", False),
        ("a" * 64, False),
    ],
)
def test_valid_team_name(name: str, valid: bool) -> None:
    assert valid_team_name(name) is valid
