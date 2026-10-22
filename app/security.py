"""CSRF protection and security headers (OWASP ASVS 4/5 baseline)."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from markupsafe import Markup, escape

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def csrf_input(request: Request) -> Markup:
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(csrf_token(request))}">')


async def verify_csrf(request: Request) -> None:
    """Global dependency: every state-changing request needs the session's synchronizer token."""
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    expected = request.session.get("csrf")
    if not expected or not sent or not secrets.compare_digest(str(sent), str(expected)):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


class SecurityHeadersMiddleware:
    def __init__(self, app, https_only: bool = False):
        self.app = app
        self.https_only = https_only

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                extra = {
                    b"content-security-policy": CSP.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"same-origin",  # no-referrer makes browsers send "Origin: null" on form POSTs
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if not scope["path"].startswith("/static/"):
                    # Pages contain PII: never cache them in browsers or proxies.
                    extra[b"cache-control"] = b"no-store"
                if self.https_only:
                    extra[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains"
                existing = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra.items() if k not in existing)
            await send(message)

        await self.app(scope, receive, send_wrapper)
