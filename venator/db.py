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
    student_age   INTEGER,            -- declaring age of the sender, in years
    state         TEXT NOT NULL DEFAULT 'RECEIVED',
    -- states: RECEIVED -> TRIAGED -> CLEAR | PENDING_CONFIRMATION | ESCALATED
    --          PENDING_CONFIRMATION -> CLEARED | CANCELLED | PENDING_GUARDIAN
    --          PENDING_GUARDIAN -> CLEARED | CANCELLED | RELEASED
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
CREATE TABLE IF NOT EXISTS guardians (
    guardian_id   TEXT PRIMARY KEY,
    student_name  TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    relation      TEXT NOT NULL,      -- mother | father | legal_guardian
    phone         TEXT
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
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,      -- must name a role in policy.yaml's `roles:`
    student_name  TEXT,               -- links a student/guardian user to transactions.student
    email         TEXT,
    api_token     TEXT,               -- demonstration-grade shared secret; see USERS.md
    created_at    TEXT DEFAULT (datetime('now')),
    active        INTEGER NOT NULL DEFAULT 1
);
-- A class is a real teaching cohort a teacher surface can show. The teacher is
-- named (not a foreign key to users) so a cohort survives even before that
-- teacher has a login, and so a class never depends on the identity layer.
CREATE TABLE IF NOT EXISTS classes (
    class_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,      -- e.g. 'Grade 11-A'
    teacher_name  TEXT NOT NULL,      -- e.g. 'Ms. Layla Hassan'
    room          TEXT                -- e.g. 'B-204'
);
-- Enrolment is the many-to-many link between a class and a student. student_name
-- matches transactions.student and users.student_name, so a teacher cohort and a
-- student's payment history resolve to the same person.
CREATE TABLE IF NOT EXISTS enrolments (
    class_id      TEXT NOT NULL,
    student_name  TEXT NOT NULL,
    PRIMARY KEY (class_id, student_name)
);
"""

# (txn_id, student, recipient_id, amount, purpose, message, student_age)
SCENARIOS = [
    ("TX-001", "Rayan",  "R-ALFALAH", 15.0,  "canteen snack",
     "yo bro thanks for grabbing the karak and paratha earlier, sending the 15 now!", 16),
    ("TX-002", "Maya",   "R-OMAR",    40.0,  "library book split",
     "hey! here's my half of the data structures book, 40 aed, ty 🙏", 17),
    ("TX-003", "Zayd",   "R-TIX99",   50.0,  "concert ticket",
     "Bro urgent! Selling my Front Row Fest pass for 50 AED, send to @campus_tix_99 via Venator fast before I give it to someone else, click here to claim: venator-claims.top/tix", 16),
    ("TX-004", "Lina",   "R-SKETCH",  0.01,  "wallet 're-verification'",
     "Hi, this is Venator support. Your wallet will be frozen in 2 hours, verify your password now at https://venator-secure-verify.click/login", 18),
    ("TX-005", "Tariq",  "R-EZTIX",   120.0, "expo pass resale",
     "final offer!! expo pass 120 aed only today, 3 other buyers waiting, transfer to R-EZTIX NOW don't be that guy https://tixswap-now.click", 17),
    ("TX-006", "Noor",   "R-SARA",    35.0,  "gift",
     "hey so i ordered the matcha cake from sara's stand for her birthday, sending you the 35 aed now", 16),
    ("TX-007", "Ali",    "R-COUSIN",  350.0, "textbook bundle",
     "bro the seller went offline, can you just send the 350 to my cousin's wallet real quick, he'll hand it over, trust me 😭", 17),
    ("TX-008", "Hana",   "R-GHOST",   260.0, "gaming acc trade",
     "oi u wanna cop the ranked acc? 260 sheckles, send to R-GHOST, no cap legit account, dm me on the other app", 18),
    ("TX-009", "Yusuf",  "R-NEWBIE",  220.0, "hoodie from 'student store'",
     "limited drop hoodies 220 aed, only 2 left, send to student_store_deals now or it's gone, new account ignore the 0 reviews i just made it lol", 16),
    ("TX-010", "Dina",   "R-LENA",    90.0,  "lab partner gift",
     "hi! we agreed on 90 aed for the shared lab kit, I'm sending it over now, see you Thursday!", 17),
    ("TX-011", "Omar",   "R-DESK",    45.0,  "printing service",
     "printing done go go go! 45 aed to R-DESK", 16),
    ("TX-012", "Rami",   "R-CYBER",   800.0, "laptop share (PC lab)",
     "here's my half of the PC for the robotics lab, 800 aed, invoice number is on the sheet you kept", 18),
    ("TX-013", "Sara",   "R-TIX99",   50.0,  "concert ticket (same scam link)",
     "selling Front Row Fest pass 50 aed!! send to @campus_tix_99 fast https://venator-claims.top/tix", 17),
    ("TX-014", "Kareem", "R-UNSTABLE", 60.0, "rideshare split",
     "yo sharing the taxi from the metro, 60 aed to R-UNSTABLE as agreed", 16),
    # --- Guardian-approval live test (school rule: minors need a guardian for
    #     peer transfers above the policy amount). Same amount and same trusted
    #     peer for both, only the sender's age differs. ---
    ("TX-015", "Bilal",  "R-OMAR",    150.0, "concert ticket share",
     "hey, sending my half of the concert tickets we agreed on, 150 aed, see you friday!", 15),
    ("TX-016", "Sami",   "R-LENA",    150.0, "study trip deposit",
     "hi! here's the 150 aed deposit for the study trip we're both going on, sending it now", 17),
]

GUARDIANS = [
    # guardian_id, student_name, display_name, relation, phone
    ("G-BILAL",  "Bilal",  "Fatima Al-Sayed (mother)",  "mother",         "+971-50-000-0001"),
    ("G-KAREEM", "Kareem", "Yousef Al-Najjar (father)", "father",         "+971-50-000-0002"),
    ("G-NOOR",   "Noor",   "Hessa Al-Marzouqi (aunt)",  "legal_guardian", "+971-50-000-0003"),
    # Every minor in the seed has a guardian, so a guardian surface is never
    # empty for a student the age gate can hold.
    ("G-LAYTH",  "Layth",  "Amal Al-Hosani (mother)",   "mother",         "+971-50-000-0004"),
    ("G-REEM",   "Reem",   "Nasser Al-Zaabi (father)",  "father",         "+971-50-000-0005"),
    ("G-SAEED",  "Saeed",  "Mariam Al-Rumaithi (mother)", "mother",       "+971-50-000-0006"),
    ("G-TALA",   "Tala",   "Rania Khoury (legal guardian)", "legal_guardian", "+971-50-000-0007"),
    ("G-NADIA",  "Nadia",  "Sultan Al-Mehairi (father)", "father",         "+971-50-000-0008"),
    # Two students OVER sixteen also have a guardian on file (a family that
    # keeps one linked); not every older student has one, which is the point.
    ("G-SAMI",   "Sami",   "Huda Al-Suwaidi (mother)",  "mother",         "+971-50-000-0009"),
    ("G-HANA",   "Hana",   "Jamilah Farouk (aunt)",     "legal_guardian", "+971-50-000-0010"),
]

# Users. Every role named in config/policy.yaml's `roles:` section is covered.
# Consistency rules the seed satisfies:
#   - a student user's student_name matches a real student name in transactions
#   - a guardian's student_name matches both guardians.student_name and the same
#     student's transactions, so scoping() links guardian -> student correctly.
# The api_token is a demonstration-grade shared secret (see USERS.md); it is
# generated per build, never printed by the application, and only ever compared
# with hmac.compare_digest. user_id, display_name, role, student_name, email.
USERS = [
    ("U-RAYAN",   "Rayan Haddad",         "student",  "Rayan",  "rayan@campus.example"),
    ("U-MAYA",    "Maya Al-Farsi",        "student",  "Maya",   "maya@campus.example"),
    ("U-BILAL",   "Bilal Othman",         "student",  "Bilal",  "bilal@campus.example"),
    ("U-KAREEM",  "Kareem Idris",         "student",  "Kareem", "kareem@campus.example"),
    ("U-NOOR",    "Noor Al-Marzouqi",     "student",  "Noor",   "noor@campus.example"),
    ("U-FATIMA",  "Fatima Al-Sayed",      "guardian", "Bilal",  "fatima@guardian.example"),
    ("U-HESSA",   "Hessa Al-Marzouqi",    "guardian", "Noor",   "hessa@guardian.example"),
    ("U-STAFIA",  "Stafia Mensah",        "staff",    None,     "stafia@staff.example"),
    ("U-ADMIN",   "System Administrator", "admin",    None,     "admin@campus.example"),
    # Students from the wider campus cohort, so a student surface has more than
    # one identity to log in as.
    ("U-LAYTH",   "Layth Al-Hosani",      "student",  "Layth",  "layth@campus.example"),
    ("U-REEM",    "Reem Al-Zaabi",        "student",  "Reem",   "reem@campus.example"),
    ("U-SAEED",   "Saeed Al-Rumaithi",    "student",  "Saeed",  "saeed@campus.example"),
    ("U-TALA",    "Tala Khoury",          "student",  "Tala",   "tala@campus.example"),
    ("U-NADIA",   "Nadia Al-Mehairi",     "student",  "Nadia",  "nadia@campus.example"),
    ("U-HANA",    "Hana Kanaan",          "student",  "Hana",   "hana@campus.example"),
    ("U-SAMI",    "Sami Al-Suwaidi",      "student",  "Sami",   "sami@campus.example"),
    # Guardians linked to the new minors and the two linked older students.
    ("U-AMAL",    "Amal Al-Hosani",       "guardian", "Layth",  "amal@guardian.example"),
    ("U-NASSER",  "Nasser Al-Zaabi",      "guardian", "Reem",   "nasser@guardian.example"),
    ("U-MARIAM",  "Mariam Al-Rumaithi",   "guardian", "Saeed",  "mariam.g@guardian.example"),
    ("U-RANIA",   "Rania Khoury",         "guardian", "Tala",   "rania@guardian.example"),
    ("U-SULTAN",  "Sultan Al-Mehairi",    "guardian", "Nadia",  "sultan@guardian.example"),
    ("U-HUDA",    "Huda Al-Suwaidi",      "guardian", "Sami",   "huda@guardian.example"),
    ("U-JAMILAH", "Jamilah Farouk",       "guardian", "Hana",   "jamilah@guardian.example"),
    # Teachers. The policy defines no `teacher` role, and policy.yaml is not ours
    # to edit, so each teacher logs in under the existing `staff` role; their
    # classes are owned via classes.teacher_name, which is what a cohort reads.
    ("U-HASSAN",  "Ms. Layla Hassan",     "staff",    None,     "layla.hassan@staff.example"),
    ("U-KAMAL",   "Mr. Idris Kamal",      "staff",    None,     "idris.kamal@staff.example"),
    ("U-NASSER_T", "Ms. Salwa Nasser",    "staff",    None,     "salwa.nasser@staff.example"),
]


RECIPIENTS = [
    # id, name, tier, days_ago, scam_reports, txns_90d, outage
    ("R-ALFALAH",  "Canteen Al-Falah",      "verified_vendor", 400, 0, 1200, 0),
    # The real campus canteen handle. The lookalike-vendor rule compares every
    # incoming name against this list, so a near-miss such as @campus_canteen_
    # or @CampusCanteen0 is caught before the payment moves.
    ("R-CANTEEN",  "@campus_canteen",       "verified_vendor", 380, 0, 2100, 0),
    ("R-OMAR",     "Omar (classmate)",      "trusted_peer",    365, 0, 47,   0),
    ("R-TIX99",    "@campus_tix_99",        "unverified",      2,   1, 0,    0),
    ("R-SKETCH",   "venator_support_x",   "unverified",      1,   4, 0,    0),
    ("R-EZTIX",    "@ez_tix_deals",         "unverified",      3,   2, 1,    0),
    ("R-SARA",     "Sara's Dessert Stand",  "verified_vendor", 180, 0, 310,  0),
    ("R-COUSIN",   "Ali's cousin (unknown)","unverified",      9,   0, 2,    0),
    ("R-GHOST",    "ghost_trades_99",       "unverified",      5,   1, 0,    0),
    ("R-NEWBIE",   "student_store_deals",   "unverified",      1,   0, 0,    0),
    ("R-LENA",     "Lena (classmate)",      "trusted_peer",    300, 0, 22,   0),
    ("R-DESK",     "Campus Print Desk",     "verified_vendor", 500, 0, 940,  0),
    ("R-CYBER",    "CyberLab Supplies",     "verified_vendor", 220, 0, 88,   0),
    ("R-UNSTABLE", "Karim (rover)",         "trusted_peer",    200, 0, 31,   1),  # outage sim!
    # --- Campus cohort recipients (added for the multi-surface demo) ---------
    # Two busy peer accounts that many students pay a little each (coordinated
    # -recipient pattern), a mini-mart vendor, and a fund-raiser peer who takes a
    # long run of small transfers (velocity pattern).
    ("R-CLASSFUND", "Grade 11-A Class Fund", "trusted_peer",    150, 0, 260,  0),
    ("R-SCHOOLSHOP", "School Shop (stationery)", "verified_vendor", 300, 0, 540, 0),
    ("R-MAYSHA",   "Maysha (classmate)",    "trusted_peer",    210, 0, 64,   0),
    ("R-HAMAD",    "Hamad (classmate)",     "trusted_peer",    190, 0, 58,   0),
    # A fresh unverified handle reused by a whole phishing campaign: several
    # students forward the same wallet to it with the same link, so R1 knows the
    # link and the coordinated-recipient view shows a spike.
    ("R-PROMO88",  "@promo_bonus_88",       "unverified",      1,   2, 0,    0),
]

# ---------------------------------------------------------------------------
# Classes and enrolments — the teacher surface's real cohorts.
# ---------------------------------------------------------------------------
# class_id, name, teacher_name, room
CLASSES = [
    ("C-11A", "Grade 11-A", "Ms. Layla Hassan",  "B-204"),
    ("C-10B", "Grade 10-B", "Mr. Idris Kamal",   "B-112"),
    ("C-12C", "Grade 12-C", "Ms. Salwa Nasser",  "A-301"),
    ("C-09D", "Grade 9-D",  "Ms. Layla Hassan",  "B-206"),  # same teacher, second class
    ("C-12A", "Grade 12-A", "Mr. Idris Kamal",   "A-210"),  # second class for Mr. Kamal
]

# class_id, student_name — everyone in CLASSES' rosters appears here.
ENROLMENTS = [
    # Grade 11-A (Ms. Hassan)
    ("C-11A", "Rayan"), ("C-11A", "Maya"), ("C-11A", "Zayd"), ("C-11A", "Lina"),
    ("C-11A", "Tariq"), ("C-11A", "Noor"),
    # Grade 10-B (Mr. Kamal)
    ("C-10B", "Yusuf"), ("C-10B", "Dina"), ("C-10B", "Omar"), ("C-10B", "Hana"),
    ("C-10B", "Bilal"), ("C-10B", "Nadia"),
    # Grade 12-C (Ms. Nasser)
    ("C-12C", "Rami"), ("C-12C", "Sara"), ("C-12C", "Kareem"), ("C-12C", "Sami"),
    ("C-12C", "Farah"), ("C-12C", "Jiad"), ("C-12C", "Mona"),
    # Grade 9-D (Ms. Hassan) — the youngest cohort, several minors
    ("C-09D", "Layth"), ("C-09D", "Reem"), ("C-09D", "Saeed"), ("C-09D", "Tala"),
    ("C-09D", "Bilal"),
    # Grade 12-A (Mr. Kamal)
    ("C-12A", "Ali"), ("C-12A", "Aisha"), ("C-12A", "Hassan"), ("C-12A", "Belal"),
    ("C-12A", "Mariam"),
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
    """Wipe and rebuild the synthetic campus dataset, augmented with REAL
    open-source scam/ham text (UCI SMS Spam Collection via miner.py). Idempotent."""
    import secrets
    from . import miner
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = connect()
    con.executemany("INSERT INTO recipients VALUES (?,?,?,?,?,?,?)",
                    RECIPIENTS + miner.mined_recipients())
    con.executemany("INSERT INTO guardians VALUES (?,?,?,?,?)", GUARDIANS)
    # Users get a fresh per-build token. secrets.token_hex is the only source of
    # randomness; the token is written to the row and never logged.
    con.executemany(
        "INSERT INTO users (user_id, display_name, role, student_name, email, api_token) "
        "VALUES (?,?,?,?,?,?)",
        [(uid, name, role, student, email, secrets.token_hex(24))
         for uid, name, role, student, email in USERS])
    scenarios = SCENARIOS + miner.mined_scenarios(start_n=len(SCENARIOS) + 1)
    for row in scenarios:
        txn_id, student, rid, amount, purpose, msg = row[:6]
        # Authored scenarios carry a student_age (7th field); mined scenarios do
        # not, and fall back to NULL so the age gate simply does not fire.
        age = row[6] if len(row) > 6 else None
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, purpose, message, student_age) "
            "VALUES (?,?,?,?,?,?,?)",
            (txn_id, student, rid, amount, purpose, msg, age))
    con.commit()
    n = con.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    print(f"dataset built: {n} transactions ({len(SCENARIOS)} authored + "
          f"{n - len(SCENARIOS)} mined from UCI SMS Spam Collection), "
          f"{len(GUARDIANS)} guardians, {len(USERS)} users -> {DB_PATH}")


if __name__ == "__main__":
    build()
