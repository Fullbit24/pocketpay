# web.py - the HTTP server.
#
# Zero external dependencies: stdlib http.server only. This layer owns no
# behaviour. It serves one HTML document and forwards every other request to
# venator/api.py, which in turn calls the same engine the terminal does.
#
#   run:  ~/.venv/bin/python main.py web   ->  http://127.0.0.1:8401
#
# The single page app talks only to /api/v1. Because the API is versioned, a
# different front end - a mobile client, a kiosk, a replacement dashboard - can
# be built against the same server without touching this file.

import json
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import api, db
from .ui_theme import THEME

PORT = int(os.environ.get("VENATOR_PORT", 8401))

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.join(_HERE, "frontend.html")

# Static HTML pages served by path. Each is read from disk on request (like the
# shell) so markup stays editable without a restart. The theme marker is
# substituted into every page, so all four share one design system.
_PAGES = {
    "/": "gate.html",
    "/login": "login.html",
    "/wallet": "wallet.html",
    "/app": "frontend.html",
    "/index.html": "frontend.html",
}


def _page(name=None):
    """One HTML page with the shared theme substituted in.

    `name` is a filename inside the package (e.g. "login.html"); it defaults to
    the console shell. The name is matched against a fixed registry, never used
    to build a path from user input, so a made-up page name cannot escape the
    package directory.
    """
    if name is None:
        path = _FRONTEND
    else:
        path = os.path.join(_HERE, name)
        if not os.path.abspath(path).startswith(_HERE + os.sep):
            path = _FRONTEND
    with open(path, encoding="utf-8") as fh:
        return fh.read().replace("<!--THEME-->", THEME)


class Handler(BaseHTTPRequestHandler):
    server_version = "Venator"

    # ---------- plumbing ----------
    def _bytes(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._bytes(json.dumps(obj).encode(), "application/json", code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, format, *args):
        pass  # keep the console clean while presenting

    def _dispatch(self, method):
        u = urlparse(self.path)
        # Strip a trailing slash so /login and /login/ are the same page.
        page = u.path.rstrip("/") or "/"
        if page in _PAGES:
            try:
                return self._bytes(_page(_PAGES[page]).encode(), "text/html; charset=utf-8")
            except OSError as e:
                return self._json({"error": f"front end not found: {e}"}, 500)
        if u.path == "/favicon.ico":
            return self._bytes(b"", "image/x-icon", 204)

        con = db.connect()
        try:
            query = {k: v[0] for k, v in parse_qs(u.query).items()}
            out, code = api.dispatch(con, method, u.path, query, self._body())
            self._json(out, code)
        finally:
            con.close()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")


def serve(port=None):
    port = port or PORT
    print(f"Venator -> http://127.0.0.1:{port}   (ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
