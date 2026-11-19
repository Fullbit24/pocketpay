#db.py — the dataset brain. campus_wallet_logs.sqlite per the brief.
#Everything deterministic lives here: transactions, seller profiles, scam-link
#feed, decision log, payment state. The LLM never writes state directly.

import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "campus_wallet_logs.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    txn_id        TEXT PRIMARY KEY,
    student       TEXT NOT NULL,
    recipient_id  TEXT NOT NULL,
    amount_aed    REAL NOT NULL,
    purpose       TEXT,
    message       TEXT,
    state         TEXT NOT NULL DEFAULT 'RECEIVED',
    -- states: RECEIVED -> TRIAGED -> CLEAR | PENDING_CONFIRMATION | ESCALATED
    --          PENDING_CONFIRMATION -> CLEARED | CANCELLED
    risk_score    INTEGER,
    confidence    REAL,
    decision      TEXT,
    explanation   TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS recipients (
    recipient_id  TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    tier          TEXT NOT NULL,      -- verified_vendor | trusted_peer | unverified
    created_days_ago INTEGER NOT NULL,
    scam_reports  INTEGER NOT NULL DEFAULT 0,
    txns_90d      INTEGER NOT NULL DEFAULT 0,
    simulate_outage INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scam_links (
    url         TEXT PRIMARY KEY,
    first_seen  TEXT DEFAULT (datetime('now')),
    source_txn  TEXT,
    broadcast   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS broadcast_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS decision_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id      TEXT NOT NULL,
    actor       TEXT NOT NULL,        -- agent | llm | tool | guard | human | system
    step        TEXT NOT NULL,
    detail      TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

# (txn_id, student, recipient_id, amount, purpose, message)
SCENARIOS = [
    ("TX-001", "Rayan",  "R-ALFALAH", 15.0,  "canteen snack",
     "yo bro thanks for grabbing the karak and paratha earlier, sending the 15 now!"),
    ("TX-002", "Maya",   "R-OMAR",    40.0,  "library book split",
     "hey! here's my half of the data structures book, 40 aed, ty 🙏"),
    ("TX-003", "Zayd",   "R-TIX99",   50.0,  "concert ticket",
     "Bro urgent! Selling my Front Row Fest pass for 50 AED, send to @campus_tix_99 via PocketPay fast before I give it to someone else, click here to claim: pocketpay-claims.top/tix"),
    ("TX-004", "Lina",   "R-SKETCH",  0.01,  "wallet 're-verification'",
     "Hi, this is PocketPay support. Your wallet will be frozen in 2 hours, verify your password now at https://pocketpay-secure-verify.click/login"),
    ("TX-005", "Tariq",  "R-EZTIX",   120.0, "expo pass resale",
     "final offer!! expo pass 120 aed only today, 3 other buyers waiting, transfer to R-EZTIX NOW don't be that guy https://tixswap-now.click"),
    ("TX-006", "Noor",   "R-SARA",    35.0,  "gift",
     "hey so i ordered the matcha cake from sara's stand for her birthday, sending you the 35 aed now"),
    ("TX-007", "Ali",    "R-COUSIN",  350.0, "textbook bundle",
     "bro the seller went offline, can you just send the 350 to my cousin's wallet real quick, he'll hand it over, trust me 😭"),
    ("TX-008", "Hana",   "R-GHOST",   260.0, "gaming acc trade",
     "oi u wanna cop the ranked acc? 260 sheckles, send to R-GHOST, no cap legit account, dm me on the other app"),
    ("TX-009", "Yusuf",  "R-NEWBIE",  220.0, "hoodie from 'student store'",
     "limited drop hoodies 220 aed, only 2 left, send to student_store_deals now or it's gone, new account ignore the 0 reviews i just made it lol"),
    ("TX-010", "Dina",   "R-LENA",    90.0,  "lab partner gift",
     "hi! we agreed on 90 aed for the shared lab kit, I'm sending it over now, see you Thursday!"),
    ("TX-011", "Omar",   "R-DESK",    45.0,  "printing service",
     "printing done go go go! 45 aed to R-DESK"),
    ("TX-012", "Rami",   "R-CYBER",   800.0, "laptop share (PC lab)",
     "here's my half of the PC for the robotics lab, 800 aed, invoice number is on the sheet you kept"),
    ("TX-013", "Sara",   "R-TIX99",   50.0,  "concert ticket (same scam link)",
     "selling Front Row Fest pass 50 aed!! send to @campus_tix_99 fast https://pocketpay-claims.top/tix"),
    ("TX-014", "Kareem", "R-UNSTABLE", 60.0, "rideshare split",
     "yo sharing the taxi from the metro, 60 aed to R-UNSTABLE as agreed"),
]

RECIPIENTS = [
    # id, name, tier, days_ago, scam_reports, txns_90d, outage
    ("R-ALFALAH",  "Canteen Al-Falah",      "verified_vendor", 400, 0, 1200, 0),
    ("R-OMAR",     "Omar (classmate)",      "trusted_peer",    365, 0, 47,   0),
    ("R-TIX99",    "@campus_tix_99",        "unverified",      2,   1, 0,    0),
    ("R-SKETCH",   "pocketpay_support_x",   "unverified",      1,   4, 0,    0),
    ("R-EZTIX",    "@ez_tix_deals",         "unverified",      3,   2, 1,    0),
    ("R-SARA",     "Sara's Dessert Stand",  "verified_vendor", 180, 0, 310,  0),
    ("R-COUSIN",   "Ali's cousin (unknown)","unverified",      9,   0, 2,    0),
    ("R-GHOST",    "ghost_trades_99",       "unverified",      5,   1, 0,    0),
    ("R-NEWBIE",   "student_store_deals",   "unverified",      1,   0, 0,    0),
    ("R-LENA",     "Lena (classmate)",      "trusted_peer",    300, 0, 22,   0),
    ("R-DESK",     "Campus Print Desk",     "verified_vendor", 500, 0, 940,  0),
    ("R-CYBER",    "CyberLab Supplies",     "verified_vendor", 220, 0, 88,   0),
    ("R-UNSTABLE", "Karim (rover)",         "trusted_peer",    200, 0, 31,   1),  # outage sim!
]


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def log_decision(con, txn_id, actor, step, detail):
    con.execute("INSERT INTO decision_log (txn_id, actor, step, detail) VALUES (?,?,?,?)",
                (txn_id, actor, step, detail))
    con.commit()
    return detail


def get_log(con, txn_id):
    return con.execute(
        "SELECT actor, step, detail, created_at FROM decision_log WHERE txn_id=? ORDER BY id",
        (txn_id,)).fetchall()


def build():
    """Wipe and rebuild the synthetic campus dataset. Idempotent."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = connect()
    con.executemany(
        "INSERT INTO recipients VALUES (?,?,?,?,?,?,?)", RECIPIENTS)
    for txn_id, student, rid, amount, purpose, msg in SCENARIOS:
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, purpose, message) "
            "VALUES (?,?,?,?,?,?)",
            (txn_id, student, rid, amount, purpose, msg))
    con.commit()
    n = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    print(f"dataset built: {n} transactions, {len(RECIPIENTS)} recipients -> {DB_PATH}")


if __name__ == "__main__":
    build()
