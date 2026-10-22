"""Detection rules page: visible config, scan history, and audited 'run scan' / 'simulate traffic' actions."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..deps import User, client_ip, get_db, require_perm
from ..engine import service
from ..web import render
from . import engine

router = APIRouter(prefix="/rules")


def _actor(user: User, request: Request) -> service.Actor:
    return service.Actor(user.subject, user.email, user.name, user.role, client_ip(request))


@router.get("")
def rules_page(request: Request, user: User = Depends(require_perm("rules:view")), db=Depends(get_db)):
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("payments", "transfers")}
    by_rule = db.execute("SELECT app, json_extract(data, '$.rule') AS rule, COUNT(*) AS n,"
                         " SUM(CASE WHEN state IN ('new','investigating','escalated','on_hold') THEN 1 ELSE 0 END) AS open"
                         " FROM items WHERE app IN ('fraud','transfers') GROUP BY app, rule ORDER BY app, rule").fetchall()
    scans = [dict(r, details=json.loads(r["details"])) for r in db.execute(
        "SELECT * FROM audit_log WHERE action IN ('rules.scanned','rules.simulated') ORDER BY id DESC LIMIT 10")]
    return render(request, "rules.html", {"config": engine.config_text(), "counts": counts, "by_rule": by_rule,
                                          "scans": scans, "sims": engine.SIM_KINDS,
                                          "can_run": user.can("rules:run")}, active="rules")


@router.post("/scan")
def run_scan(request: Request, user: User = Depends(require_perm("rules:run")), db=Depends(get_db)):
    created = engine.scan(db, request.app.state.manifests, _actor(user, request))
    request.session["flash"] = ["ok", f"Scan complete: {created.get('fraud', 0)} new payment alerts, "
                                      f"{created.get('transfers', 0)} new transfer alerts."]
    return RedirectResponse("/rules", status_code=303)


@router.post("/simulate")
async def run_simulation(request: Request, user: User = Depends(require_perm("rules:run")), db=Depends(get_db)):
    kind = str((await request.form()).get("kind", ""))
    if kind not in engine.SIM_KINDS:
        raise HTTPException(400, "unknown simulation")
    actor = _actor(user, request)
    cid = engine.simulate(db, kind, actor)
    created = engine.scan(db, request.app.state.manifests, actor)
    request.session["flash"] = ["ok", f"Injected sample traffic for {cid}; scan created {sum(created.values())} alert(s)."]
    return RedirectResponse("/rules", status_code=303)
