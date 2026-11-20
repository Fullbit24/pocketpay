#agent.py — the autonomous triage loop. Chain per transaction:
#  1. get_transaction_context()            [tool: facts]
#  2. LLM analyses the chat message        [brain: opinion only]
#  3. verify_campus_recipient()            [tool: facts, may time out]
#  4. guard.decide(...)                    [code: the ONLY decider]
#  5. update_payment_state()               [tool: the ONLY state writer]
# Every step lands in the decision log. Pending confirmations are stateful:
# re-running the agent on a PENDING txn does NOT re-ask or re-loop (loop guard).

import json
import re

from capri.openai_lib import ModelHouse
from . import db, guard, tools

SYSTEM = (
    "You are PocketPay's fraud-triage analyst for a university campus wallet. "
    "Read a student chat message about a payment and judge scam risk. "
    "Consider: manufactured urgency, scarcity pressure, too-good prices, "
    "off-platform or suspicious links, credential/password requests, "
    "unverifiable sellers, requests to redirect money to third parties. "
    "If the message is ordinary and friendly with none of these, risk is near 0. "
    "If the message is ambiguous or you cannot tell, say so with LOW confidence "
    "instead of guessing. Respond with ONLY a JSON object, no other text:\n"
    '{"risk_score": <integer 0-100>, "confidence": <float 0-1>, '
    '"indicators": ["short phrase", ...], "explanation": "<one friendly sentence for the student>"'
    "}"
)


def _parse_analysis(raw):
    """Robust JSON extraction — a small local model WILL sometimes wrap its answer,
    and sometimes the stream ends before the final '}'. Repair, don't give up."""
    raw = raw or ""
    start = raw.find("{")
    if start == -1:
        return None
    blob = raw[start:]
    candidates = [blob]
    # heal truncated/cut streams: close any dangling quote + unbalanced braces
    open_braces = blob.count("{") - blob.count("}")
    if open_braces > 0:
        healed = blob + ('"' if blob.count('"') % 2 else "") + "}" * open_braces
        candidates.append(healed)
    for cand in candidates:
        # trim trailing junk after the first balanced object
        depth, end = 0, -1
        for i, ch in enumerate(cand):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        text = cand[:end + 1] if end != -1 else cand
        try:
            d = json.loads(text)
            risk = max(0, min(100, int(float(d["risk_score"]))))
            conf = max(0.0, min(1.0, float(d["confidence"])))
            return {"risk_score": risk, "confidence": conf,
                    "indicators": list(d.get("indicators") or []),
                    "explanation": str(d.get("explanation") or "")}
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    # last resort: pull the two numbers + explanation out by regex
    m_r = re.search(r'"risk_score"\s*:\s*(\d+)', blob)
    m_c = re.search(r'"confidence"\s*:\s*([\d.]+)', blob)
    m_e = re.search(r'"explanation"\s*:\s*"([^"]+)', blob)
    if m_r and m_c:
        return {"risk_score": max(0, min(100, int(m_r.group(1)))),
                "confidence": max(0.0, min(1.0, float(m_c.group(1)))),
                "indicators": [], "explanation": m_e.group(1) if m_e else ""}
    return None


class PocketPayAgent:
    def __init__(self, con=None, llm=None):
        self.con = con or db.connect()
        self.llm = llm  # lazy: pass None to use Ray's local ModelHouse

    def _analyze(self, ctx):
        """LLM brain call. Returns analysis dict or None if the brain is unavailable."""
        prompt = (f"Payment: {ctx['amount_aed']} AED from student {ctx['student']} "
                  f"to recipient {ctx['recipient_id']} for \"{ctx['purpose']}\".\n"
                  f"Chat message:\n\"\"\"\n{ctx['message']}\n\"\"\"")
        try:
            if self.llm is None:
                mh = ModelHouse()
                raw = "".join(mh.send(sid=1, messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt}], rea="low"))
            else:
                raw = self.llm(prompt)
            analysis = _parse_analysis(raw)
            if analysis is None:
                db.log_decision(self.con, ctx["txn_id"], "llm", "parse_failed",
                                f"brain replied unparsable JSON (raw[:120]={raw[:120]!r}) — guard treats as no analysis")
            return analysis
        except Exception as e:
            db.log_decision(self.con, ctx["txn_id"], "llm", "brain_unavailable",
                            f"analysis engine failed ({type(e).__name__}) — guard falls back to HOLD")
            return None

    def triage(self, txn_id):
        """Full autonomous chain for one flagged transaction. Returns
        (action, rule, explanation). Safe to call repeatedly — stateful."""
        con = self.con

        # LOOP GUARD: a txn awaiting human/staff/guardian answer is never re-triaged.
        cur_state = con.execute("SELECT state FROM transactions WHERE txn_id=?",
                                (txn_id,)).fetchone()
        if cur_state and cur_state["state"] in ("PENDING_CONFIRMATION", "PENDING_GUARDIAN",
                                                "ESCALATED", "CLEAR", "CLEARED",
                                                "CANCELLED", "CONFIRMED_CANCELLABLE"):
            db.log_decision(con, txn_id, "agent", "loop_guard",
                            f"state is already {cur_state['state']} - not re-asking, not re-deciding")
            row = con.execute("SELECT decision, explanation FROM transactions WHERE txn_id=?",
                              (txn_id,)).fetchone()
            return cur_state["state"], "loop_guard", row["explanation"]

        # the curveball checks run as independent services, each reading its
        # thresholds from config/policy.yaml. A disabled check returns nothing
        # and the chain carries on - none of them is required for the system
        # to make a decision.
        checks = {}

        # 1. facts
        ctx = tools.get_transaction_context(con, txn_id)
        db.log_decision(con, txn_id, "tool", "get_transaction_context",
                        f"{ctx['amount_aed']} AED | {ctx['student']} -> {ctx['recipient_id']} | {ctx['purpose']}")

        # 1b. free deterministic win before we spend any brain: known scam link?
        link_hit = tools.check_scam_link(con, ctx["message"])
        if link_hit:
            db.log_decision(con, txn_id, "tool", "check_scam_link",
                            f"URL {link_hit['url']} already confirmed scam (first seen {link_hit['first_seen']})")

        # 1c. message text is DATA. Instruction-like phrasing is removed before
        # the model ever sees it, and any hit is recorded so the guard escalates.
        clean_message = ctx["message"]
        try:
            from .rules import injection_attempt as _inj
            clean_message, inj_hits = _inj.sanitize(ctx["message"])
            if inj_hits:
                checks["injection_hits"] = inj_hits
                db.log_decision(con, txn_id, "tool", "sanitize_message",
                                f"removed {len(inj_hits)} instruction-like phrase(s) "
                                f"before analysis: {inj_hits}")
        except Exception as e:
            db.log_decision(con, txn_id, "tool", "sanitize_message_skipped",
                            f"{type(e).__name__}: {e}")

        # 1d. a run of small transfers to one recipient, totalled in Python
        try:
            from .rules import split_payment as _split
            hit = _split.evaluate(con, ctx, analysis=None, recipient=None, state=checks)
            if hit:
                checks["split_total"] = hit[2]
                db.log_decision(con, txn_id, "tool", "rolling_total",
                                f"pair total crossed the policy threshold - {hit[2]}")
        except Exception as e:
            db.log_decision(con, txn_id, "tool", "rolling_total_skipped",
                            f"{type(e).__name__}: {e}")

        # 2. brain (opinion only - it cannot block anyone)
        analysis = self._analyze({**ctx, "message": clean_message})
        if analysis:
            db.log_decision(con, txn_id, "llm", "analyze_message",
                            f"risk={analysis['risk_score']}/100 conf={analysis['confidence']:.2f} "
                            f"indicators={analysis['indicators'] or 'none'} :: {analysis['explanation']}")

        # 3. directory (may time out - handled, never crashes the pipeline)
        tool_failed = False
        try:
            recipient = tools.verify_campus_recipient(con, ctx["recipient_id"])
            db.log_decision(con, txn_id, "tool", "verify_campus_recipient",
                            f"{recipient['display_name']}: tier={recipient['tier']} "
                            f"age={recipient['created_days_ago']}d reports={recipient['scam_reports']} "
                            f"txns90d={recipient['txns_90d']}")
        except tools.ToolTimeout as e:
            recipient, tool_failed = None, True
            db.log_decision(con, txn_id, "tool", "verify_campus_recipient_TIMEOUT",
                            f"{e} - falling back, no crash")

        # 3b. does the recipient name imitate a verified vendor?
        if recipient:
            try:
                from .rules import lookalike_vendor as _look
                hit = _look.evaluate(con, ctx, analysis, recipient, state=checks)
                if hit:
                    checks["lookalike"] = hit[2]
                    db.log_decision(con, txn_id, "tool", "lookalike_check", hit[2])
            except Exception as e:
                db.log_decision(con, txn_id, "tool", "lookalike_check_skipped",
                                f"{type(e).__name__}: {e}")

        # 3c. does this payment need a guardian? read the student's age from the
        # dataset - never assumed, never precomputed.
        try:
            from .rules import guardian_required as _guard
            needed, why = _guard.requires_guardian(ctx, recipient)
            if needed:
                checks["guardian_required"] = why
                db.log_decision(con, txn_id, "tool", "guardian_check", why)
        except Exception as e:
            db.log_decision(con, txn_id, "tool", "guardian_check_skipped",
                            f"{type(e).__name__}: {e}")

        # 4. THE GUARD - deterministic code owns the decision
        action, rule, reason = guard.decide(ctx, analysis, recipient,
                                            scam_link_hit=link_hit, tool_failed=tool_failed,
                                            state=checks, con=con)
        expl = (analysis["explanation"] if analysis else "")
        db.log_decision(con, txn_id, "guard", f"decide -> {action}", f"rule {rule}: {reason}")

        # 5. state write (single legal writer)
        tools.update_payment_state(con, txn_id, action,
                                   risk=analysis["risk_score"] if analysis else None,
                                   confidence=analysis["confidence"] if analysis else None,
                                   decision=f"{action} ({rule})",
                                   explanation=expl or reason)
        db.log_decision(con, txn_id, "system", "state_updated", f"txn {txn_id} -> {action}")

        # stretch goal: confirmed phishing -> register link + broadcast campus-wide warning
        if action == guard.ESCALATE:
            added = tools.register_scam_link(con, ctx["message"], txn_id)
            if added:
                db.log_decision(con, txn_id, "system", "broadcast_alert",
                                f"warned campus feed about: {', '.join(added)}")
        return action, rule, expl or reason

    def resolve(self, txn_id, human_choice, who="student"):
        """Human answers a pending transaction once.

        The human's word is the only input; the code decides the transition.
        A confirmation does not release money immediately - it opens the cancel
        window, so a student who answers in error can still take it back.
        """
        choice = human_choice.strip().lower()
        row = self.con.execute("SELECT state FROM transactions WHERE txn_id=?",
                               (txn_id,)).fetchone()
        cur = row["state"] if row else None

        if choice in ("confirm", "c", "yes", "i know this seller"):
            # a guardian approval is a different approver, and it clears outright
            if cur == "PENDING_GUARDIAN":
                new = "CLEARED"
                act = "guardian_response"
            elif cur == "PENDING_CONFIRMATION":
                try:
                    from .rules import cancel_window as _cw
                    return _cw.confirm(self.con, txn_id)
                except Exception:
                    new = "CLEARED"
                    act = f"{who}_response"
            else:
                new = "CLEARED"
                act = f"{who}_response"
        elif choice in ("cancel", "x", "no", "report"):
            if cur == "CONFIRMED_CANCELLABLE":
                try:
                    from .rules import cancel_window as _cw
                    return _cw.cancel_or_release(self.con, txn_id, "cancel")
                except Exception:
                    new = "CANCELLED"
                    act = f"{who}_response"
            else:
                new = "CANCELLED"
                act = f"{who}_response"
        else:
            raise ValueError("answer 'confirm' or 'cancel'")

        db.log_decision(self.con, txn_id, "human", act, f"chose {choice} -> {new}")
        tools.update_payment_state(self.con, txn_id, new)
        db.log_decision(self.con, txn_id, "system", "state_updated", f"txn {txn_id} -> {new}")
        return new
