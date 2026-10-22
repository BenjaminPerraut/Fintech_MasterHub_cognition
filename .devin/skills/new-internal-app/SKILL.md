---
name: new-internal-app
description: Add a new internal tool to the Northwind Master Hub (FastAPI + YAML queue engine). Use whenever someone asks for a new back-office app, queue, dashboard or approval flow, so it reuses the shared layout, RBAC, four-eyes, audit trail and hub desk instead of hand-built screens.
---

# New internal app for the Master Hub

Every app in the hub is **one YAML manifest + one seed module + one test file**. The list, filters, detail page,
comments, claim/release, four-eyes card, audit timeline, PII masking, CSV export and the hub desk tile are all
rendered by the shared engine. **Do not write per-app templates or routes** unless the engine truly can't express it
(then extend the engine generically, like `four_eyes_if`, `voting`, `set` were added, and add tests for the extension).

## 1. Read first
- `app/manifests/kyc.yaml` (reference app), `refunds.yaml` (conditional four-eyes), `flags.yaml` (voting), `fraud.yaml` (system-created alerts)
- `app/engine/manifest.py` (every key the engine accepts and its validation)
- `docs/DECISIONS.md` (why the controls are the way they are)

## 2. Write `app/manifests/<key>.yaml`
```yaml
app: <key>                 # URL /apps/<key>, lowercase
title: <Title>
icon: "◆"                  # single glyph, shown in sidebar + desk
order: 60                  # sidebar position (10 kyc, 20 refunds, 30 flags, 40 fraud, 50 transfers)
description: <one line: what it is + which actions need a second person>
id_prefix: <ABC>           # item ids ABC-00001
title_field: <field>
priority_field: <score field 0-100>   # drives sorting and the desk
desk: {min_priority: 70, reason_fields: [<enum fields that explain why it's flagged>]}
initial: <state>
permissions:               # roles: kyc_analyst, ops_lead, support, engineer, admin
  view: [...]              # need-to-know; admin is NOT a business superuser
  create: [...]            # omit if only the system creates items
  comment: [...]
  reveal_pii: [...]        # every reveal is audited
  export: [...]            # exports are masked + audited
states:   {<name>: {label: ..., color: gray|blue|amber|green|red|purple|cyan, terminal: true?}}
transitions:
  - {name: ..., label: ..., from: [...], to: ..., roles: [...], require_comment: true?, style: ok|warn|danger?}
  - name: approve
    four_eyes: true                     # or four_eyes_if: {field: amount, gte: 500}
    approve_roles: [ops_lead]           # who may be the second person (never the proposer)
    set: {status_field: value}          # optional field writes, audited from/to
quick_filters: [{label: ..., params: {f_<field>: value, open: "1", pending: "1", risk_min: "70"}}]
fields:
  - {name: ..., label: ..., type: text|enum|number|money|date|score|bool|longtext, section: ..., list: true?,
     filter: true?, input: true?, required: true?, pii: true?, options: [...], colors: {...}, default: "@today+5"?}
```

### Non-negotiable control checklist
- [ ] Every money-moving, customer-impacting or alert-closing transition is `four_eyes` or `four_eyes_if`.
- [ ] Protective actions (hold, freeze, rollback) may be single-person but `require_comment: true`.
- [ ] Rejections/denials `require_comment: true`.
- [ ] PII fields have `pii: true`; `reveal_pii` and `export` are as narrow as possible.
- [ ] `view` is need-to-know. Engineers and admins don't see customer PII apps by default.
- [ ] Nothing is deletable; use terminal states. Don't add DELETE routes.
- [ ] No business logic in templates or routes: changes go through `app/engine/service.py` only.
- [ ] Retention: if the app stores customer data, add a `retention_until` date field + quick filter (see KYC).

## 3. Seed data: `app/seed/<key>.py`
- Fictional, deterministic (`random.Random(<fixed seed>)`), realistic mix of states.
- Create and move items **through `service.*`** with the seeded actors in `app/seed/actors.py`, so the audit
  trail and record hashes are real. Never `INSERT INTO items` directly.
- Register it in `seed_all()` in `app/seed/__init__.py`.

## 4. Tests: `tests/test_<key>.py`
Use the `seeded_app` / `sclient` fixtures and `login`/`post` helpers from `tests/conftest.py`. Minimum:
- proposer cannot approve their own four-eyes decision; the right `approve_roles` can
- each role outside `view` gets 403 on `/apps/<key>` (and it is audited as `access.denied`)
- read-only roles can't transition/comment/reveal
- `store.verify_records(conn) == []` and `verify_chain(conn).ok` after seeding
- any new engine extension: manifest validation fails fast on bad config

`tests/test_phase5.py::test_every_manifest_declares_desk_and_core_controls` will fail if the manifest has no
desk config or no four-eyes transition.

## 5. Verify
```bash
. .venv/bin/activate && python -m pytest -q
uvicorn --factory app.main:create_app --port 8000   # log in as each role and check the sidebar, desk tile, list, detail
```
Then add a row to `docs/DECISIONS.md` for any trade-off you made and to `README.md`'s controls table.

## Don'ts
- Don't add a role or a role bypass ("break-glass") without a decision record and four-eyes on its use.
- Don't call `record_event` outside a transaction, or change business data without an audit event in the same transaction.
- Don't add inline `<script>`/`style=` (CSP blocks them) or new JS frameworks; reuse `app/static/style.css` classes.
