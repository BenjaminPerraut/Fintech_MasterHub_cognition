# Cost, friction and evidence log

Honest record of how this was built with Devin, for the build-vs-buy discussion.
Wall-clock times are taken from the VM clock (UTC). ACU numbers are **not visible from inside
the session**; copy them from the Devin Session Insights page into the table below.

## Usage per major step

| Phase | PR | Wall clock (start → PR) | ACUs (from Session Insights) | Notes |
|-------|----|-------------------------|------------------------------|-------|
| 0. Plan | – | 20:56 → 20:58 | _fill in_ | Repo was empty; Python 3.10, no Node → server-rendered stack |
| 1. Foundation | #1 | 20:58 → _see PR_ | _fill in_ | SSO stub, RBAC, hash-chained audit, triggers, security headers, 19 tests |

## Corrections, steering, rewrites

| # | Phase | What happened | Who caught it | Resolution |
|---|-------|---------------|---------------|------------|
| 1 | 0 | Brief said "three roles" but listed five | Devin | Built five, flagged in plan |
| 2 | 1 | First draft of the login error path used a `locals()` hack | Devin (self-review) | Rewrote with explicit `identity = None` |
| 3 | 1 | First draft created the ASGI app at import time | Devin (self-review) | Switched to `uvicorn --factory app.main:create_app` |

## Places Devin got stuck / needed steering

_None so far._

## Features cut or simplified under the 2-hour limit

See also `docs/DECISIONS.md`.

- Real OIDC (Entra/Okta) → stub provider + interface + `docs/SSO.md`.
- External anchoring of the audit head hash (WORM / ledger) → documented only (D-004).
- No password/MFA in the stub; MFA is assumed at the IdP.
- No rate limiting on login (IdP handles it in prod).
