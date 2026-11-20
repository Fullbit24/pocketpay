#pattern.py — the trajectory brain. agent.py judges ONE payment in isolation;
#this module judges the SHAPE of a student's recent payments as a SEQUENCE.
#Where agent._analyze answers "is this message a scam?", analyze_pattern answers
#"is this ACCOUNT drifting toward fraud?" — a different question over a longer
#window. A single 40 AED lunch is noise; a run of small trusted payments that
#mutates into rising sums to fresh unverified handles is a story, and stories are
#only visible when the payments are read in time order alongside the verdicts the
#system already reached on each one.
#
#Two design rules, both inherited from agent.py, are load-bearing:
#  1. THE MODEL ONLY OPINIONS. A drift_score is a number in a dict, nothing more.
#     It cannot move money, cannot set state, cannot block anyone. Only guard.py
#     decides and only tools.update_payment_state writes. This module writes no
#     state and logs nothing — it is a read-only observer that a caller (a
#     dashboard, a nightly sweep, an ops report) may ask for an opinion.
#  2. DEGRADE TO None, NEVER RAISE. A pattern read is a nice-to-have on top of
#     the per-transaction chain. If the brain is unavailable, the stream is cut,
#     or the JSON is unparsable, we return None and the caller carries on as if
#     the trajectory view did not exist. A missing opinion must never take down
#     the pipeline that already made a safe decision.
#
#The digest is deliberately plain text, one line per payment, oldest first, so a
#small local model sees the increase over time as a reading order, not a table it
#has to sort. Empty history is a short-circuit: we do NOT spend a brain call to
#be told there is nothing to look at.

import json
import re

PROMPT = (
    "You are Venator's pattern analyst for a university campus wallet. "
    "You are shown a student's recent payments IN TIME ORDER (oldest first), "
    "each with its amount, recipient, the recipient's trust tier, the stored "
    "state, the rule the system already applied, and any prior risk verdict. "
    "Judge the TRAJECTORY, not any single payment — is this account drifting "
    "toward fraud, being used as a money mule, escalating its amounts, fanning "
    "out across many new or unverified recipients, or repeating after being held? "
    "Small stable payments to trusted peers are 'stable'. Rising amounts to fresh "
    "unverified handles, or a repeat after a HOLD, is 'worsening'. An account that "
    "was risky and is now back to small trusted payments is 'improving'. "
    "This is an OPINION ONLY: it cannot move money, block a payment, or change any "
    "state — it is advisory and a human and deterministic code remain in charge. "
    "If the sequence is too short or too ordinary to tell, say 'stable' with LOW "
    "confidence rather than inventing a trend. "
    "Respond with ONLY a JSON object, no prose, no markdown, exactly these keys:\n"
    '{"drift_score": <integer 0-100>, "direction": "stable"|"worsening"|"improving", '
    '"confidence": <float 0-1>, "signals": ["short phrase", ...], '
    '"explanation": "<one sentence>"}'
)

_DIRECTIONS = ("stable", "worsening", "improving")


def _parse_pattern(raw):
    """Robust JSON extraction, mirroring agent._parse_analysis: a small local
    model WILL sometimes wrap its answer in prose or a code fence, and sometimes
    the stream ends before the closing brace. Repair, don't give up. Returns a
    normalised dict, or None if we cannot get at least drift_score + confidence —
    those two are the only fields the caller is contractually promised."""
    raw = raw or ""
    start = raw.find("{")
    if start == -1:
        return None
    blob = raw[start:]
    candidates = [blob]
    # heal truncated/cut streams: close any dangling quote + unbalanced braces
    open_braces = blob.count("{") - blob.count("}")
    if open_braces > 0:
        healed = blob + ('"' if blob.count('"') % 2 else "") + "}" * open_braces
        candidates.append(healed)
    for cand in candidates:
        # trim trailing junk after the first balanced object
        depth, end = 0, -1
        for i, ch in enumerate(cand):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        text = cand[:end + 1] if end != -1 else cand
        try:
            d = json.loads(text)
            drift = max(0, min(100, int(float(d["drift_score"]))))
            conf = max(0.0, min(1.0, float(d["confidence"])))
            direction = str(d.get("direction") or "stable").strip().lower()
            if direction not in _DIRECTIONS:
                direction = "stable"
            return {"drift_score": drift,
                    "direction": direction,
                    "confidence": conf,
                    "signals": [str(s) for s in (d.get("signals") or [])],
                    "explanation": str(d.get("explanation") or "")}
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    # last resort: pull the two guarantees (+ direction + explanation) out by regex
    m_d = re.search(r'"drift_score"\s*:\s*(\d+)', blob)
    m_c = re.search(r'"confidence"\s*:\s*([\d.]+)', blob)
    if m_d and m_c:
        m_dir = re.search(r'"direction"\s*:\s*"(\w+)', blob)
        m_e = re.search(r'"explanation"\s*:\s*"([^"]+)', blob)
        direction = m_dir.group(1).lower() if m_dir else "stable"
        if direction not in _DIRECTIONS:
            direction = "stable"
        return {"drift_score": max(0, min(100, int(m_d.group(1)))),
                "direction": direction,
                "confidence": max(0.0, min(1.0, float(m_c.group(1)))),
                "signals": [],
                "explanation": m_e.group(1) if m_e else ""}
    return None


def build_history(con, student, limit=25):
    """Assemble a student's most recent `limit` payments as a compact plain-text
    digest, NEWEST LAST so the model reads the sequence in time order.

    Each line carries everything the trajectory question needs: the amount, the
    recipient and its trust tier (LEFT JOIN recipients — a payment to a handle
    missing from the directory still shows, tier reads as 'unknown'), the stored
    state, the rule the system already applied (transactions.decision), the stored
    risk_score/confidence (the model's own prior verdict on that payment), and the
    created_at stamp. If a decision_log row from actor='llm' exists for the txn we
    append its detail trimmed to ~120 chars, so the model can see what it thought
    at the time and whether it stayed consistent.

    Returns a string. Empty history returns a short 'no history' line — the caller
    (analyze_pattern) is expected to treat that as the empty case and not call the
    model at all."""
    rows = con.execute(
        "SELECT t.txn_id, t.amount_aed, t.recipient_id, t.state, t.decision, "
        "       t.risk_score, t.confidence, t.created_at, "
        "       r.tier AS tier "
        "FROM transactions t "
        "LEFT JOIN recipients r ON r.recipient_id = t.recipient_id "
        "WHERE t.student = ? "
        "ORDER BY t.created_at DESC, t.txn_id DESC "
        "LIMIT ?",
        (student, limit)).fetchall()
    # the query reads newest-first so LIMIT keeps the most RECENT window; flip
    # to oldest-first so the model reads the story forwards.
    rows = list(reversed(rows))
    if not rows:
        return (f"No payment history on file for student {student!r}; "
                f"there is no sequence to judge.")

    lines = [f"Payment history for student {student} (most recent {len(rows)}, oldest first):"]
    for i, r in enumerate(rows, 1):
        tier = r["tier"] or "unknown"
        risk = r["risk_score"] if r["risk_score"] is not None else "-"
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "-"
        rule = r["decision"] or "-"
        when = r["created_at"] or "-"
        line = (f"{i}. {when} | {r['amount_aed']:g} AED -> {r['recipient_id']} "
                f"({tier}) | state={r['state']} | rule={rule} | "
                f"prior risk={risk}/100 conf={conf}")
        # fold in the model's own prior opinion verbatim (trimmed) if it logged one
        prior = con.execute(
            "SELECT detail FROM decision_log "
            "WHERE txn_id=? AND actor='llm' ORDER BY id DESC LIMIT 1",
            (r["txn_id"],)).fetchone()
        if prior and prior["detail"]:
            line += f" | prior llm: {prior['detail'][:120]}"
        lines.append(line)
    return "\n".join(lines)


def analyze_pattern(con, student, llm=None):
    """Ask the model for a TRAJECTORY opinion on a student's payment history.

    Empty history short-circuits to None with NO model call — there is nothing to
    judge, and we do not spend a brain invocation to be told so. Otherwise we
    build the digest and call the model EXACTLY the way agent._analyze does
    (lazy ModelHouse().send(sid=1, messages=[system, user], rea="low")), parse
    tolerantly, and return a normalised dict. On ANY failure — unavailable brain,
    cut stream, unparsable JSON — return None. Never raises: the per-transaction
    chain must not depend on this optional observer.

    `llm`, when given, is a callable user_prompt_string -> raw_model_string, used
    by tests and offline runs so nothing here ever needs the network."""
    digest = build_history(con, student)
    if not digest or digest.startswith("No payment history"):
        return None
    try:
        if llm is None:
            from capri.openai_lib import ModelHouse
            mh = ModelHouse()
            raw = "".join(mh.send(sid=1, messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": digest}], rea="low"))
        else:
            raw = llm(digest)
        return _parse_pattern(raw)
    except Exception:
        # a missing opinion must never propagate into the caller's pipeline
        return None


if __name__ == "__main__":
    # Offline self-test. Builds the REAL schema in memory (copied from db.SCHEMA
    # for the three tables this module touches), lays down a story that visibly
    # worsens, and exercises build_history + analyze_pattern with a FAKE llm.
    # No network: the fake callable is the only brain. Exit 0 on all passes.
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE transactions (
        txn_id TEXT PRIMARY KEY, student TEXT NOT NULL, recipient_id TEXT NOT NULL,
        amount_aed REAL NOT NULL, purpose TEXT, message TEXT, student_age INTEGER,
        state TEXT NOT NULL DEFAULT 'RECEIVED', risk_score INTEGER, confidence REAL,
        decision TEXT, explanation TEXT, created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT);
    CREATE TABLE recipients (
        recipient_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, tier TEXT NOT NULL,
        created_days_ago INTEGER NOT NULL, scam_reports INTEGER NOT NULL DEFAULT 0,
        txns_90d INTEGER NOT NULL DEFAULT 0, simulate_outage INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE decision_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL, actor TEXT NOT NULL,
        step TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
    """)

    con.executemany("INSERT INTO recipients VALUES (?,?,?,?,?,?,?)", [
        ("R-CANTEEN", "Canteen Al-Falah", "verified_vendor", 400, 0, 1200, 0),
        ("R-OMAR",    "Omar (classmate)", "trusted_peer",    365, 0, 47,   0),
        ("R-NEWBIE",  "student_store_deals", "unverified",   1,   0, 0,    0),
        ("R-GHOST",   "ghost_trades_99", "unverified",       5,   1, 0,    0),
    ])

    # A story of 8 payments that visibly worsens: starts as small, trusted,
    # boring peer payments, then the amounts climb and the recipients turn
    # unverified, with one HOLD that is simply repeated at a higher sum.
    story = [
        ("TX-P1", "Rayan", "R-CANTEEN", 12.0, "CLEAR",  "ALLOW (low_risk)",     5,  0.90, "2026-11-01 09:00:00"),
        ("TX-P2", "Rayan", "R-OMAR",    15.0, "CLEAR",  "ALLOW (low_risk)",     6,  0.88, "2026-11-03 10:00:00"),
        ("TX-P3", "Rayan", "R-CANTEEN", 18.0, "CLEAR",  "ALLOW (low_risk)",     4,  0.92, "2026-11-05 12:30:00"),
        ("TX-P4", "Rayan", "R-OMAR",    20.0, "CLEAR",  "ALLOW (low_risk)",     8,  0.80, "2026-11-08 11:00:00"),
        ("TX-P5", "Rayan", "R-NEWBIE",  60.0, "HOLD",   "HOLD (unverified_recipient)", 55, 0.60, "2026-11-11 14:00:00"),
        ("TX-P6", "Rayan", "R-NEWBIE",  90.0, "HOLD",   "HOLD (unverified_recipient)", 62, 0.55, "2026-11-13 15:00:00"),
        ("TX-P7", "Rayan", "R-GHOST",  220.0, "ESCALATED", "ESCALATE (scam_signal)", 81, 0.75, "2026-11-15 16:00:00"),
        ("TX-P8", "Rayan", "R-NEWBIE", 260.0, "ESCALATED", "ESCALATE (scam_signal)", 88, 0.82, "2026-11-17 18:00:00"),
    ]
    for txn_id, student, rid, amt, state, decision, risk, conf, ts in story:
        con.execute(
            "INSERT INTO transactions (txn_id, student, recipient_id, amount_aed, purpose, "
            "state, risk_score, confidence, decision, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (txn_id, student, rid, amt, "test", state, risk, conf, decision, ts))
    # a prior llm verdict so the digest's 'prior llm:' branch is exercised
    con.execute("INSERT INTO decision_log (txn_id, actor, step, detail) VALUES (?,?,?,?)",
                ("TX-P7", "llm", "analyze_message",
                 "risk=81/100 conf=0.75 indicators=['urgent', 'unverified'] :: "
                 "This reads like a scam pushing a fast transfer."))
    con.commit()

    calls = {"n": 0}

    def fake_llm(prompt):
        calls["n"] += 1
        assert "Rayan" in prompt, "digest must name the student"
        return ('{"drift_score": 78, "direction": "worsening", "confidence": 0.9, '
                '"signals": ["rising amounts", "new unverified recipients"], '
                '"explanation": "This account is drifting toward higher-risk payments."}')

    hist = build_history(con, "Rayan")
    assert isinstance(hist, str) and hist, "build_history must return a non-empty string"
    assert "Rayan" in hist, "digest must contain the student name"
    body = [ln for ln in hist.splitlines() if ln[:1].isdigit()]
    assert len(body) >= 6, f"expected >=6 payment lines, got {len(body)}"
    assert "prior llm:" in hist, "digest must fold in the model's prior opinion"
    print(f"PASS build_history: {len(body)} payment lines, digest {len(hist)} chars")
    print("PASS build_history: oldest-first ordering and prior-llm detail present")

    # digest should read oldest-first: the small trusted payment leads the story
    assert "12 AED" in body[0] and "260 AED" in body[-1], "sequence must be time-ordered"
    print("PASS build_history: time-ordered (starts small+trusted, ends large+unverified)")

    res = analyze_pattern(con, "Rayan", llm=fake_llm)
    assert res is not None, "analyze_pattern must return a dict for a real student"
    assert res["direction"] == "worsening", f"expected worsening, got {res['direction']}"
    assert res["drift_score"] == 78, f"expected drift_score 78, got {res['drift_score']}"
    assert res["confidence"] == 0.9
    assert len(res["signals"]) == 2
    print(f"PASS analyze_pattern: direction={res['direction']} drift_score={res['drift_score']} "
          f"confidence={res['confidence']} signals={res['signals']}")
    print(f"PASS analyze_pattern: fake llm called {calls['n']} time(s)")

    before = calls["n"]
    unknown = analyze_pattern(con, "Nobody", llm=fake_llm)
    assert unknown is None, "unknown student must return None"
    assert calls["n"] == before, "model must NOT be called when history is empty"
    print(f"PASS empty-history short-circuit: unknown student -> None, "
          f"model calls unchanged ({calls['n']})")

    # parser robustness: fenced + truncated answers still yield a usable dict
    fenced = _parse_pattern('```json\n{"drift_score": 44, "confidence": 0.5}\n```')
    assert fenced is not None and fenced["drift_score"] == 44
    healed = _parse_pattern('{"drift_score": 30, "direction": "improving", "confidence": 0.7')
    assert healed is not None and healed["direction"] == "improving" and healed["drift_score"] == 30
    assert _parse_pattern("no json here") is None
    print("PASS _parse_pattern: fenced, truncated and junk inputs handled")

    print("ALL PATTERN TESTS PASSED")
