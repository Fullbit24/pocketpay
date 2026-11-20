# rules/velocity_burst.py — R1g velocity-burst detector.
#
# The scam: a fraudster floods a recipient (or a run of recipients) with many
# transfers in a very short window. Each transfer is individually tiny — a burst
# of 20 AED payments sails under every per-transaction cap — so no single amount
# and no pair-total ever looks alarming. The pattern only shows up in the COUNT:
# too many transfers leaving one student inside a short window.
#
# The defence: count the student's recent transfers of a counted size inside a
# fixed time window and, once that count AND the amount sent have both crossed
# the hold thresholds, send the NEXT transfer to HOLD_FOR_CONFIRMATION even
# though its own amount is small.
#
# Schema assumption
# -----------------
# The transactions table has no dedicated "transfer created" timestamp; the only
# time column available is created_at (TEXT, sqlite datetime('now'), i.e.
# 'YYYY-MM-DD HH:MM:SS' in UTC). This rule therefore measures the window against
# created_at, which is correct as long as created_at reflects when the transfer
# was recorded, and that is how db.build() writes it. If a separate event
# timestamp is ever added, this is the query to repoint. db.py is owned
# elsewhere; this module does not modify the schema.
#
# All thresholds come from config/policy.yaml through the policy module:
#   velocity_burst.window_seconds     — width of the rolling window
#   velocity_burst.trigger_count      — counted transfers at/above which the
#                                       next transfer holds
#   velocity_burst.trigger_total      — amount sent at/above which the next
#                                       transfer holds (both must trip)
#   velocity_burst.min_counted_amount — transfers below this are ignored, so
#                                       ordinary canteen-size spending never
#                                       accumulates toward a hold
# Nothing is hard-coded.

from .. import guard
from .. import policy

CLEAR = guard.CLEAR
HOLD = guard.HOLD
ESCALATE = guard.ESCALATE

RULE_NAME = "R1g_velocity_burst"


def _rolling_total(con, student, window_seconds, min_counted_amount):
    """Count and sum amount_aed for prior transfers from `student` that fall
    inside the rolling window and are large enough to be counted.

    Reads the transactions table directly. Only rows at or above the policy's
    min_counted_amount contribute, so unrelated micro-payments (a canteen snack)
    are excluded from the aggregate. Returns (total, count)."""
    row = con.execute(
        "SELECT COALESCE(SUM(amount_aed), 0.0) AS total, COUNT(*) AS n "
        "FROM transactions "
        "WHERE student = ? "
        "  AND amount_aed >= ? "
        "  AND created_at >= datetime('now', ?)",
        (student, min_counted_amount, f"-{window_seconds} seconds"),
    ).fetchone()
    # Row may be a sqlite3.Row or a plain tuple depending on the connection.
    try:
        return float(row["total"]), int(row["n"])
    except (TypeError, IndexError):
        return float(row[0]), int(row[1])


def evaluate(con, ctx, analysis, recipient, state=None):
    """Detect a payment-velocity burst for the transaction in `ctx`.

    Returns None when the rule does not fire, otherwise a
    (ACTION, rule_name, reason) tuple matching guard.py's contract.

    con:       open SQLite connection (used read-only here).
    ctx:       transaction dict (student, recipient_id, amount_aed, ...).
    analysis:  LLM opinion dict or None; this rule does not consume it.
    recipient: directory dict or None; this rule does not require it.
    state:     optional caller-supplied state; unused by this rule.

    The rule is intentionally independent of the LLM: a burst of transfers is a
    fact about transfer history, not a matter of opinion.
    """
    # Respect the policy switch. When velocity_burst is disabled (either in its
    # own section or absent from the rule order) the rule never fires.
    if not policy.current().rule_enabled("velocity_burst"):
        return None

    pol = policy.current()
    window_seconds = pol["velocity_burst.window_seconds"]
    trigger_count = pol["velocity_burst.trigger_count"]
    trigger_total = pol["velocity_burst.trigger_total"]
    min_counted_amount = pol["velocity_burst.min_counted_amount"]

    student = ctx.get("student")
    if not student:
        # Without a sender there is nothing to aggregate.
        return None

    # Aggregate prior transfers in the window. The current transfer is assumed
    # not yet committed to the transactions table (the agent triages before the
    # state write), so this total reflects the burst that precedes it.
    prior_total, prior_count = _rolling_total(
        con, student, window_seconds, min_counted_amount)

    # Both the count and the amount sent must have crossed the threshold. Because
    # the totals are evaluated before this transfer is counted, the transfer
    # being triaged is the "next transfer" that goes to hold: the eighth counted
    # transfer of a run that has already sent at least the trigger total trips
    # the ninth.
    if prior_count < trigger_count or prior_total < trigger_total:
        return None

    return HOLD, RULE_NAME, (
        f"{prior_count} transfer(s) from {student} totalling "
        f"{prior_total:.2f} AED within {window_seconds} s have reached the "
        f"{trigger_count}-transfer / {trigger_total:.0f} AED burst threshold — "
        f"this transfer holds for confirmation despite its small individual amount"
    )


if __name__ == "__main__":
    # Self-test: a burst of counted micro-transfers inside the window pushes the
    # count and the running total past the thresholds, so the next small
    # transfer must return HOLD even though no single amount is large.
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
    window = pol["velocity_burst.window_seconds"]
    trigger_count = pol["velocity_burst.trigger_count"]
    trigger_total = pol["velocity_burst.trigger_total"]
    min_counted = pol["velocity_burst.min_counted_amount"]
    # A counted-but-small transfer just above the canteen floor: a burst of these
    # trips the count and, across trigger_count of them, the amount too.
    per_txn = min_counted + 1.0
    canteen = 15.0          # the canteen payment that must not count
    print(f"policy: window={window}s trigger_count={trigger_count} "
          f"trigger_total={trigger_total} min_counted_amount={min_counted}")

    # A burst of counted transfers, all timestamped "now".
    for i in range(trigger_count):
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
            "VALUES (?,?,?,?)",
            (f"VB-{i}", "Rayan", "R-UNVER", per_txn))
    # A small canteen payment with the same sender; it is below
    # min_counted_amount and must not contribute.
    con.execute(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
        "VALUES (?,?,?,?)",
        ("VB-CANTEEN", "Rayan", "R-ALFALAH", canteen))
    con.commit()

    ctx = {"student": "Rayan", "recipient_id": "R-UNVER", "amount_aed": per_txn}
    result = evaluate(con, ctx, analysis=None, recipient={"tier": "unverified"})

    print("evaluate ->", result)
    assert result is not None, "velocity burst should fire"
    assert result[0] == HOLD, f"expected {HOLD}, got {result[0]}"
    assert result[1] == RULE_NAME, f"unexpected rule name: {result[1]}"
    print("PASS: a burst of counted transfers -> next transfer HOLDs")

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
