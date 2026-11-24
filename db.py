"""All database access lives here. The rest of the app never writes SQL."""
import html
import json
import os
import re
import sqlite3
import time

from lookup import lookup_chemical
from reactivity import get_reactivity
from bridge import get_smiles, describe_fragment
from chemkeys import key_fragments

DB_FILE = "chem.db"
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS name_index (
    query_name TEXT PRIMARY KEY,
    cid        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS product_cache (
    key          TEXT PRIMARY KEY,
    name         TEXT,
    formula      TEXT,
    cid          INTEGER,
    hazards_json TEXT,
    fetched_at   TEXT,
    cas          TEXT
);
"""


def connect():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def db_ready():
    """True if chem.db exists and the migration has filled it."""
    if not os.path.exists(DB_FILE):
        return False
    try:
        con = connect()
        n = con.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False


def ensure_extra_tables():
    con = connect()
    con.executescript(EXTRA_SCHEMA)
    try:  # databases created before CAS was saved for products
        con.execute("ALTER TABLE product_cache ADD COLUMN cas TEXT")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


# ---------- compatibility rules ----------

def family_of(cas):
    if not cas:
        return None
    con = connect()
    row = con.execute("SELECT family FROM chemicals WHERE cas=?", (cas,)).fetchone()
    con.close()
    return row["family"] if row else None


def check_pair(cas_a, cas_b):
    fa, fb = family_of(cas_a), family_of(cas_b)
    if fa is None or fb is None:
        return ("UNKNOWN",
                "No data for one or both chemicals. Treat as potentially unsafe.")
    a, b = sorted([fa, fb])
    con = connect()
    row = con.execute(
        "SELECT level, message FROM family_rules WHERE family_a=? AND family_b=?",
        (a, b)).fetchone()
    con.close()
    if row:
        return (row["level"], row["message"])
    if "inert" in (fa, fb):
        return ("NO KNOWN HAZARD",
                "No hazard found in our rules. This is not a guarantee.")
    return ("UNKNOWN", "No rule for this pair. Treat as potentially unsafe.")


def _has(groups, prefix):
    prefix = prefix.lower()
    return any(g.strip().lower().startswith(prefix) for g in groups)


def check_groups(groups_a, groups_b):
    con = connect()
    rows = con.execute("SELECT prefix_a, prefix_b, level, message FROM group_rules").fetchall()
    con.close()
    best = None
    for r in rows:
        hit = (_has(groups_a, r["prefix_a"]) and _has(groups_b, r["prefix_b"])) or \
              (_has(groups_a, r["prefix_b"]) and _has(groups_b, r["prefix_a"]))
        if hit and (best is None or ORDER.index(r["level"]) > ORDER.index(best[0])):
            best = (r["level"], r["message"])
    if best:
        return best
    return ("UNKNOWN", "No reactive-group rule matched. Treat as potentially unsafe.")


# ---------- curated reactions ----------

def get_reactions(fam_a, fam_b):
    if not fam_a or not fam_b:
        return []
    a, b = sorted([fam_a, fam_b])
    con = connect()
    rxs = con.execute(
        "SELECT id, title, when_text, equation, severity FROM reactions "
        "WHERE family_a=? AND family_b=? ORDER BY id", (a, b)).fetchall()
    out = []
    for r in rxs:
        prods = con.execute(
            "SELECT name, lookup, state, note FROM reaction_products "
            "WHERE reaction_id=? ORDER BY id", (r["id"],)).fetchall()
        out.append({
            "title": r["title"], "when": r["when_text"], "equation": r["equation"],
            "severity": r["severity"],
            "products": [dict(p) for p in prods],
        })
    con.close()
    return out


# ---------- reaction templates ----------

def load_templates():
    con = connect()
    rows = con.execute(
        "SELECT name, smarts, byproduct, conditions, severity, needs_oxidizer "
        "FROM templates ORDER BY id").fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- saved lookups (cache) ----------

def get_chemical(name):
    """Return chemical data, using the saved copy when we have one.
    Keys: cid, formula, molar_mass, cas, smiles, hazards, reactivity. None if not found."""
    ensure_extra_tables()
    key = name.strip().lower()

    con = connect()
    row = con.execute(
        "SELECT c.* FROM name_index n JOIN chemical_cache c ON c.cid = n.cid "
        "WHERE n.query_name=?", (key,)).fetchone()
    con.close()
    if row and row["smiles"]:
        return {
            "cid": row["cid"], "formula": row["formula"], "molar_mass": row["molar_mass"],
            "cas": row["cas"], "smiles": row["smiles"],
            "hazards": json.loads(row["hazards_json"] or "[]"),
            "reactivity": json.loads(row["reactivity_json"] or "{}"),
        }

    info = lookup_chemical(name)
    if not info:
        return None
    reactivity = get_reactivity(info["cid"])
    smiles = get_smiles(info["cid"])
    data = {
        "cid": info["cid"], "formula": info["formula"], "molar_mass": info["molar_mass"],
        "cas": info["cas"], "smiles": smiles, "hazards": info["hazards"],
        "reactivity": reactivity,
    }
    # Save only complete results, so a temporary network hiccup is never stored.
    if smiles and (info["hazards"] or reactivity):
        con = connect()
        con.execute(
            "INSERT OR REPLACE INTO chemical_cache "
            "(cid, query_name, formula, molar_mass, cas, smiles, hazards_json, "
            "reactivity_json, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (data["cid"], key, data["formula"], data["molar_mass"], data["cas"], smiles,
             json.dumps(data["hazards"]), json.dumps(reactivity),
             time.strftime("%Y-%m-%d %H:%M:%S")))
        con.execute("INSERT OR REPLACE INTO name_index (query_name, cid) VALUES (?,?)",
                    (key, data["cid"]))
        con.commit()
        con.close()
    return data


def get_product(kind, value):
    """kind is 'smiles' or 'name'. Returns dict: name, formula, cid, cas, hazards."""
    ensure_extra_tables()
    key = f"{kind}:{value}"
    con = connect()
    row = con.execute("SELECT * FROM product_cache WHERE key=?", (key,)).fetchone()
    con.close()
    # rows saved before CAS was recorded have cas = NULL, so they are fetched again
    if row and row["cas"] is not None:
        return {"name": row["name"], "formula": row["formula"], "cid": row["cid"],
                "cas": row["cas"] or None,
                "hazards": json.loads(row["hazards_json"] or "[]")}

    if kind == "smiles":
        d = describe_fragment(value)
    else:
        info = lookup_chemical(value)
        d = {"name": value, "formula": info["formula"] if info else None,
             "cid": info["cid"] if info else None,
             "cas": info["cas"] if info else None,
             "hazards": info["hazards"] if info else []}

    if d["cid"] is not None:
        con = connect()
        con.execute(
            "INSERT OR REPLACE INTO product_cache "
            "(key, name, formula, cid, hazards_json, fetched_at, cas) VALUES (?,?,?,?,?,?,?)",
            (key, d["name"], d["formula"], d["cid"], json.dumps(d["hazards"]),
             time.strftime("%Y-%m-%d %H:%M:%S"), d.get("cas") or ""))
        con.commit()
        con.close()
    return {"name": d["name"], "formula": d["formula"], "cid": d["cid"],
            "cas": d.get("cas") or None, "hazards": d["hazards"]}


# ---------- transport (ADR / UNECE) ----------

def _class_meaning(con, hazard_class, labels):
    """Plain-English meaning, built from the label codes (e.g. '3+8') or the class."""
    tokens = re.findall(r"\d(?:\.\d)?", labels or "") or re.findall(r"\d(?:\.\d)?", hazard_class or "")
    meanings = []
    for t in tokens:
        for candidate in (t, t.split(".")[0]):
            row = con.execute("SELECT meaning FROM hazard_classes WHERE hazard_class=?",
                              (candidate,)).fetchone()
            if row:
                if row["meaning"] not in meanings:
                    meanings.append(row["meaning"])
                break
    return "; ".join(meanings)


def get_transport(cas, limit=6):
    """ADR transport entries for a CAS number: UN number, class, packing group, labels."""
    if not cas:
        return []
    con = connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT un_number, name, hazard_class, packing_group, labels "
            "FROM transport_hazards WHERE cas=? ORDER BY un_number", (cas,)).fetchall()
    except sqlite3.OperationalError:   # transport table not imported yet
        con.close()
        return []
    out = []
    for r in rows[:limit]:
        out.append({
            "un_number": r["un_number"] or "",
            "name": html.unescape(r["name"] or ""),
            "hazard_class": r["hazard_class"] or "",
            "packing_group": r["packing_group"] or "",
            "labels": r["labels"] or "",
            "meaning": _class_meaning(con, r["hazard_class"], r["labels"]),
        })
    con.close()
    return out


# ---------- recorded reactions (USPTO etc.) ----------

def find_recorded(smiles_a, smiles_b, limit=5):
    """Recorded reactions in which BOTH chemicals appear as reactants or reagents.
    Returns (list of reactions, number of matches counted up to 1000)."""
    frags = []
    for f in key_fragments(smiles_a) + key_fragments(smiles_b):
        if f not in frags:
            frags.append(f)
    if not frags:
        return [], 0

    con = connect()
    try:
        counts = {}
        for f in frags:
            counts[f] = con.execute("SELECT COUNT(*) FROM rxn_molecules WHERE smiles=?",
                                    (f,)).fetchone()[0]
            if counts[f] == 0:           # one of them never appears: no match possible
                con.close()
                return [], 0
    except sqlite3.OperationalError:     # recorded reactions not imported yet
        con.close()
        return [], 0

    ordered = sorted(frags, key=lambda f: counts[f])     # rarest first = fastest
    joins = "".join(f" JOIN rxn_molecules m{i} ON m{i}.rxn_id = m0.rxn_id AND m{i}.smiles = ?"
                    for i in range(1, len(ordered)))
    base = f"FROM rxn_molecules m0{joins} JOIN rxn_records r ON r.id = m0.rxn_id WHERE m0.smiles = ?"
    params = ordered[1:] + [ordered[0]]

    total = con.execute(f"SELECT COUNT(*) FROM (SELECT 1 {base} LIMIT 1000)", params).fetchone()[0]
    rows = con.execute(
        f"SELECT r.reactants, r.reagents, r.products, r.year, r.patent, r.yield, r.source "
        f"{base} ORDER BY r.n_mols ASC, r.year DESC LIMIT ?", params + [limit]).fetchall()
    con.close()
    return [dict(r) for r in rows], total


def clear_saved_lookups():
    ensure_extra_tables()
    con = connect()
    for t in ("name_index", "chemical_cache", "product_cache"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
