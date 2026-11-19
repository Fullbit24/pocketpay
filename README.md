# PocketPay — Campus Wallet & Scam Defense Agent

TechX Dubai'26 AI Agent Arena · Domain 1: FinTech Fraud & Compliance (High School Track)

## The one-sentence architecture
**The LLM only ever has an opinion. Deterministic Python owns every decision and every
move of money.** `agent.py` thinks → `guard.py` decides → `tools.py` writes state.

```
message + txn metadata
   │
   ├─ tools.get_transaction_context()      facts
   ├─ LLM (local qwen3 via capri.ModelHouse)  → risk_score, confidence, indicators, plain-language why
   ├─ tools.check_scam_link()              known-phishing URL lookup (deterministic)
   ├─ tools.verify_campus_recipient()      trust tier / age / scam reports (times out → graceful HOLD)
   ├─ guard.decide()                       RULES R1–R7, first match wins: CLEAR | PENDING_CONFIRMATION | ESCALATED
   └─ tools.update_payment_state()         the ONLY writer of wallet state; illegal transitions raise
```

No BLOCK state exists anywhere → structurally impossible to permanently freeze a wallet.
Pending confirmations are stored in SQLite and re-triaging a pending txn hits the
**loop guard** (no second LLM call, no repeated asking). If the LLM or the directory
service dies, guard rules R2/R2c/R3 hold the transaction for a human — never crash, never block.

## Run it
```bash
PY=~/.venv/bin/python   # needs openai pkg; LLM served locally at 127.0.0.1:8080
$PY -m pocketpay.db      # build dataset → campus_wallet_logs.sqlite
                         # (14 authored scenarios + 7 REAL messages mined from
                         #  the UCI SMS Spam Collection in data/ — see miner.py)
$PY -m pocketpay.tests   # guard-rule + JSON-parser unit tests (no LLM needed)
$PY -m pocketpay.demo    # full 21-scenario presentation run + staff-hours summary
$PY -m pocketpay.ui      # live interactive campus terminal (stage interface)
```

## What the demo shows (maps to the brief's checklist)
- **14 scenarios** (>10 required): canteen buys auto-clear, the brief's exact ticket scam,
  credential-phishing, repeat offenders, low-confidence slang (flagged without guessing),
  3 uncertain mid-risk cases escalated to staff, a **directory outage** with graceful fallback,
  confirm → CLEARED and cancel → CANCELLED flows.
- **Visible decision log** per txn — every chain ≥3 steps, streamed live.
- **Stateful memory**: TX-007 is triaged twice in the demo; second pass returns instantly via loop guard.
- **Human confirmation UI**: scripted in demo, live `r <txn> confirm|cancel` in ui.py.

## Stretch goals shipped
- Auto **scam-alert broadcast**: confirmed phishing links go on a campus feed; TX-013 (a second
  student hitting the same link as TX-003) is caught instantly by URL — before the LLM even speaks.
- **Staff-hours-saved** math printed at the end of the demo run.
- Ad-hoc reporting: type any message into the UI and the real chain triages it live.

## LLM / code separation (the thing judges ask about)
| | LLM (agent) | Code (guard + tools) |
|---|---|---|
| reads messy slang | ✔ | ✘ |
| outputs risk & explanation | ✔ | ✘ |
| decides CLEAR/HOLD/ESCALATE | ✘ (opinion only) | ✔ rules R1–R7 |
| moves money / writes state | ✘ | ✔ single legal writer with state machine |
