"""Reads aliases.csv and loads it into the aliases table in chem.db.
Run again any time after adding rows to the CSV file."""
import csv
import sqlite3

DB_FILE = "chem.db"
CSV_FILE = "aliases.csv"

con = sqlite3.connect(DB_FILE)
con.execute("CREATE TABLE IF NOT EXISTS aliases ("
            "alias TEXT PRIMARY KEY, canonical TEXT NOT NULL)")

count = 0
with open(CSV_FILE, newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        alias = (row.get("alias") or "").strip().lower()
        canonical = (row.get("canonical") or "").strip()
        if alias and canonical:
            con.execute("INSERT OR REPLACE INTO aliases (alias, canonical) VALUES (?,?)",
                        (alias, canonical))
            count += 1

con.commit()
total = con.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
con.close()
print(f"Imported {count} rows from {CSV_FILE}. The table now holds {total} aliases.")