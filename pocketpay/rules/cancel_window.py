#pocketpay/rules/cancel_window.py — HS-1.4, the post-confirmation cancel window.
#
# A student who confirms a held payment (PENDING_CONFIRMATION) sometimes regrets
# it seconds later. This module gives that confirmation a short, policy-defined
# grace period during which the student may still take it back.
#
# Design:
#   * Confirming does NOT clear the payment. It moves the ticket to the new
#     CONFIRMED_CANCELLABLE state, which records WHEN the confirmation happened.
#   * While the window is open the ticket may only become CANCELLED (student
#     took the confirmation back) or RELEASED (window passed, money may move).
#   * The window length is read from policy (cancel_window.seconds). It is never
#     hard-coded here — the value lives in config/policy.yaml and nowhere else.
#   * Every rejection is written to the decision log (db.log_decision) with an
#     actor and a plain-language detail string, so the audit trail explains the
#     refusal without reference to internal state names alone.
#
# Timestamp source:
#   The transactions table has no dedicated "confirmed_at" column and this rule
#   does not own db.py, so the confirmation time is stamped into the existing
#   `updated_at` TEXT column, written as a UTC epoch by tools.update_payment_state
#   at the moment of the PENDING_CONFIRMATION -> CONFIRMED_CANCELLABLE write.
#   ASSUMPTION: updated_at is the single source of truth for "confirmed at" while
#   a ticket sits in CONFIRMED_CANCELLABLE. No other writer touches a ticket in
#   that state, so the value cannot drift. If a dedicated column is added later,
#   only _confirmed_at() must change.

import time

from .. import db, tools
from .. import policy as policy_mod

# State names owned by the state machine (mirrors tools.update_payment_state).
CONFIRMED_CANCELLABLE = "CONFIRMED_CANCELLABLE"
CANCELLED = "CANCELLED"
RELEASED = "RELEASED"
PENDING_CONFIRMATION = "PENDING_CONFIRMATION"

# The outcome codes cancel_or_release() may return for a rejected request.
REJECT = "REJECT"

_ACTOR = "rule:cancel_window"


def _window_seconds():
    """Window length, read live from policy. No default, no literal fallback:
    a missing key is a configuration error the caller must see, not a silent
    guess at how long money stays reversible."""
    return float(policy_mod.current()["cancel_window.seconds"])


def _confirmed_at(con, txn_id):
    """Return the UTC epoch at which the confirmation was recorded, or None.

    Reads updated_at, which tools.update_payment_state stamps on every write.
    For a ticket in CONFIRMED_CANCELLABLE the only preceding write is the
    confirmation itself, so this is the confirmation time (see module header)."""
    row = con.execute(
        "SELECT updated_at FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such transaction: {txn_id}")
    return _parse_epoch(row["updated_at"])


def _parse_epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    # Preferred form written by this module: a bare integer/float epoch string.
    try:
        return float(text)
    except ValueError:
        pass
    # Fallback for a value written by tools.update_payment_state as
    # datetime('now') ('YYYY-MM-DD HH:MM:SS', UTC). Interpreted as UTC.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return float(time.mktime(time.strptime(text, fmt)) - time.timezone)
        except ValueError:
            continue
    return None


def _state_of(con, txn_id):
    row = con.execute("SELECT state FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such transaction: {txn_id}")
    return row["state"]


def confirm(con, txn_id) -> str:
    """Record a student's confirmation of a held payment.

    Moves PENDING_CONFIRMATION -> CONFIRMED_CANCELLABLE and stamps the moment of
    confirmation. Returns the new state. The window length is not consulted here
    (confirming always opens a window); the duration is applied at decision time
    in cancel_or_release().

    Raises ValueError if the ticket is not awaiting confirmation, via the
    state machine's own transition check.
    """
    seconds = _window_seconds()
    state = _state_of(con, txn_id)
    if state != PENDING_CONFIRMATION:
        db.log_decision(
            con, txn_id, _ACTOR, "confirm_rejected",
            f"confirmation rejected: ticket is {state}, not {PENDING_CONFIRMATION} "
            f"— only a held payment can be confirmed")
        raise ValueError(f"cannot confirm {txn_id}: state is {state}")

    # The state machine is the only legal writer. It stamps updated_at with the
    # current time; that timestamp is the confirmation time this rule relies on.
    tools.update_payment_state(con, txn_id, CONFIRMED_CANCELLABLE)

    # Re-stamp updated_at with an explicit UTC epoch so the elapsed time is
    # measured unambiguously in the same units seconds_remaining() consumes.
    epoch = time.time()
    con.execute("UPDATE transactions SET updated_at=? WHERE txn_id=?",
                (str(epoch), txn_id))
    con.commit()

    db.log_decision(
        con, txn_id, _ACTOR, "confirmed",
        f"student confirmed {txn_id}: -> {CONFIRMED_CANCELLABLE}, "
        f"cancel window open for {seconds:.0f}s (policy cancel_window.seconds)")
    return CONFIRMED_CANCELLABLE


def seconds_remaining(con, txn_id) -> "float | None":
    """Seconds left in the cancel window, or None.

    Returns None when the ticket is missing, is not in CONFIRMED_CANCELLABLE, or
    has no recorded confirmation time. Returns a negative number once the window
    has elapsed (so callers can see how far past the deadline they are)."""
    try:
        state = _state_of(con, txn_id)
    except KeyError:
        return None
    if state != CONFIRMED_CANCELLABLE:
        return None
    started = _confirmed_at(con, txn_id)
    if started is None:
        return None
    return (_window_seconds()) - (time.time() - started)


def cancel_or_release(con, txn_id, action) -> "tuple[str, str]":
    """Resolve a confirmation given human intent.

    action: 'cancel'  — the student is taking the confirmation back (they regret it).
            'release' — the confirmation stands and the payment may proceed.

    Returns (state, reason):
      * action 'cancel' inside the window  -> (CANCELLED, reason)
      * action 'cancel' after the window   -> (REJECT, reason); state unchanged
      * action 'release' inside/after win. -> (RELEASED, reason)
      * any action when state is not CONFIRMED_CANCELLABLE (e.g. already
        CLEARED/CANCELLED/RELEASED)        -> (REJECT, reason); state unchanged

    Every rejection is logged. On a rejection the returned state is REJECT and
    the transaction row is left exactly as it was.
    """
    action = (action or "").strip().lower()
    if action not in ("cancel", "release"):
        raise ValueError("action must be 'cancel' or 'release'")

    state = _state_of(con, txn_id)

    if state != CONFIRMED_CANCELLABLE:
        reason = (f"rejected: {txn_id} is {state}, not {CONFIRMED_CANCELLABLE} — "
                  f"the cancel window does not apply to a payment in this state")
        db.log_decision(con, txn_id, _ACTOR, f"{action}_rejected", reason)
        return REJECT, reason

    started = _confirmed_at(con, txn_id)
    if started is None:
        reason = (f"rejected: no confirmation timestamp recorded for {txn_id}; "
                  f"refusing to act without a verified window start")
        db.log_decision(con, txn_id, _ACTOR, f"{action}_rejected", reason)
        return REJECT, reason

    window = _window_seconds()
    elapsed = time.time() - started
    remaining = window - elapsed

    if action == "release":
        tools.update_payment_state(con, txn_id, RELEASED)
        reason = (f"released: student let the confirmation stand "
                  f"({elapsed:.0f}s after confirming, window {window:.0f}s)")
        db.log_decision(con, txn_id, _ACTOR, "released", reason)
        return RELEASED, reason

    # action == "cancel"
    if remaining > 0:
        tools.update_payment_state(con, txn_id, CANCELLED)
        reason = (f"cancelled: taken back {elapsed:.0f}s after confirming, "
                  f"within the {window:.0f}s cancel window")
        db.log_decision(con, txn_id, _ACTOR, "cancelled", reason)
        return CANCELLED, reason

    reason = (f"rejected: cancel window closed {abs(remaining):.0f}s ago "
              f"({elapsed:.0f}s since confirming, window {window:.0f}s) — "
              f"the confirmation stands")
    db.log_decision(con, txn_id, _ACTOR, "cancel_rejected", reason)
    return REJECT, reason


def evaluate(con, ctx, analysis, recipient, state=None) -> None:
    """No-op by design.

    This rule does NOT participate in the main triage chain. The chain in
    agent.py decides whether a payment is CLEAR, HELD or ESCALATED from facts and
    the model's opinion. The cancel window is a separate, human-driven stage that
    only becomes relevant AFTER a student has confirmed a held payment, so it can
    never contribute a verdict to the initial decision. It therefore returns None
    unconditionally and is intentionally absent from policy.yaml `rules:`.

    The window logic lives in confirm()/cancel_or_release()/seconds_remaining()
    and is invoked by the human-confirmation path, not by guard.decide()."""
    return None


# --------------------------------------------------------------------------
# Self-test: the three live scenarios from the specification, exercised against
# a temporary in-memory database so the demo's own dataset is never touched.
# Run: ~/.venv/bin/python -m pocketpay.rules.cancel_window
# --------------------------------------------------------------------------
def _build_temp_con():
    """In-memory sqlite with the real schema and one held payment."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    con.execute(
        "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, "
        "purpose, message, state) VALUES (?,?,?,?,?,?,?)",
        ("TX-HELD", "Test Student", "R-OMAR", 40.0, "shared book", "hey, here's my half", "PENDING_CONFIRMATION"))
    con.commit()
    return con


def _row_state(con, txn_id):
    return con.execute("SELECT state FROM transactions WHERE txn_id=?",
                       (txn_id,)).fetchone()["state"]


def _reset_to_pending(con, txn_id):
    con.execute("UPDATE transactions SET state='PENDING_CONFIRMATION', updated_at=NULL "
                "WHERE txn_id=?", (txn_id,))
    con.commit()


def _self_test():
    con = _build_temp_con()
    txn = "TX-HELD"
    window = _window_seconds()
    print(f"cancel window from policy: {window:.0f}s (cancel_window.seconds)\n")

    # --- Case 1: confirm, then cancel after 30 seconds -> CANCELLED -------------
    print("case 1: confirm a held payment, cancel after 30 seconds")
    st = confirm(con, txn)
    print(f"  confirm() -> {st}")
    left = seconds_remaining(con, txn)
    print(f"  seconds_remaining() immediately after confirm: {left:.1f}s")
    # simulate 30s of elapsed time by backdating the recorded confirmation stamp
    con.execute("UPDATE transactions SET updated_at=? WHERE txn_id=?",
                (str(time.time() - 30), txn))
    con.commit()
    left = seconds_remaining(con, txn)
    print(f"  seconds_remaining() at +30s: {left:.1f}s")
    outcome, reason = cancel_or_release(con, txn, "cancel")
    print(f"  cancel_or_release('cancel') -> {outcome}")
    print(f"  reason: {reason}")
    strict = _row_state(con, txn)
    print(f"  row state: {strict}")
    assert (outcome, strict) == (CANCELLED, CANCELLED), "case 1 failed"
    print("  PASS: cancelled inside the window\n")

    # --- Case 2: confirm, try to cancel after the window -> REJECT, RELEASED ----
    print("case 2: confirm another, try to cancel after the window")
    _reset_to_pending(con, txn)
    st = confirm(con, txn)
    print(f"  confirm() -> {st}")
    # simulate the window having elapsed
    con.execute("UPDATE transactions SET updated_at=? WHERE txn_id=?",
                (str(time.time() - (window + 5)), txn))
    con.commit()
    left = seconds_remaining(con, txn)
    print(f"  seconds_remaining() after window: {left:.1f}s (negative = closed)")
    outcome, reason = cancel_or_release(con, txn, "cancel")
    print(f"  cancel_or_release('cancel') -> {outcome}")
    print(f"  reason: {reason}")
    strict = _row_state(con, txn)
    print(f"  row state (unchanged by the rejected cancel): {strict}")
    assert outcome == REJECT and strict == CONFIRMED_CANCELLABLE, "case 2 failed"
    # and, elapsed window, an explicit release settles the ticket
    outcome2, reason2 = cancel_or_release(con, txn, "release")
    print(f"  cancel_or_release('release') -> {outcome2}")
    print(f"  reason: {reason2}")
    assert outcome2 == RELEASED and _row_state(con, txn) == RELEASED, "case 2 release failed"
    print("  PASS: cancel rejected after the window; release settles to RELEASED\n")

    # --- Case 3: cancel a payment that already cleared -> REJECT ---------------
    print("case 3: try to cancel a payment that already cleared")
    _reset_to_pending(con, txn)
    # the normal, non-window path: student confirms and it clears
    tools.update_payment_state(con, txn, "CLEARED")
    print(f"  row state: {_row_state(con, txn)}")
    outcome, reason = cancel_or_release(con, txn, "cancel")
    print(f"  cancel_or_release('cancel') -> {outcome}")
    print(f"  reason: {reason}")
    strict = _row_state(con, txn)
    print(f"  row state (unchanged): {strict}")
    assert outcome == REJECT and strict == "CLEARED", "case 3 failed"
    print("  PASS: cancel rejected on an already-cleared payment\n")

    # --- State-machine guard: illegal transitions out of the new state raise ----
    print("state-machine guard: illegal transition out of CONFIRMED_CANCELLABLE")
    _reset_to_pending(con, txn)
    confirm(con, txn)
    try:
        tools.update_payment_state(con, txn, "PENDING_CONFIRMATION")
        raise AssertionError("illegal transition was NOT rejected")
    except ValueError as e:
        print(f"  update_payment_state(CONFIRMED_CANCELLABLE -> PENDING_CONFIRMATION) raised ValueError: {e}")
    try:
        tools.update_payment_state(con, txn, "CLEAR")
        raise AssertionError("illegal transition was NOT rejected")
    except ValueError as e:
        print(f"  update_payment_state(CONFIRMED_CANCELLABLE -> CLEAR) raised ValueError: {e}")
    print("  PASS: every other transition out of the state is rejected\n")

    # --- evaluate() is a documented no-op in the main chain --------------------
    print("evaluate() in the main chain")
    result = evaluate(con, {"txn_id": txn}, None, None)
    print(f"  evaluate(...) -> {result!r} (does not participate in triage)")
    assert result is None, "evaluate must return None"
    print("  PASS: evaluate() returns None\n")

    print("--- decision log for the temporary ticket ---")
    for row in db.get_log(con, txn):
        print(f"  [{row['actor']:>20}] {row['step']}: {row['detail']}")

    print("\nall three live-test cases and both guard checks passed")


if __name__ == "__main__":
    _self_test()
