"""Review the AI-generated database entries in Excel.

    python review_queue.py export    writes data/review_queue.csv
    python review_queue.py import    applies your decisions from that file

In the file, type  approve  or  reject  in the 'decision' column (leave it empty to skip a row).
You may also correct 'title', 'equation' or 'level' on a row you approve.
Approved entries become 'reviewed' (shown with a green tick); rejected ones are hidden for good."""
import csv
import os
import sqlite3
import sys

DB_FILE = "chem.db"
FILE = os.path.join("data", "review_queue.csv")
LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}
APPROVE = {"approve", "approved", "ok", "yes", "y"}
REJECT = {"reject", "rejected", "no", "n"}
HEADER = ["id", "status", "name_a", "name_b", "level", "title", "equation", "products", "checks", "decision"]


def export():
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM pair_reactions WHERE status IN ('ai_checked','needs_review') "
                           "ORDER BY name_a, name_b, id").fetchall()
    except sqlite3.OperationalError:
        sys.exit("No AI entries have been saved yet, so there is nothing to review.")
    out = []
    for r in rows:
        prods = con.execute("SELECT name, formula FROM pair_products WHERE reaction_id=? ORDER BY id",
                            (r["id"],)).fetchall()
        plist = "; ".join(f"{p['name']} ({p['formula']})" if p["formula"] else p["name"] for p in prods)
        out.append([r["id"], r["status"], r["name_a"], r["name_b"], r["level"], r["title"],
                    r["equation"], plist, r["checks"] or "", ""])
    con.close()
    with open(FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(out)
    print(f"Wrote {len(out)} entries to {FILE}")
    print("Type approve or reject in the 'decision' column, save as CSV, then run: python review_queue.py import")


def import_decisions():
    if not os.path.exists(FILE):
        sys.exit(f"{FILE} not found. Run: python review_queue.py export")
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(FILE, encoding=enc, newline="") as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delimiter = max(",;\t", key=first.count)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]

    con = sqlite3.connect(DB_FILE)
    approved = rejected = skipped = problems = 0
    for i, row in enumerate(reader, start=2):
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            skipped += 1
            continue
        if decision not in APPROVE | REJECT:
            print(f"  line {i}: unknown decision '{decision}' (use approve or reject), skipped")
            problems += 1
            continue
        try:
            rid = int((row.get("id") or "").strip())
        except ValueError:
            print(f"  line {i}: missing id, skipped")
            problems += 1
            continue
        if con.execute("SELECT 1 FROM pair_reactions WHERE id=?", (rid,)).fetchone() is None:
            print(f"  line {i}: id {rid} is not in the database, skipped")
            problems += 1
            continue
        level = (row.get("level") or "").strip().upper()
        sets, vals = ["status=?"], ["reviewed" if decision in APPROVE else "rejected"]
        if level in LEVELS:
            sets.append("level=?"); vals.append(level)
        if (row.get("title") or "").strip():
            sets.append("title=?"); vals.append(row["title"].strip())
        if (row.get("equation") or "").strip():
            sets.append("equation=?"); vals.append(row["equation"].strip())
        con.execute(f"UPDATE pair_reactions SET {', '.join(sets)} WHERE id=?", vals + [rid])
        if decision in APPROVE:
            approved += 1
        else:
            rejected += 1
    con.commit()
    con.close()
    print(f"Approved {approved}, rejected {rejected}, left undecided {skipped}"
          + (f", {problems} row(s) had problems" if problems else "") + ".")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        export()
    elif len(sys.argv) > 1 and sys.argv[1] == "import":
        import_decisions()
    else:
        print(__doc__)
