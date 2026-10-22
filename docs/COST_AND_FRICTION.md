# Cost, friction and evidence log

Honest record of how this was built with Devin, for the build-vs-buy discussion.
Wall-clock times are taken from the VM clock (UTC). ACU numbers are **not visible from inside
the session**; copy them from the Devin Session Insights page into the table below.

## Usage per major step

| Phase | PR | Wall clock (start → PR) | ACUs (from Session Insights) | Notes |
|-------|----|-------------------------|------------------------------|-------|
| 0. Plan | – | 20:56 → 20:58 | _fill in_ | Repo was empty; Python 3.10, no Node → server-rendered stack |
| 1. Foundation | [#1](https://github.com/BenjaminPerraut/Fintech_MasterHub_cognition/pull/1) | 20:58 → 21:01 | _fill in_ | SSO stub, RBAC, hash-chained audit, triggers, security headers, 19 tests |
| 2. Queue engine + KYC | [#2](https://github.com/BenjaminPerraut/Fintech_MasterHub_cognition/pull/2) | 21:01 → 21:04 | _fill in_ | YAML manifest engine, four-eyes (service + DB trigger), record hashes, PII masking/reveal, export, 48 KYC cases, 41 tests total |
| 3. Refunds + Feature flags | [#3](https://github.com/BenjaminPerraut/Fintech_MasterHub_cognition/pull/3) | 21:04 → 21:06 | _fill in_ | Conditional four-eyes (≥ €500), payout step, voting with quorum, rollout via four-eyes, kill switch, 63 refunds + 14 flags, 60 tests total |
| 4. Fraud payments + Transfer flags | #4 | 21:06 → see PR | _fill in_ | Rules engine over append-only ledger, 2 alert queues, four-eyes closure, Ops Lead scan/simulate, 73 tests total |

## Corrections, steering, rewrites

| # | Phase | What happened | Who caught it | Resolution |
|---|-------|---------------|---------------|------------|
| 1 | 0 | Brief said "three roles" but listed five | Devin | Built five, flagged in plan |
| 2 | 1 | First draft of the login error path used a `locals()` hack | Devin (self-review) | Rewrote with explicit `identity = None` |
| 3 | 1 | First draft created the ASGI app at import time | Devin (self-review) | Switched to `uvicorn --factory app.main:create_app` |
| 4 | 1 | `pkill -f "port 8011"` killed the shell running it (pattern matched itself) | Devin | Re-ran the commit step; no code impact |
| 5 | 2 | Templates used an `extract` filter that Jinja doesn't ship (it's an Ansible filter) | Devin (before first run) | Registered a 1-line custom filter |
| 6 | 2 | Role check ran after comment validation, so an unauthorised user got a 400 instead of an audited 403 | Devin (self-review) | Moved the role check first |
| 7 | 2 | Seeded "closed" relationships could end before they were submitted | Devin (self-review) | Clamp submitted date before relationship end |
| 8 | 3 | Test asserted pretty-printed JSON, but audit details are canonical (compact) JSON | Devin (test run) | Assert on parsed JSON |
| 9 | 4 | "Relative" high-amount rule (10x median) alone would flag ordinary €300 purchases | Devin (design, before code) | Added `min_amount_eur: 1000` floor to the rule |
| 10 | 4 | Considered a separate `rule_hits` table for dedupe; its insert couldn't share the alert's transaction | Devin (design) | Simplified: `rule_key` stored on the alert itself (D-029) |

## Places Devin got stuck / needed steering

_None so far._

## Features cut or simplified under the 2-hour limit

See also `docs/DECISIONS.md`.

- Real OIDC (Entra/Okta) → stub provider + interface + `docs/SSO.md`.
- External anchoring of the audit head hash (WORM / ledger) → documented only (D-004).
- No password/MFA in the stub; MFA is assumed at the IdP.
- No rate limiting on login (IdP handles it in prod).
- Retention purge job → filter + documented policy only (D-017).
- Field-level encryption of PII → masking + audited reveal only (D-016).
- Break-glass access for engineers → not built (D-019).
- Real payout to a PSP → state change only (D-026).
- Streaming detection, ML scoring, device/IP signals → batch static rules (D-027, D-028).
- Gradual rollout percentages other than 10/100 and flag evaluation SDK → not built (D-024).
