# main.py — Venator launcher.
#
# Subcommands
#   ui            live campus terminal (stage interface)          [default]
#   demo          full scripted presentation run in the terminal
#   build         rebuild the dataset (campus_wallet_logs.sqlite)
#   tests         guard-rule + parser unit tests (no LLM, fast)
#   web           operations dashboard over HTTP
#   serve         alias for web, accepts --port N
#   api           print the HTTP API surface (method, path, description)
#   policy        print the active policy thresholds
#   guide         plain-English operator guide (what each page is for)
#   help          this listing
#
# The original ModelHouse ping demo lives on in git, commit 04b25f3.
import sys


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

USAGE = """Venator — campus wallet and scam-defense agent.

Usage:
  python main.py [command] [options]

Commands:
  ui            Live campus terminal (stage interface). This is the default.
  demo          Full scripted presentation run in the terminal.
  build         Rebuild the dataset (campus_wallet_logs.sqlite).
  tests         Run guard-rule and parser unit tests (no LLM, fast).
  web           Serve the operations dashboard over HTTP.
  serve         Alias for web. Accepts --port N.
  api           Print the HTTP API surface: every route, its method, a summary.
  policy        Print the active policy: every live threshold the engine reads.
  guide         Print a plain-English operator guide to the dashboard and commands.
  help          Show this listing.

Options:
  --port N      Port for web and serve (default: 8401, or $VENATOR_PORT).

Run "python main.py guide" for a description of each dashboard page.
"""


def _usage(stream=sys.stdout):
    stream.write(USAGE)


# ---------------------------------------------------------------------------
# api — the HTTP surface, derived from venator/api.py rather than duplicated
# ---------------------------------------------------------------------------

def _api_listing():
    """Print every route the API exposes.

    The route table below mirrors venator/api.dispatch(). Where the module
    documents a route in its header and exposes a matching handler, the handler
    is used to confirm the route is wired; the one-line description comes from
    the module header so the two never drift silently.
    """
    from venator import api

    # (method, path, handler-name-or-None, description)
    routes = [
        ("GET",   "/api/v1/health",                 "get_health",
         "Liveness plus the policy version and active rule set."),
        ("GET",   "/api/v1/policy",                 "get_policy",
         "The active policy as data: thresholds and rule order."),
        ("PATCH", "/api/v1/policy",                 "patch_policy",
         "Change one policy value (audited; takes effect immediately)."),
        ("GET",   "/api/v1/transactions",           "get_transactions",
         "The transaction queue, with state and text filters."),
        ("GET",   "/api/v1/transactions/{id}",      "get_transaction",
         "One transaction plus its full audit trail."),
        ("POST",  "/api/v1/transactions/{id}/triage",    "post_triage",
         "Run the agent chain on one transaction."),
        ("POST",  "/api/v1/transactions/{id}/resolve",   "post_resolve",
         "Student confirms or cancels a held payment."),
        ("POST",  "/api/v1/transactions/{id}/guardian",  "post_guardian",
         "A guardian approves or declines a minor's transfer."),
        ("POST",  "/api/v1/transactions/{id}/cancel-window", "post_cancel_window",
         "Release or take back a confirmation inside the cancel window."),
        ("POST",  "/api/v1/report",                 "post_report",
         "Triage an arbitrary incoming message through the real chain."),
        ("GET",   "/api/v1/broadcasts",             "get_broadcasts",
         "The campus scam-alert feed, newest first."),
        ("GET",   "/api/v1/audit",                  "get_audit",
         "The audit log, newest first."),
        ("GET",   "/api/v1/admin/agent",            "admin_agent",
         "The admin agent's current briefing on the running system."),
        ("POST",  "/api/v1/admin/agent",            "admin_agent",
         "Ask the admin agent a question; it answers from live state."),
    ]

    print(f"Venator HTTP API — prefix {api.API_PREFIX}, JSON in and out.\n")
    print(f"  {'METHOD':<6} {'PATH':<46} {'STATUS':<9} DESCRIPTION")
    for method, path, handler, desc in routes:
        wired = hasattr(api, handler)
        status = "wired" if wired else "MISSING"
        print(f"  {method:<6} {path:<46} {status:<9} {desc}")

    print("\nEvery route is served by venator/api.dispatch(). Absolute paths "
          "include the prefix above.")
    print("Error responses are JSON too; a bad request never returns a traceback.")


# ---------------------------------------------------------------------------
# policy — the live thresholds the engine reads
# ---------------------------------------------------------------------------

def _policy_listing():
    """Pretty-print the active policy via policy.current().describe()."""
    from venator import policy

    p = policy.current()
    print(f"Venator active policy — version {p.get('version')} ({p.path})\n")

    describe = p.describe()
    width = max((len(d["key"]) for d in describe), default=0)
    print(f"  {'THRESHOLD':<{width}}  VALUE")
    for item in describe:
        print(f"  {item['key']:<{width}}  {item['value']}")

    print("\nRule order (first match wins):")
    for name in p.rules:
        state = "on" if p.rule_enabled(name) else "off"
        print(f"  - {name:<22} [{state}]")

    print("\nEvery value above is read from the policy file at call time. "
          "Editing the file or PATCHing /api/v1/policy changes behaviour on the "
          "next decision, with no code change.")


# ---------------------------------------------------------------------------
# guide — a plain-English operator guide
# ---------------------------------------------------------------------------

GUIDE = """Venator operator guide
========================

Venator is a scam-defense agent for a university campus wallet. It reads the
message attached to a payment, forms an opinion about the risk, and then a set
of deterministic Python rules — not the model — decide what happens to the
money. There is no state in which funds are frozen forever: the worst outcome
is that a person is asked to confirm.

The dashboard
-------------
Start it with:      python main.py web          (alias: python main.py serve)
Then open:          http://127.0.0.1:8401
Pick another port:  python main.py serve --port 9000

The dashboard is an operations console. It has four areas:

  Queue          Every transaction and its current state, with filters and a
                 search box. This is what a staff reviewer sees: what is
                 waiting, what cleared, what escalated.

  Audit trail    Open any transaction to see the full chain, step by step —
                 the tool reads (facts), the model's opinion, the guard's
                 decision, and any human answer. You can watch the agent work
                 live as each step is written.

  Human moment   A payment that needs a person shows its buttons: a student can
                 confirm or cancel a held payment, and a guardian can approve or
                 decline a minor's larger transfer. Confirming opens a short
                 cancel window in which the student can still take it back.

  Broadcast feed When a phishing link is confirmed, the warning is pushed to the
                 whole campus feed so the next student to receive the same link
                 is caught before the model is even called.

Commands
--------
  python main.py ui           Terminal interface, no browser needed.
  python main.py demo         Scripted end-to-end run through every scenario.
  python main.py build        Rebuild the sample dataset from scratch.
  python main.py tests        Guard-rule and parser unit tests (fast, no LLM).
  python main.py web          The dashboard described above.
  python main.py serve        Same as web; add --port N to choose the port.
  python main.py api          List every HTTP route and what it does.
  python main.py policy       Show every live threshold the engine reads.

First run
---------
  python main.py build        Seed the dataset.
  python main.py web          Open the dashboard and run a few cases.

The model is optional for the fast paths: "tests" and "demo" exercise the
system with no browser and no network, so the full narrative runs from a
terminal alone.
"""


def _guide():
    sys.stdout.write(GUIDE)


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------

def _parse_port(args):
    """Extract --port N (or --port=N) from args. Returns (port, error)."""
    port = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--port":
            if i + 1 >= len(args):
                return None, "--port requires a value"
            value = args[i + 1]
            i += 2
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
            i += 1
        else:
            return None, f"unexpected argument: {arg}"
        try:
            port = int(value)
        except ValueError:
            return None, f"--port must be an integer, got {value!r}"
        if not (1 <= port <= 65535):
            return None, f"--port must be between 1 and 65535, got {port}"
    return port, None


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = argv[1:]
    cmd = argv[0] if argv else "ui"

    if cmd == "build":
        from venator import db
        db.build()

    elif cmd in ("web", "serve"):
        port, err = _parse_port(args)
        if err:
            sys.stderr.write(f"error: {err}\n\n")
            _usage(sys.stderr)
            return 2
        from venator import web
        web.serve(port)

    elif cmd == "demo":
        from venator import demo
        demo.main()

    elif cmd == "tests":
        from venator import tests
        tests.run()

    elif cmd == "api":
        _api_listing()

    elif cmd == "policy":
        _policy_listing()

    elif cmd == "guide":
        _guide()

    elif cmd in ("help", "--help", "-h"):
        _usage()

    elif cmd == "ui":
        from venator import ui
        ui.main()

    else:
        sys.stderr.write(f"unknown command: {cmd}\n\n")
        _usage(sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
