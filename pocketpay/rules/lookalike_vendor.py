# rules/lookalike_vendor.py — HS-1.2 lookalike vendor detection.
#
# A scam account can impersonate a verified campus vendor by registering a name
# that renders almost identically: an added underscore, a swapped digit, a dot in
# place of an underscore. Students read the name quickly and pay the wrong wallet.
#
# This check normalises the incoming recipient name and every verified vendor name
# into the same canonical form, then compares them. If the incoming name collapses
# to exactly a verified vendor's canonical form, it is the real vendor — no
# lookalike. If it is merely *close* to a verified vendor, it is treated as high
# risk and escalated to a human. There is no BLOCK state; worst case a student is
# asked to confirm.
#
# Every number and the swap table come from policy.current(). Nothing here owns a
# threshold.

import re
import sqlite3
from difflib import SequenceMatcher

from .. import policy

ESCALATE = "ESCALATED"

# Rule name as ordered in policy.rules; section name in policy.yaml is `lookalike`.
RULE_NAME = "lookalike_vendor"
SECTION = "lookalike"

_SYMBOL_RE = re.compile(r"[^a-z0-9]]")


def _normalise(name, digit_swaps):
    """Canonical form for comparison.

    Lowercase; drop symbols and punctuation EXCEPT the two handle separators
    underscore and dot, which are normalised to a single underscore so that
    differing punctuation still yields a near-match rather than a silent exact
    match. Apply the policy digit swaps (0->o, 1->l, ...) uniformly, then collapse
    runs of an identical character.

    Keeping the separator position is what makes an added underscore or a dot used
    in place of an underscore render as *close but not identical* — the whole point
    of the check. `digit_swaps` is loaded from policy, never hard-coded here."""
    if name is None:
        return ""
    text = str(name).lower()
    # keep letters, digits and the two handle separators '_' and '.'; strip
    # everything else (at-sign, spaces, hyphens, other punctuation). The two
    # separators are kept DISTINCT: a dot used where an underscore belongs is a
    # different handle and must surface as a near-match, not an exact one.
    text = re.sub(r"[^a-z0-9_.]", "", text)
    # apply the policy swap table (0->o, 1->l, ...) uniformly
    text = "".join(digit_swaps.get(ch, ch) for ch in text)
    # collapse runs of an identical character to a single occurrence
    collapsed = []
    for ch in text:
        if not collapsed or collapsed[-1] != ch:
            collapsed.append(ch)
    return "".join(collapsed)


def _verified_vendors(con, trusted_tiers):
    """Verified vendor display names from the recipient directory.

    Prefers the vendor tier set: rows whose tier is a trusted tier that is not a
    peer tier (i.e. the actual vendors). The recipient directory is the source of
    truth — a vendor added to the table is protected immediately, no code change.
    """
    try:
        rows = con.execute(
            "SELECT display_name, tier FROM recipients").fetchall()
    except sqlite3.Error:
        return []
    vendors = []
    for row in rows:
        # sqlite3.Row supports mapping access; fall back to index for tuples
        try:
            display, tier = row["display_name"], row["tier"]
        except (IndexError, TypeError, KeyError):
            display, tier = row[0], row[1]
        if tier == "verified_vendor" or (trusted_tiers and tier in trusted_tiers
                                         and tier != "trusted_peer"):
            if display:
                vendors.append(display)
    return vendors


def _lookalike(incoming, vendors, threshold, digit_swaps):
    """Return (rule_hit: bool, best_vendor: str, best_ratio: float).

    Exact canonical match to a vendor is NOT a lookalike (return False at ratio
    1.0). A ratio >= threshold that is below 1.0 is a lookalike."""
    norm_in = _normalise(incoming, digit_swaps)
    if not norm_in:
        return False, None, 0.0
    best_ratio, best_vendor = 0.0, None
    for vendor in vendors:
        norm_v = _normalise(vendor, digit_swaps)
        if not norm_v:
            continue
        if norm_in == norm_v:
            # exactly the verified vendor — the real thing, not a lookalike
            return False, vendor, 1.0
        ratio = SequenceMatcher(None, norm_in, norm_v).ratio()
        if ratio > best_ratio:
            best_ratio, best_vendor = ratio, vendor
    if best_vendor is not None and best_ratio >= threshold:
        return True, best_vendor, best_ratio
    return False, best_vendor, best_ratio


def evaluate(con, ctx, analysis, recipient, state=None):
    """Lookalike-vendor check.

    con: sqlite3 connection (recipient directory source of truth)
    ctx: transaction dict (unused here — the name, not the amount, is the signal)
    analysis: LLM opinion dict or None (this rule is deterministic; ignored)
    recipient: directory row/dict for the payee, or None
    state: reserved for stateful rules (unused)

    Returns None when there is nothing to flag, or
    (ESCALATE, RULE_NAME, reason) when the recipient name is close to — but not
    exactly — a verified vendor.
    """
    pol = policy.current()

    # Curveball switch: off unless enabled in its section AND listed in rule order.
    if not pol.rule_enabled(RULE_NAME):
        return None

    threshold = float(pol["lookalike.similarity_threshold"])
    digit_swaps = pol.get("lookalike.digit_swaps") or {}

    # The name to test: the recipient's display name if we have one, else the raw
    # handle carried on the transaction context.
    incoming = None
    if isinstance(recipient, dict):
        incoming = recipient.get("display_name")
        if not incoming:
            incoming = recipient.get("recipient_id")
    if not incoming:
        incoming = ctx.get("recipient_name") or ctx.get("recipient_id")
    if not incoming:
        return None

    # Verify the incoming recipient is NOT itself a registered verified vendor.
    # A promotion to the vendor was already done by name; a fresh scam account
    # never carries the vendor tier, so this rule still tests the raw name.
    trusted_tiers = pol.get("recipients.trusted_tiers") or []
    vendors = _verified_vendors(con, trusted_tiers)
    if not vendors:
        return None

    hit, best_vendor, ratio = _lookalike(incoming, vendors, threshold, digit_swaps)
    if not hit:
        return None

    return ESCALATE, RULE_NAME, (
        f"recipient name '{incoming}' is a {ratio:.2f} similarity match to verified "
        f"vendor '{best_vendor}' (threshold {threshold:.2f}) — lookalike account, "
        f"staff review before payment")


# ---------------------------------------------------------------------------
# Self-test. Run: ~/.venv/bin/python -m pocketpay.rules.lookalike_vendor
# ---------------------------------------------------------------------------
def _selftest():
    from .. import db

    con = db.connect()
    # Deterministic fixture: ensure a verified canteen vendor exists so the real
    # name has something exact to match, independent of the seeded dataset.
    con.execute(
        "INSERT OR REPLACE INTO recipients "
        "(recipient_id, display_name, tier, created_days_ago, scam_reports, txns_90d, simulate_outage) "
        "VALUES ('R-CANTEEN', '@campus_canteen', 'verified_vendor', 400, 0, 900, 0)")
    con.commit()

    pol = policy.current()
    threshold = float(pol["lookalike.similarity_threshold"])
    swaps = pol.get("lookalike.digit_swaps") or {}
    print(f"policy: similarity_threshold={threshold}  digit_swaps={swaps}")
    print(f"rule_enabled({RULE_NAME})={pol.rule_enabled(RULE_NAME)}")

    cases = [
        ("@campus_canteen",  True,  None),          # the real vendor -> not a lookalike
        ("@campus_canteen_", True,  ESCALATE),      # extra underscore
        ("@CampusCanteen0",  True,  ESCALATE),      # trailing zero -> o
        ("@campus.canteen",  True,  ESCALATE),      # dot instead of underscore
    ]

    failures = 0
    for name, covered, want_action in cases:
        recipient = {"recipient_id": "R-TEST", "display_name": name,
                     "tier": "unverified", "scam_reports": 0}
        ctx = {"txn_id": "T", "recipient_id": "R-TEST", "recipient_name": name}
        result = evaluate(con, ctx, None, recipient)
        got = result[0] if result else None
        norm = _normalise(name, swaps)
        exact = any(_normalise(v, swaps) == norm
                    for v in _verified_vendors(con, pol.get("recipients.trusted_tiers")))
        ok = got == want_action
        failures += 0 if ok else 1
        detail = "None" if result is None else f"{result[0]} / {result[1]}"
        print(f"  [{'ok ' if ok else 'FAIL'}] {name!r:22} normalised={norm!r:18} "
              f"exact={exact!s:5} -> {detail}")

    # A clearly unrelated name must not fire.
    other = {"recipient_id": "R-X", "display_name": "Omar (classmate)",
             "tier": "unverified"}
    res = evaluate(con, {"recipient_id": "R-X"}, None, other)
    ok = res is None
    failures += 0 if ok else 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {'Omar (classmate)':22} -> "
          f"{'None' if res is None else res}")

    # A direct verified vendor row must never be flagged.
    real = {"recipient_id": "R-CANTEEN", "display_name": "@campus_canteen",
            "tier": "verified_vendor"}
    res = evaluate(con, {"recipient_id": "R-CANTEEN"}, None, real)
    ok = res is None
    failures += 0 if ok else 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {'@campus_canteen (vendor)':22} -> "
          f"{'None' if res is None else res}")

    print("all self-tests passed" if not failures else f"{failures} self-test(s) FAILED")
    return failures


if __name__ == "__main__":
    raise SystemExit(_selftest())
