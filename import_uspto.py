"""Imports the USPTO reaction dataset (Daniel Lowe, 1976-Sep2016) into chem.db.

Usage:
    python import_uspto.py 1976_Sep2016_USPTOgrants_smiles.7z
    python import_uspto.py file.rsmi --limit 100000      (only the first 100,000 lines)
    python import_uspto.py file.rsmi --skip 500000       (continue after a stop)

You can press Ctrl+C at any time: progress is saved, and the message at the end
tells you which --skip value to use to carry on.
Accepts .7z (needs: pip install py7zr), .zip, .gz, or an already unpacked .rsmi/.txt/.csv file."""
import argparse
import gzip
import hashlib
import os
import sqlite3
import sys
import time
import zipfile

from chemkeys import parse_reaction_smiles

DB_FILE = "chem.db"
BATCH = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS rxn_records (
    id        INTEGER PRIMARY KEY,
    reactants TEXT NOT NULL,
    reagents  TEXT,
    products  TEXT NOT NULL,
    year      INTEGER,
    patent    TEXT,
    yield     TEXT,
    source    TEXT,
    n_mols    INTEGER,
    rkey      INTEGER
);
CREATE TABLE IF NOT EXISTS rxn_molecules (
    rxn_id INTEGER NOT NULL,
    smiles TEXT NOT NULL,
    role   TEXT NOT NULL
);
"""
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_rxnmol_smiles ON rxn_molecules(smiles)",
    "CREATE INDEX IF NOT EXISTS idx_rxnmol_rxn ON rxn_molecules(rxn_id)",
]
DROP_INDEXES = ["DROP INDEX IF EXISTS idx_rxnmol_smiles", "DROP INDEX IF EXISTS idx_rxnmol_rxn"]


def open_lines(path):
    """Yield text lines from .7z / .zip / .gz / plain files."""
    low = path.lower()
    if low.endswith(".7z"):
        try:
            import py7zr
        except ImportError:
            sys.exit("This is a .7z file. Install the reader first:  pip install py7zr")
        folder = os.path.splitext(path)[0] + "_unpacked"
        if not os.path.isdir(folder):
            print(f"Unpacking {path} into {folder} (this can take a few minutes)...")
            with py7zr.SevenZipFile(path, "r") as z:
                z.extractall(folder)
        found = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs
                 if f.lower().endswith((".rsmi", ".smi", ".txt", ".csv", ".tsv"))]
        if not found:
            sys.exit(f"No reaction SMILES file found inside {folder}.")
        found.sort(key=os.path.getsize, reverse=True)
        print("Reading", found[0])
        yield from open_lines(found[0])
    elif low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            with z.open(names[0]) as f:
                for line in f:
                    yield line.decode("utf-8", errors="replace")
    elif low.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            yield from f
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            yield from f


def make_key(reactants, reagents, products):
    text = "|".join([".".join(sorted(reactants)), ".".join(sorted(reagents)),
                     ".".join(sorted(products))])
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "big", signed=True)


def main():
    ap = argparse.ArgumentParser(description="Import USPTO reactions into chem.db")
    ap.add_argument("file")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many data lines")
    ap.add_argument("--skip", type=int, default=0, help="skip this many data lines first")
    ap.add_argument("--source", default="USPTO")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        sys.exit(f"File not found: {args.file}")
    if not os.path.exists(DB_FILE):
        sys.exit("chem.db not found. Run this from the chem-screening folder.")

    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    for stmt in DROP_INDEXES:          # faster inserts; rebuilt at the end
        con.execute(stmt)
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA journal_mode=MEMORY")

    seen = {r[0] for r in con.execute("SELECT rkey FROM rxn_records WHERE rkey IS NOT NULL")}
    next_id = (con.execute("SELECT COALESCE(MAX(id), 0) FROM rxn_records").fetchone()[0]) + 1
    print(f"Existing recorded reactions: {len(seen):,}")

    rec_rows, mol_rows = [], []
    lines_done = added = dup = bad = 0
    col = {"reaction": 0, "patent": None, "year": None, "yield1": None, "yield2": None}
    header_checked = False
    start = time.time()
    stopped = False

    def flush():
        nonlocal rec_rows, mol_rows
        if rec_rows:
            con.executemany("INSERT INTO rxn_records (id, reactants, reagents, products, year, "
                            "patent, yield, source, n_mols, rkey) VALUES (?,?,?,?,?,?,?,?,?,?)", rec_rows)
            con.executemany("INSERT INTO rxn_molecules (rxn_id, smiles, role) VALUES (?,?,?)", mol_rows)
            con.commit()
        rec_rows, mol_rows = [], []

    try:
        for raw in open_lines(args.file):
            line = raw.rstrip("\n\r")
            if not line.strip():
                continue

            if not header_checked:
                header_checked = True
                if ">" not in line.split("\t")[0]:       # first line is a header
                    names = [h.strip().lower() for h in line.split("\t")]
                    for i, h in enumerate(names):
                        if "reaction" in h and "smiles" in h:
                            col["reaction"] = i
                        elif "patent" in h:
                            col["patent"] = i
                        elif h == "year":
                            col["year"] = i
                        elif "calculated" in h and "yield" in h:
                            col["yield2"] = i
                        elif "yield" in h:
                            col["yield1"] = i
                    continue

            lines_done += 1
            if lines_done <= args.skip:
                continue
            if args.limit and (lines_done - args.skip) > args.limit:
                break

            cells = line.split("\t")
            parsed = parse_reaction_smiles(cells[col["reaction"]]) if col["reaction"] < len(cells) else None
            if parsed is None:
                bad += 1
            else:
                reactants, reagents, products = parsed
                key = make_key(reactants, reagents, products)
                if key in seen:
                    dup += 1
                else:
                    seen.add(key)

                    def cell(i):
                        return cells[i].strip() if i is not None and i < len(cells) else ""
                    year = cell(col["year"])
                    yld = cell(col["yield2"]) or cell(col["yield1"])
                    mols = list(dict.fromkeys(reactants))
                    gs = [g for g in dict.fromkeys(reagents) if g not in mols]
                    rec_rows.append((next_id, ".".join(reactants), ".".join(reagents),
                                     ".".join(products), int(year) if year.isdigit() else None,
                                     cell(col["patent"]), yld, args.source,
                                     len(mols) + len(gs), key))
                    mol_rows.extend((next_id, m, "R") for m in mols)
                    mol_rows.extend((next_id, g, "G") for g in gs)
                    next_id += 1
                    added += 1

            if len(rec_rows) >= BATCH:
                flush()
            if lines_done % 20000 == 0:
                rate = lines_done / max(time.time() - start, 0.001)
                print(f"  line {lines_done:,}  added {added:,}  duplicates {dup:,}  "
                      f"unreadable {bad:,}  ({rate:,.0f} lines/s)")
    except KeyboardInterrupt:
        stopped = True
        print("\nStopped by you. Saving what was read so far...")
    finally:
        flush()
        print("Building search indexes (can take a minute on large imports)...")
        for stmt in INDEXES:
            con.execute(stmt)
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM rxn_records").fetchone()[0]
        con.close()

    print(f"\nDone. Lines read: {lines_done:,}   added: {added:,}   "
          f"duplicates skipped: {dup:,}   unreadable: {bad:,}")
    print(f"Recorded reactions now in the database: {total:,}")
    if stopped:
        print(f"To continue later, run the same command with:  --skip {lines_done}")


if __name__ == "__main__":
    main()
