# Fintech Master Hub (Northwind Pay, sample data)

Proof of concept: Devin rebuilds a fintech's Power Apps internal tools as code the team owns.
The screens are the easy part; this repo focuses on the controls a fintech can't get wrong:
**who can do what, who approved it, and whether anyone changed the record afterwards.**

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export MASTERHUB_SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
uvicorn --factory app.main:create_app --port 8000
pytest -q
```

Open http://localhost:8000 and pick a seeded account on the stub SSO page.

## Controls

| Control | Where |
|---------|-------|
| SSO seam (stub today, Entra/Okta tomorrow) | `app/auth/provider.py`, `docs/SSO.md` |
| 5 roles, one per person, no superuser | `app/auth/rbac.py` |
| Server-side guard on every protected route | `require_perm` / `require_role` in `app/deps.py` |
| Append-only, hash-chained audit log + triggers | `app/audit.py`, `app/db.py`, `/audit/verify` |
| CSRF, CSP, no-store, no API docs exposed | `app/security.py`, `app/main.py` |

## Seeded accounts

| Account | Role |
|---------|------|
| Alice Martin, Karim Haddad | KYC Analyst |
| Olivia Chen, Omar Diallo | Ops Lead |
| Sam Rivera | Support |
| Erin Novak, Ethan Brooks | Engineer |
| Ada Okafor | Admin |
| Max Dual | Misconfigured (two roles) → login refused |

## Docs

- `docs/DECISIONS.md`: design decisions and trade-offs
- `docs/SSO.md`: swapping in Entra ID / Okta
- `docs/COST_AND_FRICTION.md`: time, corrections and cuts while building this with Devin
