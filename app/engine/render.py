"""Display helpers shared by all queue apps (masking, formatting, badges)."""
from __future__ import annotations

from markupsafe import Markup, escape

from .manifest import FieldDef


def mask(value) -> str:
    s = str(value)
    if len(s) <= 4:
        return "••••"
    return "•" * min(len(s) - 4, 8) + s[-4:]


def fmt(field: FieldDef, value, revealed: bool = False) -> Markup:
    if value is None or value == "":
        return Markup('<span class="muted">—</span>')
    if field.pii and not revealed:
        return Markup(f'<span class="masked" title="Masked PII">{escape(mask(value))}</span>')
    if field.type == "enum":
        color = field.colors.get(str(value), "gray")
        return Markup(f'<span class="badge badge-{escape(color)}">{escape(str(value).replace("_", " "))}</span>')
    if field.type == "score":
        v = int(float(value))
        cls = "high" if v >= 70 else "medium" if v >= 40 else "low"
        if field.name.startswith("selfie"):
            cls = "low" if v >= 80 else "medium" if v >= 60 else "high"
        return Markup(f'<span class="risk risk-{cls}">{v}</span>')
    if field.type == "money":
        return Markup(f'<span class="mono">{float(value):,.2f}</span>')
    if field.type == "longtext":
        return Markup(f'<span class="pre">{escape(value)}</span>')
    return Markup(escape(str(value)))
