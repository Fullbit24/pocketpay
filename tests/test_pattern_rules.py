# tests/test_pattern_rules.py — independent, self-contained tests for the three
# payment-PATTERN rules:
#
#   R1f split_payment      — one student -> ONE recipient, total crosses threshold
#   R1g velocity_burst     — one student, many counted transfers in a short window
#   R1h structuring_fanout — one student -> MANY recipients, total crosses threshold
#
# Each rule is exercised against a FRESH in-memory SQLite database built from the
# real `transactions` schema. Every threshold is read FROM POLICY at runtime via
# `policy.current()`; no number is hard-coded in this test. The current transfer
# being triaged is assumed NOT yet committed, so the inserted rows are the "prior"
# run that precedes it — exactly the model the rules document.
#
# Exit code 0 when every case passes, non-zero on any failure.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3  # noqa: E402

from venator import policy  # noqa: E402
from venator.guard import HOLD  # noqa: E402
from venator.rules import split_payment, velocity_burst, structuring_fanout  # noqa: E402

SCHEMA = (
    "CREATE TABLE transactions ("
    " txn_id TEXT PRIMARY KEY, student TEXT, recipient_id TEXT,"
    " amount_aed REAL, created_at TEXT DEFAULT (datetime('now')))"
)


def _con():
    """A fresh in-memory connection with the real transactions schema."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _insert(con, rows):
    """rows: iterable of (txn_id, student, recipient_id, amount_aed)."""
    con.executemany(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed) "
        "VALUES (?,?,?,?)",
        rows,
    )
    con.commit()


# ---------------------------------------------------------------------------
# Case harness
# ---------------------------------------------------------------------------
_RESULTS = []


def _check(label, cond, detail=""):
    _RESULTS.append((label, bool(cond)))
    status = "PASS" if cond else "FAIL"
    line = f"[{status}] {label}"
    if detail:
        line += f"  ({detail})"
    print(line)


# ---------------------------------------------------------------------------
# R1f — split_payment
# ---------------------------------------------------------------------------
def test_r1f_split_payment_fires():
    pol = policy.current()
    window = pol["split_payment.window_minutes"]
    trigger = pol["split_payment.trigger_total"]
    min_counted = pol["split_payment.min_counted_amount"]

    # Five counted transfers (each >= min_counted_amount) to ONE recipient whose
    # sum reaches the trigger total. Use ceil(trigger/5)-ish sizing so it crosses
    # regardless of the exact policy value.
    per_txn = max(min_counted, trigger / 5.0) + 0.01
    con = _con()
    _insert(con, [(f"SP-{i}", "S-R1F", "R-ONE", per_txn) for i in range(5)])

    ctx = {"student": "S-R1F", "recipient_id": "R-ONE", "amount_aed": per_txn}
    result = split_payment.evaluate(con, ctx, analysis=None,
                                    recipient={"tier": "unverified"})
    _check("R1f: 5 counted transfers to one recipient -> 6th HOLDs",
           result is not None and result[0] == HOLD
           and result[1] == "R1f_split_payment",
           f"result={result!r}, trigger_total={trigger}, "
           f"min_counted={min_counted}, per_txn={per_txn:.2f}")


def test_r1f_split_payment_control():
    pol = policy.current()
    min_counted = pol["split_payment.min_counted_amount"]

    # A lone canteen-size transfer BELOW min_counted_amount must not fire.
    canteen = min_counted / 2.0
    con = _con()
    _insert(con, [("SP-CANTEEN", "S-R1F-C", "R-CANTEEN", canteen)])

    ctx = {"student": "S-R1F-C", "recipient_id": "R-CANTEEN",
           "amount_aed": canteen}
    result = split_payment.evaluate(con, ctx, analysis=None,
                                    recipient={"tier": "verified_vendor"})
    _check("R1f control: lone sub-threshold canteen transfer -> None",
           result is None,
           f"result={result!r}, amount={canteen:.2f} < min_counted={min_counted}")


# ---------------------------------------------------------------------------
# R1g — velocity_burst
# ---------------------------------------------------------------------------
def test_r1g_velocity_burst_fires():
    pol = policy.current()
    window = pol["velocity_burst.window_seconds"]
    trigger_count = pol["velocity_burst.trigger_count"]
    trigger_total = pol["velocity_burst.trigger_total"]
    min_counted = pol["velocity_burst.min_counted_amount"]

    # Enough counted transfers inside the window to reach BOTH trigger_count AND
    # trigger_total. Size each transfer so that trigger_count of them comfortably
    # crosses the total as well; ensure it is at least min_counted_amount.
    per_txn = max(min_counted + 0.01, (trigger_total / trigger_count) + 0.01)

    con = _con()
    # All timestamps default to datetime('now') -> inside the window.
    _insert(con, [(f"VB-{i}", "S-R1G", "R-UNVER", per_txn)
                  for i in range(trigger_count)])

    ctx = {"student": "S-R1G", "recipient_id": "R-UNVER", "amount_aed": per_txn}
    result = velocity_burst.evaluate(con, ctx, analysis=None,
                                     recipient={"tier": "unverified"})
    _check("R1g: burst reaching trigger_count AND trigger_total -> next HOLDs",
           result is not None and result[0] == HOLD
           and result[1] == "R1g_velocity_burst",
           f"result={result!r}, trigger_count={trigger_count}, "
           f"trigger_total={trigger_total}, per_txn={per_txn:.2f}")


def test_r1g_velocity_burst_control():
    pol = policy.current()
    min_counted = pol["velocity_burst.min_counted_amount"]

    # Two slow/small transfers: count is far below trigger_count and the total is
    # far below trigger_total, so the rule must stay silent.
    slow = min_counted / 2.0
    con = _con()
    _insert(con, [("VB-SLOW-0", "S-R1G-C", "R-A", slow),
                  ("VB-SLOW-1", "S-R1G-C", "R-B", slow)])

    ctx = {"student": "S-R1G-C", "recipient_id": "R-C", "amount_aed": slow}
    result = velocity_burst.evaluate(con, ctx, analysis=None,
                                     recipient={"tier": "verified_vendor"})
    _check("R1g control: two small/slow transfers -> None",
           result is None,
           f"result={result!r}, amount={slow:.2f}")


# ---------------------------------------------------------------------------
# R1h — structuring_fanout
# ---------------------------------------------------------------------------
def test_r1h_structuring_fanout_fires():
    pol = policy.current()
    window = pol["structuring.window_minutes"]
    trigger_total = pol["structuring.trigger_total"]
    min_distinct = pol["structuring.min_distinct_recipients"]
    min_counted = pol["structuring.min_counted_amount"]

    # One student pays AT LEAST min_distinct_recipients DIFFERENT recipients with
    # a total >= trigger_total. Size each so the fan-out crosses the total.
    per_txn = max(min_counted + 0.01, (trigger_total / min_distinct) + 0.01)

    con = _con()
    _insert(con, [(f"SF-{i}", "S-R1H", f"R-FRESH{i}", per_txn)
                  for i in range(min_distinct)])

    ctx = {"student": "S-R1H", "recipient_id": "R-FRESH-NEXT",
           "amount_aed": per_txn}
    result = structuring_fanout.evaluate(con, ctx, analysis=None,
                                         recipient={"tier": "unverified"})
    _check("R1h: min_distinct recipients, total >= trigger -> next HOLDs",
           result is not None and result[0] == HOLD
           and result[1] == "R1h_structuring_fanout",
           f"result={result!r}, min_distinct={min_distinct}, "
           f"trigger_total={trigger_total}, per_txn={per_txn:.2f}")


def test_r1h_structuring_fanout_control():
    pol = policy.current()
    trigger_total = pol["structuring.trigger_total"]
    min_distinct = pol["structuring.min_distinct_recipients"]
    min_counted = pol["structuring.min_counted_amount"]

    # Same total, but ALL to ONE recipient -> not a fan-out. Size each transfer
    # so the pair total (== the sender total here) still crosses trigger_total,
    # proving it is the distinct-recipient count, not the money, that saves it.
    n = min_distinct  # same number of transfers as the firing case
    per_txn = max(min_counted + 0.01, (trigger_total / n) + 0.01)

    con = _con()
    _insert(con, [(f"SFC-{i}", "S-R1H-C", "R-ONLY", per_txn) for i in range(n)])

    ctx = {"student": "S-R1H-C", "recipient_id": "R-ONLY", "amount_aed": per_txn}
    result = structuring_fanout.evaluate(con, ctx, analysis=None,
                                          recipient={"tier": "unverified"})
    _check("R1h control: same total all to ONE recipient -> None",
           result is None,
           f"result={result!r}, total={per_txn * n:.2f} >= trigger_total={trigger_total}, "
           f"distinct=1 < min_distinct={min_distinct}")


def main():
    pol = policy.current()
    print("policy thresholds in effect:")
    print(f"  split_payment:     window_minutes={pol['split_payment.window_minutes']} "
          f"trigger_total={pol['split_payment.trigger_total']} "
          f"min_counted_amount={pol['split_payment.min_counted_amount']}")
    print(f"  velocity_burst:    window_seconds={pol['velocity_burst.window_seconds']} "
          f"trigger_count={pol['velocity_burst.trigger_count']} "
          f"trigger_total={pol['velocity_burst.trigger_total']} "
          f"min_counted_amount={pol['velocity_burst.min_counted_amount']}")
    print(f"  structuring:       window_minutes={pol['structuring.window_minutes']} "
          f"trigger_total={pol['structuring.trigger_total']} "
          f"min_distinct_recipients={pol['structuring.min_distinct_recipients']} "
          f"min_counted_amount={pol['structuring.min_counted_amount']}")
    print("-" * 70)

    test_r1f_split_payment_fires()
    test_r1f_split_payment_control()
    test_r1g_velocity_burst_fires()
    test_r1g_velocity_burst_control()
    test_r1h_structuring_fanout_fires()
    test_r1h_structuring_fanout_control()

    print("-" * 70)
    failed = [label for label, ok in _RESULTS if not ok]
    total = len(_RESULTS)
    if failed:
        print(f"RESULT: {total - len(failed)}/{total} passed, {len(failed)} FAILED")
        for label in failed:
            print(f"  FAILED: {label}")
        return 1
    print(f"RESULT: all {total} cases PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
