"""Stores reaction entries for specific chemical pairs in chem.db.

Statuses:  reviewed     approved by a person
           ai_checked   suggested by the AI and passed the automatic checks (not reviewed)
           needs_review suggested by the AI but failed a check (never shown as a database answer)
           rejected     rejected by a person (never shown, never overwritten)
A person's decision (reviewed / rejected) is never overwritten by new AI output."""
import sqlite3
import time

DB_FILE = "chem.db"
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS pair_reactions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key_a     TEXT NOT NULL,
    key_b     TEXT NOT NULL,
    name_a    TEXT,
    name_b    TEXT,
    title     TEXT NOT NULL,
    when_text TEXT,
    equation  TEXT,
    level     TEXT,
    notes     TEXT,
    status    TEXT NOT NULL,
    checks    TEXT,
    model     TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pair_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reaction_id INTEGER NOT NULL,
    name        TEXT,
    formula     TEXT,
    state       TEXT,
    note        TEXT,
    cas         TEXT,
    matched     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pair_keys ON pair_reactions(key_a, key_b);
"""


def _con():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def key_for(chem):
    """Stable identifier for a chemical: its CAS number, else its lower-case name."""
    cas = (chem.get("cas") or "").strip()
    return cas if cas else "name:" + (chem.get("name") or "").strip().lower()


def _ordered(key_a, key_b):
    return (key_a, key_b) if key_a <= key_b else (key_b, key_a)


def save_pair_answer(a, b, answer, checks, decisions, model=""):
    """Save every reaction of an AI answer with its status.
    Returns {'checked': n, 'review': m, 'kept': True/False}."""
    ka, kb = _ordered(key_for(a), key_for(b))
    con = _con()
    human = con.execute("SELECT COUNT(*) FROM pair_reactions WHERE key_a=? AND key_b=? "
                        "AND status IN ('reviewed','rejected')", (ka, kb)).fetchone()[0]
    if human:                                   # a person already decided about this pair
        con.close()
        return {"checked": 0, "review": 0, "kept": True}

    old = [r[0] for r in con.execute("SELECT id FROM pair_reactions WHERE key_a=? AND key_b=?", (ka, kb))]
    for rid in old:
        con.execute("DELETE FROM pair_products WHERE reaction_id=?", (rid,))
        con.execute("DELETE FROM pair_reactions WHERE id=?", (rid,))

    names = (a["name"], b["name"]) if key_for(a) <= key_for(b) else (b["name"], a["name"])
    counts = {"checked": 0, "review": 0, "kept": False}
    for rx, res, (status, reasons) in zip(answer["reactions"], checks, decisions):
        cur = con.execute(
            "INSERT INTO pair_reactions (key_a, key_b, name_a, name_b, title, when_text, equation, "
            "level, notes, status, checks, model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ka, kb, names[0], names[1], rx["name"], rx["conditions"], rx["equation"],
             rx["hazard_level"], rx["notes"], status, "; ".join(reasons), model,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        rid = cur.lastrowid
        for p, pc in zip(rx["products"], res["products"]):
            con.execute("INSERT INTO pair_products (reaction_id, name, formula, state, note, cas, matched) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (rid, p["name"], p["formula"], p["state"], "; ".join(p["hazards"]),
                         pc.get("cas"), 1 if pc["matched"] else 0))
        counts["checked" if status == "ai_checked" else "review"] += 1
    con.commit()
    con.close()
    return counts


def get_pair_reactions(key_a, key_b, statuses=("reviewed", "ai_checked")):
    """Entries for a pair with the given statuses, with their products."""
    ka, kb = _ordered(key_a, key_b)
    con = _con()
    marks = ",".join("?" * len(statuses))
    rows = con.execute(f"SELECT * FROM pair_reactions WHERE key_a=? AND key_b=? "
                       f"AND status IN ({marks}) ORDER BY (status='reviewed') DESC, id",
                       (ka, kb) + tuple(statuses)).fetchall()
    out = []
    for r in rows:
        prods = con.execute("SELECT name, formula, state, note, cas, matched FROM pair_products "
                            "WHERE reaction_id=? ORDER BY id", (r["id"],)).fetchall()
        d = dict(r)
        d["products"] = [dict(p) for p in prods]
        out.append(d)
    con.close()
    return out


def pair_has_rows(key_a, key_b):
    """True if the pair has any saved entry at all, whatever its status (even rejected)."""
    ka, kb = _ordered(key_a, key_b)
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM pair_reactions WHERE key_a=? AND key_b=?", (ka, kb)).fetchone()[0]
    con.close()
    return n > 0


def worst_level(entries):
    """Highest known level among entries, or None."""
    levels = [e["level"] for e in entries if e["level"] in ORDER]
    return max(levels, key=ORDER.index) if levels else None
