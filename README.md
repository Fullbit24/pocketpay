# PocketPay — Campus Wallet & Scam Defense Agent

**TechX Dubai '26 · AI Agent Arena · Domain 1: FinTech Fraud & Compliance (High School Track)**

A campus wallet where students pay each other by message. That's the attack surface. PocketPay
puts an autonomous agent in front of every transfer: it reads the chat like a human would, then
hands its finding to a piece of code that has *no opinions and full authority*.

> **Thesis — the LLM never gets to move money.**
> The model produces an **opinion** (a risk score, a confidence, a plain-English reason).
> Deterministic Python owns every **decision**, and a single guarded writer owns every
> **state change**. Intelligence is probabilistic. Authority is not.

---

## Contents
- [The 30-second version](#the-30-second-version)
- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [The decision engine (rules R1–R7)](#the-decision-engine-rules-r1r7)
- [The safety invariants](#the-safety-invariants)
- [Run it](#run-it)
- [The dashboard](#the-dashboard)
- [Demo script for judges (≈4 min)](#demo-script-for-judges-4-min)
- [The dataset](#the-dataset)
- [What we shipped against the brief](#what-we-shipped-against-the-brief)
- [Stretch goals](#stretch-goals)
- [Engineering notes](#engineering-notes)
- [Repository layout](#repository-layout)

---

## The 30-second version

```
student message + transfer intent
        │
   ①   tools.get_transaction_context() ............ FACTS   (deterministic)
   ②   tools.check_scam_link() .................... FACTS   (deterministic, free, runs first)
   ③   LLM.analyze() .............................. OPINION (probabilistic, contained)
   ④   tools.verify_campus_recipient() ............ FACTS   (deterministic, may time out)
   ⑤   guard.decide() ............................. DECISION (deterministic, exhaustive)
   ⑥   tools.update_payment_state() ............... MONEY   (single legal writer, state machine)
        │
        └─ every step appended to decision_log → the visible audit trail
```

Five facts-or-opinion stages, one authority stage, one writer. Nothing else can touch a wallet.

---

## Why this exists

Peer-to-peer payment on a campus is a social problem wearing a technical costume. The money moves
because a *message* said so — and messages are where fraud lives: manufactured urgency, scarcity
pressure, prices that are too good, off-platform redirects, credential requests, "send it to my
cousin instead", brand-new accounts with zero history.

Three things are true at once:

1. **A human can't read every message.** The scam usually works because nobody was watching.
2. **A language model is genuinely good at reading messy human text** — and genuinely unsafe as
   an authority. It hallucinates, it can be prompt-injected, and it fails non-deterministically.
3. **Money needs an answer you can defend to a regulator**, not a plausible-sounding paragraph.

So we split the two. The model does what it's good at (reading), code does what it must (deciding),
and a human is always the last resort for anything uncertain. The result is a system that is
*autonomous in its judgement and auditable in its actions*.

---

## Architecture

Four modules, no framework, no orchestration library — the state machine is written by hand,
deliberately. Every arrow below is a real function call, and every one of them writes a row.

| Stage | File | Responsibility | Nature |
|---|---|---|---|
| ① Context | `pocketpay/tools.py: get_transaction_context` | load the flagged txn | fact |
| ② Link cache | `pocketpay/tools.py: check_scam_link` | known-phishing URL lookup | fact |
| ③ Analysis | `pocketpay/agent.py: _analyze` | risk, confidence, indicators, reason | **opinion** |
| ④ Directory | `pocketpay/tools.py: verify_campus_recipient` | tier, account age, report count | fact |
| ⑤ Guard | `pocketpay/guard.py: decide` | the verdict | **authority** |
| ⑥ Writer | `pocketpay/tools.py: update_payment_state` | the only legal state mutation | money |

**The loop guard** (`pocketpay/agent.py: triage`): if a transaction is already awaiting a human,
already escalated, or already settled, `triage()` returns immediately — no second LLM call, no
re-asking, no polling. State lives in SQLite, so it survives a restart. There is no daemon, no
queue worker, no `while True` anywhere in the codebase: the agent is *called*, it reads the world,
it acts, it exits.

**The parser** (`pocketpay/agent.py: _parse_analysis`): a small local model sometimes truncates its
JSON. The parser repairs unbalanced braces, trims trailing junk, and falls back to a regex
extraction of the two numbers it needs. If all of that fails the analysis is `None` — which the
guard treats as *no opinion*, not as *safe*. That distinction is the whole design in one line.

---

## The decision engine (rules R1–R7)

`guard.py` is a pure function — no I/O, no clock, no randomness, 74 lines. Rules fire in order,
first match wins, and the matching rule's name is written into the audit trail so a reviewer can
replay the reasoning.

| Rule | Trigger | Verdict | Why |
|---|---|---|---|
| **R1** | message contains a URL already confirmed as a scam | `ESCALATED` | hard fact beats any opinion |
| **R1b** | recipient has ≥2 confirmed scam reports | `ESCALATED` | hard fact, repeat offender |
| **R2** | analysis engine unavailable | `HOLD` | never guess, never block |
| **R2b** | model confidence < 0.5 | `HOLD` | flag uncertainty, don't act on it |
| **R2c** | analysis returned no risk score | `HOLD` | missing data is not "safe" |
| **R1c** | unverified recipient **and** risk ≥ 70 | `ESCALATED` | fact + confident opinion |
| **R3** | directory service timed out | `HOLD` | degrade gracefully, wallet stays open |
| **R4** | risk ≥ 70 | `ESCALATED` | clear scam pattern, staff review |
| **R5** | risk 30–69, unverified, > 150 AED | `ESCALATED` | the genuinely uncertain middle |
| **R6** | trusted party, risk < 30, ≤ 300 AED | `CLEAR` | canteen snacks never wait |
| **R7** | everything else | `HOLD` | ask the student, exactly once |

Note the ordering, because it is the point: **facts (R1, R1b) outrank opinions (R1c, R4, R5)**,
and **uncertainty (R2, R2b, R2c, R3, R7) never resolves to a freeze** — it resolves to a question.

---

## The safety invariants

These are the claims we'd defend under questioning, and each one is enforced by structure rather
than by care.

**1. There is no `BLOCK` state.** The state machine has exactly three terminal-ish outcomes, and
the schema literally cannot express a permanent freeze:

```
RECEIVED ──► TRIAGED ──► CLEAR
                    └──► PENDING_CONFIRMATION ──► CLEARED | CANCELLED
                    └──► ESCALATED ──────────────► PENDING_CONFIRMATION | CANCELLED
```

The worst state a transaction can reach is *awaiting a human*, and a human can always resolve it.
Even `ESCALATED` can be downgraded. It is not policy that funds are never frozen forever — it is
arithmetic. (`pocketpay/tools.py: update_payment_state`)

**2. One writer, and it validates.** `update_payment_state` is the only function in the codebase
that mutates `transactions.state`, and it rejects any illegal transition with `ValueError`. The LLM
has no import path to it. The web layer has no route to it. There is exactly one door.

**3. Degradation, not failure.** The brain dying (R2), the brain mumbling (R2b/R2c), and the
directory service hanging (R3) are all *modeled states* with defined behaviour, not crashes. We hit
the brain-unavailable path during development — live, on TX-008 — and the system held the transfer
and stayed up. That was not a demo; that was the design working.

**4. No unbounded loop.** Nothing polls. A transaction awaiting a human is never re-triaged
(loop guard), so the agent cannot spin, re-ask, or re-bill the model. The number of LLM calls for
any transaction is at most one, ever.

---

## Run it

```bash
PY=~/.venv/bin/python          # needs the `openai` package; local model at 127.0.0.1:8080

$PY main.py build              # seed the dataset → campus_wallet_logs.sqlite
$PY main.py web                # ★ the dashboard → http://127.0.0.1:8401
$PY main.py tests              # guard-rule + parser unit tests (no LLM, instant)
$PY main.py demo               # scripted 21-scenario run in the terminal
$PY main.py ui                 # original terminal interface (fallback if the browser dies)
```

The dashboard is the stage interface. `tests` and `demo` are the fallbacks — if the venue's
network, browser, or the local model misbehaves, `demo` runs the entire narrative from a terminal
with zero dependencies.

---

## The dashboard

`pocketpay/web.py` is stdlib-only — `http.server` plus a single embedded HTML page, no build step,
no CDN, no npm. It decides **nothing**; it reads the same SQLite database and calls the same
`PocketPayAgent` the terminal does. Same architecture, wearing a product face.

What it shows:

- **The queue** — every transaction with its live state, filterable and searchable, exactly as a
  staff reviewer would see it.
- **The audit trail** — per transaction, the full chain: blue tool steps (facts), cyan LLM step
  (opinion), red guard step (the decision), green human step (the answer).
- **Live streaming** — while the agent works, the board polls the trail and paints each step as it
  commits, with a pulsing ghost row while the model is reading. You watch the agent think.
- **The human moment** — a pending transaction shows *Respond as student*; confirm releases it,
  cancel stops it and reports the seller. Confirmation and cancellation are logged like everything
  else.
- **The broadcast feed** — when a phishing link is confirmed, the alert appears in the footer and
  as a toast, campus-wide, in real time.

Design note: the palette is muted and semantic — green clears, amber waits, red escalates, never
decorative — with focus rings and `prefers-reduced-motion` respected. It's an operations console,
not a pitch deck.

---

## Demo script for judges (≈4 min)

**1 — the brief's own scam (TX-003).** Open it → *Run agent chain*. Narrate the steps as they
paint: the tool reads the facts, the model calls it at risk 96 with the indicator "fake .top
domain", the guard escalates under R1c. Then point at the footer: the link is now on the campus
broadcast feed.

**2 — the second student (TX-013).** Same phishing URL, different sender. This one never reaches
the model — `check_scam_link` catches it first and R1 escalates on the hard fact alone. *"The
system learned from a case that happened ten seconds ago."*

**3 — money that needs a human (TX-012).** 800 AED for a laptop share, all above board. Chain →
`PENDING_CONFIRMATION`. *"The guard will not auto-clear 800 AED, and the model can't make it.
Watch who does."* Hit **Respond as student** → confirm → `CLEARED`.

**4 — the judge moment.** Ask a judge to read you a shady message, or say this one out loud:

> *"selling front row fest pass 50 aed!! send to @campus_tix_99 fast https://pocketpay-claims.top/tix"*

Paste it into the live box on the queue panel → the real chain triages it in front of them, and
escalates with a plain-English reason. Nothing is scripted. Nothing is faked.

**5 — optional, the killer detail.** Run the chain on TX-003 *again*. Instead of re-asking, the
loop-guard step appears: state is already `ESCALATED`, no second model call. That's the brief's
"no infinite loops / no repeated questioning" requirement, demonstrated rather than asserted.

Every word on screen is real output from real code, including the LLM's own phrasing. If the model
says something odd, we say so — it's an opinion, and the opinion isn't what decides.

---

## The dataset

`pocketpay/db.py` builds `campus_wallet_logs.sqlite` — the schema, the 21 scenarios, the recipient
directory (tiers, account ages, report counts), the scam-link registry, the broadcast feed, and the
decision log.

- **14 authored scenarios** covering the full rule surface: canteen auto-clears, the brief's exact
  ticket scam, credential phishing, a repeat offender, low-confidence slang, three genuinely
  uncertain mid-risk cases, a **directory outage**, and both human resolutions.
- **7 real messages mined from the UCI SMS Spam Collection** (`pocketpay/miner.py`) — the open
  dataset the brief recommends — pulled into the same pipeline. All 7 real scams escalate.

Sample data is synthetic and labelled as such in the UI; the mined corpus is public research data.

---

## What we shipped against the brief

| Requirement | Status | Where |
|---|---|---|
| ≥10 scenarios | **21** | `db.py: SCENARIOS` + `miner.py` |
| Visible decision log, ≥3 steps per case | chain per txn, typically 5–7 steps | `db.py: decision_log`, web chain panel |
| Human confirmation of uncertain payments | pending → human answers once → resolved | `agent.resolve`, web modal |
| Stateful memory (no re-asking) | loop guard, SQLite-persisted | `agent.triage` |
| Graceful degradation on service failure | R2 / R2b / R2c / R3 | `guard.py` |
| Never permanently freeze a wallet | no `BLOCK` state exists | `tools.update_payment_state` |
| LLM vs deterministic separation | opinion vs authority vs writer | whole architecture |
| Three mock services | context, directory, state writer | `tools.py` |

## Stretch goals

- **Automatic scam-alert broadcast.** A confirmed phishing URL is registered and pushed to a campus
  feed; the next student to receive the same link is caught by URL *before the model is invoked*.
  Demonstrated live by the TX-003 → TX-013 pair.
- **Staff hours saved.** Every auto-cleared transfer is quantified on the dashboard header — the
  assistant's worth, in the units a school actually cares about.
- **Live ad-hoc triage.** Any message, typed in front of the judges, runs the real agent chain.

---

## Engineering notes

**Opinion at the edges, authority in the middle.** The model sits inside a bounded box: it receives
a formatted prompt, it returns JSON that gets parsed defensively, and its output can only ever
reach `guard.decide` as one argument. It cannot call tools, cannot choose actions, cannot write
state. The worst thing a hallucinating or prompt-injected model can do is produce a *wrong opinion*
— which the deterministic rules are already written to distrust when confidence is low.

**Fail soft, log everything.** Every non-happy path writes its reason into the audit trail before
the verdict lands: parser failure, engine outage, directory timeout. A reviewer never has to guess
why a transfer was held.

**Boring where it counts.** No framework, no ORM, no async, no orchestration library. SQLite and
stdlib. The parts that matter — rules, state machine, single writer — are short enough to read
aloud on stage, which is the point.

**Known limits, stated honestly.** The directory and the model are local mocks, so "tiers" and
"report counts" are synthetic. Scam-link matching is exact-string, not fuzzy, so a typosquat
evades it until it's been seen once. The guard's thresholds (300/150/70/0.5) are defensible, not
tuned on real fraud data. Everything else — the separation, the state machine, the loop guard,
the fallbacks — is production-shaped as-is.

---

## Repository layout

```
main.py                 launcher: build | web | tests | demo | ui
pocketpay/
  db.py                 schema + 21 scenarios + recipient directory (the dataset brain)
  miner.py              mines 7 real messages from the UCI SMS Spam Collection
  tools.py              the 3 mock services + scam-link feed; the state machine lives here
  agent.py              the triage loop, the defensive JSON parser, the loop guard
  guard.py              R1–R7, pure function, the only decider
  web.py                stage dashboard (stdlib http.server + one HTML page)
  ui.py                 terminal fallback interface
  demo.py               scripted 21-scenario presentation run
  tests.py              12 unit tests: guard rules + parser repair
capri/
  openai_lib.py         ModelHouse — thin streaming client for the local model
  session.py            session id handling
data/
  SMSSpamCollection     the UCI SMS Spam Collection (public research dataset)
CODE_GUIDE.md           the defence cheat-sheet: rules, state machine, trick questions
```

---

*Built for TechX Dubai '26. Every number on screen during the demo comes from a live run of this
code against the local model — nothing is a mock-up, and nothing is hard-coded for the pitch.*
