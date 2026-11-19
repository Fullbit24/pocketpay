#miner.py — pulls REAL open-source scam text into our scenario pool.
#Source: UCI SMS Spam Collection (Almeida et al.), the dataset the brief itself
#recommends. We mine payment/claim/link-flavored spam as live-fire test cases
#and money-adjacent ham as clean controls. Selection is deterministic (fixed
#filters + order) so the demo is reproducible in front of judges.

import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "SMSSpamCollection")

FRAUD_HINTS = ("transfer", "bank", "claim", "cash", "fund", "urgent", "prize",
               "win", "won", "click", "www.", "http", "free", "offer", "deadline")
HAM_HINTS = ("pay you back", "split", "reimburs", "owe you", "for the lunch", "for the taxi")
# romance/spam-flavored ham that matched money words — not demo-safe for a school event
HAM_BAN = ("babe", "darling", "love", "miss you", "xxx", "xoxo", "wine", "bath")


def load():
    rows = []
    with open(DATA, encoding="utf-8", errors="replace") as f:
        for line in f:
            label, _, text = line.partition("\t")
            if label in ("ham", "spam") and text.strip():
                rows.append((label, text.strip().replace("\n", " ")))
    return rows


def mined_scenarios(start_n=15, n_spam=6, n_ham=2):
    """Deterministic picks -> (txn_id, student, recipient, amount, purpose, message).
    Spam becomes scam scenarios against fresh unverified accounts; ham becomes
    legit peer payments."""
    rows = load()
    seen = set()
    spam_picks, ham_picks = [], []
    for label, text in rows:  # file order = deterministic, no random seed needed
        t = text.lower()
        if not (80 <= len(text) <= 220) or text in seen:
            continue
        if label == "spam" and len(spam_picks) < n_spam and any(h in t for h in FRAUD_HINTS):
            spam_picks.append(text); seen.add(text)
        elif (label == "ham" and len(ham_picks) < n_ham
              and any(h in t for h in HAM_HINTS)
              and not any(b in t for b in HAM_BAN)):
            ham_picks.append(text); seen.add(text)
        if len(spam_picks) == n_spam and len(ham_picks) == n_ham:
            break

    students = ["Farah", "Jiad", "Mona", "Belal", "Aisha", "Hassan"]
    out = []
    n = start_n
    for i, msg in enumerate(spam_picks):
        out.append((f"TX-{n:03d}", students[i % len(students)], f"R-REALSPAM{i+1}",
                    round(20 + 35 * i, 0), "real-world scam SMS (UCI dataset)", msg))
        n += 1
    for i, msg in enumerate(ham_picks):
        out.append((f"TX-{n:03d}", students[(i + 3) % len(students)], "R-OMAR" if i == 0 else "R-LENA",
                    25.0 + 20 * i, "real-world normal peer message (UCI dataset)", msg))
        n += 1
    return out


def mined_recipients(count=6):
    # fresh unverified accounts — same pattern as real scam SMS senders
    return [(f"R-REALSPAM{i+1}", f"unknown_sender_{i+1}", "unverified", i + 1, 0, 0, 0)
            for i in range(count)]


if __name__ == "__main__":
    for s in mined_scenarios():
        print(s[0], "|", s[5][:90])
