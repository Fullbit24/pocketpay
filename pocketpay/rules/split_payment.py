# rules/split_payment.py — HS-1.1 split-payment ("smurfing") detector.
#
# The scam: a fraudster avoids the single-transfer size limits by breaking one
# larger payment into a run of individually small transfers to the same
# recipient, each one too small to trip a per-transaction cap. No single amount
# looks alarming; the pattern only shows up in the aggregate.
#
# The defence: keep a rolling total per (student -> recipient_id) pair over a
# fixed time window and, once that total has crossed the hold threshold, send the
# NEXT transfer to HOLD_FOR_CONFIRMATION even though its own amount is small.
#
# Schema assumption
# -----------------
# The transactions table has no dedicated "transfer created" timestamp; the only
# time column available is created_at (TEXT, sqlite datetime('now'), i.e.
# 'YYYY-MM-DD HH:MM:SS' in UTC). This rule therefore measures the rolling window
# against created_at. This is correct as long as created_at reflects when the
# transfer was recorded, which is how db.build() writes it. If a separate event
# timestamp is ever added, this is the query to repoint. db.py is owned
# elsewhere; this module does not modify the schema.
#
# All thresholds come from config/policy.yaml through the policy module:
#   split_payment.window_minutes     — width of the rolling window
#   split_payment.trigger_total      — rolling total at/above which the next
#                                      transfer holds
#   split_payment.min_counted_amount — transfers below this are ignored, so
#                                      ordinary canteen-size spending never
#                                      accumulates toward a hold
# Nothing is hard-coded.

from .. import guard
from .. import policy

CLEAR = guard.CLEAR
HOLD = guard.HOLD
ESCALATE = guard.ESCALATE

RULE_NAME = "R1f_split_payment"


def _rolling_total(con, student, recipient_id, window_minutes, min_counted_amount):
    """Sum amount_aed for prior transfers from `student` to `recipient_id` that
    fall inside the rolling window and are large enough to be counted.

    Reads the transactions table directly. Only rows at or above the policy's
    min_counted_amount contribute, so unrelated micro-payments (a canteen snack)
    are excluded from the aggregate. Returns (total, count)."""
    row = con.execute(
        "SELECT COALESCE(SUM(amount_aed), 0.0) AS total, COUNT(*) AS n "
        "FROM transactions "
        "WHERE student = ? "
        "  AND recipient_id = ? "
        "  AND amount_aed >= ? "
        "  AND created_at >= datetime('now', ?)",
        (student, recipient_id, min_counted_amount,
         f"-{window_minutes} minutes"),
    ).fetchone()
    # Row may be a sqlite3.Row or a plain tuple depending on the connection.
    try:
        return float(row["total"]), int(row["n"])
    except (TypeError, IndexError):
        return float(row[0]), int(row[1])


def evaluate(con, ctx, analysis, recipient, state=None):
    """Detect a split-payment run for the transaction in `ctx`.

    Returns None when the rule does not fire, otherwise a
    (ACTION, rule_name, reason) tuple matching guard.py's contract.

    con:       open SQLite connection (used read-only here).
    ctx:       transaction dict (student, recipient_id, amount_aed, ...).
    analysis:  LLM opinion dict or None; this rule does not consume it.
    recipient: directory dict or None; this rule does not require it.
    state:     optional caller-supplied state; unused by this rule.

    The rule is intentionally independent of the LLM: a split-payment run is a
    fact about transfer history, not a matter of opinion.
    """
    # Respect the policy switch. When split_payment is disabled (either in its
    # own section or absent from the rule order) the rule never fires.
    if not policy.current().rule_enabled("split_payment"):
        return None

    pol = policy.current()
    window_minutes = pol["split_payment.window_minutes"]
    trigger_total = pol["split_payment.trigger_total"]
    min_counted_amount = pol["split_payment.min_counted_amount"]

    student = ctx.get("student")
    recipient_id = ctx.get("recipient_id")
    if not student or not recipient_id:
        # Without a sender/recipient pair there is nothing to aggregate.
        return None

    # Aggregate prior transfers in the window. The current transfer is assumed
    # not yet committed to the transactions table (the agent triages before the
    # state write), so this total reflects the run that precedes it.
    prior_total, prior_count = _rolling_total(
        con, student, recipient_id, window_minutes, min_counted_amount)

    # The run must already have crossed the threshold. Because the total is
    # evaluated before this transfer is counted, the transfer being triaged is
    # the "next transfer" that goes to hold: five counted transfers of a size
    # that accumulate to at least the trigger total trip the sixth.
    if prior_total < trigger_total:
        return None

    return HOLD, RULE_NAME, (
        f"{prior_count} transfer(s) from {student} to {recipient_id} totalling "
        f"{prior_total:.2f} AED within {window_minutes} min have reached the "
        f"{trigger_total:.0f} AED hold threshold — this transfer holds for "
        f"confirmation despite its small individual amount"
    )


if __name__ == "__main__":
    # Self-test: five counted transfers to an unverified recipient inside the
    # window push the running total past the threshold, so the next small
    # transfer must return HOLD.
    import os
    import sqlite3
    import tempfile

    SCHEMA = (
        "CREATE TABLE transactions ("
        " txn_id TEXT PRIMARY KEY, student TEXT, recipient_id TEXT,"
        " amount_aed REAL, created_at TEXT DEFAULT (datetime('now')))"
    )

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    pol = policy.current()
    per_txn = 45.0          # the five small transfers in the spec's live test
    canteen = 15.0          # the canteen payment that must not count
    window = pol["split_payment.window_minutes"]
    trigger = pol["split_payment.trigger_total"]
    min_counted = pol["split_payment.min_counted_amount"]
    print(f"policy: window={window}min trigger_total={trigger} "
          f"min_counted_amount={min_counted}")

    # Five transfers of the counted size, all timestamped "now".
    for i in range(5):
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
            "VALUES (?,?,?,?)",
            (f"SP-{i}", "Rayan", "R-UNVER", per_txn))
    # A small canteen payment to a different recipient with the same sender;
    # it is below min_counted_amount and must not contribute.
    con.execute(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
        "VALUES (?,?,?,?)",
        ("SP-CANTEEN", "Rayan", "R-ALFALAH", canteen))
    con.commit()

    ctx = {"student": "Rayan", "recipient_id": "R-UNVER", "amount_aed": per_txn}
    result = evaluate(con, ctx, analysis=None, recipient={"tier": "unverified"})

    print("evaluate ->", result)
    assert result is not None, "split-payment run should fire"
    assert result[0] == HOLD, f"expected {HOLD}, got {result[0]}"
    assert result[1] == RULE_NAME, f"unexpected rule name: {result[1]}"
    print("PASS: five counted transfers -> next transfer HOLDs")

    # Control: with the canteen payment alone, nothing accumulates.
    con2 = sqlite3.connect(":memory:")
    con2.row_factory = sqlite3.Row
    con2.executescript(SCHEMA)
    con2.execute(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
        "VALUES (?,?,?,?)",
        ("C-1", "Rayan", "R-ALFALAH", canteen))
    con2.commit()
    quiet = evaluate(con2, {"student": "Rayan", "recipient_id": "R-ALFALAH",
                            "amount_aed": canteen}, None, {"tier": "verified_vendor"})
    print("canteen-only evaluate ->", quiet)
    assert quiet is None, "a lone sub-threshold canteen payment must not fire"
    print("PASS: canteen payment alone -> rule does not fire")
