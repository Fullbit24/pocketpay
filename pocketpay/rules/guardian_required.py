#guardian_required.py — HS-1.5, guardian approval for younger students.
#
# School rule: a student strictly under the policy's guardian.minor_age_below
# needs a parent or guardian to approve any peer transfer above
# guardian.requires_approval_above. The student's own confirmation is no longer
# sufficient for those transfers.
#
# This module is the guard rule (R6) plus the second-approver path. It never
# hard-codes an age or an amount: every number is read from policy.yaml, so the
# rule changes behaviour by editing the policy, not the code.
#
# Two entry points the rest of the system uses:
#   requires_guardian(ctx, recipient, policy_obj=None) -> (bool, str)
#       Pure predicate. True when the sender is a minor, the amount is above the
#       threshold, and the recipient tier counts as a peer.
#   evaluate(con, ctx, analysis, recipient, state=None)
#       Guard rule. Returns (HOLD_STATE, "R6_guardian_required", reason) when the
#       predicate holds, otherwise None so the next rule in order may decide.
#   resolve_guardian(con, txn_id, decision, guardian_id=None) -> str
#       The second approver. 'approve' -> CLEARED, 'decline' -> CANCELLED.

from .. import db, tools
from ..guard import HOLD
from ..policy import current as current_policy

# The literal state name this rule holds a transfer in while the guardian decides.
HOLD_STATE = "PENDING_GUARDIAN"

RULE_NAME = "R6_guardian_required"


def _policy(policy_obj):
    """Normalise whatever the caller passed into a policy view.

    Accepts a Policy instance, a raw loaded mapping, or None (load the cached
    policy from policy.yaml). Keeping this in one place means the rule and its
    self-test always read numbers from the same source.
    """
    if policy_obj is None:
        return current_policy()
    if hasattr(policy_obj, "get") and hasattr(policy_obj, "rule_enabled"):
        return policy_obj  # already a Policy
    # a raw dict/mapping -> wrap it so dotted access works
    from ..policy import Policy
    return Policy(data=policy_obj)


def requires_guardian(ctx, recipient, policy_obj=None):
    """Decide whether this transfer needs a guardian's approval.

    Returns (needed: bool, reason: str). Reason always explains the verdict,
    for the decision log. Reads from policy.guardian:
      minor_age_below         - students strictly under this age are minors
      requires_approval_above - only amounts strictly above this are gated
      peer_tiers              - tiers that count as peer (not vendor) transfers
    """
    policy = _policy(policy_obj)

    age = ctx.get("student_age")
    amount = ctx.get("amount_aed")
    tier = recipient.get("tier") if recipient else None

    minor_below = policy["guardian.minor_age_below"]
    approval_above = policy["guardian.requires_approval_above"]
    peer_tiers = policy.get("guardian.peer_tiers", []) or []

    # Missing age is treated as "not a minor" so an unknown sender is never
    # silently gated on a fact we do not have; other guard rules still apply.
    if age is None:
        return False, "sender age unknown — age rule does not apply"
    if age >= minor_below:
        return False, (f"sender age {age} is not under the minor threshold "
                       f"({minor_below}) — no guardian needed")
    if tier not in peer_tiers:
        return False, (f"recipient tier '{tier}' is not a peer tier "
                       f"({peer_tiers}) — no guardian needed")
    if amount is None or amount <= approval_above:
        return False, (f"amount {amount} AED is at or below the guardian "
                       f"approval threshold ({approval_above} AED) — no guardian needed")

    reason = (f"sender age {age} is under {minor_below} and this is a "
              f"{amount:.0f} AED peer transfer to a '{tier}' recipient, above the "
              f"{approval_above:.0f} AED limit — a guardian must approve")
    return True, reason


def evaluate(con, ctx, analysis, recipient, state=None):
    """Guard rule R6. Returns a guard tuple when a guardian is required, else None.

    The returned hold state is the literal 'PENDING_GUARDIAN', matching the
    (action, rule_name, reason) contract guard.decide() uses. Returning None lets
    the next rule in policy order decide, so this rule never overrides a more
    serious escalation that already fired.
    """
    policy = current_policy()
    if not policy.rule_enabled("guardian_required"):
        return None

    needed, reason = requires_guardian(ctx, recipient, policy)
    if not needed:
        return None

    if con is not None:
        db.log_decision(con, ctx["txn_id"], "guard", f"decide -> {HOLD_STATE}",
                        f"rule {RULE_NAME}: {reason}")
    return HOLD_STATE, RULE_NAME, reason


def resolve_guardian(con, txn_id, decision, guardian_id=None):
    """Second approver path. The guardian approves or declines a held transfer.

    decision: 'approve' -> CLEARED, 'decline' -> CANCELLED. Anything else is an
    error; the guardian's word maps to a state deterministically, as with the
    student confirmation path. guardian_id identifies which guardian answered and
    is recorded in the decision log.
    """
    choice = (decision or "").strip().lower()
    if choice in ("approve", "approved", "a", "yes"):
        new_state = "CLEARED"
    elif choice in ("decline", "declined", "d", "no"):
        new_state = "CANCELLED"
    else:
        raise ValueError("guardian decision must be 'approve' or 'decline'")

    who = f"guardian:{guardian_id}" if guardian_id else "guardian"
    db.log_decision(con, txn_id, "guardian", f"{choice}_response",
                    f"{who} chose {choice} -> {new_state}")
    tools.update_payment_state(con, txn_id, new_state)
    db.log_decision(con, txn_id, "system", "state_updated", f"txn {txn_id} -> {new_state}")
    return new_state


if __name__ == "__main__":
    # Self-test. No database and no LLM required — the predicate is pure, so the
    # four required cases can be proven directly against the loaded policy.
    from ..policy import current as pol

    policy = pol()
    peer = {"recipient_id": "R-OMAR", "tier": "trusted_peer"}
    vendor = {"recipient_id": "R-ARTS", "tier": "verified_vendor"}

    cases = [
        ("age 15 + AED 150 + trusted_peer   -> requires guardian",
         {"txn_id": "T-1", "student": "Bilal", "amount_aed": 150.0, "student_age": 15}, peer, True),
        ("age 17 + AED 150 + trusted_peer   -> does NOT require guardian",
         {"txn_id": "T-2", "student": "Sami", "amount_aed": 150.0, "student_age": 17}, peer, False),
        ("age 15 + AED 50  + trusted_peer   -> below amount, no guardian",
         {"txn_id": "T-3", "student": "Bilal", "amount_aed": 50.0, "student_age": 15}, peer, False),
        ("age 15 + AED 150 + verified_vendor -> not a peer, no guardian",
         {"txn_id": "T-4", "student": "Bilal", "amount_aed": 150.0, "student_age": 15}, vendor, False),
    ]

    print(f"policy: minor_age_below={policy['guardian.minor_age_below']} "
          f"requires_approval_above={policy['guardian.requires_approval_above']} "
          f"peer_tiers={policy.get('guardian.peer_tiers')}")

    ok = True
    for label, ctx, recipient, expected in cases:
        got, reason = requires_guardian(ctx, recipient, policy)
        mark = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"[{mark}] {label}: requires_guardian={got} ({reason})")

    print("SELF-TEST PASSED" if ok else "SELF-TEST FAILED")
    raise SystemExit(0 if ok else 1)
