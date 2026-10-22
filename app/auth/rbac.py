"""Roles and hub-level permissions. App-level permissions live in each app's YAML manifest.

There is deliberately no superuser: `admin` manages people and config, it does not
inherit the right to approve KYC, refunds or rollouts.
"""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    KYC_ANALYST = "kyc_analyst"
    OPS_LEAD = "ops_lead"
    SUPPORT = "support"
    ENGINEER = "engineer"
    ADMIN = "admin"


ROLE_LABELS = {
    Role.KYC_ANALYST: "KYC Analyst",
    Role.OPS_LEAD: "Ops Lead",
    Role.SUPPORT: "Support",
    Role.ENGINEER: "Engineer",
    Role.ADMIN: "Admin",
}

# IdP group claim -> app role. With Entra ID / Okta these become group object IDs / names.
GROUP_TO_ROLE = {
    "mh-kyc-analysts": Role.KYC_ANALYST,
    "mh-ops-leads": Role.OPS_LEAD,
    "mh-support": Role.SUPPORT,
    "mh-engineers": Role.ENGINEER,
    "mh-admins": Role.ADMIN,
}

ALL_ROLES = frozenset(Role)

PERMISSIONS: dict[str, frozenset[Role]] = {
    "hub:view": ALL_ROLES,
    "audit:read": frozenset({Role.ADMIN, Role.OPS_LEAD}),
    "audit:verify": frozenset({Role.ADMIN, Role.OPS_LEAD, Role.ENGINEER}),
    "users:read": frozenset({Role.ADMIN}),
    "rules:view": frozenset({Role.OPS_LEAD, Role.KYC_ANALYST, Role.ENGINEER, Role.ADMIN}),
    "rules:run": frozenset({Role.OPS_LEAD}),
}


class RoleResolutionError(Exception):
    pass


def role_from_groups(groups: tuple[str, ...] | list[str]) -> Role:
    """Exactly one mapped role per person. Multiple roles = refuse login (segregation of duties)."""
    roles = {GROUP_TO_ROLE[g] for g in groups if g in GROUP_TO_ROLE}
    if not roles:
        raise RoleResolutionError("no Master Hub role assigned in the identity provider")
    if len(roles) > 1:
        raise RoleResolutionError("conflicting Master Hub roles assigned; one role per person")
    return roles.pop()


def has_perm(role: str | Role, perm: str) -> bool:
    try:
        r = Role(role)
    except ValueError:
        return False
    return r in PERMISSIONS.get(perm, frozenset())
