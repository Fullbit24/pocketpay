#tests.py — fast, deterministic, no LLM needed. Run: ~/.venv/bin/python -m pocketpay.tests
from . import guard
from .agent import _parse_analysis

CTX = {"txn_id": "T", "student": "S", "amount_aed": 50.0, "purpose": "p", "message": "m"}
TRUSTED = {"tier": "verified_vendor", "scam_reports": 0}
UNVER = {"tier": "unverified", "scam_reports": 0}
A = lambda r, c: {"risk_score": r, "confidence": c, "indicators": [], "explanation": "x"}


def check(name, got, want):
    assert got == want, f"{name}: got {got}, want {want}"
    print(f"  ok  {name}")


def run():
    # --- guard rules ---
    check("R1 known scam link beats everything",
          guard.decide(CTX, A(0, 1.0), TRUSTED, scam_link_hit={"url": "u"})[0], "ESCALATED")
    check("R6 trusted small clears",
          guard.decide(CTX, A(5, 0.9), TRUSTED)[0], "CLEAR")
    check("R6 amount cap: 301 not auto-cleared",
          guard.decide({**CTX, "amount_aed": 301}, A(5, 0.9), TRUSTED)[0], "PENDING_CONFIRMATION")
    check("R1c unverified + high risk escalates",
          guard.decide(CTX, A(85, 0.9), UNVER)[0], "ESCALATED")
    check("R5 uncertain mid-risk big money escalates",
          guard.decide({**CTX, "amount_aed": 250}, A(55, 0.8), UNVER)[0], "ESCALATED")
    check("R2 no analysis -> hold (never block)",
          guard.decide(CTX, None, UNVER)[0], "PENDING_CONFIRMATION")
    check("R2b low confidence -> hold",
          guard.decide(CTX, A(80, 0.3), UNVER)[0], "PENDING_CONFIRMATION")
    check("R3 outage -> hold",
          guard.decide(CTX, A(10, 0.9), None, tool_failed=True)[0], "PENDING_CONFIRMATION")
    check("guard NEVER returns a block/freeze action",
          all(guard.decide({**CTX, "amount_aed": a}, a_, r_)[0]
              in ("CLEAR", "PENDING_CONFIRMATION", "ESCALATED")
              for a in (0, 50, 5000) for a_ in (None, A(0, 1), A(100, 1), A(50, 0.2))
              for r_ in (TRUSTED, UNVER, None)),
          True)

    # --- parser healing (real observed failure: stream cut before final '}') ---
    broken = ('{"risk_score": 98, "confidence": 0.99, "indicators": ["urgency"], '
              '"explanation": "phishing, do not click"}')  # missing }
    p = _parse_analysis("```json\n" + broken + "\n```")
    assert p, "truncated json should heal"
    check("parse fenced + truncated json", (p["risk_score"], p["confidence"]), (98, 0.99))
    p2 = _parse_analysis('sure! {"risk_score": 10, "confidence": 0.8, "indicators": [], "explanation": "fine"} trust me')
    assert p2, "prefixed json should parse"
    check("parse with prose prefix", p2["risk_score"], 10)
    check("parse garbage -> None", _parse_analysis("I cannot answer that"), None)
    print("all tests passed ✨")


if __name__ == "__main__":
    run()
