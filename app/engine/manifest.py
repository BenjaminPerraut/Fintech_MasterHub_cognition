"""YAML manifest per internal app: fields, states, transitions and who may do what.

Manifests are validated at startup; a typo in a role or state fails fast instead of
silently opening a hole.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from ..auth.rbac import Role

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "manifests"
FIELD_TYPES = {"text", "enum", "number", "money", "date", "score", "bool", "longtext"}
COLORS = {"gray", "blue", "amber", "green", "red", "purple", "cyan"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    type: str = "text"
    section: str = "Details"
    options: tuple[str, ...] = ()
    colors: dict = field(default_factory=dict)
    pii: bool = False
    list: bool = False
    filter: bool = False
    input: bool = False
    required: bool = False
    help: str = ""
    default: str | None = None


@dataclass(frozen=True)
class TransitionDef:
    name: str
    label: str
    from_: tuple[str, ...]
    to: str
    roles: frozenset[str]
    four_eyes: bool = False
    approve_roles: frozenset[str] = frozenset()
    require_comment: bool = False
    style: str = ""
    confirm: str = ""
    four_eyes_if: tuple | None = None  # (field, threshold): four-eyes only when data[field] >= threshold
    requires_quorum: bool = False
    set: dict = field(default_factory=dict)  # data fields written when the transition is applied

    def needs_four_eyes(self, data: dict) -> bool:
        if self.four_eyes:
            return True
        if self.four_eyes_if:
            name, threshold = self.four_eyes_if
            try:
                return float(data.get(name) or 0) >= threshold
            except (TypeError, ValueError):
                return True  # unparseable amount: fail closed
        return False


@dataclass(frozen=True)
class VotingDef:
    roles: frozenset[str]
    quorum: int
    states: tuple[str, ...]

    def passed(self, tally: dict) -> bool:
        up, down = tally.get("up", 0), tally.get("down", 0)
        return up >= self.quorum and up > down


@dataclass(frozen=True)
class StateDef:
    name: str
    label: str
    color: str = "gray"
    terminal: bool = False


@dataclass(frozen=True)
class Manifest:
    key: str
    title: str
    icon: str
    description: str
    id_prefix: str
    states: dict[str, StateDef]
    initial: str
    fields: tuple[FieldDef, ...]
    transitions: tuple[TransitionDef, ...]
    view_roles: frozenset[str]
    comment_roles: frozenset[str]
    reveal_roles: frozenset[str]
    create_roles: frozenset[str]
    export_roles: frozenset[str]
    title_field: str
    priority_field: str | None
    quick_filters: tuple[dict, ...] = ()
    order: int = 100
    voting: VotingDef | None = None
    desk: dict = field(default_factory=dict)  # {min_priority, reason_fields}: what the hub desk shows as flagged

    def field(self, name: str) -> FieldDef:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def transition(self, name: str) -> TransitionDef:
        for t in self.transitions:
            if t.name == name:
                return t
        raise KeyError(name)

    @property
    def list_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.list]

    @property
    def filter_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.filter]

    @property
    def input_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.input]

    @property
    def sections(self) -> list[tuple[str, list[FieldDef]]]:
        out: dict[str, list[FieldDef]] = {}
        for f in self.fields:
            out.setdefault(f.section, []).append(f)
        return list(out.items())

    @property
    def open_states(self) -> list[str]:
        return [s.name for s in self.states.values() if not s.terminal]


def resolve_token(value):
    """'@today', '@today+5', '@today-1' -> ISO date. Used by manifest defaults and quick filters."""
    if isinstance(value, str) and value.startswith("@today"):
        return (date.today() + timedelta(days=int(value[6:] or 0))).isoformat()
    return value


def _roles(value, where: str) -> frozenset[str]:
    roles = frozenset(value or [])
    valid = {r.value for r in Role}
    bad = roles - valid
    if bad:
        raise ManifestError(f"{where}: unknown roles {sorted(bad)}")
    return roles


def parse_manifest(raw: dict) -> Manifest:
    key = raw["app"]
    states = {}
    for name, spec in raw["states"].items():
        spec = spec or {}
        color = spec.get("color", "gray")
        if color not in COLORS:
            raise ManifestError(f"{key}: state {name} has unknown color {color}")
        states[name] = StateDef(name, spec.get("label", name.replace("_", " ").title()), color, bool(spec.get("terminal")))
    if raw["initial"] not in states:
        raise ManifestError(f"{key}: initial state {raw['initial']} not declared")

    fields = []
    for f in raw["fields"]:
        ftype = f.get("type", "text")
        if ftype not in FIELD_TYPES:
            raise ManifestError(f"{key}: field {f['name']} has unknown type {ftype}")
        fields.append(FieldDef(
            name=f["name"], label=f.get("label", f["name"].replace("_", " ").title()), type=ftype,
            section=f.get("section", "Details"), options=tuple(f.get("options", ())), colors=f.get("colors", {}),
            pii=bool(f.get("pii")), list=bool(f.get("list")), filter=bool(f.get("filter")),
            input=bool(f.get("input")), required=bool(f.get("required")), help=f.get("help", ""),
            default=None if f.get("default") is None else str(f.get("default")),
        ))
    names = {f.name for f in fields}
    types = {f.name: f.type for f in fields}

    voting = None
    if raw.get("voting"):
        v = raw["voting"]
        vstates = tuple(v.get("states", ()))
        for s in vstates:
            if s not in states:
                raise ManifestError(f"{key}.voting: unknown state {s}")
        voting = VotingDef(_roles(v.get("roles"), f"{key}.voting"), int(v.get("quorum", 2)), vstates)
        if voting.quorum < 1 or not voting.roles or not vstates:
            raise ManifestError(f"{key}.voting: needs roles, states and quorum >= 1")

    transitions = []
    for t in raw.get("transitions", []):
        where = f"{key}.{t['name']}"
        from_ = tuple(t["from"] if isinstance(t["from"], list) else [t["from"]])
        for s in (*from_, t["to"]):
            if s not in states:
                raise ManifestError(f"{where}: unknown state {s}")
        four_eyes = bool(t.get("four_eyes"))
        approve_roles = _roles(t.get("approve_roles"), where)
        fe_if = t.get("four_eyes_if")
        if fe_if:
            if types.get(fe_if.get("field")) not in ("number", "money", "score"):
                raise ManifestError(f"{where}: four_eyes_if needs a numeric field")
            fe_if = (fe_if["field"], float(fe_if["gte"]))
        if (four_eyes or fe_if) and not approve_roles:
            raise ManifestError(f"{where}: four_eyes transitions need approve_roles")
        if t.get("requires_quorum") and voting is None:
            raise ManifestError(f"{where}: requires_quorum needs a voting section")
        set_ = dict(t.get("set") or {})
        for k in set_:
            if k not in names:
                raise ManifestError(f"{where}: set writes undeclared field {k}")
        transitions.append(TransitionDef(
            name=t["name"], label=t.get("label", t["name"].replace("_", " ").title()), from_=from_, to=t["to"],
            roles=_roles(t["roles"], where), four_eyes=four_eyes, approve_roles=approve_roles,
            require_comment=bool(t.get("require_comment")), style=t.get("style", ""), confirm=t.get("confirm", ""),
            four_eyes_if=fe_if or None, requires_quorum=bool(t.get("requires_quorum")), set=set_,
        ))

    perms = raw.get("permissions", {})
    title_field = raw.get("title_field", fields[0].name)
    priority_field = raw.get("priority_field")
    desk = dict(raw.get("desk") or {})
    for ref in desk.get("reason_fields", []):
        if ref not in names:
            raise ManifestError(f"{key}.desk: field {ref} not declared")
    for ref in (title_field, priority_field):
        if ref and ref not in names:
            raise ManifestError(f"{key}: field {ref} not declared")
    return Manifest(
        key=key, title=raw["title"], icon=raw.get("icon", "•"), description=raw.get("description", ""),
        id_prefix=raw["id_prefix"], states=states, initial=raw["initial"], fields=tuple(fields),
        transitions=tuple(transitions),
        view_roles=_roles(perms.get("view"), f"{key}.view"),
        comment_roles=_roles(perms.get("comment"), f"{key}.comment"),
        reveal_roles=_roles(perms.get("reveal_pii"), f"{key}.reveal_pii"),
        create_roles=_roles(perms.get("create"), f"{key}.create"),
        export_roles=_roles(perms.get("export"), f"{key}.export"),
        title_field=title_field, priority_field=priority_field,
        quick_filters=tuple(raw.get("quick_filters", ())), order=int(raw.get("order", 100)), voting=voting, desk=desk,
    )


def load_manifests(directory: Path = MANIFEST_DIR) -> dict[str, Manifest]:
    manifests = {}
    for path in sorted(directory.glob("*.yaml")):
        m = parse_manifest(yaml.safe_load(path.read_text()))
        if m.key in manifests:
            raise ManifestError(f"duplicate app key {m.key}")
        manifests[m.key] = m
    return dict(sorted(manifests.items(), key=lambda kv: kv[1].order))
