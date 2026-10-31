"""The config generator: a form built from pydebuprep's own check registry and option
definitions, and the rules a config the portal serves to hosts must follow.

A config is kept as its values: only what differs from pydebuprep's defaults, shaped like
the config file (report, logs, checks). The YAML a host gets is rendered from them with
the portal's own upload URL, and checked by pydebuprep's own parse_remote."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

import yaml
from pydebuprep import checks, remote_config
from pydebuprep.checks.base import Option
from pydebuprep.config import LOGS_HELP, REPORT_HELP, SEVERITIES, ConfigError, ReportSettings
from pydebuprep.model import CATEGORY_ORDER
from pydebuprep.portal import MAX_CONFIG_BYTES
from pydebuprep.remote_config import parse_remote
from pydebuprep.render import FORMATS

Kind = Literal["bool", "int", "float", "str", "list", "choice"]


def _pinned_forbidden() -> frozenset[str]:
    # A string in pydebuprep 0.5.0, a tuple since.
    pinned: str | tuple[str, ...] = remote_config.CHECK_FORBIDDEN
    return frozenset([pinned] if isinstance(pinned, str) else pinned)


# Check options a served config never sets: files to open, roots to walk, URLs to probe.
# The newest pydebuprep's list, whatever the pinned one says.
SERVED_FORBIDDEN_OPTIONS = (
    frozenset({"credentials_file", "search_paths", "http_probes"}) | _pinned_forbidden()
)
TOP_LEVEL = ("report", "logs", "checks")
BASELINES = ("auto", "none")
# Room for the header comment the portal adds when it serves a config.
MAX_VALUES_BYTES = MAX_CONFIG_BYTES - 1024


class ConfigValuesError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Field:
    key: str  # the form field's name: report.format, logs.ignore, checks.<id>.<option>
    label: str
    help: str
    kind: Kind
    default: Any
    choices: tuple[str, ...] = ()
    option: Option | None = field(default=None, compare=False)


@dataclass(frozen=True)
class CheckForm:
    id: str
    title: str
    category: str
    notes: str
    fields: tuple[Field, ...]


def _report_field(key: str, label: str, kind: Kind, choices: tuple[str, ...] = ()) -> Field:
    default = getattr(ReportSettings(), key)
    return Field(f"report.{key}", label, REPORT_HELP[key], kind, default, choices)


REPORT_FIELDS = (
    _report_field("format", "Format", "choice", tuple(FORMATS)),
    _report_field("min_severity", "Lowest severity shown", "choice", tuple(SEVERITIES)),
    _report_field("todo", "TODO checklist", "bool"),
    _report_field("color", "Color", "bool"),
    Field(
        "report.baseline",
        "Baseline",
        "Snapshot to compare against: auto (the newest snapshot) or none",
        "choice",
        ReportSettings().baseline,
        BASELINES,
    ),
)
LOGS_FIELD = Field("logs.ignore", "Ignore log messages", LOGS_HELP, "list", [])
EDITABLE_REPORT = tuple(f.key.split(".", 1)[1] for f in REPORT_FIELDS)


def _kind(default: Any) -> Kind:
    for kind, cls in (("bool", bool), ("int", int), ("float", float), ("list", list)):
        if isinstance(default, cls):
            return kind  # type: ignore[return-value]  # one of the literals above
    return "str"


@cache
def check_forms() -> tuple[CheckForm, ...]:
    """Every check pydebuprep knows, ordered like its example config, with its options."""
    order = {category: i for i, category in enumerate(CATEGORY_ORDER)}
    forms = []
    for cls in sorted(checks.ALL_CHECKS, key=lambda c: (order.get(c.category, 99), c.id)):
        notes = [cls.category]
        if cls.requires_root:
            notes.append("needs root")
        if cls.requires_commands:
            notes.append("uses " + ", ".join(cls.requires_commands))
        fields = [Field(f"checks.{cls.id}.enabled", "enabled", "Run this check", "bool", True)]
        fields += [
            Field(
                f"checks.{cls.id}.{o.name}",
                f"{cls.id} {o.name}",
                o.help,
                _kind(o.default),
                o.default,
                option=o,
            )
            for o in cls.options
            if o.name not in SERVED_FORBIDDEN_OPTIONS
        ]
        forms.append(CheckForm(cls.id, cls.title, cls.category, "; ".join(notes), tuple(fields)))
    return tuple(forms)


@cache
def _fields() -> dict[str, Field]:
    every = [*REPORT_FIELDS, LOGS_FIELD, *(f for form in check_forms() for f in form.fields)]
    return {f.key: f for f in every}


def generator_field(key: str) -> Field | None:
    return _fields().get(key)


def _get(values: Mapping[str, Any], key: str) -> Any:
    node: Any = values
    for part in key.split(".", 2) if key.startswith("checks.") else key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _set(values: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".", 2) if key.startswith("checks.") else key.split(".")
    node = values
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _from_form(f: Field, raw: str | None) -> Any:
    if f.kind == "bool":
        return raw is not None and raw != ""
    text = raw or ""
    if f.kind == "choice":
        if text not in f.choices:
            raise ValueError(f"expected one of {', '.join(f.choices)}")
        return text
    if f.kind == "list":
        return [line.strip() for line in text.splitlines() if line.strip()]
    if f.option is None:  # pragma: no cover - only report and logs fields have none
        return text
    return f.option.coerce(text)


def values_from_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """The values a submitted form sets, leaving out pydebuprep's defaults. Only the fields
    named in `_fields` (the ones the page rendered) are read: an unchecked checkbox sends
    nothing, so it counts as false only where it was on the page."""
    names = form.get("_fields")
    keys = [names] if isinstance(names, str) else list(names or _fields())
    values: dict[str, Any] = {}
    errors = []
    for key in keys:
        f = _fields().get(key)
        if f is None:
            continue
        raw = form.get(key)
        try:
            value = _from_form(f, raw if isinstance(raw, str) or raw is None else str(raw))
        except ValueError as exc:
            errors.append(f"{f.label}: {exc}")
            continue
        if value != f.default:
            _set(values, key, value)
    if errors:
        raise ConfigValuesError(errors)
    return values


def form_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """The value to show in every form field: the config's, or the default."""
    shown = {}
    for key, f in _fields().items():
        value = _get(values, key)
        shown[key] = f.default if value is None else value
    return shown


def render_config(values: Mapping[str, Any], *, upload_url: str, header: str) -> str:
    """The YAML a host gets: the values, with the portal's upload URL."""
    document = {k: values[k] for k in TOP_LEVEL if k in values} | {"upload": {"url": upload_url}}
    comment = "".join(f"# {line}\n" for line in header.splitlines())
    body = yaml.safe_dump(document, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return comment + body


def _shape_errors(values: Mapping[str, Any]) -> list[str]:
    """What a served config may not contain, before pydebuprep reads it."""
    errors = []
    for key in values:
        if key == "upload":
            errors.append("upload: filled in by the portal")
        elif key not in TOP_LEVEL:
            errors.append(f"unknown top-level key {key!r}")
    report = values.get("report")
    if isinstance(report, Mapping):
        errors += [
            f"report.{key}: can't be set in a config served by the portal"
            for key in report
            if key not in EDITABLE_REPORT
        ]
    sections = values.get("checks")
    if isinstance(sections, Mapping):
        errors += [
            f"checks.{check_id}.{name}: can't be set in a config served by the portal"
            for check_id, options in sections.items()
            if isinstance(options, Mapping)
            for name in options
            if name in SERVED_FORBIDDEN_OPTIONS
        ]
    return errors


def _nul_errors(values: object, path: str = "") -> list[str]:
    """NUL characters: PostgreSQL can't store them, and no setting needs one."""
    if isinstance(values, str):
        return [f"{path or 'value'}: contains a NUL character"] if "\x00" in values else []
    if isinstance(values, Mapping):
        return [e for k, v in values.items() for e in _nul_errors(v, f"{path}.{k}".lstrip("."))]
    if isinstance(values, list):
        return [e for v in values for e in _nul_errors(v, path)]
    return []


def validate_values(values: object, served_url: str, upload_url: str) -> dict[str, Any]:
    """Check VALUES as a config served from SERVED_URL; the values without defaults.
    ConfigValuesError lists every problem, in pydebuprep's words where it found them."""
    if not isinstance(values, Mapping):
        raise ConfigValuesError(["a config is a mapping with report, logs and checks"])
    errors = _shape_errors(values) + _nul_errors(values)
    if errors:
        raise ConfigValuesError(errors)
    text = render_config(values, upload_url=upload_url, header="")
    if len(text.encode()) > MAX_VALUES_BYTES:
        raise ConfigValuesError(["the config is larger than hosts accept (1 MiB)"])
    try:
        config = parse_remote(text, served_url)
    except ConfigError as exc:
        prefix = f"{served_url}: "
        raise ConfigValuesError([e.removeprefix(prefix) for e in exc.errors]) from None
    except RecursionError:  # re.compile on a pattern nested thousands deep
        raise ConfigValuesError(["a pattern is nested too deeply"]) from None
    return _normalised(config)


def _normalised(config: Any) -> dict[str, Any]:
    """The values of a parsed config that differ from pydebuprep's defaults."""
    values: dict[str, Any] = {}
    defaults = ReportSettings()
    report = {
        key: getattr(config.report, key)
        for key in EDITABLE_REPORT
        if getattr(config.report, key) != getattr(defaults, key)
    }
    if report:
        values["report"] = report
    if config.log_ignore:
        values["logs"] = {"ignore": list(config.log_ignore)}
    sections: dict[str, Any] = {}
    for form in check_forms():
        entry: dict[str, Any] = {}
        if form.id in config.disabled:
            entry["enabled"] = False
        for name, value in config.check_settings.get(form.id, {}).items():
            f = _fields()[f"checks.{form.id}.{name}"]
            if value != f.default:
                entry[name] = value
        if entry:
            sections[form.id] = entry
    if sections:
        values["checks"] = sections
    return values
