# rules/structuring_fanout.py — HS-1.1b structuring / fan-out detector.
#
# The scam: R1f watches ONE sender->recipient pair, so it is blind to a fraudster
# who splits a large sum across MANY recipients instead of one. Pay five different
# fresh accounts 80 AED each and no single pair ever reaches the hold threshold,
# even though 400 AED left the wallet in an hour. This rule closes that gap.
#
# The defence: keep a rolling total for the SENDER across ALL recipients over a
# fixed time window, and count how many DISTINCT recipients they paid. Once both
# the total and the distinct-recipient count cross their thresholds, the next
# transfer holds for confirmation.
#
# Why fan-out specifically: honest students pay a small set of people repeatedly
# (the canteen, a couple of friends). A burst spread across many DIFFERENT,
# previously-unpaid accounts is the signature of layering, not of campus life.
#
# All thresholds come from config/policy.yaml through the policy module:
#   structuring.window_minutes       — width of the rolling window
#   structuring.trigger_total        — sender total at/above which the next holds
#   structuring.min_distinct_recipients — sender must have paid at least this
#                                        many DIFFERENT recipients in the window
#   structuring.min_counted_amount   — transfers below this are ignored, so
#                                        ordinary canteen-size spending never
#                                        accumulates toward a hold
# Nothing is hard-coded.

from .. import guard
from .. import policy

CLEAR = guard.CLEAR
HOLD = guard.HOLD
ESCALATE = guard.ESCALATE

RULE_NAME = "R1h_structuring_fanout"


def _sender_spread(con, student, window_minutes, min_counted_amount):
    """Aggregate the sender's prior transfers across ALL recipients in the window.

    Returns (total, distinct_recipients, count). Only rows at or above the
    policy's min_counted_amount contribute, so unrelated micro-payments (a
    canteen snack) are excluded from the aggregate. The current transfer is
    assumed not yet committed, so this reflects the run that precedes it.
    """
    row = con.execute(
        "SELECT COALESCE(SUM(amount_aed), 0.0) AS total, "
        "       COUNT(DISTINCT recipient_id) AS distinct_n, "
        "       COUNT(*) AS n "
        "FROM transactions "
        "WHERE student = ? "
        "  AND amount_aed >= ? "
        "  AND created_at >= datetime('now', ?)",
        (student, min_counted_amount, f"-{window_minutes} minutes"),
    ).fetchone()
    # Row may be a sqlite3.Row or a plain tuple depending on the connection.
    try:
        return float(row["total"]), int(row["distinct_n"]), int(row["n"])
    except (TypeError, IndexError):
        return float(row[0]), int(row[1]), int(row[2])


def evaluate(con, ctx, analysis, recipient, state=None):
    """Detect a structuring fan-out by the sender in `ctx`.

    Returns None when the rule does not fire, otherwise a
    (ACTION, rule_name, reason) tuple matching guard.py's contract.

    con:       open SQLite connection (used read-only here).
    ctx:       transaction dict (student, recipient_id, amount_aed, ...).
    analysis:  LLM opinion dict or None; this rule does not consume it.
    recipient: directory dict or None; this rule does not require it.
    state:     optional caller-supplied state; unused by this rule.

    Like R1f this rule is independent of the LLM: a fan-out is a fact about
    transfer history, not a matter of opinion.
    """
    if not policy.current().rule_enabled("structuring_fanout"):
        return None

    pol = policy.current()
    window_minutes = pol["structuring.window_minutes"]
    trigger_total = pol["structuring.trigger_total"]
    min_distinct = pol["structuring.min_distinct_recipients"]
    min_counted_amount = pol["structuring.min_counted_amount"]

    student = ctx.get("student")
    if not student:
        # Without a sender there is no one to aggregate a spread over.
        return None

    prior_total, distinct_n, count = _sender_spread(
        con, student, window_minutes, min_counted_amount)

    # Both conditions must be met: enough money AND spread across enough
    # different recipients. A single large payment to one person is R1f's or a
    # normal high-value flow's business, not structuring.
    if prior_total < trigger_total or distinct_n < min_distinct:
        return None

    return HOLD, RULE_NAME, (
        f"{student} has paid {distinct_n} different recipients "
        f"{prior_total:.2f} AED across {count} transfer(s) within "
        f"{window_minutes} min - a fan-out totalling at or above the "
        f"{trigger_total:.0f} AED threshold across {min_distinct}+ accounts holds "
        f"the next transfer for confirmation"
    )


if __name__ == "__main__":
    # Self-test: five 80 AED transfers to five DIFFERENT unverified recipients
    # cross both the total and the distinct-recipient thresholds, so the next
    # transfer must return HOLD. Paying the same total to ONE recipient must not
    # trip this rule (that case belongs to R1f / normal flow).
    import sqlite3

    SCHEMA = (
        "CREATE TABLE transactions ("
        " txn_id TEXT PRIMARY KEY, student TEXT, recipient_id TEXT,"
        " amount_aed REAL, created_at TEXT DEFAULT (datetime('now')))"
    )

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)

    pol = policy.current()
    per_txn = 80.0          # five 80 AED transfers = 400 AED spread
    canteen = 15.0          # below min_counted_amount, must not count
    window = pol["structuring.window_minutes"]
    trigger = pol["structuring.trigger_total"]
    min_distinct = pol["structuring.min_distinct_recipients"]
    min_counted = pol["structuring.min_counted_amount"]
    print(f"policy: window={window}min trigger_total={trigger} "
          f"min_distinct_recipients={min_distinct} min_counted_amount={min_counted}")

    # Five transfers of the counted size, each to a DIFFERENT recipient.
    for i in range(5):
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
            "VALUES (?,?,?,?)",
            (f"SF-{i}", "Rayan", f"R-FRESH{i}", per_txn))
    # A small canteen payment; below min_counted_amount, must not contribute.
    con.execute(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
        "VALUES (?,?,?,?)",
        ("SF-CANTEEN", "Rayan", "R-ALFALAH", canteen))
    con.commit()

    ctx = {"student": "Rayan", "recipient_id": "R-FRESH9", "amount_aed": per_txn}
    result = evaluate(con, ctx, analysis=None, recipient={"tier": "unverified"})

    print("evaluate ->", result)
    assert result is not None, "structuring fan-out should fire"
    assert result[0] == HOLD, f"expected {HOLD}, got {result[0]}"
    assert result[1] == RULE_NAME, f"unexpected rule name: {result[1]}"
    print("PASS: five recipients, 400 AED spread -> next transfer HOLDs")

    # Control: same 400 AED but all to ONE recipient — not a fan-out.
    con2 = sqlite3.connect(":memory:")
    con2.row_factory = sqlite3.Row
    con2.executescript(SCHEMA)
    for i in range(5):
        con2.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
            "VALUES (?,?,?,?)",
            (f"SFC-{i}", "Rayan", "R-ONLY", per_txn))
    con2.commit()
    one = evaluate(con2, {"student": "Rayan", "recipient_id": "R-ONLY",
                          "amount_aed": per_txn}, None, {"tier": "unverified"})
    print("single-recipient evaluate ->", one)
    assert one is None, "one recipient is not a fan-out; this rule must not fire"
    print("PASS: same total to one recipient -> rule does not fire")
