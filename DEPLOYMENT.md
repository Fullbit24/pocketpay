# Venator Deployment Guide

This guide covers running the Venator dashboard and its HTTP API locally,
configuring it, changing behaviour at runtime, running it as a background
service, and troubleshooting common failures.

For the HTTP contract that any front end builds against, see `API.md`.

## 1. Requirements

- Python 3.11. The launcher and engine use the standard library plus `PyYAML`
  and the `openai` client.
- A local model endpoint at `http://127.0.0.1:8080/v1/` for the analysis step.
  This is the only external dependency at runtime. If it is unavailable the
  system degrades rather than failing (see section 7).
- SQLite. Provided by the Python standard library; no server required.
- A virtual environment. This guide uses `/home/rayan/.venv`.

Install dependencies into the venv:

```bash
/home/rayan/.venv/bin/python -m pip install pyyaml openai
```

## 2. Local run

All commands are launched through `main.py`.

```bash
PY=/home/rayan/.venv/bin/python
cd /home/rayan/Documents/agent

$PY main.py build      # seed the dataset -> campus_wallet_logs.sqlite
$PY main.py web        # dashboard + HTTP API -> http://127.0.0.1:8401
$PY main.py tests      # guard-rule + parser unit tests (no model, no network)
$PY main.py demo       # scripted end-to-end run in the terminal
$PY main.py ui         # terminal interface (fallback)
```

Running `main.py` with no subcommand defaults to the terminal interface. `build`
must be run at least once before `web`, because the dashboard reads the SQLite
database that `build` creates.

The web server binds `127.0.0.1` only — it is not reachable from other hosts.

## 3. Configuration

Two surfaces configure the system.

### 3.1 Policy file: `config/policy.yaml`

Every threshold and toggle the guard reads lives in this file. No threshold is
hard-coded in Python. Key sections:

| Section | Controls |
|---|---|
| `amounts` | Auto-clear limit, mid-risk escalation amount, and the amount above which a human always confirms. |
| `risk` | High and mid risk thresholds, and the minimum model confidence below which a human is asked. |
| `split_payment` | Rolling-window split-payment detection: window, trigger total, minimum counted amount. |
| `lookalike` | Name-similarity threshold for lookalike vendors, plus digit-swap normalisation. |
| `injection` | Instruction-like patterns removed from message text before the model sees it. |
| `cancel_window` | Seconds a student has to take back a confirmation. |
| `guardian` | Minor age, approval amount, and which tiers count as peer transfers. |
| `recipients` | Trusted tiers and the confirmed-report count that escalates a recipient. |
| `services` | Directory lookup timeout. On timeout the transfer holds, never blocks. |
| `rules` | The guard rule order, first match wins. Removing an entry switches that rule off; the system still runs. |
| `roles` | Roles and the permissions each holds. Which role may do what is declared here, not in code. See `USERS.md`. |

Change a value here and restart the process for it to apply everywhere.

### 3.2 Environment variables

The application reads exactly one environment variable. The full list of
variables the code reads is in `.env.example`.

| Variable | Read by | Default | Effect |
|---|---|---|---|
| `VENATOR_PORT` | `venator/web.py` | `8401` | Port the dashboard / HTTP API binds to. The interface stays `127.0.0.1`. |

To apply an `.env` file for a manual run:

```bash
set -a; . ./.env; set +a
/home/rayan/.venv/bin/python main.py web
```

## 4. Changing a threshold live through the API

A single policy value can be changed at runtime with `PATCH /api/v1/policy`,
without editing the file by hand or restarting.

```bash
curl -s -X PATCH http://127.0.0.1:8401/api/v1/policy \
  -H 'Content-Type: application/json' \
  -d '{"key": "amounts.auto_clear_limit", "value": 350, "actor": "ops"}'
```

Rules of the operation:

- The key must be an existing scalar key in `config/policy.yaml`, written as a
  dotted path. An unknown key is rejected.
- `rules` and `version` cannot be changed through the API. The rule order,
  the rule modules, and the file structure are not editable this way.
- The new value is coerced to the type of the existing value; a non-numeric
  value for a numeric key is rejected.
- The file is rewritten in place, preserving comments, and the policy is
  reloaded immediately.

Effect: the policy is reloaded immediately, so the new value is picked up at the
next decision and takes effect on the next triage with no restart. Every change is
audited: `db.log_decision` records an entry of the form
`<actor> set <key>: <old> -> <new>` under the `policy_changed` step, visible in
`GET /api/v1/audit`.

Response:

```json
{"changed": "amounts.auto_clear_limit", "from": 300, "to": 350, "actor": "ops", "policy_version": 3}
```

## 5. Running as a background service

A systemd user unit is provided at `deploy/venator.service`. It runs the
dashboard with the venv python, sets `WorkingDirectory` to the repository, sets
`VENATOR_PORT`, and restarts on failure.

Verify the unit parses before installing:

```bash
systemd-analyze verify /home/rayan/Documents/agent/deploy/venator.service
```

Install and start it as a user service:

```bash
mkdir -p ~/.config/systemd/user
cp /home/rayan/Documents/agent/deploy/venator.service ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now venator.service

# inspect
systemctl --user status venator.service
journalctl --user -u venator.service -f
```

To keep the service running after logout, enable lingering for the account:

```bash
loginctl enable-linger "$USER"
```

Stop or disable:

```bash
systemctl --user stop venator.service
systemctl --user disable venator.service
```

## 6. Health check

The liveness endpoint returns the running status and the loaded policy version.

```bash
curl -s http://127.0.0.1:8401/api/v1/health
```

Expected response:

```json
{"status": "ok", "time": "...", "policy_version": 3, "policy_path": "config/policy.yaml", "rules": ["..."], "active_curveballs": ["..."], "database": "campus_wallet_logs.sqlite"}
```

A `200` with `"status": "ok"` means the server is up and the policy loaded.
Failure to connect means the process is not running or the port differs from
what you are querying.

## 7. Model endpoint unavailable

The analysis step calls a local model at `http://127.0.0.1:8080/v1/`. If that
endpoint is down, slow, or returns nothing usable, the system does not fail:

- The parser returns no analysis, and the guard treats a missing analysis as
  **no opinion, not as safe**.
- The affected transaction is held (`HOLD`) and the reason is written to the
  audit trail before the verdict lands.
- The server stays up and continues serving the queue, the audit log, and the
  admin agent. The wallet is never frozen.

The same graceful behaviour covers a low-confidence model result and a directory
service timeout: in every case the transfer holds, the reason is logged, and a
human resolves it. No configuration change is required to survive a model
outage; restart the model and subsequent triages use it again.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `web` starts but the page errors, or requests return a database error | `campus_wallet_logs.sqlite` is missing | Rebuild the dataset: `/home/rayan/.venv/bin/python main.py build`, then restart. |
| `OSError: [Errno 98] Address already in use` on start | The port (`VENATOR_PORT`, default `8401`) is already bound | Find and stop the other listener (`ss -ltnp \| grep 8401`), or start with a different port: `VENATOR_PORT=8402 /home/rayan/.venv/bin/python main.py web`. |
| Dashboard unreachable at the expected URL | Process not running, wrong port, or crashed | Check `systemctl --user status venator.service` or the terminal output; confirm the port with `GET /api/v1/health`. |
| Service stops after logout | Systemd user session is torn down | Enable lingering: `loginctl enable-linger "$USER"`. |
| `systemd-analyze verify` reports `Command ... not found` | The venv python path in the unit is wrong | Correct `ExecStart` in the unit to the real venv interpreter, then `systemctl --user daemon-reload`. |
| Every transaction holds with an "engine unavailable" style reason | The local model at `127.0.0.1:8080` is down | Restart the model endpoint. This is expected degradation, not a fault (see section 7). |
| Model call hangs and transfers hold | Directory service timeout or slow model | Expected: on timeout the transfer holds. Tune `services.directory_timeout_seconds` in the policy if needed. |
| A `PATCH /api/v1/policy` returns `400 unknown policy key` | The key is not an existing scalar key | Use `GET /api/v1/policy` to list valid keys (`thresholds`), then retry with the exact dotted path. |
| Changes to `config/policy.yaml` by hand have no effect | The file was edited while the process ran | Restart the process, or change the value through `PATCH /api/v1/policy`, which reloads immediately. |
| `import yaml` / `import openai` fails | Dependencies not installed in the venv | `/home/rayan/.venv/bin/python -m pip install pyyaml openai`. |
