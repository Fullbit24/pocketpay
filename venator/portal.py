# portal.py - the multi-surface portal shell.
#
# The web app is one HTML host document (frontend.html) plus one HTML fragment
# per role surface (venator/surfaces/*.html). This module owns the registry that
# binds them together: which surface each role lands on, which navigation groups
# exist, and how a fragment is read from disk.
#
# Design
# ------
# The host is deliberately thin. It carries the shell (top bar, sidebar, footer)
# and a small vanilla-JS router. Each surface fragment is inert markup with a
# <template> of its own plus a <script> that owns fetch/state/render for that
# surface only. When the router switches surfaces it injects the fragment's
# markup into the main region and runs its script, so a surface never has to
# know the others exist.
#
# Nothing here decides authority. Which groups a person may see is decided by
# the permissions the API returns from GET /api/v1/me, and every surface's data
# comes from the same scoped routes the terminal uses. This module only knows
# the *names* of things: role -> landing surface, and permission -> group.
#
# The pieces are read from disk on every page build (see web.py: _page()), so a
# fragment is editable without restarting the server.

import os

# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------
# The five surfaces the portal ships. Each entry:
#   id      - stable key, used in the URL hash (#student), data-surface, and the
#             fragment filename <id>.html
#   label   - sidebar link text
#   group   - the navigation group it lives under
#   title / blurb / crumb
#           - the header line the surface opens with
#   perms   - the permissions that grant access; ANY one of them is enough. The
#             list is matched against GET /api/v1/me. An empty list means the
#             surface is open to any authenticated identity (needed for the
#             student's own wallet, which every self-viewing role has).
#   roles   - the roles that land here by default. Purely a convenience for the
#             identity picker; the perms list is what actually gates the nav.

SURFACES = (
    {
        "id": "wallet",
        "label": "My wallet",
        "group": "My account",
        "title": "My wallet",
        "blurb": "Your balance, your transfers, and anything waiting on you or "
                 "your guardian.",
        "crumb": "My account",
        "perms": [],
        "roles": ("student",),
    },
    {
        "id": "children",
        "label": "My children",
        "group": "My account",
        "title": "My children",
        "blurb": "Activity and pending approvals for each student linked to "
                 "your account.",
        "crumb": "My account",
        "perms": ["guardian_approve"],
        "roles": ("guardian",),
    },
    {
        "id": "class",
        "label": "My class",
        "group": "Teaching",
        "title": "My class",
        "blurb": "Enrolment, peer transfers and incidents for the students in "
                 "your class, with a risk summary for each.",
        "crumb": "Teaching",
        "perms": ["view_queue", "triage"],
        "roles": ("teacher", "staff"),
    },
    {
        "id": "institution",
        "label": "Institution",
        "group": "Administration",
        "title": "Institution",
        "blurb": "Institution-wide position, the full queue, the policy console, "
                 "the user directory and the audit log.",
        "crumb": "Administration",
        "perms": ["change_policy", "view_users", "view_audit"],
        "roles": ("admin",),
    },
    {
        "id": "review",
        "label": "Review desk",
        "group": "Operations",
        "title": "Review desk",
        "blurb": "The escalation queue, each case with its full decision trail "
                 "and a resolve action.",
        "crumb": "Operations",
        "perms": ["resolve_any", "view_audit"],
        "roles": ("staff",),
    },
)

# Navigation groups, in sidebar order. A group with no visible surface for the
# current identity is hidden by the router.
GROUPS = (
    {"id": "account", "label": "My account"},
    {"id": "teaching", "label": "Teaching"},
    {"id": "operations", "label": "Operations"},
    {"id": "administration", "label": "Administration"},
)

SURFACES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "surfaces")


def by_id(surface_id):
    """One surface descriptor by id, or None."""
    for s in SURFACES:
        if s["id"] == surface_id:
            return s
    return None


def fragment_path(surface_id):
    """Absolute path to a surface fragment, or None if the id is unknown.

    The id is matched against the registry rather than interpolated into a
    path, so a made-up id cannot walk outside the surfaces directory.
    """
    s = by_id(surface_id)
    if s is None:
        return None
    return os.path.join(SURFACES_DIR, f"{s['id']}.html")


def read_fragment(surface_id):
    """The markup of one surface fragment, or None when it is unavailable.

    A missing file is not an error the caller must handle: the host renders a
    short explanatory panel instead, so the shell never comes up blank.
    """
    path = fragment_path(surface_id)
    if path is None or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def surface_index():
    """The registry as plain data, for the host's router.

    Kept JSON-safe (tuples become lists) so web.py could embed it directly if a
    future host chooses to, instead of hard-coding the map in the page.
    """
    return {
        "surfaces": [
            {**{k: v for k, v in s.items() if k != "roles"},
             "roles": list(s["roles"])}
            for s in SURFACES
        ],
        "groups": [dict(g) for g in GROUPS],
    }


def landing_for(role):
    """The surface id a role lands on, or None if the role has no home.

    A role is matched on the `roles` convenience list first; a role that names
    no surface (for example a future 'teacher' that only holds view_queue) falls
    back to the first surface its permissions could open, which the router
    resolves from /api/v1/me at load time.
    """
    if not role:
        return None
    for s in SURFACES:
        if role in s["roles"]:
            return s["id"]
    return None


if __name__ == "__main__":
    print(f"portal: {len(SURFACES)} surfaces, {len(GROUPS)} groups")
    for s in SURFACES:
        path = fragment_path(s["id"])
        present = "present" if path and os.path.exists(path) else "MISSING"
        print(f"  {s['id']:<12} {s['group']:<16} {present:<8} {os.path.basename(path or '')}")
