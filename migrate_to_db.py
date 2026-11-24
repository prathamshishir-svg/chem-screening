"""One-time migration: copies the knowledge currently written in Python files
into the SQLite database chem.db. Safe to run again; it refreshes only the
core tables and never touches tables that were imported from data files."""
import sqlite3

from compatibility import GROUPS, RULES
from group_rules import GROUP_RULES
from reactions import REACTIONS
from reaction_engine import TEMPLATES

from pathlib import Path

DB_FILE = str(Path(__file__).resolve().parent / "chem.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS chemicals (
    cas     TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    family  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS family_rules (
    family_a TEXT NOT NULL,
    family_b TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL,
    PRIMARY KEY (family_a, family_b)
);
CREATE TABLE IF NOT EXISTS group_rules (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    prefix_a TEXT NOT NULL,
    prefix_b TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    family_a TEXT NOT NULL,
    family_b TEXT NOT NULL,
    title    TEXT NOT NULL,
    when_text TEXT,
    equation TEXT,
    severity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reaction_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reaction_id INTEGER NOT NULL REFERENCES reactions(id),
    name        TEXT NOT NULL,
    lookup      TEXT,
    state       TEXT,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    smarts         TEXT NOT NULL,
    byproduct      TEXT,
    conditions     TEXT,
    severity       TEXT NOT NULL,
    needs_oxidizer INTEGER NOT NULL DEFAULT 0
);
-- Tables below are filled later (automatically or from data files)
CREATE TABLE IF NOT EXISTS chemical_cache (
    cid            INTEGER PRIMARY KEY,
    query_name     TEXT,
    formula        TEXT,
    molar_mass     REAL,
    cas            TEXT,
    smiles         TEXT,
    hazards_json   TEXT,
    reactivity_json TEXT,
    fetched_at     TEXT
);
CREATE TABLE IF NOT EXISTS transport_hazards (
    un_number      TEXT,
    cas            TEXT,
    name           TEXT,
    hazard_class   TEXT,
    classification_code TEXT,
    packing_group  TEXT,
    labels         TEXT
);
CREATE TABLE IF NOT EXISTS recorded_reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reactants   TEXT NOT NULL,
    products    TEXT NOT NULL,
    conditions  TEXT,
    source      TEXT
);
"""

CORE_TABLES = ["reaction_products", "reactions", "templates",
               "group_rules", "family_rules", "chemicals"]


def pair(fs):
    """Turn a frozenset of one or two family names into an ordered pair."""
    items = sorted(fs)
    return (items[0], items[-1])


def main():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.executescript(SCHEMA)

    for t in CORE_TABLES:
        cur.execute(f"DELETE FROM {t}")

    for cas, (name, family) in GROUPS.items():
        cur.execute("INSERT INTO chemicals VALUES (?,?,?)", (cas, name, family))

    for fs, (level, msg) in RULES.items():
        a, b = pair(fs)
        cur.execute("INSERT INTO family_rules VALUES (?,?,?,?)", (a, b, level, msg))

    for pa, pb, level, msg in GROUP_RULES:
        cur.execute("INSERT INTO group_rules (prefix_a, prefix_b, level, message) "
                    "VALUES (?,?,?,?)", (pa, pb, level, msg))

    for fs, rx_list in REACTIONS.items():
        a, b = pair(fs)
        for rx in rx_list:
            cur.execute(
                "INSERT INTO reactions (family_a, family_b, title, when_text, equation, severity) "
                "VALUES (?,?,?,?,?,?)",
                (a, b, rx["title"], rx["when"], rx["equation"], rx["severity"]))
            rid = cur.lastrowid
            for p in rx["products"]:
                cur.execute(
                    "INSERT INTO reaction_products (reaction_id, name, lookup, state, note) "
                    "VALUES (?,?,?,?,?)",
                    (rid, p["name"], p["lookup"], p["state"], p["note"]))

    for t in TEMPLATES:
        cur.execute(
            "INSERT INTO templates (name, smarts, byproduct, conditions, severity, needs_oxidizer) "
            "VALUES (?,?,?,?,?,?)",
            (t["name"], t["smarts"], t["byproduct"], t["conditions"], t["severity"],
             1 if t.get("needs_oxidizer") else 0))

    con.commit()

    print("Database written to", DB_FILE)
    for table in ["chemicals", "family_rules", "group_rules", "reactions",
                  "reaction_products", "templates"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:20s} {n} rows")
    con.close()


if __name__ == "__main__":
    main()