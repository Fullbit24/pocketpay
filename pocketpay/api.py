# api.py - the versioned HTTP API surface.
#
# This module is the ONLY thing the front end talks to. It is a thin layer: it
# validates input, calls the same engine the terminal does, and returns JSON. It
# decides nothing itself. Any other client - a mobile app, a kiosk, a
# replacement dashboard - can be built against this without touching the engine.
#
# Routes are versioned under /api/v1 so a breaking change is a new prefix, not a
# broken integration. Every route returns JSON, including errors.
#
#   GET  /api/v1/health              liveness + policy version
#   GET  /api/v1/policy              the active policy, as data
#   PATCH /api/v1/policy             change one policy value (audited)
#   GET  /api/v1/transactions        the queue, with filters
#   GET  /api/v1/transactions/{id}   one transaction plus its full audit trail
#   POST /api/v1/transactions/{id}/triage      run the agent chain
#   POST /api/v1/transactions/{id}/resolve     student confirms or cancels
#   POST /api/v1/transactions/{id}/guardian    guardian approves or declines
#   POST /api/v1/transactions/{id}/cancel-window   release or take back
#   POST /api/v1/report              triage an arbitrary incoming message
#   GET  /api/v1/broadcasts          the campus scam-alert feed
#   GET  /api/v1/audit               the audit log, newest first
#   GET  /api/v1/admin/agent         the admin agent's current briefing
#   POST /api/v1/admin/agent         ask the admin agent a question

import json
import os
import re
import time
from datetime import datetime, timezone

from . import db, guard, policy, tools
from .agent import PocketPayAgent

API_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _txn(con, tid):
    row = con.execute("SELECT * FROM transactions WHERE txn_id=?", (tid,)).fetchone()
    return dict(row) if row else None


def _trail(con, tid):
    return [dict(r) for r in db.get_log(con, tid)]


def _stats(con):
    rows = con.execute("SELECT state, COUNT(*) c FROM transactions GROUP BY state").fetchall()
    return {r["state"]: r["c"] for r in rows}


def _banner(con):
    row = con.execute("SELECT message, created_at FROM broadcast_log ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


_reload_agent = {}


def _agent(con):
    """One PocketPayAgent per connection, so an injected LLM stub is reused."""
    key = id(con)
    if key not in _reload_agent:
        _reload_agent[key] = PocketPayAgent(con)
    return _reload_agent[key]


# ---------------------------------------------------------------------------
# read routes
# ---------------------------------------------------------------------------

def get_health(con):
    p = policy.current()
    return {
        "status": "ok",
        "time": _now(),
        "policy_version": p.get("version"),
        "policy_path": os.path.relpath(p.path),
        "rules": p.rules,
        "active_curveballs": [r for r in p.rules if p.rule_enabled(r)],
        "database": os.path.basename(db.DB_PATH) if hasattr(db, "DB_PATH") else "campus_wallet_logs.sqlite",
    }


def get_policy(con):
    p = policy.current()
    return {
        "path": os.path.relpath(p.path),
        "version": p.get("version"),
        "thresholds": p.describe(),
        "rule_order": p.rules,
        "raw": p.data,
    }


def get_transactions(con, state=None, q=None, limit=None):
    sql = ("SELECT txn_id, student, student_age, recipient_id, amount_aed, purpose, "
           "state, risk_score, confidence, decision FROM transactions")
    clauses, args = [], []
    if state and state != "ALL":
        clauses.append("state = ?")
        args.append(state)
    if q:
        clauses.append("(lower(txn_id || student || recipient_id || purpose) LIKE ?)")
        args.append(f"%{q.lower()}%")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY txn_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(sql, args)]
    return {"transactions": rows, "stats": _stats(con), "broadcast": _banner(con)}


def get_transaction(con, tid):
    t = _txn(con, tid)
    if t is None:
        return None
    out = {"txn": t, "trail": _trail(con, tid)}
    if t["state"] == "CONFIRMED_CANCELLABLE":
        try:
            from .rules import cancel_window as cw
            out["cancel_window_seconds_remaining"] = cw.seconds_remaining(con, tid)
        except Exception:
            pass
    return out


def get_broadcasts(con, limit=20):
    rows = con.execute("SELECT id, message, created_at FROM broadcast_log "
                       "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"broadcasts": [dict(r) for r in rows]}


def get_audit(con, limit=100):
    rows = con.execute("SELECT txn_id, actor, step, detail, created_at FROM decision_log "
                       "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"entries": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# write routes
# ---------------------------------------------------------------------------

def post_triage(con, tid):
    t = _txn(con, tid)
    if t is None:
        return None, f"no such transaction: {tid}"
    action, rule, reason = _agent(con).triage(tid)
    return {"txn_id": tid, "action": action, "rule": rule, "explanation": reason}, None


def post_resolve(con, tid, choice, who="student"):
    if _txn(con, tid) is None:
        return None, f"no such transaction: {tid}"
    final = _agent(con).resolve(tid, choice, who=who)
    return {"txn_id": tid, "final": final}, None


def post_guardian(con, tid, decision, guardian_id=None):
    if _txn(con, tid) is None:
        return None, f"no such transaction: {tid}"
    from .rules import guardian_required as g
    final = g.resolve_guardian(con, tid, decision, guardian_id=guardian_id)
    return {"txn_id": tid, "final": final, "approver": "guardian"}, None


def post_cancel_window(con, tid, action):
    if _txn(con, tid) is None:
        return None, f"no such transaction: {tid}"
    from .rules import cancel_window as cw
    if action == "confirm":
        return {"txn_id": tid, "state": cw.confirm(con, tid)}, None
    state, reason = cw.cancel_or_release(con, tid, action)
    return {"txn_id": tid, "state": state, "detail": reason}, None


def post_report(con, message, student="You", recipient_id="R-GHOST", amount=100.0, age=17):
    """Triage an arbitrary incoming message through the real chain.

    The amount and recipient can be supplied by the caller so the same endpoint
    serves the dashboard's live box and any future submission channel.
    """
    msg = (message or "").strip()
    if not msg:
        return None, "message is empty"
    n = con.execute("SELECT COUNT(*) c FROM transactions WHERE txn_id LIKE 'LIVE%'").fetchone()["c"]
    tid = f"LIVE-{n + 1:03d}"
    con.execute("INSERT INTO transactions "
                "(txn_id, student, student_age, recipient_id, amount_aed, purpose, message) "
                "VALUES (?,?,?,?,?,?,?)",
                (tid, student, age, recipient_id, float(amount), "live submission", msg))
    con.commit()
    action, rule, reason = _agent(con).triage(tid)
    return {"txn_id": tid, "action": action, "rule": rule, "explanation": reason}, None


def patch_policy(con, key, value, actor="admin"):
    """Change one policy value and record who did it.

    Only scalar values on an existing key may be set. The rule order, the rule
    modules and the file's structure are not editable through the API: a
    mis-typed threshold is recoverable, a deleted safety check is not.
    """
    p = policy.current()
    if not isinstance(key, str) or key.count(".") < 1:
        return None, "key must be a dotted path, e.g. amounts.auto_clear_limit"
    if key in ("rules", "version"):
        return None, f"{key} cannot be changed through the API"
    sentinel = object()
    if p.get(key, sentinel) is sentinel:
        return None, f"unknown policy key: {key}"

    old = p.get(key)
    new = value
    try:
        if isinstance(old, bool):
            new = bool(value)
        elif isinstance(old, float):
            new = float(value)
        elif isinstance(old, int):
            new = int(value)
    except (TypeError, ValueError):
        return None, f"value for {key} must be a number"

    # Write the file back, preserving comments by replacing the line in place.
    with open(p.path, encoding="utf-8") as fh:
        lines = fh.readlines()
    leaf = key.split(".")[-1]
    pat = re.compile(rf"^(\s*){re.escape(leaf)}\s*:\s*(.+?)(\s*#.*)?$")
    changed = False
    for i, line in enumerate(lines):
        m = pat.match(line.rstrip("\n"))
        if m:
            indent, _old_val, comment = m.group(1), m.group(2), m.group(3) or ""
            lines[i] = f"{indent}{leaf}: {new}{comment}\n"
            changed = True
            break
    if not changed:
        return None, f"could not locate {leaf} in the policy file"

    tmp = p.path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, p.path)

    fresh = policy.reload()
    db.log_decision(con, "POLICY", "admin", "policy_changed",
                    f"{actor} set {key}: {old} -> {new}")
    con.commit()
    return {"changed": key, "from": old, "to": new, "actor": actor,
            "policy_version": fresh.get("version")}, None


# ---------------------------------------------------------------------------
# the admin agent
# ---------------------------------------------------------------------------
# A technical co-pilot for whoever runs the system. It is deliberately plain: it
# reports what the engine is configured to do, what state the data is in, and
# why a given case was held. It reads the same policy and audit log a human
# would, and it never changes anything on its own - every change is an explicit
# API call from the person.

_ADMIN_TOPICS = (
    ("threshold", "amounts", "auto-clear and escalation amounts, by policy"),
    ("policy", "policy", "the active configuration"),
    ("rule", "rules", "the rule order and which rules are enabled"),
    ("hold", "hold", "why a payment is currently held"),
    ("stuck", "stuck", "anything waiting on a human"),
    ("guardian", "guardian", "minor-approval configuration and pending cases"),
    ("split", "split", "rolling-total configuration"),
    ("lookalike", "lookalike", "name-similarity configuration"),
    ("inject", "inject", "message-text handling"),
    ("health", "health", "system state"),
)


def _admin_briefing(con):
    p = policy.current()
    stats = _stats(con)
    stuck = [dict(r) for r in con.execute(
        "SELECT txn_id, student, student_age, amount_aed, state, recipient_id "
        "FROM transactions WHERE state IN ('PENDING_CONFIRMATION','PENDING_GUARDIAN',"
        "'ESCALATED','CONFIRMED_CANCELLABLE') ORDER BY txn_id")]
    return {
        "generated": _now(),
        "policy_version": p.get("version"),
        "rule_count": len(p.rules),
        "enabled_rules": [r for r in p.rules if p.rule_enabled(r)],
        "disabled_rules": [r for r in p.rules if not p.rule_enabled(r)],
        "thresholds": p.describe(),
        "queue": stats,
        "awaiting_a_human": len(stuck),
        "awaiting_detail": stuck,
        "notes": [
            "Every threshold above is read from config/policy.yaml at call time.",
            "Changing a value through PATCH /api/v1/policy takes effect on the next "
            "decision and is written to the audit log.",
        ],
    }


def admin_agent(con, question=None):
    """Answer a question about the running system, from real state only."""
    q = (question or "").strip().lower()
    brief = _admin_briefing(con)

    if not q:
        return {"answer": "Ask about thresholds, policy, rules, holds, health, "
                          "guardian approval, split payments, lookalikes or message "
                          "text handling.",
                "briefing": brief}

    p = policy.current()
    topic = next((slug for kw, slug, _ in _ADMIN_TOPICS if kw in q), None)

    if topic == "amounts" or topic == "threshold":
        t = {d["key"]: d["value"] for d in p.describe()}
        answer = ("Current amounts: auto-clear up to "
                  f"{t.get('amounts.auto_clear_limit')} AED, mid-risk escalation above "
                  f"{t.get('amounts.uncertain_escalate_above')} AED, and a human always "
                  f"confirms above {t.get('amounts.human_confirm_always_above')} AED. "
                  "All three come from config/policy.yaml.")
    elif topic == "policy":
        answer = (f"Policy version {p.get('version')}, loaded from "
                  f"{os.path.relpath(p.path)}, exposing "
                  f"{len(p.describe())} tunable thresholds.")
    elif topic == "rules":
        on = [r for r in p.rules if p.rule_enabled(r)]
        off = [r for r in p.rules if not p.rule_enabled(r)]
        answer = (f"{len(on)} rules active in order: {', '.join(on)}."
                  + (f" Disabled: {', '.join(off)}." if off else ""))
    elif topic == "hold":
        held = [r for r in brief["awaiting_detail"]
                if r["state"] in ("PENDING_CONFIRMATION", "PENDING_GUARDIAN")]
        if not held:
            answer = "Nothing is currently held. Every pending payment has been answered."
        else:
            lines = [f"{r['txn_id']} ({r['student']}, age {r['student_age']}, "
                     f"{r['amount_aed']:.0f} AED, {r['state']})" for r in held]
            answer = f"{len(held)} payment(s) held, awaiting a human: " + "; ".join(lines)
    elif topic == "stuck":
        answer = (f"{brief['awaiting_a_human']} transaction(s) are waiting on a human "
                  "decision. No payment is frozen: every one of these states can be "
                  "resolved by a student, a guardian or staff.")
    elif topic == "guardian":
        pend = [r for r in brief["awaiting_detail"] if r["state"] == "PENDING_GUARDIAN"]
        answer = (f"A guardian is required for students under "
                  f"{p['guardian.minor_age_below']} sending more than "
                  f"{p['guardian.requires_approval_above']} AED to a peer. "
                  f"{len(pend)} payment(s) are currently awaiting guardian approval.")
    elif topic == "split":
        answer = (f"Small transfers to one recipient are totalled over "
                  f"{p['split_payment.window_minutes']} minutes; at "
                  f"{p['split_payment.trigger_total']} AED the next transfer holds. "
                  f"Only transfers of {p['split_payment.min_counted_amount']} AED or "
                  "more count toward the total.")
    elif topic == "lookalike":
        answer = (f"Recipient names are normalised and compared against verified "
                  f"vendors; a match at or above "
                  f"{p['lookalike.similarity_threshold']} that is not exact is escalated.")
    elif topic == "inject":
        answer = (f"{len(p.get('injection.patterns') or [])} instruction-like patterns "
                  "are removed from message text before the model sees it. A message "
                  "can flag a payment; it can never clear one.")
    elif topic == "health":
        answer = (f"{brief['queue']} - {brief['awaiting_a_human']} awaiting a human. "
                  f"Policy v{brief['policy_version']}, {brief['rule_count']} rules.")
    else:
        answer = ("I can explain thresholds, the active policy, the rule order, "
                  "current holds, guardian approval, split-payment totals, lookalike "
                  "detection, message-text handling, or system health. Ask about one "
                  "of those and I will answer from live data.")

    return {"question": question, "answer": answer, "briefing": brief}


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def dispatch(con, method, path, query=None, body=None):
    """Route one request. Returns (payload, status_code)."""
    query = query or {}
    body = body if isinstance(body, dict) else {}
    p = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path

    def one(prefix):
        m = re.fullmatch(prefix + r"/([^/]+)", p)
        return m.group(1) if m else None

    try:
        if method == "GET":
            if p in ("/health", ""):
                return get_health(con), 200
            if p == "/policy":
                return get_policy(con), 200
            if p == "/transactions":
                return get_transactions(con, query.get("state"), query.get("q"),
                                        query.get("limit")), 200
            tid = one("/transactions")
            if tid:
                out = get_transaction(con, tid)
                return (out, 200) if out else ({"error": "no such transaction"}, 404)
            if p == "/broadcasts":
                return get_broadcasts(con), 200
            if p == "/audit":
                return get_audit(con, int(query.get("limit") or 100)), 200
            if p == "/admin/agent":
                return admin_agent(con), 200
            return {"error": "no such route"}, 404

        if method == "PATCH":
            if p == "/policy":
                out, err = patch_policy(con, body.get("key", ""), body.get("value"),
                                        body.get("actor", "admin"))
                return (out, 200) if out else ({"error": err}, 400)
            return {"error": "no such route"}, 404

        if method == "POST":
            tid = one("/transactions")
            if tid and p.endswith("/triage"):
                out, err = post_triage(con, tid)
                return (out, 200) if out else ({"error": err}, 404)
            if tid and p.endswith("/resolve"):
                out, err = post_resolve(con, tid, body.get("choice", ""),
                                        body.get("who", "student"))
                return (out, 200) if out else ({"error": err}, 404)
            if tid and p.endswith("/guardian"):
                out, err = post_guardian(con, tid, body.get("decision", ""),
                                         body.get("guardian_id"))
                return (out, 200) if out else ({"error": err}, 404)
            if tid and p.endswith("/cancel-window"):
                out, err = post_cancel_window(con, tid, body.get("action", ""))
                return (out, 200) if out else ({"error": err}, 404)
            if p == "/report":
                out, err = post_report(con, body.get("message"),
                                       body.get("student", "You"),
                                       body.get("recipient_id", "R-GHOST"),
                                       body.get("amount", 100.0),
                                       body.get("age", 17))
                return (out, 200) if out else ({"error": err}, 400)
            if p == "/admin/agent":
                return admin_agent(con, body.get("question")), 200
            return {"error": "no such route"}, 404

        return {"error": "method not allowed"}, 405
    except ValueError as e:
        return {"error": str(e)}, 400
    except KeyError as e:
        return {"error": f"missing or unknown: {e}"}, 400
    except Exception as e:  # never leak a traceback to a client
        return {"error": f"{type(e).__name__}: {e}"}, 500
