"""The audit log: who changed what, and when."""

from __future__ import annotations

from sqlalchemy.orm import Session

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.audit import actor_of, list_audit, record


def test_record_joins_the_callers_transaction(db: Session) -> None:
    record(db, "Ada <user-1>", "team.create", "web", public=False)
    db.rollback()
    assert list_audit(db) == []
    record(db, "Ada <user-1>", "team.create", "web", public=False)
    db.commit()
    (entry,) = list_audit(db)
    assert (entry.actor, entry.action, entry.target, entry.details) == (
        "Ada <user-1>", "team.create", "web", {"public": False},
    )  # fmt: skip
    assert entry.at is not None


def test_list_audit_newest_first_and_paged(db: Session) -> None:
    for i in range(5):
        record(db, "a", "x", f"t{i}")
    db.commit()
    first = list_audit(db, limit=3)
    assert [e.target for e in first] == ["t4", "t3", "t2"]
    assert [e.target for e in list_audit(db, before=first[-1].id)] == ["t1", "t0"]


def test_actor_of() -> None:
    assert actor_of(Viewer("user-1", "Ada", frozenset())) == "Ada <user-1>"
