# CODE_GUIDE.md — explain this code tomorrow without knowing shit

## The 60-second story (say this first, verbatim-ish)
"Our system separates intelligence from authority. The LLM only ever produces an
**opinion** — a risk score, a confidence, and a plain-language explanation. It can
never touch money. All decisions come from **deterministic Python rules** in guard.py,
and all state changes go through **one single writer** in tools.py with an enforced
state machine. That's how we guarantee the brief's safety rule: no permanent wallet
freeze without a human. There is literally no BLOCK state in our schema."

## The pipeline (draw this if they let you)
message → ① get_transaction_context (facts) → ② LLM analyze (opinion) →
③ verify_campus_recipient (facts) → ④ guard.decide (JUDGEMENT) → ⑤ update_payment_state (MONEY)
Every arrow is logged in decision_log → that's the visible audit trail on stage.

## File map — what each file IS
| file | one-line truth | key lines |
|---|---|---|
| db.py | SQLite schema + 21 scenarios (14 written by us + 7 real spam from UCI dataset) | SCHEMA:13, build:130 |
| miner.py | pulls 7 REAL scam/ham texts from UCI SMS Spam Collection into the pool | mined_scenarios:29 |
| tools.py | the 3 mock services the brief demands. Dumb on purpose. State machine lives here | get_transaction_context:23, verify_campus_recipient:31, update_payment_state:75 |
| agent.py | the loop: call tools, call LLM, hand opinion to guard. LLM result is parsed JSON or None — never a decision | SYSTEM:16, triage:105, resolve:174 |
| guard.py | THE DECIDER. Pure function, no I/O. Rules R1→R7, first match wins | decide:15 |
| demo.py | runs all 21 scenarios with scripted human answers | main:26 |
| ui.py | stage interface. t=triage, r=resolve, n=live ad-hoc message | main:22 |
| tests.py | 12 unit tests for guard rules + the JSON repair parser | run() |

## The guard rules (the "algorithm" — know these cold)
R1  message contains a URL already confirmed as scam → ESCALATE (hard fact, beats everything)
R1b recipient has ≥2 scam reports → ESCALATE (hard fact)
R2  LLM dead / unparsable / confidence <0.5 → HOLD, ask a human (never guess, never block)
R1c unverified recipient AND risk ≥70 (confident opinion) → ESCALATE
R3  directory service timed out → HOLD, wallet stays open (outage fallback)
R4  risk ≥70 → ESCALATE
R5  risk 30–69 + unverified + >150 AED → ESCALATE (the "uncertain" cases the brief wants)
R6  verified/trusted + risk <30 + ≤300 AED → CLEAR (canteen snacks never wait)
R7  everything else → hold once, ask the student, never freeze

Three possible outcomes ONLY: CLEAR, PENDING_CONFIRMATION, ESCALATED.
No BLOCK exists. That sentence is your armor for every safety question.

## State machine (tools.py:75-97 — they WILL ask about the "no infinite loop" rule)
RECEIVED → TRIAGED → CLEAR | PENDING_CONFIRMATION | ESCALATED
PENDING_CONFIRMATION → CLEARED | CANCELLED (the human answered once)
Illegal transitions raise ValueError. The loop guard (agent.py:110-118): if a txn is
already pending/escalated/settled, triage() returns instantly — NO second LLM call,
NO re-asking. State lives in SQLite, so it survives restarts; there's no polling loop
because nothing polls — the agent is called, it reads state, it exits.

## Trick questions and answers
Q: "Why doesn't the LLM just decide?" A: "It's non-deterministic and hallucinates. We
saw it live — it once returned JSON missing the closing brace (our parser heals it,
tests.py proves it). Money needs deterministic rules, so the LLM only feeds them."
Q: "What if the LLM is down?" A: "R2 catches it — analysis=None becomes a HOLD, wallet
stays open, human decides. It happened to us DURING development, live, TX-008."
Q: "What if a tool hangs?" A: "verify_campus_recipient raises ToolTimeout, caught at
agent.py:140ish, guard rule R3 holds the txn. demo TX-014 shows it."
Q: "Where's your dataset?" A: "campus_wallet_logs.sqlite built by db.py — 14 authored
scenarios + 7 real messages mined from the UCI SMS Spam Collection, the exact open
dataset the brief recommends. All 7 real scams escalated."
Q: "What does the human confirmation do?" A: "agent.resolve() maps confirm→CLEARED /
cancel→CANCELLED deterministically — the human's word is the only input, code acts."
Q: "How do you prevent the agent freezing funds forever?" A: "Show them the schema:
no BLOCK state exists. Worst possible state is PENDING_CONFIRMATION, which a human
can always clear. Also escalated cases can be downgraded, never frozen."

## Words that win points
"single writer for money state" · "opinion vs authority separation" · "deterministic
boundaries around a non-deterministic component" · "stateful in SQLite, not in the
conversation" · "audit trail as a first-class table" · "graceful degradation"

## Words that lose points
"the AI decides" (NO) · "we use LangGraph" (we don't, we built the state by hand — brag about that) ·
"machine learning model" (it's an LLM reading text, say "LLM" not "ML model")

## If asked to demo, exactly these 3 moves
1. `t TX-003` → the brief's own scam message, chain streams, ESCALATED
2. `n sell me your notes 20 aed send to my new account quick` → live ad-hoc triage, judge's jaw
3. `r TX-012 confirm` (after rebuilding + triaging TX-012) → human-in-the-loop close
Belt-and-braces: `f` shows the scam-alert broadcast feed (stretch goal).

## Study plan tonight (30 min max, then SLEEP)
10 min: read guard.py top to bottom — it's 74 lines, it's the whole brain of the answers
10 min: run the UI yourself: t TX-003, then t TX-003 AGAIN (loop guard), then f
5 min: read agent.py triage() once, tracing the 5 steps against a decision log printout
5 min: say the 60-second story out loud twice, in your own words

You wrote the wrapper it runs on, remember — "I built the LLM plumbing, we built the
agent on top" is 100% true and it's yours.
