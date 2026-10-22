"""Identity provider seam.

The app only ever talks to `IdentityProvider`. Today it is a stubbed directory
(pick a seeded user). Swapping in Entra ID or Okta means implementing the same
two methods with OIDC (authorization-code + PKCE) – see docs/SSO.md.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request


@dataclass(frozen=True)
class Identity:
    subject: str          # stable IdP subject (OIDC `sub` / Entra `oid`)
    email: str
    name: str
    groups: tuple[str, ...]


class AuthenticationError(Exception):
    """`str(exc)` is shown to the user; `audit_reason` (if set) is what goes in the audit log."""

    def __init__(self, message: str, audit_reason: str | None = None):
        super().__init__(message)
        self.audit_reason = audit_reason or message


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


DEFAULT_DEMO_PASSWORD = "Northwind-Demo-2026"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


@lru_cache(maxsize=8)
def _hashed(password: str, emails: tuple[str, ...]) -> dict[str, tuple[bytes, bytes]]:
    """Per-user salted scrypt hashes, computed once per process. Plaintext is never stored."""
    out = {}
    for e in emails:
        salt = secrets.token_bytes(16)
        out[e] = (salt, _scrypt(password, salt))
    return out


_DUMMY_SALT = secrets.token_bytes(16)


class StubDirectoryProvider(IdentityProvider):
    """Sandbox-only directory with email + password sign-in (shared demo password, scrypt-hashed).

    Brute force: 5 failures within 5 minutes lock the email for 5 minutes from the 5th failure; the lockout
    answers with the same generic error so it doesn't reveal account state. Real deployments use Entra ID / Okta (MFA at the IdP).
    """
    key = "stub"
    display_name = "Northwind SSO (stub)"

    def __init__(self, directory: tuple[DirectoryEntry, ...] = STUB_DIRECTORY, demo_password: str | None = None):
        self._directory = directory
        self._by_email = {d.email.lower(): d for d in directory}
        password = demo_password or os.environ.get("MASTERHUB_DEMO_PASSWORD") or DEFAULT_DEMO_PASSWORD
        self._show_password = password == DEFAULT_DEMO_PASSWORD
        self._hashes = _hashed(password, tuple(sorted(self._by_email)))
        self._failures: dict[str, tuple[int, float, float]] = {}

    def login_context(self) -> dict:
        return {"accounts": self._directory, "provider": self.display_name, "stub": True,
                "demo_password": DEFAULT_DEMO_PASSWORD if self._show_password else None}

    async def authenticate(self, request: Request) -> Identity:
        form = await request.form()
        email = str(form.get("email", "")).strip().lower()[:200]
        password = str(form.get("password", ""))[:200]
        now = time.time()
        count, window_start, locked_until = self._failures.get(email, (0, now, 0.0))
        if locked_until > now:
            raise AuthenticationError("invalid email or password", audit_reason="locked out")
        if locked_until or now - window_start >= LOCKOUT_SECONDS:
            count, window_start = 0, now
        salt, expected = self._hashes.get(email, (_DUMMY_SALT, b""))
        digest = await run_in_threadpool(_scrypt, password, salt)
        ok = hmac.compare_digest(digest, expected) and email in self._by_email
        if not ok:
            count += 1
            if len(self._failures) > 10_000:
                self._failures.clear()
            self._failures[email] = (count, window_start, now + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0.0)
            raise AuthenticationError("invalid email or password")
        self._failures.pop(email, None)
        entry = self._by_email[email]
        return Identity(entry.subject, entry.email, entry.name, entry.groups)


def build_provider(key: str) -> IdentityProvider:
    if key == "stub":
        return StubDirectoryProvider()
    raise ValueError(f"identity provider '{key}' not implemented; see docs/SSO.md")
