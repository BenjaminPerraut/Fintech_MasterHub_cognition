"""Master Hub application factory."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote

from .auth.provider import build_provider
from .config import Settings, load_settings
from .db import connect, init_db
from .deps import LoginRequired
from .routes import audit as audit_routes
from .routes import auth as auth_routes
from .routes import hub as hub_routes
from .security import SecurityHeadersMiddleware, verify_csrf
from .web import render


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Master Hub", docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(verify_csrf)])
    app.state.settings = settings
    app.state.idp = build_provider(settings.idp)

    conn = connect(settings.db_path)
    try:
        init_db(conn)
    finally:
        conn.close()

    app.add_middleware(SecurityHeadersMiddleware, https_only=settings.https_only)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="mh_session",
                       max_age=settings.session_max_age, same_site="lax", https_only=settings.https_only)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    app.include_router(auth_routes.router)
    app.include_router(hub_routes.router)
    app.include_router(audit_routes.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return render(request, "error.html", {"status": exc.status_code, "detail": exc.detail},
                      status_code=exc.status_code)

    return app

