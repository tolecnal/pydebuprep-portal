"""The config generator: a form built from pydebuprep's own check registry, and the
rules a config served to hosts must follow."""

from __future__ import annotations

from typing import Any

import pytest
from pydebuprep import checks, remote_config
from pydebuprep.remote_config import parse_remote

from pydebuprep_portal.generator import (
    LOGS_FIELD,
    REPORT_FIELDS,
    SERVED_FORBIDDEN_OPTIONS,
    ConfigValuesError,
    check_forms,
    form_values,
    render_config,
    validate_values,
    values_from_form,
)

UPLOAD = "https://portal.test"
SERVED = f"{UPLOAD}/api/v1/configs/web/default"


def all_fields() -> list[Any]:
    return [*REPORT_FIELDS, LOGS_FIELD, *(f for c in check_forms() for f in c.fields)]


def defaults_form() -> dict[str, Any]:
    form: dict[str, Any] = {"_fields": [f.key for f in all_fields()]}
    for field in all_fields():
        if field.kind == "bool":
            if field.default:
                form[field.key] = "1"
        elif field.kind == "list":
            form[field.key] = "\n".join(field.default)
        else:
            form[field.key] = str(field.default)
    return form


def test_every_check_and_option_is_offered() -> None:
    forms = {c.id: c for c in check_forms()}
    assert set(forms) == {cls.id for cls in checks.ALL_CHECKS}
    kinds = {bool: "bool", int: "int", float: "float", str: "str", list: "list"}
    for cls in checks.ALL_CHECKS:
        fields = {f.key: f for f in forms[cls.id].fields}
        assert fields[f"checks.{cls.id}.enabled"].kind == "bool"
        for option in cls.options:
            key = f"checks.{cls.id}.{option.name}"
            if option.name in SERVED_FORBIDDEN_OPTIONS:
                assert key not in fields
            else:
                assert fields[key].kind == kinds[type(option.default)], key
                assert fields[key].help == option.help


def test_forbidden_options_are_never_offered() -> None:
    pinned = remote_config.CHECK_FORBIDDEN
    assert {"credentials_file", "search_paths", "http_probes"} <= SERVED_FORBIDDEN_OPTIONS
    assert set([pinned] if isinstance(pinned, str) else pinned) <= SERVED_FORBIDDEN_OPTIONS
    for field in all_fields():
        assert field.key.rsplit(".", 1)[-1] not in SERVED_FORBIDDEN_OPTIONS | {
            "output_dir",
            "snapshot_dir",
        }


def test_report_fields_and_choices() -> None:
    fields = {f.key: f for f in REPORT_FIELDS}
    assert set(fields) == {
        "report.format",
        "report.min_severity",
        "report.todo",
        "report.color",
        "report.baseline",
    }
    assert fields["report.baseline"].choices == ("auto", "none")
    assert fields["report.min_severity"].choices == ("info", "warning", "critical")
    assert "json" in fields["report.format"].choices


def test_defaults_give_an_empty_config() -> None:
    assert values_from_form(defaults_form()) == {}


def test_form_round_trip() -> None:
    form = defaults_form()
    form["report.min_severity"] = "warning"
    form.pop("checks.debsums.enabled")
    form["checks.disk-space.warning_pct"] = "85"
    values = values_from_form(form)
    assert values == {
        "report": {"min_severity": "warning"},
        "checks": {"disk-space": {"warning_pct": 85}, "debsums": {"enabled": False}},
    }
    shown = form_values(values)
    assert shown["report.min_severity"] == "warning"
    assert shown["checks.debsums.enabled"] is False
    assert shown["checks.disk-space.warning_pct"] == 85
    assert shown["report.format"] == "text"


def test_bad_form_values_are_named() -> None:
    form = defaults_form()
    form["checks.disk-space.warning_pct"] = "abc"
    form["report.format"] = "pdf"
    with pytest.raises(ConfigValuesError) as exc:
        values_from_form(form)
    text = " ".join(exc.value.errors)
    assert "disk-space warning_pct" in text and "Format" in text


def test_list_from_lines() -> None:
    form = defaults_form()
    form["logs.ignore"] = "a\n\n b \n"
    assert values_from_form(form) == {"logs": {"ignore": ["a", " b "]}}


def test_fields_not_rendered_are_left_alone() -> None:
    assert values_from_form({"_fields": ["report.todo"], "report.todo": "1"}) == {
        "report": {"todo": True}
    }


def test_rendered_config_is_what_the_cli_accepts() -> None:
    values = {"report": {"min_severity": "warning", "todo": True}, "logs": {"ignore": ["^noise"]},
              "checks": {"debsums": {"enabled": False}, "disk-space": {"warning_pct": 85}}}  # fmt: skip
    text = render_config(
        values, upload_url=UPLOAD, header="pydebuprep config web/default, version 3"
    )
    assert text.startswith("# pydebuprep config web/default, version 3\n")
    config = parse_remote(text, SERVED)
    assert config.upload.url == UPLOAD
    assert (config.report.min_severity, config.report.todo, config.log_ignore) == (
        "warning",
        True,
        ["^noise"],
    )
    assert "debsums" in config.disabled
    assert config.check_settings["disk-space"] == {"warning_pct": 85}


@pytest.mark.parametrize(
    "values",
    [
        {"upload": {"url": "https://evil.example"}},
        {"report": {"output_dir": "/etc"}},
        {"report": {"snapshot_dir": "/etc"}},
        {"report": {"baseline": "/etc/shadow"}},
        {"checks": {"postgresql": {"credentials_file": "/root/.pgpass"}}},
        {"checks": {"python-venvs": {"search_paths": ["/"]}}},
        {"checks": {"docker": {"http_probes": ["x=http://127.0.0.1/"]}}},
        {"checks": {"nope": {}}},
        {"checks": {"disk-space": {"nope": 1}}},
        {"checks": {"disk-space": "off"}},
        {"report": "x"},
        {"surprise": 1},
        [],
        "x",
    ],
)
def test_unsafe_or_unknown_values_are_refused(values: Any) -> None:
    with pytest.raises(ConfigValuesError):
        validate_values(values, SERVED, UPLOAD)


def test_bad_regex_in_logs_ignore() -> None:
    with pytest.raises(ConfigValuesError) as exc:
        validate_values({"logs": {"ignore": ["("]}}, SERVED, UPLOAD)
    assert "invalid pattern" in " ".join(exc.value.errors)


def test_too_big_is_refused() -> None:
    patterns = [f"^{i:07d}-" + "x" * 100 for i in range(10000)]
    with pytest.raises(ConfigValuesError) as exc:
        validate_values({"logs": {"ignore": patterns}}, SERVED, UPLOAD)
    assert "1 MiB" in " ".join(exc.value.errors)


def test_validate_normalises_defaults_away() -> None:
    assert validate_values({"report": {"format": "text"}}, SERVED, UPLOAD) == {}
    assert validate_values({"checks": {"disk-space": {"enabled": True, "warning_pct": "85"}}},
                           SERVED, UPLOAD) == {"checks": {"disk-space": {"warning_pct": 85}}}  # fmt: skip


def test_errors_read_without_the_url() -> None:
    with pytest.raises(ConfigValuesError) as exc:
        validate_values({"checks": {"nope": {}}}, SERVED, UPLOAD)
    assert exc.value.errors == ["checks: unknown check: nope"]


def test_string_options_are_text_fields() -> None:
    from pydebuprep_portal.generator import _kind

    assert (_kind("x"), _kind(True), _kind(3), _kind(1.5), _kind([])) == (
        "str",
        "bool",
        "int",
        "float",
        "list",
    )


def test_unknown_form_keys_are_ignored() -> None:
    assert values_from_form({"_fields": ["nope", "checks.nope.x"], "nope": "1"}) == {}


def test_validate_keeps_every_kind_of_change() -> None:
    values = {"report": {"todo": True}, "logs": {"ignore": ["^x"]},
              "checks": {"debsums": {"enabled": False}}}  # fmt: skip
    assert validate_values(values, SERVED, UPLOAD) == values
    default = next(o for c in checks.ALL_CHECKS if c.id == "disk-space" for o in c.options).default
    first = next(o for c in checks.ALL_CHECKS if c.id == "disk-space" for o in c.options).name
    assert validate_values({"checks": {"disk-space": {first: default}}}, SERVED, UPLOAD) == {}


@pytest.mark.parametrize(
    "values",
    [
        {"logs": {"ignore": ["a\x00b"]}},
        {"checks": {"disk-space": {"exclude_mounts": ["x\x00"]}}},
        {"logs": {"ignore": ["(" * 5000 + ")" * 5000]}},
    ],
)
def test_nul_and_deeply_nested_patterns_are_refused(values: Any) -> None:
    with pytest.raises(ConfigValuesError):
        validate_values(values, SERVED, UPLOAD)


def test_list_items_are_kept_as_typed() -> None:
    form = defaults_form()
    form["logs.ignore"] = " leading\r\ntrailing \r\n\r\n  \r\nx"
    assert values_from_form(form) == {"logs": {"ignore": [" leading", "trailing ", "x"]}}
