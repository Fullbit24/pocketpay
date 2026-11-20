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
#   GET  /api/v1/users               the user directory (view_users)
#   GET  /api/v1/users/{id}          one user (view_users, or self)
#   GET  /api/v1/me                  the identity behind this request
#
# IDENTITY
#   A request may carry an identity in one of two ways:
#     X-Venator-User: <user_id>:<token>      (HTTP header)
#     ?user=<user_id>:<token>                (query parameter, for plain curl)
#   When present, the id is resolved to a live user and every route checks the
#   permission that route needs against the user's role, read from
#   config/policy.yaml at request time. Reads are scoped to the user.
#
#   When ABSENT, the request is an anonymous operator with full rights - exactly
#   the behaviour this module had before identity existed. That keeps the demo
#   dashboard and the QA scripts working unchanged. An identity that is present
#   but unresolvable (bad id, wrong token, inactive account) is a 401, never a
#   silent fallback to the anonymous operator.

import json
import os
import re
import time
from datetime import datetime, timezone

from . import db, guard, policy, tools, users
from .agent import VenatorAgent

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
    """One VenatorAgent per connection, so an injected LLM stub is reused."""
    key = id(con)
    if key not in _reload_agent:
        _reload_agent[key] = VenatorAgent(con)
    return _reload_agent[key]


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
# Resolve who is asking, once per request. Two carriers, in priority order:
# the X-Venator-User header and the `user` query parameter, both of the form
# "<user_id>:<token>". Absence means the anonymous full-rights operator.

def _presented_identity(query, headers):
    """The raw '<id>:<token>' string a request carries, or None."""
    headers = headers or {}
    # Header lookup is case-insensitive but callers may pass a plain dict.
    raw = None
    for k, v in headers.items():
        if k.lower() == "x-venator-user":
            raw = v
            break
    if raw is None:
        raw = query.get("user")
    return raw


def _resolve_identity(con, query, headers):
    """Return (user_or_None, error_or_None).

    user is None and error is None      -> anonymous operator (full rights)
    user is a user dict, error is None  -> authenticated identity
    user is None, error is a string     -> an identity was presented but invalid
    """
    raw = _presented_identity(query, headers)
    if not raw:
        return None, None
    user_id, _, token = str(raw).partition(":")
    user = users.authenticate(con, user_id.strip(), token.strip())
    if user is None:
        # No token value is echoed back; only the fact of the failure.
        return None, "unknown user or invalid token"
    return user, None


class _Anon:
    """A stand-in identity for an anonymous request: full rights, no scope limit.

    An anonymous caller is treated as a fully privileged operator so the demo
    and the QA scripts keep behaving exactly as before identity existed. This is
    the only place that grants blanket authority, and it is granted by absence
    of an identity, never by a role name.
    """

    def __init__(self):
        self.user = None
        self.anonymous = True

    def can(self, permission):
        return True

    def scoping(self, con):
        return {"scope": "all", "student_name": None}


class _Actor:
    """A resolved, authenticated identity, checked against policy."""

    def __init__(self, con, user):
        self.con = con
        self.user = user
        self.anonymous = False

    def can(self, permission):
        return users.can(self.user, permission, policy.current())

    def scoping(self, con=None):
        return users.scoping(con or self.con, self.user)


def _actor(con, query, headers):
    """(actor, error). error set only when a presented identity is invalid."""
    user, err = _resolve_identity(con, query, headers)
    if err:
        return None, err
    if user is None:
        return _Anon(), None
    return _Actor(con, user), None


def _forbidden(permission):
    return {"error": f"permission denied: this role does not hold '{permission}'"}, 403


def _forbidden_for_txn(actor, tid):
    """403 for a transaction outside the caller's scope."""
    who = "this user" if not actor.anonymous else "the anonymous operator"
    return {"error": (f"forbidden: {who} may not act on transaction {tid}, "
                      "which is not theirs")}, 403


def _may_resolve(actor, txn):
    """True when the actor may resolve / cancel-window this transaction.

    resolve_any   -> any transaction (staff review)
    resolve_own   -> only a transaction whose student matches the actor's linked
                     student_name (a student their own; a guardian their child's)
    """
    if actor.can("resolve_any"):
        return True
    if actor.can("resolve_own"):
        name = actor.user.get("student_name") if actor.user else None
        return name is not None and txn.get("student") == name
    return False


def _may_view(actor, con, txn):
    """True when the actor may read this transaction row.

    The anonymous operator sees everything (pre-identity behaviour). An
    authenticated actor is limited by users.may_view_transaction, which derives
    the scope from the actor's policy permissions.
    """
    if actor.anonymous:
        return True
    return users.may_view_transaction(con, actor.user, txn.get("student"))



def _may_guardian_act(actor, txn):
    """True when the actor may use the guardian route for this transaction.

    The permission gate (guardian_approve) is checked by the caller. Here we
    bind the action to the right student: an identity whose student_name is set
    may act only on that student's transaction; the anonymous operator, which
    has no linkage, is not restricted.
    """
    if actor.anonymous:
        return True
    name = actor.user.get("student_name") if actor.user else None
    if name is None:
        # A role that holds guardian_approve without a linkage (e.g. admin)
        # may act for any student - the authority is the policy permission.
        return True
    return txn.get("student") == name


def _guardian_id(con, actor):
    """The guardians-table id linked to the acting user's student, if any."""
    if actor.anonymous or not actor.user:
        return None
    name = actor.user.get("student_name")
    if not name:
        return None
    row = con.execute("SELECT guardian_id FROM guardians WHERE student_name=? "
                      "ORDER BY guardian_id LIMIT 1", (name,)).fetchone()
    return row["guardian_id"] if row else None


def _actor_label(actor):
    """A human-readable actor label for the audit log (never the token)."""
    if actor.anonymous:
        return "admin"  # preserves the pre-identity default label
    u = actor.user
    return f"{u.get('user_id')} ({u.get('role')})"



def _scoped_transactions(con, actor, state=None, q=None, limit=None):
    """The queue, filtered by the actor's scope.

    Scoped reads are enforced in SQL: a student's rows are limited to their own
    student_name, a guardian's to the linked student's. Stats and the broadcast
    banner are recomputed over the visible set so a scoped view never leaks an
    out-of-scope count.
    """
    s = actor.scoping(con)
    sql = ("SELECT txn_id, student, student_age, recipient_id, amount_aed, purpose, "
           "state, risk_score, confidence, decision FROM transactions")
    clauses, args = [], []
    if s["scope"] == "linked":
        if not s["student_name"]:
            # A "linked" actor with nothing to link to sees nothing, ever.
            return {"transactions": [], "stats": {}, "broadcast": _banner(con)}
        clauses.append("student = ?")
        args.append(s["student_name"])
    elif s["scope"] == "none":
        return {"transactions": [], "stats": {}, "broadcast": _banner(con)}
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
    stats = {}
    for r in rows:
        stats[r["state"]] = stats.get(r["state"], 0) + 1
    return {"transactions": rows, "stats": stats, "broadcast": _banner(con)}


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


def get_users(con):
    """The user directory, token-free."""
    return {"users": users.list_users(con)}


def get_user(con, user_id):
    """One user, or None. Served to a view_users holder or to the user itself."""
    return users.public(users.get_user(con, user_id))


def get_me(actor):
    """The identity behind the request, plus the permissions its role holds."""
    if actor.anonymous:
        return {
            "anonymous": True,
            "user": None,
            "role": None,
            "permissions": list(users.KNOWN_PERMISSIONS),
            "scope": "all",
            "note": ("No identity presented. Treated as the anonymous operator "
                     "with full rights, for the unauthenticated demo."),
        }
    user = actor.user
    return {
        "anonymous": False,
        "user": users.public(user),
        "role": user.get("role"),
        "permissions": users.permissions(user, policy.current()),
        "scope": actor.scoping(con=None)["scope"],
    }



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

def dispatch(con, method, path, query=None, body=None, headers=None):
    """Route one request. Returns (payload, status_code).

    `headers` is optional and defaults to None so existing callers that pass
    only five arguments keep working; an identity may also arrive as the `user`
    query parameter. See the module header for how identity is presented.
    """
    query = query or {}
    body = body if isinstance(body, dict) else {}
    p = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path

    # Resolve the caller once. A presented-but-invalid identity is rejected
    # before routing, so a bad token can never reach a handler as the anonymous
    # operator.
    actor, ident_err = _actor(con, query, headers)
    if ident_err:
        return {"error": "unauthorized: " + ident_err}, 401
    assert actor is not None

    def one(prefix):
        """Match a single-segment id: /transactions/<id> exactly."""
        m = re.fullmatch(prefix + r"/([^/]+)", p)
        return m.group(1) if m else None

    def sub(prefix):
        """Match an id plus a trailing action: /transactions/<id>/<action>.

        Returns (id, action) or (None, None). `one()` deliberately refuses a
        trailing segment, so the two are kept separate: a bare id and an
        id-with-action are different routes and must not be confused.
        """
        m = re.fullmatch(prefix + r"/([^/]+)/([^/]+)", p)
        return (m.group(1), m.group(2)) if m else (None, None)

    try:
        if method == "GET":
            if p in ("/health", ""):
                return get_health(con), 200
            if p == "/me":
                return get_me(actor), 200
            if p == "/users":
                if not actor.can("view_users"):
                    return _forbidden("view_users")
                return get_users(con), 200
            uid = one("/users")
            if uid:
                # view_users sees everyone; otherwise a user may read only its
                # own record.
                if not actor.can("view_users"):
                    if actor.anonymous or actor.user.get("user_id") != uid:
                        return _forbidden("view_users")
                out = get_user(con, uid)
                return (out, 200) if out else ({"error": "no such user"}, 404)
            if p == "/policy":
                return get_policy(con), 200
            if p == "/transactions":
                if not actor.can("view_queue"):
                    return _forbidden("view_queue")
                return _scoped_transactions(con, actor, query.get("state"),
                                            query.get("q"), query.get("limit")), 200
            tid = one("/transactions")
            if tid:
                if not actor.can("view_queue"):
                    return _forbidden("view_queue")
                t = _txn(con, tid)
                if t is None:
                    return {"error": "no such transaction"}, 404
                if not _may_view(actor, con, t):
                    return _forbidden_for_txn(actor, tid)
                out = get_transaction(con, tid)
                return (out, 200) if out else ({"error": "no such transaction"}, 404)
            if p == "/broadcasts":
                return get_broadcasts(con), 200
            if p == "/audit":
                if not actor.can("view_audit"):
                    return _forbidden("view_audit")
                return get_audit(con, int(query.get("limit") or 100)), 200
            if p == "/admin/agent":
                return admin_agent(con), 200
            return {"error": "no such route"}, 404

        if method == "PATCH":
            if p == "/policy":
                if not actor.can("change_policy"):
                    return _forbidden("change_policy")
                actor_label = str(body.get("actor") or _actor_label(actor))
                out, err = patch_policy(con, body.get("key", ""), body.get("value"),
                                        actor_label)
                return (out, 200) if out else ({"error": err}, 400)
            return {"error": "no such route"}, 404

        if method == "POST":
            tid, action = sub("/transactions")
            if tid:
                if action == "triage":
                    if not actor.can("triage"):
                        return _forbidden("triage")
                    out, err = post_triage(con, tid)
                    return (out, 200) if out else ({"error": err}, 404)
                if action == "resolve":
                    t = _txn(con, tid)
                    if t is None:
                        return {"error": f"no such transaction: {tid}"}, 404
                    if not _may_resolve(actor, t):
                        return _forbidden_for_txn(actor, tid)
                    out, err = post_resolve(con, tid, body.get("choice", ""),
                                            body.get("who", "student"))
                    return (out, 200) if out else ({"error": err}, 404)
                if action == "guardian":
                    if not actor.can("guardian_approve"):
                        return _forbidden("guardian_approve")
                    t = _txn(con, tid)
                    if t is None:
                        return {"error": f"no such transaction: {tid}"}, 404
                    # A resolved guardian identity may act only for their own
                    # linked student; an anonymous operator may act for any.
                    if not _may_guardian_act(actor, t):
                        return _forbidden_for_txn(actor, tid)
                    guardian_id = body.get("guardian_id") or _guardian_id(con, actor)
                    out, err = post_guardian(con, tid, body.get("decision", ""),
                                             guardian_id)
                    return (out, 200) if out else ({"error": err}, 404)
                if action == "cancel-window":
                    t = _txn(con, tid)
                    if t is None:
                        return {"error": f"no such transaction: {tid}"}, 404
                    if not _may_resolve(actor, t):
                        return _forbidden_for_txn(actor, tid)
                    out, err = post_cancel_window(con, tid, body.get("action", ""))
                    return (out, 200) if out else ({"error": err}, 404)
                return {"error": f"no such action on a transaction: {action}"}, 404
            if p == "/report":
                if not actor.can("triage"):
                    return _forbidden("triage")
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
