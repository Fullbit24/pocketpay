# Venator HTTP API

The HTTP API is the sole interface the dashboard talks to. It is a thin layer
over the same decision engine the terminal uses: it validates input, calls the
engine, and returns JSON. It decides nothing itself.

This API is the contract for any alternative front end. A mobile client, a
kiosk, or a replacement dashboard can be built against the routes below without
touching `venator/web.py` or the engine. Routes are versioned under `/api/v1`
so that a breaking change is a new prefix rather than a broken integration.

Implementation: `venator/api.py` (`dispatch()`), served by `venator/web.py`.

## Conventions

- Base path: `/api/v1`. All routes return JSON, including errors.
- Server: `127.0.0.1` on port `VENATOR_PORT` (default `8401`). See
  `DEPLOYMENT.md` for how to change the port.
- Request bodies, where required, are JSON objects (`Content-Type:
  application/json`). Query parameters are read from the URL query string.
- Error responses use the shape `{"error": "<message>"}`.
- Status codes in use: `200` success, `400` bad input, `401` an identity was
  presented but is invalid, `403` the caller's role may not perform the
  operation, `404` unknown route or unknown transaction, `405` method not
  allowed on an otherwise valid path, `500` unexpected server error (the message
  is the exception type and text; no traceback is ever returned).

## Identity

A request may present an identity, which selects a role and its permissions
(defined in `config/policy.yaml`). Two carriers are accepted:

- `X-Venator-User: <user_id>:<token>` (HTTP header)
- `?user=<user_id>:<token>` (query parameter, for plain `curl`)

When an identity is present it is resolved to a live user and each route checks
the permission it needs against the user's role. Reads are scoped: a student
sees only their own transactions and a guardian only their linked student's;
staff and admin see all.

When no identity is present, the request is the anonymous operator and keeps
full rights, identical to the behaviour before identity existed. This is what
allows the unauthenticated dashboard and the QA scripts to run unchanged. An
identity that is present but invalid (unknown user, wrong token, inactive
account) is refused with `401`, never silently downgraded to the anonymous
operator.

See `USERS.md` for the roles, the permission matrix, and the demonstration-grade
caveat on the token scheme.


## Route index

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness plus active policy version |
| GET | `/api/v1/policy` | The active policy, as data |
| PATCH | `/api/v1/policy` | Change one policy value (audited) |
| GET | `/api/v1/transactions` | The transaction queue, with filters |
| GET | `/api/v1/transactions/{id}` | One transaction plus its audit trail |
| POST | `/api/v1/transactions/{id}/triage` | Run the agent chain |
| POST | `/api/v1/transactions/{id}/resolve` | Student confirms or cancels |
| POST | `/api/v1/transactions/{id}/guardian` | Guardian approves or declines |
| POST | `/api/v1/transactions/{id}/cancel-window` | Release or take back a confirmation |
| POST | `/api/v1/report` | Triage an arbitrary incoming message |
| GET | `/api/v1/broadcasts` | The campus scam-alert feed |
| GET | `/api/v1/audit` | The audit log, newest first |
| GET | `/api/v1/admin/agent` | The admin agent's current briefing |
| POST | `/api/v1/admin/agent` | Ask the admin agent a question |
| GET | `/api/v1/me` | The identity behind the request (any caller) |
| GET | `/api/v1/users` | The user directory (`view_users`) |
| GET | `/api/v1/users/{id}` | One user (`view_users`, or self) |

---

## GET /api/v1/health

Purpose: liveness check and the currently loaded policy version.

Request: no body, no parameters.

Response `200`:

```json
{
  "status": "ok",
  "time": "2026-11-20T10:15:00Z",
  "policy_version": 3,
  "policy_path": "config/policy.yaml",
  "rules": ["known_scam_link", "repeat_offender", "...", "default_hold"],
  "active_curveballs": ["known_scam_link", "..."],
  "database": "campus_wallet_logs.sqlite"
}
```

- `time` is UTC in `YYYY-MM-DDTHH:MM:SSZ`.
- `policy_path` is relative to the working directory.
- `rules` is the full configured rule order; `active_curveballs` lists only the
  rules currently enabled.

Status codes: `200`.

```bash
curl -s http://127.0.0.1:8401/api/v1/health
```

---

## GET /api/v1/policy

Purpose: read the active policy as data, including every tunable threshold.

Request: no body, no parameters.

Response `200`:

```json
{
  "path": "config/policy.yaml",
  "version": 3,
  "thresholds": [
    {"key": "amounts.auto_clear_limit", "value": 300},
    {"key": "amounts.uncertain_escalate_above", "value": 150},
    {"key": "amounts.human_confirm_always_above", "value": 250}
  ],
  "rule_order": ["known_scam_link", "repeat_offender", "...", "default_hold"],
  "raw": { "version": 3, "currency": "AED", "...": "..." }
}
```

- `thresholds` is the flattened set of scalar keys, each as `{"key", "value"}`.
- `raw` is the parsed policy file.

Status codes: `200`.

```bash
curl -s http://127.0.0.1:8401/api/v1/policy
```

---

## PATCH /api/v1/policy

Purpose: change one policy value at runtime. Every change is audited.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `key` | string | yes | Dotted path to an existing scalar key, e.g. `amounts.auto_clear_limit`. Must contain at least one dot. |
| `value` | number, boolean, or numeric string | yes | The new value. |
| `actor` | string | no | Who made the change. Defaults to `admin`. Recorded in the audit log. |

Constraints, enforced by the handler:

- Only **existing scalar keys** can be changed. The key must already be present
  in `config/policy.yaml`; an unknown key is rejected with `400` and the message
  `unknown policy key: <key>`.
- `rules` and `version` cannot be changed through the API.
- The rule order, the rule modules, and the file's structure are not editable.
  A mis-typed threshold is recoverable; a deleted safety check is not.
- The new value is coerced to the type of the existing value (bool, float, or
  int). A non-numeric value for a numeric key is rejected with `400`.
- The change is written back to the file in place, preserving comments, then the
  policy is reloaded and the change is recorded via `db.log_decision` as
  `policy_changed` with the text `<actor> set <key>: <old> -> <new>`.

Response `200`:

```json
{
  "changed": "amounts.auto_clear_limit",
  "from": 300,
  "to": 350,
  "actor": "admin",
  "policy_version": 3
}
```

Status codes: `200` success; `400` on a malformed key, an unknown key, a
protected key, a non-numeric value for a numeric key, or a key that cannot be
located in the file.

Effect: the new value is read from `config/policy.yaml` at the next decision, so
the change takes effect on the next triage without a restart.

```bash
curl -s -X PATCH http://127.0.0.1:8401/api/v1/policy \
  -H 'Content-Type: application/json' \
  -d '{"key": "amounts.auto_clear_limit", "value": 350, "actor": "ops"}'
```

---

## GET /api/v1/transactions

Purpose: list the transaction queue, optionally filtered.

Query parameters:

| Parameter | Notes |
|---|---|
| `state` | Filter by state. `ALL` or absent means no state filter. |
| `q` | Case-insensitive substring match across `txn_id`, `student`, `recipient_id`, and `purpose`. |
| `limit` | Maximum number of rows (integer). |

Response `200`:

```json
{
  "transactions": [
    {
      "txn_id": "TX-003",
      "student": "Ahmed",
      "student_age": 17,
      "recipient_id": "R-...",
      "amount_aed": 250.0,
      "purpose": "...",
      "state": "RECEIVED",
      "risk_score": null,
      "confidence": null,
      "decision": null
    }
  ],
  "stats": {"RECEIVED": 5, "CLEARED": 3},
  "broadcast": {"message": "...", "created_at": "2026-11-20T10:00:00Z"}
}
```

- `stats` maps each state to its row count.
- `broadcast` is the most recent broadcast, or `null` if there is none.

Status codes: `200`; `400` if `limit` is not an integer.

```bash
curl -s 'http://127.0.0.1:8401/api/v1/transactions?state=PENDING_CONFIRMATION&limit=20'
```

---

## GET /api/v1/transactions/{id}

Purpose: one transaction plus its full audit trail.

Path parameter: `id` — the transaction id.

Response `200`:

```json
{
  "txn": { "...": "all columns of the transaction row" },
  "trail": [
    {"txn_id": "TX-003", "actor": "tool", "step": "...", "detail": "...", "created_at": "..."}
  ],
  "cancel_window_seconds_remaining": 118
}
```

- `cancel_window_seconds_remaining` is present only when the transaction is in
  state `CONFIRMED_CANCELLABLE`.

Status codes: `200`; `404` with `{"error": "no such transaction"}` if the id is
unknown.

```bash
curl -s http://127.0.0.1:8401/api/v1/transactions/TX-003
```

---

## POST /api/v1/transactions/{id}/triage

Purpose: run the agent chain on a transaction (the same chain the terminal
runs). The chain is idempotent-per-state: a transaction already awaiting a
human, already escalated, or already settled returns immediately without a
second model call.

Request body: none required.

Response `200`:

```json
{
  "txn_id": "TX-003",
  "action": "ESCALATED",
  "rule": "unverified_high_risk",
  "explanation": "plain-English reason"
}
```

Status codes: `200`; `404` with `{"error": "no such transaction: <id>"}` if the
id is unknown.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/transactions/TX-003/triage
```

---

## POST /api/v1/transactions/{id}/resolve

Purpose: the student confirms or cancels a pending transaction.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `choice` | string | yes | The student's decision (e.g. confirm / cancel). |
| `who` | string | no | Acting party. Defaults to `student`. |

Response `200`:

```json
{"txn_id": "TX-012", "final": "CLEARED"}
```

Status codes: `200`; `404` with `{"error": "no such transaction: <id>"}`.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/transactions/TX-012/resolve \
  -H 'Content-Type: application/json' \
  -d '{"choice": "confirm", "who": "student"}'
```

---

## POST /api/v1/transactions/{id}/guardian

Purpose: a guardian approves or declines a transaction requiring guardian
approval.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `decision` | string | yes | The guardian's decision. |
| `guardian_id` | string | no | Identifier of the approving guardian. |

Response `200`:

```json
{"txn_id": "TX-0xx", "final": "CLEARED", "approver": "guardian"}
```

Status codes: `200`; `404` with `{"error": "no such transaction: <id>"}`.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/transactions/TX-0xx/guardian \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approve", "guardian_id": "G-01"}'
```

---

## POST /api/v1/transactions/{id}/cancel-window

Purpose: release or take back a confirmation within the cancel window.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `action` | string | yes | `confirm` finalises the confirmation. Any other value is passed to `cancel_or_release` (take back / release). |

Response `200`:

- On `action == "confirm"`: `{"txn_id": "TX-0xx", "state": "<state>"}`
- Otherwise: `{"txn_id": "TX-0xx", "state": "<state>", "detail": "<reason>"}`

Status codes: `200`; `404` with `{"error": "no such transaction: <id>"}`.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/transactions/TX-0xx/cancel-window \
  -H 'Content-Type: application/json' \
  -d '{"action": "confirm"}'
```

---

## POST /api/v1/report

Purpose: triage an arbitrary incoming message through the real chain. The
caller may supply the amount and recipient, so the same endpoint serves the
dashboard's live input box and any future submission channel.

Request body:

| Field | Type | Required | Default |
|---|---|---|---|
| `message` | string | yes | — |
| `student` | string | no | `"You"` |
| `recipient_id` | string | no | `"R-GHOST"` |
| `amount` | number | no | `100.0` |
| `age` | integer | no | `17` |

Behaviour: a new transaction is created with a generated `LIVE-NNN` id and the
chain is run. An empty or whitespace-only `message` is rejected.

Response `200`:

```json
{
  "txn_id": "LIVE-001",
  "action": "ESCALATED",
  "rule": "known_scam_link",
  "explanation": "plain-English reason"
}
```

Status codes: `200`; `400` with `{"error": "message is empty"}` for an empty
message.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/report \
  -H 'Content-Type: application/json' \
  -d '{"message": "selling front row fest pass 50 aed fast https://venator-claims.top/tix"}'
```

---

## GET /api/v1/broadcasts

Purpose: the campus scam-alert feed.

Query parameters:

| Parameter | Notes |
|---|---|
| `limit` | Maximum rows. Default `20`. |

Response `200`:

```json
{"broadcasts": [{"id": 12, "message": "...", "created_at": "2026-11-20T10:00:00Z"}]}
```

Newest first.

Status codes: `200`.

```bash
curl -s 'http://127.0.0.1:8401/api/v1/broadcasts?limit=5'
```

---

## GET /api/v1/audit

Purpose: the audit log, newest first.

Query parameters:

| Parameter | Notes |
|---|---|
| `limit` | Maximum rows. Default `100`. |

Response `200`:

```json
{"entries": [{"txn_id": "TX-003", "actor": "guard", "step": "...", "detail": "...", "created_at": "..."}]}
```

Status codes: `200`; `400` if `limit` is not an integer.

```bash
curl -s 'http://127.0.0.1:8401/api/v1/audit?limit=50'
```

---

## Admin agent endpoints

The admin agent is a technical co-pilot for whoever runs the system. It is
deliberately plain: it reports what the engine is configured to do, what state
the data is in, and why a given case was held. It reads the same policy and
audit log a human would, and it never changes anything on its own — every change
is an explicit API call from the person.

### GET /api/v1/admin/agent

Purpose: the admin agent's current briefing, built from live state.

Request: no body, no parameters.

Response `200`:

```json
{
  "answer": "Ask about thresholds, policy, rules, holds, health, guardian approval, split payments, lookalikes or message text handling.",
  "briefing": {
    "generated": "2026-11-20T10:15:00Z",
    "policy_version": 3,
    "rule_count": 15,
    "enabled_rules": ["known_scam_link", "..."],
    "disabled_rules": [],
    "thresholds": [{"key": "amounts.auto_clear_limit", "value": 300}],
    "queue": {"RECEIVED": 5},
    "awaiting_a_human": 2,
    "awaiting_detail": [
      {"txn_id": "TX-012", "student": "Sara", "student_age": 17, "amount_aed": 800.0, "state": "PENDING_CONFIRMATION", "recipient_id": "R-..."}
    ],
    "notes": ["..."]
  }
}
```

Status codes: `200`.

### POST /api/v1/admin/agent

Purpose: ask the admin agent a question; it answers from real state only.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `question` | string | no | If empty or absent, the route returns the briefing and a prompt listing the topics it can answer. |

Topics recognised (matched by keyword in the question): thresholds / amounts,
policy, rules, holds, stuck items, guardian approval, split payments, lookalike
detection, message-text (injection) handling, and health.

Response `200`:

```json
{
  "question": "why is a payment held?",
  "answer": "2 payment(s) held, awaiting a human: ...",
  "briefing": {"...": "same briefing object as GET"}
}
```

Status codes: `200`.

```bash
curl -s -X POST http://127.0.0.1:8401/api/v1/admin/agent \
  -H 'Content-Type: application/json' \
  -d '{"question": "what are the current thresholds?"}'
```

---

## GET /api/v1/me

Purpose: report the identity behind the request and the permissions its role
holds. Useful to confirm which role a credential resolves to.

Request: an identity via header or query parameter, or none.

Response `200` (identity present):

```json
{
  "anonymous": false,
  "user": {
    "user_id": "U-STAFIA",
    "display_name": "Stafia Mensah",
    "role": "staff",
    "student_name": null,
    "email": "stafia@staff.example",
    "created_at": "2026-11-20 10:00:00",
    "active": true
  },
  "role": "staff",
  "permissions": ["view_queue", "triage", "resolve_any", "view_audit"],
  "scope": "all"
}
```

Response `200` (no identity): `anonymous` is `true`, `user` is `null`, and the
permissions list is the full set, reflecting the anonymous operator.

Status codes: `200`.

```bash
curl -s http://127.0.0.1:8401/api/v1/me -H 'X-Venator-User: U-STAFIA:<token>'
```

---

## GET /api/v1/users

Purpose: the user directory. Tokens are never included in the response.

Permission: `view_users`. Any other role receives `403` with
`{"error": "permission denied: this role does not hold 'view_users'"}`.

Response `200`:

```json
{"users": [{"user_id": "U-ADMIN", "display_name": "System Administrator", "role": "admin", "student_name": null, "email": "admin@campus.example", "created_at": "...", "active": true}]}
```

Status codes: `200`; `403` without the permission; `401` for an invalid identity.

```bash
curl -s http://127.0.0.1:8401/api/v1/users -H 'X-Venator-User: U-ADMIN:<token>'
```

---

## GET /api/v1/users/{id}

Purpose: one user record.

Permission: `view_users` (any record), or the user reading its own record. Any
other caller receives `403`.

Path parameter: `id` — the user id.

Response `200`: the same user object shape as one entry of `/users`.

Status codes: `200`; `403` without the permission (and not the user's own
record); `404` with `{"error": "no such user"}` if the id is unknown.

```bash
curl -s http://127.0.0.1:8401/api/v1/users/U-RAYAN -H 'X-Venator-User: U-ADMIN:<token>'
```

---

## Method and error behaviour

- An unknown path under any supported method returns `404` with
  `{"error": "no such route"}`.
- A valid path reached with an unsupported method (e.g. `DELETE`) returns `405`
  with `{"error": "method not allowed"}`. The server implements `GET`, `POST`,
  and `PATCH` only.
- A `ValueError` raised inside the engine is returned as `400`; a `KeyError` as
  `400` with `{"error": "missing or unknown: <key>"}`; any other exception as
  `500` with `{"error": "<ExceptionType>: <message>"}`. A traceback is never
  sent to a client.
