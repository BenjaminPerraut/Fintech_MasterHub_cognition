"""Runtime settings. Everything sensitive comes from the environment; nothing is hard-coded.

Outside sandbox mode the app refuses to start with demo-only settings (stub login, sample data,
missing secret key, plain HTTP). Sandbox mode must be switched on explicitly: MASTERHUB_SANDBOX=1.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("masterhub")

BASE_DIR = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: str
    secret_key: str
    https_only: bool
    session_max_age: int
    idp: str
    env_label: str
    seed: bool = False
    sandbox: bool = False


def load_settings(**overrides) -> Settings:
    sandbox = bool(overrides.get("sandbox", os.environ.get("MASTERHUB_SANDBOX", "0") == "1"))
    values = dict(
        db_path=os.environ.get("MASTERHUB_DB", str(BASE_DIR / "data" / "masterhub.db")),
        secret_key=os.environ.get("MASTERHUB_SECRET_KEY", ""),
        https_only=os.environ.get("MASTERHUB_HTTPS_ONLY", "0") == "1",
        session_max_age=int(os.environ.get("MASTERHUB_SESSION_MAX_AGE", str(8 * 3600))),
        idp=os.environ.get("MASTERHUB_IDP", "stub"),
        env_label=os.environ.get("MASTERHUB_ENV_LABEL", "SANDBOX · sample data" if sandbox else "PRODUCTION"),
        seed=os.environ.get("MASTERHUB_SEED", "1" if sandbox else "0") == "1",
        sandbox=sandbox,
    )
    values.update(overrides)
    if not values["sandbox"]:
        problems = []
        if values["idp"] == "stub":
            problems.append("the password-free stub identity provider only runs with MASTERHUB_SANDBOX=1")
        if values["seed"]:
            problems.append("sample-data seeding only runs with MASTERHUB_SANDBOX=1")
        if not values["secret_key"]:
            problems.append("MASTERHUB_SECRET_KEY is required")
        if not values["https_only"]:
            problems.append("MASTERHUB_HTTPS_ONLY=1 is required")
        if problems:
            raise ConfigError("Refusing to start outside sandbox mode: " + "; ".join(problems))
    if not values["secret_key"]:
        # Sandbox only: sessions are invalidated on restart and differ per worker.
        values["secret_key"] = secrets.token_urlsafe(48)
        log.warning("MASTERHUB_SECRET_KEY not set; using an ephemeral random key (sandbox only)")
    return Settings(**values)
