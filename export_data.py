"""Writes the knowledge tables in chem.db out as CSV files in the 'data' folder,
so you can open and edit them in Excel. Load them back with: python import_data.py"""
import csv
import os
import sqlite3

DB_FILE = "chem.db"
OUT = "data"


def write(name, header, rows):
    path = os.path.join(OUT, name)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  {name:24s} {len(rows)} rows")


def main():
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    print(f"Writing CSV files into the '{OUT}' folder:")

    write("chemicals.csv", ["cas", "name", "family"],
          con.execute("SELECT cas, name, family FROM chemicals ORDER BY family, name").fetchall())
    write("family_rules.csv", ["family_a", "family_b", "level", "message"],
          con.execute("SELECT family_a, family_b, level, message FROM family_rules "
                      "ORDER BY family_a, family_b").fetchall())
    write("group_rules.csv", ["prefix_a", "prefix_b", "level", "message"],
          con.execute("SELECT prefix_a, prefix_b, level, message FROM group_rules ORDER BY id").fetchall())
    write("templates.csv", ["name", "smarts", "byproduct", "conditions", "severity", "needs_oxidizer"],
          con.execute("SELECT name, smarts, byproduct, conditions, severity, needs_oxidizer "
                      "FROM templates ORDER BY id").fetchall())

    reactions, products, counter = [], [], {}
    for r in con.execute("SELECT id, family_a, family_b, title, when_text, equation, severity "
                         "FROM reactions ORDER BY family_a, family_b, id").fetchall():
        pair = (r[1], r[2])
        counter[pair] = counter.get(pair, 0) + 1
        key = f"{r[1]}__{r[2]}__{counter[pair]}"
        reactions.append([key, r[1], r[2], r[3], r[4], r[5], r[6]])
        for p in con.execute("SELECT name, lookup, state, note FROM reaction_products "
                             "WHERE reaction_id=? ORDER BY id", (r[0],)).fetchall():
            products.append([key, p[0], p[1] or "", p[2] or "", p[3] or ""])
    write("reactions.csv", ["key", "family_a", "family_b", "title", "when", "equation", "severity"],
          reactions)
    write("reaction_products.csv", ["reaction_key", "name", "lookup", "state", "note"], products)
    con.close()
    print("\nEdit these files in Excel, save as CSV, then run:  python import_data.py")


if __name__ == "__main__":
    main()
