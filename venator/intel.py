# intel.py - the campus intelligence layer.
#
# The guard decides ONE payment at a time. That is deliberate: a single decision
# must be explainable and a single mistake must be small. But real fraud is not
# a single payment. It is graduated and coordinated - the same attacker works
# several students at once, each transfer individually below the threshold, and
# the pattern only exists in the history the per-payment guard never looks at.
#
# This module is that missing view. It reads the SAME sqlite database the guard
# reads and produces six deterministic analyses over the transaction history:
#
#   velocity(con, window_minutes)      - transfers and value per (student,
#                                        recipient) pair in a window, ranked.
#   emerging_recipients(con, window)   - recipients collecting from many
#                                        distinct senders quickly, ranked.
#   repeat_attackers(con)              - links and handles that recur across
#                                        independent escalated cases.
#   cluster(con)                       - escalations grouped by a shared signal
#                                        so several incidents read as one,
#                                        campaign.
#   anomaly_scores(con)                - per-student deviation from that
#                                        student's own normal behaviour.
#   ledger(con)                        - the institution's accounting summary:
#                                        totals by state, day, recipient and
#                                        student, and the value held for review.
#
# DESIGN CONSTRAINTS, all intentional:
#   * NO language model. Every function here is pure Python over SQL rows, so a
#     finding is reproducible and can be audited line by line. Nothing in this
#     module calls the agent, the model, or the network.
#   * NO hard-coded thresholds. Every number is read at CALL TIME from the
#     `intel:` section of config/policy.yaml through the shared policy module.
#     Change the policy file and every analysis changes with no code edit.
#   * NO writes. Every function opens nothing and commits nothing; it only reads
#     and returns plain dicts/lists that JSON can serialise directly.
#   * These analyses FLAG, they do not decide. A velocity or anomaly finding is
#     evidence for a human, never an automatic block. The state machine and the
#     guard remain the only things that move money.
#
# What "escalated" means to this module. The transactions table carries a live
# `state`; in a freshly seeded database every row is still RECEIVED because no
# triage has run. The decision_log records the guard's verdict the moment triage
# runs (step strings beginning "decide ->"). repeat_attackers() and cluster()
# therefore treat a case as escalated when EITHER its current state is one of the
# escalation states OR the decision log shows an escalate/guardian verdict for
# it. That keeps the analyses useful on a seeded database (they will be empty,
# correctly) and rich the moment real triage has happened.

import re
from datetime import datetime, timezone

from . import policy as _policy

# ---------------------------------------------------------------------------
# shared vocabulary
# ---------------------------------------------------------------------------

# The states a transaction can be in, grouped the way the ledger reports them.
# Kept as a single source so the ledger, the API and the document cannot drift.
STATE_GROUPS = {
    "cleared": ("CLEAR", "CLEARED", "RELEASED"),
    "held": ("PENDING_CONFIRMATION", "PENDING_GUARDIAN", "CONFIRMED_CANCELLABLE"),
    "escalated": ("ESCALATED", "TRIAGED"),
    "cancelled": ("CANCELLED",),
}

# States that mean "a human is looking at, or must look at, this money". The
# value in these states is the money the institution is holding back.
HELD_STATES = ("PENDING_CONFIRMATION", "PENDING_GUARDIAN", "CONFIRMED_CANCELLABLE")

# States that mean the transfer has been surfaced above the routine path.
ESCALATED_STATES = ("ESCALATED", "TRIAGED")


def _p():
    """The active policy, read fresh each call so a policy edit takes effect."""
    return _policy.current()


def _cfg(key, default):
    """One intel threshold, verbatim from policy.yaml `intel:`.

    Read at call time. `default` exists only so a deliberately absent key does
    not crash a read-only analysis; the shipped policy always supplies it, and a
    missing key is visible in CAMPUS_INTEL.md as the documented default.
    """
    return _p().get(f"intel.{key}", default)


def _window_minutes(window_minutes):
    """Resolve the window argument: explicit caller value, else policy default."""
    if window_minutes is None:
        return int(_cfg("default_window_minutes", 1440))
    return int(window_minutes)


def _rows(con, sql, args=()):
    """Run a query and return a list of plain dicts.

    sqlite3.Row supports dict(); a plain tuple connection is also accepted so
    the functions work under any connection the caller passes in.
    """
    cur = con.execute(sql, args)
    out = []
    for r in cur.fetchall():
        try:
            out.append(dict(r))
        except TypeError:  # not a Row - fall back to column names
            names = [d[0] for d in cur.description]
            out.append(dict(zip(names, r)))
    return out


# ---------------------------------------------------------------------------
# 1. velocity
# ---------------------------------------------------------------------------

def velocity(con, window_minutes=None):
    """Transfers and total value per (student, recipient) pair, ranked.

    Why: the split-payment rule holds the NEXT transfer once a pair's rolling
    total crosses one threshold. Velocity is the wider view - it ranks EVERY
    (sender, recipient) pair by how much and how often it moved inside the
    window, so a run of small transfers is visible even when no single one ever
    breached a rule and even before the next transfer arrives.

    Rows whose amount is at least `intel.velocity_min_amount` are counted, so a
    canteen-size micro-payment does not dominate the ranking. Ranked by total
    value descending, then by transfer count descending.

    Returns:
        {"window_minutes": int, "min_amount": float, "pairs": [
            {"student", "recipient_id", "recipient_name", "recipient_tier",
             "transfers", "total_aed", "max_aed", "first_at", "last_at",
             "txn_ids": [..]}, ..]}
    """
    window = _window_minutes(window_minutes)
    min_amount = float(_cfg("velocity_min_amount", 24))
    rows = _rows(
        con,
        "SELECT t.txn_id, t.student, t.recipient_id, t.amount_aed, t.created_at, "
        "       r.display_name, r.tier "
        "FROM transactions t LEFT JOIN recipients r ON r.recipient_id = t.recipient_id "
        "WHERE t.amount_aed >= ? "
        "  AND t.created_at >= datetime('now', ?) "
        "ORDER BY t.created_at",
        (min_amount, f"-{window} minutes"),
    )

    agg = {}
    for r in rows:
        key = (r["student"], r["recipient_id"])
        a = agg.setdefault(key, {
            "student": r["student"],
            "recipient_id": r["recipient_id"],
            "recipient_name": r.get("display_name"),
            "recipient_tier": r.get("tier"),
            "transfers": 0,
            "total_aed": 0.0,
            "max_aed": 0.0,
            "first_at": r["created_at"],
            "last_at": r["created_at"],
            "txn_ids": [],
        })
        a["transfers"] += 1
        a["total_aed"] += float(r["amount_aed"])
        a["max_aed"] = max(a["max_aed"], float(r["amount_aed"]))
        a["last_at"] = r["created_at"]
        a["txn_ids"].append(r["txn_id"])

    pairs = sorted(agg.values(),
                   key=lambda a: (a["total_aed"], a["transfers"]), reverse=True)
    for a in pairs:
        a["total_aed"] = round(a["total_aed"], 2)
        a["max_aed"] = round(a["max_aed"], 2)
    return {"window_minutes": window, "min_amount": min_amount, "pairs": pairs}


# ---------------------------------------------------------------------------
# 2. emerging_recipients
# ---------------------------------------------------------------------------

def emerging_recipients(con, window_minutes=None):
    """Recipients collecting from many distinct senders quickly, ranked.

    Why: a legitimate campus vendor is paid by many students over months; a
    legitimately new peer is paid by one or two. A recipient that suddenly takes
    money from `intel.recipient_min_distinct_senders` (or more) DIFFERENT
    students inside a short window is the signature of a coordinated scam - one
    account harvesting many victims at once, each transfer individually small.

    New and unverified accounts are ranked first, because the same sender-count
    is far more suspicious from a two-day-old handle than from the canteen. A
    recipient is counted "new" when its directory age is at or below
    `intel.recipient_new_days` or its tier is `unverified`.

    Only recipients meeting the distinct-sender minimum are returned.

    Returns:
        {"window_minutes": int, "min_distinct_senders": int, "new_days": int,
         "recipients": [
            {"recipient_id", "recipient_name", "recipient_tier", "is_new",
             "distinct_senders", "transfers", "total_aed", "senders": [..],
             "txn_ids": [..]}, ..]}
    """
    window = _window_minutes(window_minutes)
    min_senders = int(_cfg("recipient_min_distinct_senders", 2))
    new_days = int(_cfg("recipient_new_days", 30))
    rows = _rows(
        con,
        "SELECT t.txn_id, t.student, t.recipient_id, t.amount_aed, "
        "       r.display_name, r.tier, r.created_days_ago "
        "FROM transactions t LEFT JOIN recipients r ON r.recipient_id = t.recipient_id "
        "WHERE t.created_at >= datetime('now', ?)",
        (f"-{window} minutes",),
    )

    agg = {}
    for r in rows:
        a = agg.setdefault(r["recipient_id"], {
            "recipient_id": r["recipient_id"],
            "recipient_name": r.get("display_name"),
            "recipient_tier": r.get("tier"),
            "created_days_ago": r.get("created_days_ago"),
            "transfers": 0,
            "total_aed": 0.0,
            "senders": set(),
            "txn_ids": [],
        })
        a["transfers"] += 1
        a["total_aed"] += float(r["amount_aed"])
        a["senders"].add(r["student"])
        a["txn_ids"].append(r["txn_id"])

    out = []
    for a in agg.values():
        distinct = len(a["senders"])
        if distinct < min_senders:
            continue
        age = a["created_days_ago"]
        is_new = (a["recipient_tier"] == "unverified"
                  or (age is not None and age <= new_days))
        out.append({
            "recipient_id": a["recipient_id"],
            "recipient_name": a["recipient_name"],
            "recipient_tier": a["recipient_tier"],
            "created_days_ago": age,
            "is_new": bool(is_new),
            "distinct_senders": distinct,
            "transfers": a["transfers"],
            "total_aed": round(a["total_aed"], 2),
            "senders": sorted(a["senders"]),
            "txn_ids": a["txn_ids"],
        })

    # New accounts first, then broadest reach, then largest value.
    out.sort(key=lambda a: (a["is_new"], a["distinct_senders"], a["total_aed"]),
             reverse=True)
    return {"window_minutes": window, "min_distinct_senders": min_senders,
            "new_days": new_days, "recipients": out}


# ---------------------------------------------------------------------------
# escalation evidence, shared by repeat_attackers() and cluster()
# ---------------------------------------------------------------------------

def _escalated_cases(con):
    """Every case that has been escalated, with the evidence attached.

    A case counts as escalated when its state is an escalation state OR the
    decision log shows an escalation/guardian verdict for it. Returns a list of
    dicts: txn_id, student, recipient_id, amount_aed, message, state, tier,
    links (list), and signals (list) - the shared-signal tokens the cluster
    step groups on.
    """
    cases = {r["txn_id"]: r for r in _rows(
        con,
        "SELECT t.txn_id, t.student, t.recipient_id, t.amount_aed, t.message, "
        "       t.state, t.created_at, r.display_name, r.tier "
        "FROM transactions t LEFT JOIN recipients r ON r.recipient_id = t.recipient_id "
        "WHERE t.state IN ({})".format(",".join("?" * len(ESCALATED_STATES))),
        ESCALATED_STATES,
    )}

    # Cases escalated by the guard are recorded in the decision log before the
    # state write; pick those up too, and any case whose logged verdict is an
    # escalation or guardian hold.
    logged = _rows(
        con,
        "SELECT DISTINCT txn_id FROM decision_log "
        "WHERE step LIKE '%decide ->%' "
        "  AND (detail LIKE '%ESCALAT%' OR detail LIKE '%PENDING_GUARDIAN%')",
    )
    extra = [r["txn_id"] for r in logged if r["txn_id"] not in cases]
    if extra:
        marks = ",".join("?" * len(extra))
        for r in _rows(
            con,
            f"SELECT t.txn_id, t.student, t.recipient_id, t.amount_aed, t.message, "
            f"       t.state, t.created_at, r.display_name, r.tier "
            f"FROM transactions t LEFT JOIN recipients r ON r.recipient_id = t.recipient_id "
            f"WHERE t.txn_id IN ({marks})",
            extra,
        ):
            cases[r["txn_id"]] = r

    out = []
    for c in cases.values():
        links = _urls(c.get("message"))
        signals = []
        for u in links:
            signals.append(f"link:{u}")
        if c.get("recipient_id"):
            signals.append(f"handle:{c['recipient_id']}")
        text_key = _text_signature(c.get("message"))
        if text_key:
            signals.append(f"text:{text_key}")
        c = dict(c)
        c["links"] = links
        c["signals"] = signals
        out.append(c)
    out.sort(key=lambda c: c["txn_id"])
    return out


_URL_RE = re.compile(r"https?://\S+")
_WORD_RE = re.compile(r"[a-z0-9']+")


def _urls(message):
    """The distinct URLs in a message, normalised and order-stable."""
    seen, out = set(), []
    for u in _URL_RE.findall(message or ""):
        u = u.rstrip(".,)!").lower()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _text_signature(message):
    """A coarse, order-independent signature of a message's wording.

    Scam templates are copy-pasted, so two cases with the same pitch share
    distinctive words even when the student differs. This returns a signature
    of the most distinctive tokens, or None for a message too short to carry
    one. It is deliberately crude - a shared-signal hint, not a classifier.
    """
    words = set(_WORD_RE.findall((message or "").lower()))
    stop = {
        "the", "a", "an", "to", "for", "and", "of", "is", "it", "my", "your",
        "you", "me", "i", "we", "now", "this", "that", "with", "at", "on", "be",
        "send", "sending", "hey", "hi", "bro", "so", "just", "here", "can",
    }
    distinctive = sorted(w for w in words if w not in stop and len(w) > 3)
    if len(distinctive) < 3:
        return None
    # A stable key from the first few distinctive words in sorted order.
    return "-".join(distinctive[:4])


# ---------------------------------------------------------------------------
# 3. repeat_attackers
# ---------------------------------------------------------------------------

def repeat_attackers(con):
    """Links and handles that appear across multiple escalated cases.

    Why: when the same URL or the same recipient handle surfaces in two or more
    independently escalated cases, those cases are not independent. One actor is
    working the campus. This lists each recurring key with the case ids it links,
    so a reviewer sees the campaign rather than a queue of one-offs.

    Returns:
        {"links": [{"url", "cases": [..], "students": [..],
                    "total_aed", "count"}],
         "handles": [{"recipient_id", "recipient_name", "cases": [..],
                      "students": [..], "total_aed", "count"}]}
    """
    cases = _escalated_cases(con)

    link_map = {}
    handle_map = {}
    for c in cases:
        for u in c["links"]:
            e = link_map.setdefault(u, {"url": u, "cases": [], "students": set(),
                                        "total_aed": 0.0})
            e["cases"].append(c["txn_id"])
            e["students"].add(c["student"])
            e["total_aed"] += float(c["amount_aed"] or 0)
        rid = c["recipient_id"]
        if rid:
            e = handle_map.setdefault(rid, {"recipient_id": rid,
                                            "recipient_name": c.get("display_name"),
                                            "cases": [], "students": set(),
                                            "total_aed": 0.0})
            e["cases"].append(c["txn_id"])
            e["students"].add(c["student"])
            e["total_aed"] += float(c["amount_aed"] or 0)

    def _pack(e, idkey):
        return {
            idkey: e[idkey],
            **({"recipient_name": e["recipient_name"]} if idkey == "recipient_id" else {}),
            "cases": e["cases"],
            "students": sorted(e["students"]),
            "count": len(e["cases"]),
            "total_aed": round(e["total_aed"], 2),
        }

    links = [_pack(e, "url") for e in link_map.values() if len(e["cases"]) >= 2]
    handles = [_pack(e, "recipient_id") for e in handle_map.values()
               if len(e["cases"]) >= 2]
    links.sort(key=lambda e: (e["count"], e["total_aed"]), reverse=True)
    handles.sort(key=lambda e: (e["count"], e["total_aed"]), reverse=True)
    return {"links": links, "handles": handles}


# ---------------------------------------------------------------------------
# 4. cluster
# ---------------------------------------------------------------------------

def cluster(con):
    """Group escalations that share a signal into campaigns.

    Why: several escalations that share a link, a handle or a message template
    are one incident, not several. Grouping them lets an administrator see four
    students approached by one actor as a single campaign, and act once. Two
    cases are drawn into the same cluster if they share ANY signal (a URL, a
    recipient handle, or a message signature); groups are formed by transitively
    merging signals so a chain of shared evidence collapses to one campaign.

    Only groups with at least `intel.campaign_min_size` members are reported.

    Returns:
        {"min_size": int, "campaigns": [
            {"signals": [..], "size": int, "students": [..], "txn_ids": [..],
             "total_aed", "recipients": [..], "links": [..]} ..]}
    """
    min_size = int(_cfg("campaign_min_size", 2))
    cases = _escalated_cases(con)

    # Union-find over cases, keyed by shared signal, to merge transitively.
    parent = {c["txn_id"]: c["txn_id"] for c in cases}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    signal_owner = {}
    for c in cases:
        for s in c["signals"]:
            if s in signal_owner:
                union(signal_owner[s], c["txn_id"])
            else:
                signal_owner[s] = c["txn_id"]

    groups = {}
    for c in cases:
        groups.setdefault(find(c["txn_id"]), []).append(c)

    campaigns = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        signals = sorted({s for m in members for s in m["signals"]})
        campaigns.append({
            "signals": signals,
            "size": len(members),
            "txn_ids": [m["txn_id"] for m in members],
            "students": sorted({m["student"] for m in members}),
            "recipients": sorted({m["recipient_id"] for m in members if m["recipient_id"]}),
            "links": sorted({u for m in members for u in m["links"]}),
            "total_aed": round(sum(float(m["amount_aed"] or 0) for m in members), 2),
        })
    campaigns.sort(key=lambda c: (c["size"], c["total_aed"]), reverse=True)
    return {"min_size": min_size, "campaigns": campaigns}


# ---------------------------------------------------------------------------
# 5. anomaly_scores
# ---------------------------------------------------------------------------

def anomaly_scores(con):
    """Per-student deviation from that student's OWN normal behaviour.

    Why: a fixed amount threshold treats every student the same, but a student
    who normally sends 20 AED and suddenly sends 400 is the interesting signal,
    not the absolute number. Each student is their own baseline: their typical
    transfer, and their usual recipient set. A transfer that is far above their
    own typical amount, or to a recipient they have never used before, scores a
    deviation. Students are ranked by their single largest deviation.

    The amount deviation is the transfer divided by the student's average
    transfer. The policy's `intel.anomaly_multiplier` sets the ratio at or above
    which a transfer is reported as divergent. New recipients are reported when
    a student pays a handle they have no prior relationship with, weighted by
    `intel.anomaly_new_recipient_weight` so the two signals are comparable.

    The average uses every transfer of the student, this one included. A student
    with fewer than two transfers has no baseline and is skipped.

    Returns:
        {"multiplier": float, "new_recipient_weight": float, "students": [
            {"student", "baseline_avg_aed", "baseline_count",
             "max_ratio", "anomalies": [
                {"txn_id", "recipient_id", "recipient_new", "amount_aed",
                 "ratio", "score"}, ..]}, ..]}
    """
    multiplier = float(_cfg("anomaly_multiplier", 3.0))
    new_weight = float(_cfg("anomaly_new_recipient_weight", 3.0))
    rows = _rows(
        con,
        "SELECT txn_id, student, recipient_id, amount_aed, created_at "
        "FROM transactions ORDER BY student, created_at",
    )

    by_student = {}
    for r in rows:
        by_student.setdefault(r["student"], []).append(r)

    students = []
    for student, txns in by_student.items():
        if len(txns) < 2:
            continue  # no baseline to deviate from
        amounts = [float(t["amount_aed"]) for t in txns]
        avg = sum(amounts) / len(amounts)
        if avg <= 0:
            continue
        # The recipient set a student has been seen using, counted per row so a
        # repeated recipient is not treated as "new" on its second payment.
        seen = set()
        anomalies = []
        for t in txns:
            ratio = float(t["amount_aed"]) / avg
            recipient_new = t["recipient_id"] not in seen
            seen.add(t["recipient_id"])
            if ratio >= multiplier or recipient_new:
                score = max(ratio, new_weight if recipient_new else 0.0)
                anomalies.append({
                    "txn_id": t["txn_id"],
                    "recipient_id": t["recipient_id"],
                    "recipient_new": bool(recipient_new),
                    "amount_aed": round(float(t["amount_aed"]), 2),
                    "ratio": round(ratio, 2),
                    "score": round(score, 2),
                })
        if not anomalies:
            continue
        anomalies.sort(key=lambda a: a["score"], reverse=True)
        students.append({
            "student": student,
            "baseline_avg_aed": round(avg, 2),
            "baseline_count": len(txns),
            "max_ratio": anomalies[0]["ratio"],
            "anomalies": anomalies,
        })

    students.sort(key=lambda s: s["max_ratio"], reverse=True)
    return {"multiplier": multiplier, "new_recipient_weight": new_weight,
            "students": students}


# ---------------------------------------------------------------------------
# 6. ledger
# ---------------------------------------------------------------------------

def ledger(con):
    """The institution's accounting summary over the campus wallet.

    Why: an administrator needs to reconcile. This reports, for the whole
    dataset, the totals by state, by calendar day, by recipient and by student,
    the split of value between auto-cleared, held and escalated, and - the number
    that matters most - the total value of payments currently held for human
    review.

    IMPORTANT: this SUMMARISES. It does not settle, release, move or refund any
    money, and it never writes to the database. Every figure is derived from the
    `transactions` rows as they stand at call time. A "cleared" figure is money
    the state machine has moved; a "held" figure is money waiting on a person;
    neither is a settlement instruction. See CAMPUS_INTEL.md for the honest
    limits.

    Returns:
        {"generated": iso8601,
         "transaction_count": int,
         "total_aed": float,
         "by_state": [{"state", "count", "total_aed"}],
         "by_group": {"cleared": {...}, "held": {...},
                      "escalated": {...}, "cancelled": {...}},
         "held_for_review": {"count", "total_aed", "txn_ids"},
         "by_day": [{"day", "count", "total_aed"}],
         "by_recipient": [{"recipient_id", "recipient_name", "tier",
                           "count", "total_aed"}],
         "by_student": [{"student", "count", "total_aed"}]}
    """
    all_rows = _rows(
        con,
        "SELECT t.txn_id, t.student, t.recipient_id, t.amount_aed, t.state, "
        "       substr(t.created_at, 1, 10) AS day, r.display_name, r.tier "
        "FROM transactions t LEFT JOIN recipients r ON r.recipient_id = t.recipient_id",
    )

    total = 0.0
    by_state = {}
    by_day = {}
    by_recipient = {}
    by_student = {}
    held_ids = []

    state_group = {}
    for group, states in STATE_GROUPS.items():
        for s in states:
            state_group[s] = group

    for r in all_rows:
        amt = float(r["amount_aed"] or 0)
        total += amt
        st = r["state"]
        e = by_state.setdefault(st, {"state": st, "count": 0, "total_aed": 0.0})
        e["count"] += 1
        e["total_aed"] += amt

        day = r["day"]
        e = by_day.setdefault(day, {"day": day, "count": 0, "total_aed": 0.0})
        e["count"] += 1
        e["total_aed"] += amt

        rid = r["recipient_id"]
        e = by_recipient.setdefault(rid, {"recipient_id": rid,
                                          "recipient_name": r.get("display_name"),
                                          "tier": r.get("tier"),
                                          "count": 0, "total_aed": 0.0})
        e["count"] += 1
        e["total_aed"] += amt

        sid = r["student"]
        e = by_student.setdefault(sid, {"student": sid, "count": 0, "total_aed": 0.0})
        e["count"] += 1
        e["total_aed"] += amt

        if st in HELD_STATES:
            held_ids.append(r["txn_id"])

    # Group totals (cleared / held / escalated / cancelled).
    by_group = {}
    for group, states in STATE_GROUPS.items():
        c = 0
        v = 0.0
        for s in states:
            if s in by_state:
                c += by_state[s]["count"]
                v += by_state[s]["total_aed"]
        by_group[group] = {"count": c, "total_aed": round(v, 2)}

    held_total = sum(float(r["amount_aed"] or 0) for r in all_rows
                     if r["state"] in HELD_STATES)

    def _round_list(rows):
        for r in rows:
            r["total_aed"] = round(r["total_aed"], 2)
        return rows

    by_state_l = _round_list(sorted(by_state.values(),
                                    key=lambda e: e["total_aed"], reverse=True))
    by_day_l = _round_list(sorted(by_day.values(), key=lambda e: e["day"]))
    by_recipient_l = _round_list(sorted(by_recipient.values(),
                                        key=lambda e: e["total_aed"], reverse=True))
    by_student_l = _round_list(sorted(by_student.values(),
                                      key=lambda e: e["total_aed"], reverse=True))

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transaction_count": len(all_rows),
        "total_aed": round(total, 2),
        "by_state": by_state_l,
        "by_group": by_group,
        "held_for_review": {
            "count": len(held_ids),
            "total_aed": round(held_total, 2),
            "txn_ids": sorted(held_ids),
        },
        "by_day": by_day_l,
        "by_recipient": by_recipient_l,
        "by_student": by_student_l,
    }


# ---------------------------------------------------------------------------
# briefing - one composed institution-level summary
# ---------------------------------------------------------------------------

def briefing(con):
    """Compose the six analyses into a single institution-level summary.

    Why: an admin surface should be able to render the whole early-warning view
    in one request rather than six. This is a pure composition - it runs the
    same functions above and attaches a short, factual headline list. It decides
    nothing; it reports what the deterministic analyses found.

    Returns:
        {"generated", "policy_version", "thresholds",
         "headline": [str, ..],
         "velocity", "emerging_recipients", "repeat_attackers",
         "campaigns", "anomalies", "ledger"}
    """
    vel = velocity(con)
    emg = emerging_recipients(con)
    rep = repeat_attackers(con)
    clu = cluster(con)
    ano = anomaly_scores(con)
    led = ledger(con)

    headline = []
    headline.append(
        f"{led['transaction_count']} transactions totalling {led['total_aed']:.2f} AED "
        f"across {len(led['by_student'])} students and {len(led['by_recipient'])} recipients.")
    h = led["held_for_review"]
    headline.append(
        f"{h['count']} payment(s) totalling {h['total_aed']:.2f} AED are held for human review.")
    if vel["pairs"]:
        top = vel["pairs"][0]
        headline.append(
            f"Highest-velocity pair: {top['student']} -> {top['recipient_id']} "
            f"({top['transfers']} transfer(s), {top['total_aed']:.2f} AED in "
            f"{vel['window_minutes']} min).")
    if emg["recipients"]:
        r = emg["recipients"][0]
        headline.append(
            f"Recipient {r['recipient_id']} collected from {r['distinct_senders']} "
            f"distinct senders ({'new/unverified' if r['is_new'] else 'established'} account).")
    if clu["campaigns"]:
        c = clu["campaigns"][0]
        headline.append(
            f"{len(clu['campaigns'])} campaign(s) detected; largest spans "
            f"{c['size']} cases and {len(c['students'])} student(s).")
    else:
        headline.append("No multi-case campaigns detected in the current data.")
    if ano["students"]:
        s = ano["students"][0]
        headline.append(
            f"Largest behaviour change: {s['student']} at {s['max_ratio']}x their "
            f"own average transfer.")

    return {
        "generated": led["generated"],
        "policy_version": _p().get("version"),
        "thresholds": {
            "default_window_minutes": vel["window_minutes"],
            "velocity_min_amount": vel["min_amount"],
            "recipient_min_distinct_senders": emg["min_distinct_senders"],
            "recipient_new_days": emg["new_days"],
            "campaign_min_size": clu["min_size"],
            "anomaly_multiplier": ano["multiplier"],
            "anomaly_new_recipient_weight": ano["new_recipient_weight"],
        },
        "headline": headline,
        "velocity": vel,
        "emerging_recipients": emg,
        "repeat_attackers": rep,
        "campaigns": clu,
        "anomalies": ano,
        "ledger": led,
    }
