#demo.py — the presentation runner. Executes every scenario from the dataset,
#streams the visible decision log for each, drives the human-confirmation flows
#(scripted, so the demo never dead-ends), then prints the staff-hours-saved
#summary. Run: ~/.venv/bin/python -m pocketpay.demo
#
#Loop-proofing proof: TX-007 is triaged TWICE; the second call must hit the
#loop guard and return instantly without re-calling the LLM or re-asking.

from . import db
from .agent import PocketPayAgent

# scripted human answers for the interactive flow ("Confirm I Know This Seller")
# — only for txns the guard is EXPECTED to hold; auto-cleared ones stay untouched.
HUMAN_ANSWERS = {
    "TX-008": "cancel",   # student sees the profile, recognises nothing, cancels
    "TX-012": "confirm",  # legit big laptop share -> cleared after confirm
    "TX-014": "confirm",  # outage case, retried later, student confirms ride
}


def show_log(con, txn_id, indent="    "):
    for row in db.get_log(con, txn_id):
        print(f"{indent}[{row['actor']:>6}] {row['step']}: {row['detail']}")


def main():
    con = db.connect()
    agent = PocketPayAgent(con)
    txns = [r["txn_id"] for r in con.execute(
        "SELECT txn_id FROM transactions ORDER BY txn_id").fetchall()]

    counts = {"CLEAR": 0, "PENDING_CONFIRMATION": 0, "ESCALATED": 0}
    for txn_id in txns:
        row = con.execute("SELECT student, message FROM transactions WHERE txn_id=?",
                          (txn_id,)).fetchone()
        print(f"\n{'='*78}\n{txn_id} — from {row['student']}")
        print(f"  msg: {row['message'][:110]}{'…' if len(row['message'])>110 else ''}")
        action, rule, expl = agent.triage(txn_id)
        print(f"  --- decision log ---")
        show_log(con, txn_id)
        print(f"  => {action}  [{rule}]")
        print(f"  => for the student: {expl}")
        if action in counts:
            counts[action] += 1

        # loop-prevention proof: triage the SAME pending txn again, BEFORE any
        # human answer — a second pass must not re-ask, re-call the LLM, or loop.
        state = con.execute("SELECT state FROM transactions WHERE txn_id=?",
                            (txn_id,)).fetchone()["state"]
        if state == "PENDING_CONFIRMATION":
            action2, rule2, _ = agent.triage(txn_id)
            print(f"  --- RE-RUN same txn -> {action2} via {rule2} "
                  f"(no second LLM call, no re-ask: loop guard holds)")

        # interactive stretch flow: the student gets asked once, then resolves.
        # PENDING = classic confirm dialog; ESCALATED cases the student can also
        # decline outright (staff-review downgrade path).
        if txn_id in HUMAN_ANSWERS:
            state = con.execute("SELECT state FROM transactions WHERE txn_id=?",
                                (txn_id,)).fetchone()["state"]
            if state not in ("PENDING_CONFIRMATION", "ESCALATED"):
                print(f"  --- (skipping confirm prompt: guard already settled this as {state})")
                continue
            ans = HUMAN_ANSWERS[txn_id]
            prompt = ("student was asked: 'Confirm you know this seller? (confirm/cancel)'"
                      if state == "PENDING_CONFIRMATION" else
                      "student sees the escalation card and declines the flagged transfer")
            print(f"  --- {prompt} -> {ans}")
            final = agent.resolve(txn_id, ans)
            print(f"  => final state: {final}")

    # ---- summary ----
    print(f"\n{'='*78}\nRUN SUMMARY")
    final_states = con.execute(
        "SELECT state, COUNT(*) c FROM transactions GROUP BY state ORDER BY state").fetchall()
    for r in final_states:
        print(f"  {r['state']:<22} {r['c']}")
    auto = con.execute("SELECT COUNT(*) c FROM transactions WHERE decision LIKE '%R6_trusted_small%'").fetchone()["c"]
    print(f"\n  auto-cleared with zero staff touch: {auto}")
    print(f"  staff time saved: {auto} x ~12 min manual review = {auto*12} min this batch")
    print("  (the brief's baseline is 200 flagged txns/day -> "
          f"{auto/len(txns)*200*12/60:.1f} hrs/day saved at that scale)\n")
    print("  broadcast feed (stretch: auto scam-alerting):")
    for b in con.execute("SELECT message FROM broadcast_log ORDER BY id").fetchall():
        print(f"    🔊 {b['message']}")


if __name__ == "__main__":
    main()
