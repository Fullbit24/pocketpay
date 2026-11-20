#tools.py — the three mock Python services the brief asks for, plus a scam-link
#feed. These are DUMB on purpose: no intelligence here, just facts and state
#writes. All the judgement is split between the LLM (analysis) and guard.py
#(decisions). Tools can time out; callers must handle ToolTimeout gracefully.

import time


class ToolTimeout(Exception):
    pass


_TIMEOUT_S = 4.0


def _sleep_with_outage_check(con, recipient_row):
    if recipient_row["simulate_outage"]:
        # mock service degraded: burn the timeout budget then bail
        time.sleep(min(0.4, _TIMEOUT_S))
        raise ToolTimeout(f"recipient service for {recipient_row['recipient_id']} timed out")


def get_transaction_context(con, txn_id):
    """Fetch the flagged payment: student, amount, purpose, message, current state."""
    row = con.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such transaction: {txn_id}")
    return dict(row)


def verify_campus_recipient(con, recipient_id, _retry=True):
    """Campus directory lookup: tier, account age, scam reports, activity.
    Raises ToolTimeout if the mock directory service is down."""
    row = con.execute("SELECT * FROM recipients WHERE recipient_id=?",
                      (recipient_id,)).fetchone()
    if row is None:
        raise KeyError(f"recipient not in directory: {recipient_id}")
    _sleep_with_outage_check(con, row)
    return dict(row)


def check_scam_link(con, message):
    """Cheap deterministic check: does this message contain a URL we've already
    confirmed as a scam (from a prior escalation/broadcast)? Returns row or None."""
    import re
    urls = re.findall(r"https?://\S+", message or "")
    for u in urls:
        hit = con.execute("SELECT * FROM scam_links WHERE url=?", (u.rstrip('.,)!'),)).fetchone()
        if hit:
            return dict(hit)
    return None


def register_scam_link(con, message, source_txn, broadcast=True):
    """Confirm a phishing URL and (stretch goal) auto-broadcast a warning to
    every other student so the same link gets caught instantly next time."""
    import re
    urls = [u.rstrip('.,)!') for u in re.findall(r"https?://\S+", message or "")]
    added = []
    for u in urls:
        cur = con.execute(
            "INSERT OR IGNORE INTO scam_links (url, source_txn, broadcast) VALUES (?,?,?)",
            (u, source_txn, 1 if broadcast else 0))
        if cur.rowcount:
            added.append(u)
    if added and broadcast:
        con.execute(
            "INSERT INTO broadcast_log (message) VALUES (?)",
            (f"⚠ SCAM ALERT — new phishing link confirmed: {', '.join(added)} "
             f"(source case {source_txn}). Do NOT click or transfer.",))
    con.commit()
    return added


def update_payment_state(con, txn_id, new_state, risk=None, confidence=None,
                         decision=None, explanation=None):
    """THE only writer of transaction.state. State machine is enforced here —
    code, not the LLM, owns money movement."""
    row = con.execute("SELECT state FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such transaction: {txn_id}")
    legal = {
        "RECEIVED": {"TRIAGED", "PENDING_CONFIRMATION", "PENDING_GUARDIAN", "ESCALATED", "CLEAR"},
        "TRIAGED": {"CLEAR", "PENDING_CONFIRMATION", "PENDING_GUARDIAN", "ESCALATED"},
        "PENDING_CONFIRMATION": {"CLEARED", "CANCELLED", "ESCALATED",
                                 "CONFIRMED_CANCELLABLE", "PENDING_GUARDIAN"},  # student confirmed; window opens
        # A confirmation is reversible only through the cancel window. The ticket
        # may resolve to CANCELLED (taken back) or RELEASED (window elapsed, money
        # moves) and to nothing else; every other transition out of this state is
        # illegal and raises below.
        "CONFIRMED_CANCELLABLE": {"CANCELLED", "RELEASED"},
        # A transfer awaiting a guardian is decided by the second approver: the
        # guardian approves (CLEARED), declines (CANCELLED), or the hold lapses
        # (RELEASED). Nothing else moves it.
        "PENDING_GUARDIAN": {"CLEARED", "CANCELLED", "RELEASED"},
        "CLEAR": set(), "CLEARED": set(), "CANCELLED": set(), "RELEASED": set(),
        "ESCALATED": {"PENDING_CONFIRMATION", "CANCELLED"},  # staff may downgrade
    }
    if new_state not in legal.get(row["state"], set()):
        raise ValueError(f"illegal transition {row['state']} -> {new_state} for {txn_id}")
    con.execute(
        "UPDATE transactions SET state=?, risk_score=COALESCE(?, risk_score), "
        "confidence=COALESCE(?, confidence), decision=COALESCE(?, decision), "
        "explanation=COALESCE(?, explanation), updated_at=datetime('now') WHERE txn_id=?",
        (new_state, risk, confidence, decision, explanation, txn_id))
    con.commit()
    return new_state
