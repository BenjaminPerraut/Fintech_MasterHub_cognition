"""Identity provider seam.

The app only ever talks to `IdentityProvider`. Today it is a stubbed directory
(pick a seeded user). Swapping in Entra ID or Okta means implementing the same
two methods with OIDC (authorization-code + PKCE) – see docs/SSO.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from starlette.requests import Request


@dataclass(frozen=True)
class Identity:
    subject: str          # stable IdP subject (OIDC `sub` / Entra `oid`)
    email: str
    name: str
    groups: tuple[str, ...]


class AuthenticationError(Exception):
    pass


class IdentityProvider(ABC):
    key: str
    display_name: str

    @abstractmethod
    def login_context(self) -> dict:
        """Data for the login page (stub: account picker; OIDC: just a button)."""

    @abstractmethod
    async def authenticate(self, request: Request) -> Identity:
        """Handle the IdP callback and return a verified identity, or raise AuthenticationError."""


@dataclass(frozen=True)
class DirectoryEntry:
    subject: str
    email: str
    name: str
    title: str
    groups: tuple[str, ...]


# Seeded workforce directory for the stub IdP. Fictional people, fictional company.
STUB_DIRECTORY: tuple[DirectoryEntry, ...] = (
    DirectoryEntry("u-1001", "alice.martin@northwindpay.example", "Alice Martin", "KYC Analyst", ("mh-kyc-analysts",)),
    DirectoryEntry("u-1002", "karim.haddad@northwindpay.example", "Karim Haddad", "Senior KYC Analyst", ("mh-kyc-analysts",)),
    DirectoryEntry("u-1003", "olivia.chen@northwindpay.example", "Olivia Chen", "Ops Lead, Risk", ("mh-ops-leads",)),
    DirectoryEntry("u-1004", "omar.diallo@northwindpay.example", "Omar Diallo", "Ops Lead, Payments", ("mh-ops-leads",)),
    DirectoryEntry("u-1005", "sam.rivera@northwindpay.example", "Sam Rivera", "Support Specialist", ("mh-support",)),
    DirectoryEntry("u-1006", "erin.novak@northwindpay.example", "Erin Novak", "Staff Engineer", ("mh-engineers",)),
    DirectoryEntry("u-1007", "ethan.brooks@northwindpay.example", "Ethan Brooks", "Backend Engineer", ("mh-engineers",)),
    DirectoryEntry("u-1008", "ada.okafor@northwindpay.example", "Ada Okafor", "IT Administrator", ("mh-admins",)),
    DirectoryEntry("u-1009", "max.dual@northwindpay.example", "Max Dual", "Misconfigured account (2 roles)", ("mh-kyc-analysts", "mh-ops-leads")),
)


class StubDirectoryProvider(IdentityProvider):
    key = "stub"
    display_name = "Northwind SSO (stub)"

    def __init__(self, directory: tuple[DirectoryEntry, ...] = STUB_DIRECTORY):
        self._by_subject = {d.subject: d for d in directory}
        self._directory = directory

    def login_context(self) -> dict:
        return {"accounts": self._directory, "provider": self.display_name, "stub": True}

    async def authenticate(self, request: Request) -> Identity:
        form = await request.form()
        entry = self._by_subject.get(str(form.get("subject", "")))
        if entry is None:
            raise AuthenticationError("unknown account")
        return Identity(entry.subject, entry.email, entry.name, entry.groups)


def build_provider(key: str) -> IdentityProvider:
    if key == "stub":
        return StubDirectoryProvider()
    raise ValueError(f"identity provider '{key}' not implemented; see docs/SSO.md")
