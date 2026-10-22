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
| 4. Fraud payments + Transfer flags | [#4](https://github.com/BenjaminPerraut/Fintech_MasterHub_cognition/pull/4) | 21:06 → 21:08 | _fill in_ | Rules engine over append-only ledger, 2 alert queues, four-eyes closure, Ops Lead scan/simulate, 73 tests total |
| 6. Security & bug fixes from post-build review | #6 | after review | _fill in_ | Sandbox gating, session revocation, refund segregation of duties, IBAN not typeable, 400s, 88 tests total |
| 5. Hub desk + new-app skill + docs | #5 | 21:08 → 21:11 | _fill in_ | Cross-app desk (RBAC-filtered, four-eyes-aware), `.devin/skills/new-internal-app`, final docs, 79 tests total |

**Total so far:** about 15 minutes of VM wall-clock time from plan to the last PR (20:56 → 21:11 UTC on the VM clock).
That is Devin's machine time only. It excludes your review time and the time sessions spent waiting for you, and it is
the number least worth trusting: use the **ACUs from Session Insights** as the real cost figure.

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
| 13 | 6 | Devin Review found 4 issues in my #6 fixes: refunds created before #6 skipped the creator check, Ops Leads were asked to approve refunds they had logged themselves, the creator lost the reject button, the sessions table grew forever | Devin Review, confirmed by Devin, fixed after you approved | Fixed in #6 before merge (91 tests) |
| 14 | post-merge | **Live preview: account names invisible on the sign-in page.** On iPad Safari in dark mode, the browser's default button text color was white, on a white card | You (screenshot) | Explicit text color on account buttons + `color-scheme: light` |
| 15 | post-merge | **Live preview: preview link didn't open (ERR_NAME_NOT_RESOLVED).** The app and the preview host were fine (public DNS resolves, proxy answers 401 until you sign in). Your network/VPN couldn't resolve the preview hostname | You (screenshot) | No code change. Workaround: Desktop tab, or VPN off / Devin web app preview chip |
| 16 | post-merge | **Live preview: "bad origin" after clicking a user.** Every test passed because they post to the app directly. The preview goes through a proxy that checks the `Origin` header on POSTs. My `Referrer-Policy: no-referrer` makes browsers send `Origin: null` on form submissions, so the proxy refused sign-in before it reached the app. Reproduced in Chrome: `no-referrer` → `Origin: null`, `same-origin` → real origin | You (screenshot); root cause found by Devin | `Referrer-Policy: same-origin` (D-041) + regression test (92 tests) |
| 12 | 6 | Post-build security review (requested by you) found 2 High + 5 Medium issues in my own code: stub login default, refund self-approval to a typed IBAN, unrevocable sessions, simulation/seeding not sandbox-gated, ephemeral key per worker, 500s on bad input | Devin (review), 1 also flagged by Devin Review | Fixed in #6; Low findings left in the review report |
| 11 | 5 | Desk test assumed engineer Erin still had flags to vote on, but the seed has her voting on (or authoring) all of them | Devin (test run) | Asserted the vote prompt for the admin instead; the seed was right, the test was wrong |

## Places Devin got stuck / needed steering

No human steering was needed after the plan was approved (one approval message, zero corrections from the user).
Honest friction points on Devin's side:

- **Context window:** the conversation was summarized once (between Phase 2 and 3), and the context limit was extended once in
  Phase 4 so the rules engine stayed in view while writing its tests. No work was lost, but re-reading files cost time.
- **No browser check yet:** pages were verified by rendering every list, detail page and quick filter for every role through the
  test client (all HTTP 200, no template errors). A human or the testing agent still hasn't clicked through the UI.
- **Stacked PRs:** each PR is based on the previous phase's branch. Merge them in order (#1 → #5) and retarget each one to
  `main` after its parent merges.

**Lesson from 14 to 16:** green tests and a localhost check aren't enough. The demo has to be checked through the same path the user takes: the preview proxy, their browser (iPad Safari, dark mode) and their network. Automated tests and the recorded demo all ran on the VM, so none of them could catch these issues.

## Time log: build to checks (VM clock, UTC, 22 Oct 2026)

Sources: git commit times and session timestamps. Wall clock includes time Devin spent waiting on you; "active" is Devin's own estimate of time spent working.

| Step | Window (UTC) | Wall clock | What |
|------|--------------|-----------|------|
| Plan | 20:56 → 20:58 | 2 min | Read empty repo, phased plan, approval |
| Phase 1 Foundation (#1) | 20:58 → 21:00 | 3 min | SSO stub, RBAC, audit chain, headers |
| Phase 2 Queue engine + KYC (#2) | 21:00 → 21:04 | 3 min | YAML engine, four-eyes, 48 KYC cases |
| Phase 3 Refunds + flags (#3) | 21:04 → 21:07 | 3 min | Refunds, voting, rollout |
| Phase 4 Fraud + transfers (#4) | 21:07 → 21:09 | 2 min | Rules, alert queues |
| Phase 5 Desk + skill + docs (#5) | 21:09 → 21:11 | 2 min | Hub desk, new-app skill, docs |
| **Build subtotal** | **20:56 → 21:11** | **~15 min** | 5 PRs, 79 tests |
| Recorded browser demo (testing agent) | 21:11 → 21:21 | ~10 min | 6 README flows as each role, 2.5-min video |
| Security + bug review of all PRs | 21:11 → 21:22 | ~11 min (in parallel with the demo) | Review report: 2 High, 5 Medium, 8 Low |
| Security fixes (#6) | 21:22 → 21:27 | ~5 min | Sandbox gating, session revocation, refund segregation of duties |
| Preview: DNS check, invisible names fix | 21:28 → 21:37 | ~9 min | Mostly waiting on screenshots |
| Devin Review fixes on #6 + merge #2–#6 | 21:37 → 21:39 | ~2 min | 4 findings fixed, 91 tests |
| "bad origin" root cause + fix (#7) | 21:39 → 21:41 | ~2 min | Referrer-Policy, friction log |
| Email/password sign-in + light/dark (#8) | 21:43 → 21:48 | ~5 min | 96 tests, screenshots, PR |
| Demo-account button fills password, this log | 21:50 → 21:55 | ~5 min | Fix + docs |
| **Total so far** | **20:56 → ~21:55** | **~60 min** | 8 PRs, 97 tests |

## Cost estimate (not measured)

Devin can't read its own usage from inside the session. The real figure is at Settings → Usage & limits (self-serve) or
Settings → Consumption (Enterprise, ACUs). Per the Devin docs, ACU consumption depends on tokens, the model, and VM/infra time,
so there's no fixed rate per minute.

Rough order of magnitude only: about 60 min of agent wall clock (Devin + testing agent + Devin Review runs). Under the older
public rule of thumb of roughly 15 min of agent work per ACU at about $2 to $2.25 per ACU, that's **about 4 to 8 ACUs, i.e. roughly
$10 to $20**. Replace this with the real number from the usage page.

For the business case: the brief's Power Apps spend is about $250K a year. This session produced 5 internal apps + a hub in about 1 hour of agent
time. The ongoing cost of owning the code (hosting, a real IdP integration, maintenance, security review by humans) is not
included here and would dominate the agent cost.

## Features cut or simplified under the 2-hour limit

See also `docs/DECISIONS.md`.

- Real OIDC (Entra/Okta) → stub provider + interface + `docs/SSO.md`.
- External anchoring of the audit head hash (WORM / ledger) → documented only (D-004).
- Sandbox stub has a shared demo password (scrypt-hashed, 5-failure lockout) but no MFA; MFA is assumed at the IdP (D-042).
- Retention purge job → filter + documented policy only (D-017).
- Field-level encryption of PII → masking + audited reveal only (D-016).
- Break-glass access for engineers → not built (D-019).
- Real payout to a PSP → state change only (D-026).
- Streaming detection, ML scoring, device/IP signals → batch static rules (D-027, D-028).
- Desk counts computed per request, with no caching and no notifications (email/Slack) for pending approvals.
- Gradual rollout percentages other than 10/100 and flag evaluation SDK → not built (D-024).
