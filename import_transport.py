"""Imports a dangerous-goods table (ADR / UN list) from a CSV or Excel file
into the transport_hazards table in chem.db.

Usage:  python import_transport.py yourfile.csv
        python import_transport.py yourfile.xlsx

It finds the right columns by reading the header names, and prints what it found.
Safe to run again: it replaces the previous transport data each time."""
import csv
import io
import re
import sqlite3
import sys

from pathlib import Path

DB_FILE = str(Path(__file__).resolve().parent / "chem.db")
CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")

CLASS_MEANINGS = [
    ("1", "Explosives"),
    ("2.1", "Flammable gases"),
    ("2.2", "Non-flammable, non-toxic gases"),
    ("2.3", "Toxic gases"),
    ("3", "Flammable liquids"),
    ("4.1", "Flammable solids, self-reactive substances"),
    ("4.2", "Substances liable to spontaneous combustion"),
    ("4.3", "Substances that emit flammable gases in contact with water"),
    ("5.1", "Oxidizing substances"),
    ("5.2", "Organic peroxides"),
    ("6.1", "Toxic substances"),
    ("6.2", "Infectious substances"),
    ("7", "Radioactive material"),
    ("8", "Corrosive substances"),
    ("9", "Miscellaneous dangerous substances and articles"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS transport_hazards (
    un_number TEXT, cas TEXT, name TEXT, hazard_class TEXT,
    classification_code TEXT, packing_group TEXT, labels TEXT
);
CREATE TABLE IF NOT EXISTS hazard_classes (
    hazard_class TEXT PRIMARY KEY, meaning TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transport_cas ON transport_hazards(cas);
"""


def _find_header(rows):
    """The real header row is the first one mentioning both 'cas' and 'class'."""
    for i, r in enumerate(rows[:60]):
        text = " ".join(c.lower() for c in r)
        if "cas" in text and "class" in text:
            return i
    return 0


def read_rows(path):
    """Return (headers, list of row lists) from a CSV or Excel file.
    Skips any notes or group-title lines above the real header row."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        text = None
        for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
            try:
                with open(path, encoding=enc, newline="") as f:
                    text = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        lines = text.splitlines()
        # delimiter: judged on the line that holds the column names
        probe = next((ln for ln in lines[:60] if "cas" in ln.lower() and "class" in ln.lower()),
                     lines[0] if lines else "")
        delimiter = max(",;\t|", key=probe.count)
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    h = _find_header(rows)
    return [c.strip() for c in rows[h]], rows[h + 1:]


def detect_columns(headers):
    """Guess which columns are which, from header words.
    Returns {field: [column indexes]} (CAS may come from several columns)."""
    norm = [h.lower() for h in headers]
    wanted = {
        "un_number": lambda h: bool(re.search(r"\bun\b", h)) or (h.startswith("un") and "number" in h),
        "cas": lambda h: "cas" in h,
        "name": lambda h: any(w in h for w in ("name", "substance", "description", "designation")),
        "classification_code": lambda h: "classification code" in h or h == "code",
        "hazard_class": lambda h: "class" in h and "classification" not in h,
        "packing_group": lambda h: "packing group" in h or h in ("pg", "packing"),
        "labels": lambda h: "label" in h,
    }
    found = {}
    for field, test in wanted.items():
        found[field] = [i for i, h in enumerate(norm) if test(h)]
    # one column per field, except CAS (all) and name (the last one is the ADR entry name)
    cols = {}
    used = set()
    for field in ["un_number", "classification_code", "hazard_class", "packing_group", "labels"]:
        for i in found[field]:
            if i not in used:
                cols[field] = [i]
                used.add(i)
                break
    cols["cas"] = found["cas"]
    used.update(found["cas"])
    names = [i for i in found["name"] if i not in used]
    if names:
        cols["name"] = [names[-1]]
    return {k: v for k, v in cols.items() if v}


def main(path=None):
    if path is None:
        if len(sys.argv) < 2:
            print(__doc__)
            return
        path = sys.argv[1]
    headers, rows = read_rows(path)
    if not headers:
        print("The file looks empty.")
        return

    cols = detect_columns(headers)
    print("Column headers found:", headers)
    print("Matched columns:")
    for field, idx in cols.items():
        print(f"  {field:20s} <- " + ", ".join(f"'{headers[i]}'" for i in idx))

    if "cas" not in cols and "un_number" not in cols:
        print("\nCould not find a CAS or UN number column. Send me the header list "
              "above and I will adjust the importer.")
        return
    if "cas" not in cols:
        print("\nWarning: no CAS column found. Products cannot be matched to this "
              "table by CAS, only by UN number.")

    def get(row, field):
        idx = cols.get(field)
        if not idx:
            return ""
        return " ".join(row[i].strip() for i in idx if i < len(row) and row[i].strip())

    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM transport_hazards")
    for cls, meaning in CLASS_MEANINGS:
        con.execute("INSERT OR REPLACE INTO hazard_classes VALUES (?,?)", (cls, meaning))

    n_rows = n_cas = 0
    for row in rows:
        cas_list = list(dict.fromkeys(CAS_RE.findall(get(row, "cas")))) or [None]
        for cas in cas_list:
            con.execute(
                "INSERT INTO transport_hazards VALUES (?,?,?,?,?,?,?)",
                (get(row, "un_number"), cas, get(row, "name"), get(row, "hazard_class"),
                 get(row, "classification_code"), get(row, "packing_group"),
                 get(row, "labels")))
            n_rows += 1
            if cas:
                n_cas += 1
    con.commit()
    con.close()
    print(f"\nImported {n_rows} rows ({n_cas} with a CAS number) into transport_hazards.")


if __name__ == "__main__":
    main()