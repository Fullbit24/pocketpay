#ui.py — live campus terminal for the demo. This is what you open on stage.
#Run: ~/.venv/bin/python -m pocketpay.ui
#  t <txn_id>  triage a flagged transaction (full agent chain)
#  r <txn_id>  answer a pending confirmation (confirm/cancel)
#  l <txn_id>  show decision log
#  n <msg>     triage a brand-new ad-hoc message for recipient R-GHOST (100 AED)
#  f           show scam-alert broadcast feed
#  q           quit

import sys

from . import db
from .agent import PocketPayAgent

BANNER = """
  ╔══════════════════════════════════════════════╗
  ║  PocketPay · Campus Wallet & Scam Defense    ║
  ║  agent = opinion · guard = decision · code = money
  ╚══════════════════════════════════════════════╝"""


def main():
    con = db.connect()
    agent = PocketPayAgent(con)
    print(BANNER)
    n = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    pend = con.execute("SELECT COUNT(*) c FROM transactions WHERE state='PENDING_CONFIRMATION'").fetchone()["c"]
    print(f"  {n} flagged transactions in queue · {pend} awaiting your answer")
    print("  commands: t <txn_id> | r <txn_id> <confirm|cancel> | l <txn_id> | n <message> | f | q\n")

    while True:
        try:
            cmd = input("pocketpay> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not cmd:
            continue
        parts = cmd.split(None, 1)
        op, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        try:
            if op == "q":
                return
            elif op == "t":
                action, rule, expl = agent.triage(rest.split()[0])
                print(f"  => {action} [{rule}]\n  => {expl}\n  --- decision log ---")
                for row in db.get_log(con, rest.split()[0]):
                    print(f"    [{row['actor']:>6}] {row['step']}: {row['detail']}")
            elif op == "r":
                txn_id, choice = rest.split()
                print(f"  => resolved: {agent.resolve(txn_id, choice)}")
            elif op == "l":
                for row in db.get_log(con, rest.split()[0]):
                    print(f"    [{row['actor']:>6}] {row['step']}: {row['detail']}")
            elif op == "n":
                # ad-hoc: file a synthetic flag and run the real chain on it
                cnt = con.execute(
                    "SELECT COUNT(*) c FROM transactions WHERE txn_id LIKE 'ADHOC%'").fetchone()["c"]
                txn_id = f"ADHOC-{cnt + 1:03d}"
                con.execute("INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, purpose, message) VALUES (?,?,?,?,?,?)",
                            (txn_id, "You", "R-GHOST", 100.0, "ad-hoc report", rest))
                con.commit()
                print(f"  filed {txn_id}")
                action, rule, expl = agent.triage(txn_id)
                print(f"  => {action} [{rule}]\n  => {expl}")
                for row in db.get_log(con, txn_id):
                    print(f"    [{row['actor']:>6}] {row['step']}: {row['detail']}")
            elif op == "f":
                for b in con.execute("SELECT message, created_at FROM broadcast_log ORDER BY id").fetchall():
                    print(f"    🔊 [{b['created_at']}] {b['message']}")
            else:
                print("  unknown command — t/r/l/n/f/q")
        except Exception as e:
            print(f"  ! {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
