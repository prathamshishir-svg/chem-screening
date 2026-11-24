"""Loads the CSV files in the 'data' folder into chem.db.

Usage:
    python import_data.py                 (loads every file it finds in 'data')
    python import_data.py chemicals       (loads only data/chemicals.csv)
    python import_data.py chemicals templates

Existing rows are updated, new rows are added, nothing else is removed.
Rows with a problem are skipped and explained, and the rest still load."""
import csv
import os
import sqlite3
import sys

from rdkit.Chem import AllChem
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

DB_FILE = "chem.db"
FOLDER = "data"
LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
TRUE = {"1", "true", "yes", "y"}


def read_csv(name):
    path = os.path.join(FOLDER, name + ".csv")
    if not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delimiter = max(",;\t", key=first.count)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    rows = []
    for r in reader:
        rows.append({k: (v or "").strip() for k, v in r.items() if k})
    return rows


def need(row, *cols):
    """Return an error text if a required column is empty, else None."""
    for c in cols:
        if not row.get(c):
            return f"'{c}' is empty"
    return None


def level_ok(row, col):
    if row.get(col, "").upper() not in LEVELS:
        return f"'{col}' must be one of LOW, MEDIUM, HIGH, CRITICAL (found '{row.get(col, '')}')"
    return None


def fam(x):
    return x.strip().lower()


def pair(a, b):
    a, b = fam(a), fam(b)
    return (a, b) if a <= b else (b, a)


# ---------------- one loader per table: each returns (added_or_updated, errors) ----------------

def load_chemicals(con, rows):
    done, errs = 0, []
    for i, r in enumerate(rows, start=2):
        e = need(r, "cas", "name", "family")
        if e:
            errs.append(f"line {i}: {e}")
            continue
        con.execute("INSERT OR REPLACE INTO chemicals (cas, name, family) VALUES (?,?,?)",
                    (r["cas"], r["name"], fam(r["family"])))
        done += 1
    return done, errs


def load_family_rules(con, rows):
    done, errs = 0, []
    for i, r in enumerate(rows, start=2):
        e = need(r, "family_a", "family_b", "message") or level_ok(r, "level")
        if e:
            errs.append(f"line {i}: {e}")
            continue
        a, b = pair(r["family_a"], r["family_b"])
        con.execute("INSERT OR REPLACE INTO family_rules (family_a, family_b, level, message) "
                    "VALUES (?,?,?,?)", (a, b, r["level"].upper(), r["message"]))
        done += 1
    return done, errs


def load_group_rules(con, rows):
    done, errs = 0, []
    for i, r in enumerate(rows, start=2):
        e = need(r, "prefix_a", "prefix_b", "message") or level_ok(r, "level")
        if e:
            errs.append(f"line {i}: {e}")
            continue
        a, b = r["prefix_a"].lower(), r["prefix_b"].lower()
        row = con.execute("SELECT id FROM group_rules WHERE (prefix_a=? AND prefix_b=?) "
                          "OR (prefix_a=? AND prefix_b=?)", (a, b, b, a)).fetchone()
        if row:
            con.execute("UPDATE group_rules SET level=?, message=? WHERE id=?",
                        (r["level"].upper(), r["message"], row[0]))
        else:
            con.execute("INSERT INTO group_rules (prefix_a, prefix_b, level, message) VALUES (?,?,?,?)",
                        (a, b, r["level"].upper(), r["message"]))
        done += 1
    return done, errs


def load_templates(con, rows):
    done, errs = 0, []
    for i, r in enumerate(rows, start=2):
        e = need(r, "name", "smarts") or level_ok(r, "severity")
        if e:
            errs.append(f"line {i}: {e}")
            continue
        try:
            rxn = AllChem.ReactionFromSmarts(r["smarts"])
            if rxn is None or rxn.GetNumReactantTemplates() < 1 or rxn.GetNumProductTemplates() < 1:
                raise ValueError("needs reactants and products")
        except Exception as ex:
            errs.append(f"line {i}: the SMARTS pattern is not valid ({ex}). Row skipped.")
            continue
        needs_ox = 1 if r.get("needs_oxidizer", "").lower() in TRUE else 0
        row = con.execute("SELECT id FROM templates WHERE name=?", (r["name"],)).fetchone()
        vals = (r["smarts"], r.get("byproduct", ""), r.get("conditions", ""),
                r["severity"].upper(), needs_ox)
        if row:
            con.execute("UPDATE templates SET smarts=?, byproduct=?, conditions=?, severity=?, "
                        "needs_oxidizer=? WHERE id=?", vals + (row[0],))
        else:
            con.execute("INSERT INTO templates (smarts, byproduct, conditions, severity, "
                        "needs_oxidizer, name) VALUES (?,?,?,?,?,?)", vals + (r["name"],))
        done += 1
    return done, errs


def load_reactions(con, rows, product_rows):
    """Reactions and their products come from two files, linked by 'key'."""
    done, errs, key_to_id = 0, [], {}
    for i, r in enumerate(rows, start=2):
        e = need(r, "key", "family_a", "family_b", "title") or level_ok(r, "severity")
        if e:
            errs.append(f"reactions.csv line {i}: {e}")
            continue
        a, b = pair(r["family_a"], r["family_b"])
        row = con.execute("SELECT id FROM reactions WHERE family_a=? AND family_b=? AND title=?",
                          (a, b, r["title"])).fetchone()
        vals = (r.get("when", ""), r.get("equation", ""), r["severity"].upper())
        if row:
            rid = row[0]
            con.execute("UPDATE reactions SET when_text=?, equation=?, severity=? WHERE id=?",
                        vals + (rid,))
        else:
            cur = con.execute("INSERT INTO reactions (family_a, family_b, title, when_text, "
                              "equation, severity) VALUES (?,?,?,?,?,?)",
                              (a, b, r["title"]) + vals)
            rid = cur.lastrowid
        key_to_id[r["key"]] = rid
        done += 1

    cleared = set()
    pdone = 0
    for i, p in enumerate(product_rows or [], start=2):
        e = need(p, "reaction_key", "name")
        if e:
            errs.append(f"reaction_products.csv line {i}: {e}")
            continue
        rid = key_to_id.get(p["reaction_key"])
        if rid is None:
            errs.append(f"reaction_products.csv line {i}: reaction_key '{p['reaction_key']}' "
                        "is not in reactions.csv. Row skipped.")
            continue
        if rid not in cleared:      # replace the old product list for reactions in the file
            con.execute("DELETE FROM reaction_products WHERE reaction_id=?", (rid,))
            cleared.add(rid)
        con.execute("INSERT INTO reaction_products (reaction_id, name, lookup, state, note) "
                    "VALUES (?,?,?,?,?)",
                    (rid, p["name"], p.get("lookup") or None, p.get("state", ""), p.get("note", "")))
        pdone += 1
    return done, pdone, errs


def main():
    if not os.path.exists(DB_FILE):
        sys.exit("chem.db not found. Run this from the chem-screening folder.")
    if not os.path.isdir(FOLDER):
        sys.exit(f"The '{FOLDER}' folder does not exist. Run:  python export_data.py")

    wanted = [a.lower().replace(".csv", "") for a in sys.argv[1:]]
    tables = ["chemicals", "family_rules", "group_rules", "templates", "reactions"]
    if wanted:
        tables = [t for t in tables if t in wanted or (t == "reactions" and "reaction_products" in wanted)]

    con = sqlite3.connect(DB_FILE)
    total_errors = 0
    loaders = {"chemicals": load_chemicals, "family_rules": load_family_rules,
               "group_rules": load_group_rules, "templates": load_templates}

    for t in tables:
        rows = read_csv(t)
        if rows is None:
            print(f"  {t + '.csv':24s} not found, skipped")
            continue
        if t == "reactions":
            done, pdone, errs = load_reactions(con, rows, read_csv("reaction_products"))
            print(f"  {'reactions.csv':24s} {done} reactions, {pdone} products loaded")
        else:
            done, errs = loaders[t](con, rows)
            print(f"  {t + '.csv':24s} {done} rows loaded")
        for e in errs:
            print("      PROBLEM:", e)
        total_errors += len(errs)

    con.commit()
    con.close()
    print("\nFinished." + (f" {total_errors} row(s) were skipped, see PROBLEM lines above."
                           if total_errors else " No problems found."))


if __name__ == "__main__":
    main()
