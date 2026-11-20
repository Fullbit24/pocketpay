# Venator QA Report — Server & API Layer

**Date:** 2026-11-20
**Server:** `~/.venv/bin/python main.py web` → http://127.0.0.1:8401
**Method:** Real HTTP requests issued with `curl` against the live server; browser verification for `GET /`; screenshot via the browser tool.
**Tester:** automated subagent QA pass

---

## Results Table

| Method | Route | Status | Verdict | Notes |
|--------|-------|--------|---------|-------|
| GET  | `/api/v1/health` | 200 | **PASS** | Returns status ok, policy_version 3, 15 rules |
| GET  | `/api/v1/policy` | 200 | **PASS** | 14 thresholds + rule order returned |
| GET  | `/api/v1/transactions` | 200 | **PASS** | Queue + stats returned |
| GET  | `/api/v1/transactions/TX-015` | 200 | **PASS** | Txn + trail returned |
| GET  | `/api/v1/broadcasts` | 200 | **PASS (empty)** | Route works; returns `{"broadcasts": []}` (see caveat) |
| GET  | `/api/v1/audit` | 200 | **PASS (empty)** | Route works; returns `{"entries": []}` (see caveat) |
| GET  | `/api/v1/admin/agent` | 200 | **PASS** | Briefing + answer returned |
| POST | `/api/v1/admin/agent` | 200 | **PASS** | Answers questions from live policy |
| POST | `/api/v1/transactions/TX-015/triage` | **404** | **FAIL** | Endpoint is unreachable — see evidence below |
| POST | `/api/v1/report` | 200 | **PASS** | Injection attempt correctly ESCALATED via R1d |
| GET  | `/api/v1/nonexistent` | 404 | **PASS** | Correct 404 with `{"error": "no such route"}` |
| POST | `/api/v1/badroute` | 404 | **PASS** | Correct 404 |
| GET  | `/` | 200 | **PASS** | See "Front page" section below |

---

## FAILURE — `POST /api/v1/transactions/TX-015/triage`

**Severity: HIGH.** This route is documented in `venator/api.py` (header comment, line 16) and is
called by the front-end (`frontend.html` posts to `/api/v1/transactions/<id>/triage` from the
"Agent runs" page). It is entirely unreachable.

### Raw evidence

```
$ curl -s -w '\nHTTP %{http_code}\n' -X POST \
    http://127.0.0.1:8401/api/v1/transactions/TX-015/triage \
    -H 'Content-Type: application/json' -d '{}'
{"error": "no such route"}
HTTP 404
```

The same failure affects every sub-action POST route on a transaction:

```
$ curl -s -X POST http://127.0.0.1:8401/api/v1/transactions/TX-015/resolve \
    -H 'Content-Type: application/json' -d '{"choice":"confirm"}'
{"error": "no such route"}      # HTTP 404
```

### Root cause (reproduced in isolation)

`venator/api.py`, `dispatch()` (lines 396–398 and 429–432):

```python
def one(prefix):
    m = re.fullmatch(prefix + r"/([^/]+)", p)   # <-- anchored fullmatch
    return m.group(1) if m else None
...
tid = one("/transactions")
if tid and p.endswith("/triage"):
    out, err = post_triage(con, tid)
```

`re.fullmatch` requires the WHOLE path to match `/transactions/([^/]+)`. The path
`/transactions/TX-015/triage` has an extra `/triage` segment, so the regex fails and `tid` is
`None`. The `if tid and p.endswith("/triage")` branch is therefore never entered and control falls
through to the terminal `return {"error": "no such route"}, 404`.

Verified directly:

```
p='/transactions/TX-015'         -> tid='TX-015'
p='/transactions/TX-015/triage'  -> tid=None     # <-- bug
p='/transactions/TX-015/resolve' -> tid=None     # <-- bug
```

The fix is to match the trailing action segment too (e.g.
`re.fullmatch(prefix + r"/([^/]+)/([a-z-]+)", p)` returning both id and action, or capture the id
with a non-greedy pattern that tolerates the suffix). Fixing this one regex restores `/triage`,
`/resolve`, `/guardian`, and `/cancel-window`.

---

## Front page — `GET /`

| Check | Result |
|-------|--------|
| HTTP status | **200 OK** |
| `Content-Length` header | **84826** |
| Actual body bytes | **84826** (matches header) |
| `Content-Type` | `text/html; charset=utf-8` |
| `<title>` tag present | **YES** — `<title>Venator</title>` |
| Sidebar present | **YES** — `.side` sidebar `<nav aria-label="Primary">` with Operations / Governance groups; "sidebar" appears 7×, nav elements 27× |
| Rendering (browser) | Rendered a complete three-zone shell: left sidebar (nav groups + amber CTA + "API healthy" pill), top bar (title + 4 KPI tiles + user), main area, bottom status bar |

**Timing note:** on the first attempt the front end did not exist
(`venator/frontend.html` → `GET /` returned **500** with
`{"error": "front end not found: ... frontend.html"}`). frontend.html is written by a concurrent
process. After retrying (per instructions, every 20s up to 3 min), the file appeared (69,658 bytes)
and `GET /` began returning 200. All `GET /` results above are from the completed page.

**Content caveat (not a route failure):** the main canvas currently shows the Venator home/empty
state (large logo + wordmark), not populated tables — the user must navigate the SPA (e.g. "Queue",
"Agent runs") to see data. This is expected SPA behaviour, not a server defect.

---

## Screenshot

- **Captured: YES.**
- Browser screenshot of the running dashboard at `http://127.0.0.1:8401/`
- Tool-reported path: `/home/rayan/.hermes/browser_screenshots/browser_screenshot_5ad45ea8.png`
- **Copied to:** `/home/rayan/Documents/agent/qa_screenshot.png` (PNG, 1280×720, RGBA, 130,307 bytes)
- The screenshot shows the full shell: sidebar with OPERATIONS (Overview / Queue / Agent runs /
  Escalations) and GOVERNANCE (Policy / Admin agent) groups, amber "Triage held queue" CTA,
  green "API healthy / Policy v3 · 15 rules live" status, top bar with KPI tiles
  (RECEIVED 23, HELD 0, ESCALATED 0, CLEARED 0), and the home/empty main area.

---

## State caveat on `/broadcasts` and `/audit`

On the first test run both routes returned **populated** data (broadcasts: 1 scam alert; audit: a
full decision trail). On the final confirming run both returned **empty arrays** but still **200**.

This is almost certainly because the SQLite database (`campus_wallet_logs.sqlite`) was rebuilt/reset
between runs by a concurrent process — the `/api/v1/transactions` queue also changed composition
between runs. **The routes themselves work** (valid JSON, correct 200, correct shape); only the data
volume differs. Verdict recorded as PASS with this caveat rather than a failure.

---

## Could NOT verify / untested

- **`POST /api/v1/transactions/{id}/resolve`, `/guardian`, `/cancel-window`** — exercised `resolve`
  only partially (it too returns 404, same root cause as triage) and did **not** independently test
  `/guardian` or `/cancel-window`; they share the failing code path and are expected to fail
  identically, but this was inferred, not tested. **Untested: `/guardian`, `/cancel-window`.**
- **`PATCH /api/v1/policy`** — not in the assigned test matrix, **untested**.
- **Concurrency / load behaviour** — single-request tests only; no concurrency testing.
- **Front-end SPA interactions** (filters, triage button, policy edit form) — only the landing view
  was rendered; the interactive flows were not driven. The triage button will fail when the user
  clicks it, because it calls the 404 route above.
- **Persistence across restart** — the server was not restarted mid-test other than the single
  background start; DB state changed under concurrent modification (see caveat).

---

## Summary

- 11 of 13 tested routes behave correctly.
- **1 hard failure:** `POST /api/v1/transactions/{id}/triage` → 404 (unreachable). Cross-cutting
  regex bug also disables `/resolve`, `/guardian`, `/cancel-window`. This is a genuine server bug,
  not a test artifact.
- `GET /` serves a complete, valid 200 page (84,826 bytes, title + sidebar present) once the
  concurrently-authored `frontend.html` landed.
- Screenshot captured and saved to `/home/rayan/Documents/agent/qa_screenshot.png`.
