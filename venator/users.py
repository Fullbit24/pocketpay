# users.py - the identity layer.
#
# This module answers four questions and nothing else:
#   list_users / get_user        what accounts exist
#   authenticate                 does this (user_id, token) pair name a live user
#   can                          does this user hold a named permission
#   scoping                      which transactions may this user see
#
# The authority itself is NOT decided here. `can()` reads the permission list
# for the user's role out of config/policy.yaml at call time, so moving a
# permission between roles, or adding a role, is a policy edit rather than a
# code change. No function in this file branches on a role *name*.
#
# SECURITY: the token scheme is demonstration-grade. A single long-lived shared
# secret per row, compared in constant time, with no expiry, rotation, hashing
# at rest, or session binding. It is enough to show scoped authority working
# end to end. See USERS.md for what a production system would require instead.

import hmac

from . import policy as _policy

# The permission names the API understands. Which role holds each one is read
# from policy.yaml's `roles:` section; this tuple only names the closed set so a
# typo in a policy file is caught instead of silently granting nothing.
KNOWN_PERMISSIONS = (
    "view_queue", "triage", "resolve_own", "resolve_any",
    "guardian_approve", "change_policy", "view_audit", "view_users",
)


def _row_to_user(row):
    """sqlite3.Row -> a plain dict. Never expose the token in a response.

    `public()` is what routes return; this raw dict is used internally so
    authenticate() can compare the token.
    """
    if row is None:
        return None
    u = dict(row)
    u["active"] = bool(u.get("active"))
    return u


def public(user):
    """The safe, token-free view of a user for API responses."""
    if user is None:
        return None
    return {k: user.get(k) for k in
            ("user_id", "display_name", "role", "student_name", "email",
             "created_at", "active")}


def list_users(con):
    """Every active user, ordered by role then id. Tokens are never included."""
    rows = con.execute(
        "SELECT user_id, display_name, role, student_name, email, created_at, active "
        "FROM users WHERE active=1 ORDER BY role, user_id").fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(con, user_id):
    """One user by id, or None. Includes the token for internal auth use."""
    row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return _row_to_user(row)


def authenticate(con, user_id, token):
    """Resolve (user_id, token) to an active user dict, or None.

    Honest about what it is: an equality check on a stored secret, done in
    constant time. An unknown id, an inactive account, or a wrong token all
    return None, and none of them is logged with the token value.

    An empty or missing token is rejected up front, so a row whose api_token is
    NULL can never be entered with a blank credential.
    """
    if not user_id:
        return None
    user = get_user(con, user_id)
    if user is None or not user.get("active"):
        return None
    stored = user.get("api_token")
    if not stored or not token:
        return None
    # compare_digest needs equal-length str/bytes; encode both sides.
    try:
        if not hmac.compare_digest(str(stored), str(token)):
            return None
    except (TypeError, ValueError):
        return None
    return user


def _role_permissions(role, policy):
    """The permission list for a role, read from the policy at call time.

    Accepts a Policy view or a raw mapping. An unknown role yields an empty
    list, which means 'may do nothing' - the safe default for a role name that
    is not declared in policy.yaml.
    """
    if policy is None:
        policy = _policy.current()
    perms = policy.get(f"roles.{role}") if hasattr(policy, "get") else None
    if not isinstance(perms, (list, tuple)):
        return []
    return [p for p in perms]


def permissions(user, policy=None):
    """The resolved permission list for a user, from policy."""
    if user is None:
        return []
    return _role_permissions(user.get("role"), policy)


def can(user, permission, policy=None):
    """True when the user's role holds `permission` per policy.yaml.

    Every permission decision in the API goes through here. The check is a
    membership test on a policy-declared list, so it is traceable to the file
    and never to an if-statement on a role name.
    """
    if user is None:
        return False
    if permission not in KNOWN_PERMISSIONS:
        # An unknown permission name is never granted; a typo cannot open a door.
        return False
    return permission in _role_permissions(user.get("role"), policy)


def scoping(con, user):
    """Describe which transactions a user may see.

    Returns a dict:
        {"scope": "all"|"linked"|"none",
         "student_name": <str or None>}

    The *scope* is derived from the user's policy permissions, not from the
    role name:
        resolve_any  -> "all"    (staff and admin: the whole queue)
        resolve_own  -> "linked" (a student sees only their own rows; a guardian
                                  sees their linked student's rows, because both
                                  link through the same student_name column)
        neither      -> "none"   (may read no transactions)

    The caller turns this into SQL. A "linked" scope with no student_name is
    treated by the caller as matching nothing, so a mis-seeded row cannot widen
    visibility.
    """
    if user is None:
        return {"scope": "none", "student_name": None}
    if can(user, "resolve_any"):
        return {"scope": "all", "student_name": None}
    if can(user, "resolve_own"):
        return {"scope": "linked", "student_name": user.get("student_name")}
    return {"scope": "none", "student_name": None}


def may_view_transaction(con, user, txn_student):
    """True when this user may see a transaction belonging to txn_student."""
    s = scoping(con, user)
    if s["scope"] == "all":
        return True
    if s["scope"] == "linked":
        return txn_student is not None and txn_student == s["student_name"]
    return False


if __name__ == "__main__":
    # Self-test of the permission layer against the loaded policy. No database
    # and no network: `can()` and the role table are pure reads of policy.yaml.
    pol = _policy.current()
    print("roles in policy.yaml:")
    roles = pol.get("roles") or {}
    for role, perms in roles.items():
        print(f"  {role:<10} {perms}")

    cases = [
        ("student has resolve_own",         {"role": "student"}, "resolve_own", True),
        ("student lacks resolve_any",       {"role": "student"}, "resolve_any", False),
        ("student cannot change_policy",    {"role": "student"}, "change_policy", False),
        ("guardian has guardian_approve",   {"role": "guardian"}, "guardian_approve", True),
        ("guardian lacks change_policy",    {"role": "guardian"}, "change_policy", False),
        ("staff has resolve_any",           {"role": "staff"}, "resolve_any", True),
        ("staff cannot change_policy",      {"role": "staff"}, "change_policy", False),
        ("admin has change_policy",         {"role": "admin"}, "change_policy", True),
        ("admin has view_users",            {"role": "admin"}, "view_users", True),
        ("unknown role has nothing",        {"role": "ghost"}, "view_queue", False),
        ("unknown permission is denied",    {"role": "admin"}, "make_coffee", False),
    ]
    ok = True
    for label, user, perm, expected in cases:
        got = can(user, perm, pol)
        mark = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"[{mark}] {label}: can({perm})={got}")
    print("USER LAYER SELF-TEST PASSED" if ok else "USER LAYER SELF-TEST FAILED")
    raise SystemExit(0 if ok else 1)
