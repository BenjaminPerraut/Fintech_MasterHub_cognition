"""14 fictional feature flags across the lifecycle: drafts, live votes, pending rollouts, a rollback."""
from __future__ import annotations

from ..engine import service, store
from ..engine.manifest import Manifest
from .actors import ADA, ERIN, ETHAN, OLIVIA, OMAR

FLAGS = [
    ("instant-sepa-payouts", "Instant SEPA payouts for refunds", "payments", "backoffice", "production", "high", 80, "All EU customers", "canary_pending_promote"),
    ("kyc-auto-approve-low-risk", "Auto-approve low-risk KYC (score < 15)", "kyc", "backoffice", "production", "high", 70, "Individuals, EU", "voting_quorum"),
    ("card-freeze-toggle-v2", "Card freeze toggle v2", "cards", "mobile_app", "production", "medium", 55, "iOS + Android", "live"),
    ("new-onboarding-flow", "Onboarding flow redesign", "growth", "mobile_app", "production", "medium", 60, "New signups", "canary"),
    ("fx-rates-provider-b", "Switch FX rate provider to vendor B", "ledger", "api", "production", "high", 85, "All FX conversions", "rolled_back"),
    ("dark-mode-web", "Dark mode on web app", "growth", "web_app", "production", "low", 15, "All web users", "live"),
    ("refund-reason-suggest", "Suggest refund reason from chat transcript", "payments", "backoffice", "staging", "low", 20, "Support agents", "voting"),
    ("velocity-rule-v3", "Fraud velocity rule v3 (5 tx/min)", "payments", "api", "production", "high", 90, "All card payments", "ready_pending_canary"),
    ("pdf-statements-v2", "PDF statements v2", "platform", "web_app", "production", "low", 25, "All customers", "ready"),
    ("crypto-onramp", "Crypto on-ramp (beta)", "growth", "mobile_app", "production", "high", 75, "Invite-only", "voting_split"),
    ("ledger-double-entry-checks", "Ledger double-entry nightly checks", "ledger", "ledger", "production", "medium", 40, "Internal", "live"),
    ("support-macro-library", "Support macro library", "platform", "backoffice", "staging", "low", 10, "Support team", "draft"),
    ("biometric-login-android", "Biometric login on Android", "cards", "mobile_app", "staging", "medium", 45, "Android 12+", "draft"),
    ("legacy-sms-otp", "Legacy SMS OTP fallback", "platform", "api", "production", "medium", 50, "All customers", "shelved"),
]
UP = "Reviewed design doc and rollback plan. LGTM."
DOWN = "Missing load-test results and a compliance sign-off."


def seed(conn, m: Manifest) -> None:
    for i, (key, title, team, surface, env, risk, impact, segment, target) in enumerate(FLAGS):
        creator, peer = (ERIN, ETHAN) if i % 2 == 0 else (ETHAN, ERIN)
        voters = [peer, OLIVIA, ADA, OMAR]
        item = service.create(conn, m, creator, {
            "flag_key": key, "title": title, "description": f"{title}. Guarded by a kill switch; rollback plan in {f'ENG-{1200 + i}'}.",
            "owner_team": team, "surface": surface, "environment": env, "target_segment": segment, "risk": risk,
            "customer_impact": impact, "ticket": f"ENG-{1200 + i}"})
        if target == "draft":
            continue
        service.transition(conn, m, creator, item.id, "open_vote")
        ballots = {"voting": ["up"], "voting_split": ["up", "down", "up", "down"], "shelved": ["up", "down", "down"]}
        for voter, v in zip(voters, ballots.get(target, ["up", "up", "up"])):
            service.vote(conn, m, voter, item.id, v, UP if v == "up" else DOWN)
        if target.startswith("voting"):
            continue
        if target == "shelved":
            service.transition(conn, m, OLIVIA, item.id, "shelve", "Replaced by passkeys project; SMS OTP is being phased out.")
            continue
        service.transition(conn, m, creator, item.id, "accept")
        if target == "ready":
            continue
        service.transition(conn, m, creator, item.id, "start_canary", "Canary on 10% with error-rate alert at 0.5%.")
        if target == "ready_pending_canary":
            continue
        service.approve(conn, m, peer, item.id, store.open_decision(conn, item.id)["id"], "Dashboards and alerts checked.")
        if target == "canary":
            continue
        if target == "rolled_back":
            service.transition(conn, m, OMAR, item.id, "rollback", "FX conversion errors up 4x on canary. Kill switch pulled.")
            continue
        service.transition(conn, m, creator, item.id, "promote", "Canary clean for 72h: error rate and latency flat.")
        if target == "canary_pending_promote":
            continue
        service.approve(conn, m, OMAR, item.id, store.open_decision(conn, item.id)["id"], "Canary metrics reviewed. Go.")
