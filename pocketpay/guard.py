#guard.py — THE CODE GUARD. Deterministic, exhaustive, boring. The LLM only ever
#produces an *opinion* (risk_score / confidence); this file turns opinions plus
#hard facts into one of three actions, and it is structurally impossible for it
#to permanently freeze a wallet: there is no BLOCK state, worst case is a human
#is asked. Every rule fires in order, first match wins, rule name is logged.

AUTO_CLEAR_LIMIT_AED = 300.0   # deterministic threshold, per the brief
UNCERTAIN_ESCALATE_AED = 150.0

CLEAR = "CLEAR"
HOLD = "PENDING_CONFIRMATION"
ESCALATE = "ESCALATED"


def decide(ctx, analysis, recipient, scam_link_hit=None, tool_failed=False):
    """Return (action, rule_name, reason). Pure function — no I/O, testable dead simple.

    ctx: transaction dict, analysis: LLM output dict (risk_score, confidence,
    explanation, indicators) or None on LLM failure, recipient: directory dict
    or None, scam_link_hit: known-scam-link row or None."""
    amount = ctx["amount_aed"]
    risk = analysis["risk_score"] if analysis else None
    conf = analysis["confidence"] if analysis else 0.0
    tier = recipient["tier"] if recipient else None
    trusted = tier in ("verified_vendor", "trusted_peer")
    reports = recipient["scam_reports"] if recipient else 0

    # R1 — confirmed fraud facts beat any LLM opinion
    if scam_link_hit:
        return ESCALATE, "R1_known_scam_link", \
            "message contains a URL already confirmed as a scam by this system"
    if reports >= 2:
        return ESCALATE, "R1b_repeat_offender", \
            f"recipient has {reports} confirmed scam reports on file"

    # R2 — never act decisively on weak information (flag, not guess).
    # Everything below this line consumes the LLM opinion, so it passes HERE.
    if analysis is None:
        return HOLD, "R2_llm_unavailable_fallback", \
            "analysis engine unavailable — holding for human review, wallet stays open"
    if conf < 0.5:
        return HOLD, "R2b_low_confidence", \
            f"agent confidence {conf:.2f} too low to decide — flagged for confirmation, not blocked"
    if risk is None:
        return HOLD, "R2c_missing_risk", "analysis produced no risk score — asking a human instead of guessing"
    risk = float(risk)

    # R1c — now, and only now, opinion-backed fraud rule (facts + confident opinion)
    if tier == "unverified" and risk >= 70:
        return ESCALATE, "R1c_unverified_high_risk", \
            "new/unverified account + high scam likelihood in message"

    # R3 — outage fallback: missing recipient data must not crash or block
    if tool_failed:
        return HOLD, "R3_outage_fallback", \
            "recipient directory timed out — holding, will retry, wallet stays open"

    # R4 — high risk with decent confidence -> staff review (not a freeze)
    if risk >= 70:
        return ESCALATE, "R4_high_risk", "clear scam pattern with sufficient confidence"

    # R5 — uncertain mid-risk on unverified money -> escalate uncertain to staff
    if 30 <= risk < 70 and tier == "unverified" and amount > UNCERTAIN_ESCALATE_AED:
        return ESCALATE, "R5_uncertain_value", \
            f"mid-risk signal ({risk}) on unverified recipient for {amount:.0f} AED — uncertain, staff decides"

    # R6 — the happy path: trusted party, small-ish money, quiet message
    if trusted and risk < 30 and amount <= AUTO_CLEAR_LIMIT_AED:
        return CLEAR, "R6_trusted_small", \
            f"{tier}, {amount:.0f} AED under the {AUTO_CLEAR_LIMIT_AED:.0f} AED auto-clear cap"

    # R7 — everything else: a human answers, once, statefully. Wallet never frozen.
    return HOLD, "R7_default_hold", \
        "no rule clears this automatically — asking the student to confirm the seller"
