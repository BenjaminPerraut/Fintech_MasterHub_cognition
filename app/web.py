"""Template rendering + navigation registry shared by every internal app."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .auth.rbac import ROLE_LABELS
from .security import csrf_input

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["csrf_input"] = csrf_input
templates.env.globals["ROLE_LABELS"] = {r.value: label for r, label in ROLE_LABELS.items()}


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str
    perm: str
    icon: str
    section: str = "Apps"


NAV: list[NavItem] = [
    NavItem("hub", "Master Hub", "/", "hub:view", "◆", "Overview"),
]
GOVERNANCE_NAV: list[NavItem] = [
    NavItem("audit", "Audit log", "/audit", "audit:read", "≡", "Governance"),
    NavItem("verify", "Verify chain", "/audit/verify", "audit:verify", "✓", "Governance"),
]


def register_nav(item: NavItem) -> None:
    if all(n.key != item.key for n in NAV):
        NAV.append(item)


def nav_for(user) -> list[NavItem]:
    if user is None:
        return []
    return [n for n in NAV + GOVERNANCE_NAV if user.can(n.perm)]


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200, active: str = ""):
    user = getattr(request.state, "user", None)
    context = {
        "request": request,
        "user": user,
        "nav": nav_for(user),
        "active": active,
        "env_label": request.app.state.settings.env_label,
        **(ctx or {}),
    }
    return templates.TemplateResponse(request, name, context, status_code=status_code)
