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
| YAML queue engine (fields, states, transitions, roles) | `app/manifests/*.yaml`, `app/engine/` |
| Four-eyes (service + DB trigger + tests) | `app/engine/service.py`, `app/engine/store.py`, `tests/test_engine.py` |
| Record tamper detection (content hash vs audit) | `store.verify_records`, `/audit/verify` |
| PII masked by default, audited reveal and export | `app/engine/routes.py` |
| Refunds: four-eyes only at or above €500, payout by Ops Lead | `app/manifests/refunds.yaml` |
| Feature flags: final votes, quorum, four-eyes rollout, instant kill switch | `app/manifests/flags.yaml`, `service.vote` |
| Fraud payments + transfer flags: visible rules, append-only ledger, four-eyes closure | `app/rules/`, `app/manifests/fraud.yaml`, `transfers.yaml` |
| Hub desk: top flagged issues + "needs your approval", RBAC-filtered | `app/routes/hub.py` |
| New app = 1 YAML + 1 seed + 1 test, same layout and controls | `.devin/skills/new-internal-app/SKILL.md` |

## Apps and who can do what

| App | KYC Analyst | Ops Lead | Support | Engineer | Admin |
|-----|-------------|----------|---------|----------|-------|
| KYC review | work, approve customers | work, approve rejections / reopen, export | read (masked) | none | none |
| Refunds | none | work, 2nd approval ≥ €500, payout, export | create, work | none | none |
| Feature flags | none | vote, accept, approve promote/retire | read | create, vote, canary, rollback | vote, approve retire |
| Fraud payments | work, propose closure | work, 2nd approval | read (masked) | none | none |
| Transfer flags | work, hold, propose release/block | work, 2nd approval | read (masked) | none | none |
| Detection rules | read | read, run scan, simulate | none | read | read |
| Audit log / verify | none | read + verify | none | verify | read + verify |

## Five-minute demo

1. Log in as **Alice** (KYC Analyst) and open a KYC case in review. Propose "Approve customer" and notice you can't approve it yourself.
2. Log in as **Karim**: the desk shows it under *Needs your approval*. Approve it, and the audit trail shows both names.
3. Log in as **Sam** (Support) and create a €900 refund, then approve it: it becomes a proposal. **Omar** approves, then executes the payout.
4. Log in as **Olivia** (Ops Lead) and open *Detection rules*. Click *4 card payments in 24 seconds* and a new Fraud alert appears on the desk.
5. Log in as **Erin** (Engineer): only Feature Flags is visible, and the KYC/refund URLs return 403 (audited).
6. Open `/audit/verify` as **Olivia**: the chain and every record match. Edit a row with `sqlite3 data/masterhub.db` and reload to see it flagged.

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
