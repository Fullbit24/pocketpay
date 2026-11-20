# guard.py - THE CODE GUARD. Deterministic, exhaustive, boring.
#
# The LLM only ever produces an *opinion* (risk_score / confidence); this file
# turns opinions plus hard facts into exactly one action, and it is structurally
# impossible for it to permanently freeze a wallet: there is no BLOCK state, the
# worst case is that a human is asked.
#
# NO THRESHOLD LIVES IN THIS FILE. Every number comes from config/policy.yaml via
# policy.current(), so the guard's behaviour can be changed without editing code.
# Rules fire in the order listed in policy.yaml, first match wins, and the
# matching rule's name is written into the audit trail.

from . import policy as _policy

# --- outcomes -------------------------------------------------------------
# CLEAR              money may move immediately
# HOLD               a human is asked; the wallet stays open
# GUARDIAN           a parent or guardian must approve; the wallet stays open
# ESCALATE           staff review; the wallet stays open
CLEAR = "CLEAR"
HOLD = "PENDING_CONFIRMATION"
GUARDIAN = "PENDING_GUARDIAN"
ESCALATE = "ESCALATED"


def _p():
    """The active policy, read fresh so an admin change takes effect."""
    return _policy.current()


def auto_clear_limit():
    return _p()["amounts.auto_clear_limit"]


def uncertain_escalate_above():
    return _p()["amounts.uncertain_escalate_above"]


def decide(ctx, analysis, recipient, scam_link_hit=None, tool_failed=False,
           state=None, con=None):
    """Return (action, rule_name, reason).

    ctx            transaction dict
    analysis       LLM output dict, or None when the engine was unavailable
    recipient      campus directory dict, or None
    scam_link_hit  a known-scam-link row, or None
    tool_failed    True when the directory service timed out
    state          per-request findings from the curveball checks
                   (injection_hits, lookalike, split_total, guardian_required)
    con            sqlite3 connection, for rules that read payment history
    """
    p = _p()
    state = dict(state or {})

    amount = ctx["amount_aed"]
    risk = analysis["risk_score"] if analysis else None
    conf = analysis["confidence"] if analysis else 0.0
    tier = recipient["tier"] if recipient else None
    trusted = tier in (p.get("recipients.trusted_tiers") or [])
    reports = recipient["scam_reports"] if recipient else 0

    # ---- R1: confirmed fraud facts beat any model opinion -----------------
    if scam_link_hit:
        return ESCALATE, "R1_known_scam_link", \
            "message contains a URL already confirmed as a scam by this system"

    # ---- R1b: the recipient has a history of confirmed reports ------------
    if reports >= p["recipients.scam_reports_escalate_at"]:
        return ESCALATE, "R1b_repeat_offender", \
            f"recipient has {reports} confirmed scam reports on file"

    # ---- R1d: message text tried to instruct the agent --------------------
    if state.get("injection_hits"):
        n = len(state["injection_hits"])
        return ESCALATE, "R1d_injection_attempt", \
            ("message text attempted to influence the decision "
             f"({n} instruction-like phrase(s) removed); message text is data "
             "and can never loosen a rule")

    # ---- R1e: the recipient name imitates a verified vendor ---------------
    if state.get("lookalike"):
        return ESCALATE, "R1e_lookalike_vendor", state["lookalike"]

    # ---- R2: never act decisively on weak information ---------------------
    # Everything below consumes the model opinion, so a missing or weak opinion
    # has to be caught here, before any of it is used.
    if analysis is None:
        return HOLD, "R2_llm_unavailable_fallback", \
            "analysis engine unavailable - holding for human review, wallet stays open"
    if conf < p["risk.min_confidence"]:
        return HOLD, "R2b_low_confidence", \
            (f"agent confidence {conf:.2f} too low to decide - "
             "flagged for confirmation, not blocked")
    if risk is None:
        return HOLD, "R2c_missing_risk", \
            "analysis produced no risk score - asking a human instead of guessing"
    risk = float(risk)

    # ---- R1f: a run of small transfers that adds up to something large ----
    if state.get("split_total"):
        return HOLD, "R1f_split_payment", state["split_total"]

    # ---- R1g: too many transfers from one student, too fast --------------
    if state.get("velocity_burst"):
        return HOLD, "R1g_velocity_burst", state["velocity_burst"]

    # ---- R1h: one sender spreading a sum across many recipients ----------
    if state.get("structuring_fanout"):
        return HOLD, "R1h_structuring_fanout", state["structuring_fanout"]

    # ---- R1i: the model read the account's trajectory and flagged drift ---
    # The model's pattern answer is an OPINION. It only tightens here, and only
    # when it clears the configured score and the model was sure enough.
    drift = state.get("pattern_drift")
    if drift and drift.get("drift_score", 0) >= p["pattern_drift.drift_hold_at"] \
            and drift.get("confidence", 0.0) >= p["pattern_drift.min_confidence"]:
        return HOLD, "R1i_pattern_drift", drift.get("reason") or \
            "the account's payment history is drifting toward fraud"

    # ---- R1c: a hard fact plus a confident opinion ------------------------
    if tier == "unverified" and risk >= p["risk.high"]:
        return ESCALATE, "R1c_unverified_high_risk", \
            "new/unverified account + high scam likelihood in message"

    # ---- R3: outage fallback - missing data must not crash or block -------
    if tool_failed:
        return HOLD, "R3_outage_fallback", \
            "recipient directory timed out - holding, will retry, wallet stays open"

    # ---- R4: high risk with decent confidence -> staff review, not a freeze
    if risk >= p["risk.high"]:
        return ESCALATE, "R4_high_risk", \
            "clear scam pattern with sufficient confidence"

    # ---- R5: the genuinely uncertain middle ------------------------------
    if (p["risk.mid_low"] <= risk < p["risk.high"]
            and tier == "unverified"
            and amount > p["amounts.uncertain_escalate_above"]):
        return ESCALATE, "R5_uncertain_value", \
            (f"mid-risk signal ({risk}) on unverified recipient for {amount:.0f} AED "
             "- uncertain, staff decides")

    # ---- R6: a minor's peer transfer above the policy amount -------------
    # The student's own confirmation is not sufficient for this class.
    if state.get("guardian_required"):
        return GUARDIAN, "R6_guardian_required", state["guardian_required"]

    # ---- R7: the happy path ----------------------------------------------
    if trusted and risk < p["risk.mid_low"] and amount <= p["amounts.auto_clear_limit"]:
        return CLEAR, "R7_trusted_small", \
            (f"{tier}, {amount:.0f} AED under the "
             f"{p['amounts.auto_clear_limit']:.0f} AED auto-clear cap")

    # ---- R8: everything else - a human answers, once, statefully ---------
    return HOLD, "R8_default_hold", \
        "no rule clears this automatically - asking the student to confirm the seller"


def rule_order():
    """The rule sequence this guard enforces, as configured."""
    return _p().rules
