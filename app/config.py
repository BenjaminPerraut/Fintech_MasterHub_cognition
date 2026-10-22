"""Runtime settings. Everything sensitive comes from the environment; nothing is hard-coded."""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("masterhub")

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: str
    secret_key: str
    https_only: bool
    session_max_age: int
    idp: str
    env_label: str


def load_settings(**overrides) -> Settings:
    key = os.environ.get("MASTERHUB_SECRET_KEY")
    if not key:
        # Ephemeral key: sessions are invalidated on restart. Fine for a sandbox, never for prod.
        key = secrets.token_urlsafe(48)
        log.warning("MASTERHUB_SECRET_KEY not set; using an ephemeral random key")
    values = dict(
        db_path=os.environ.get("MASTERHUB_DB", str(BASE_DIR / "data" / "masterhub.db")),
        secret_key=key,
        https_only=os.environ.get("MASTERHUB_HTTPS_ONLY", "0") == "1",
        session_max_age=int(os.environ.get("MASTERHUB_SESSION_MAX_AGE", str(8 * 3600))),
        idp=os.environ.get("MASTERHUB_IDP", "stub"),
        env_label=os.environ.get("MASTERHUB_ENV_LABEL", "SANDBOX · sample data"),
    )
    values.update(overrides)
    return Settings(**values)
