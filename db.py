"""All database access lives here. The rest of the app never writes SQL."""
import html
import json
import os
import re
import sqlite3
import time
import timing
from rdkit import Chem

from lookup import lookup_chemical
from reactivity import get_reactivity
from bridge import get_smiles, get_structure, describe_fragment
from chemkeys import key_fragments

from pathlib import Path

DB_FILE = str(Path(__file__).resolve().parent / "chem.db")
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ReactionSearchError(ValueError):
    """A two-input reaction search could not validate one of its structures."""

    def __init__(self, input_label, reason):
        self.input_label = input_label
        self.reason = reason
        super().__init__(f"{input_label}: {reason}")

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

    local_lookup_started = timing.start_timer()
    con = connect()
    row = con.execute(
        "SELECT c.* FROM name_index n JOIN chemical_cache c ON c.cid = n.cid "
        "WHERE n.query_name=?", (key,)).fetchone()
    con.close()
    timing.log_duration("Local chemical database lookup", local_lookup_started)
    if row and row["smiles"] and Chem.MolFromSmiles(row["smiles"]) is not None:
        return {
            "cid": row["cid"], "formula": row["formula"], "molar_mass": row["molar_mass"],
            "cas": row["cas"], "smiles": row["smiles"],
            "hazards": json.loads(row["hazards_json"] or "[]"),
            "reactivity": json.loads(row["reactivity_json"] or "{}"),
            "structure_source": "local chemical cache",
            "structure_identifier_type": "PubChem CID",
            "structure_identifier": str(row["cid"]),
            "structure_source_detail": (
                "Original structure source was not stored; cache row timestamp: "
                f"{row['fetched_at'] or 'not recorded'}."
            ),
            "structure_validation": "rdkit_valid",
        }
    if row:
        # Keep the cached chemical identity, hazards and reactivity while
        # requesting a current source structure if the saved one is absent or
        # invalid. Never replace it with a formula-derived guess.
        structure = get_structure(row["cid"])
        return {
            "cid": row["cid"], "formula": row["formula"], "molar_mass": row["molar_mass"],
            "cas": row["cas"], "smiles": structure["smiles"],
            "hazards": json.loads(row["hazards_json"] or "[]"),
            "reactivity": json.loads(row["reactivity_json"] or "{}"),
            "structure_source": structure["source"],
            "structure_identifier_type": structure["identifier_type"],
            "structure_identifier": structure["identifier"],
            "structure_source_property": structure["source_property"],
            "structure_retrieved_at": structure["retrieved_at"],
            "structure_validation": structure["validation"],
            "structure_source_smiles": structure["source_smiles"],
            "structure_error": structure["error"],
        }

    # ORD supplies structure-only records for many mined reaction participants.
    # Resolve an exact structure locally before making an external name lookup.
    molecule = Chem.MolFromSmiles(name)
    if molecule is not None:
        for atom in molecule.GetAtoms():
            atom.SetAtomMapNum(0)
        structure_key = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        con = connect()
        try:
            structure = con.execute(
                "SELECT s.canonical_smiles,s.molecular_formula,s.molecular_weight,"
                "s.first_source_record_id,r.dataset_id,r.source_doi "
                "FROM ord_chemical_structures s JOIN dataset_release d ON d.id=s.first_release_id "
                "JOIN dataset_source src ON src.id=d.source_id "
                "JOIN ord_reaction_records r ON r.release_id=d.id "
                "AND r.source_record_id=s.first_source_record_id "
                "WHERE s.canonical_smiles=? AND src.source_key='open-reaction-database' LIMIT 1",
                (structure_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            structure = None
        finally:
            con.close()
        if structure:
            return {
                "cid": None, "formula": structure["molecular_formula"],
                "molar_mass": structure["molecular_weight"], "cas": None,
                "smiles": structure["canonical_smiles"], "hazards": [],
                "reactivity": {}, "data_source": "ORD structure-only record",
                "source_record_id": structure["first_source_record_id"],
                "dataset_id": structure["dataset_id"], "source_doi": structure["source_doi"],
            }

    info = lookup_chemical(name)
    if not info:
        return None
    reactivity = get_reactivity(info["cid"])
    structure = get_structure(info["cid"])
    smiles = structure["smiles"]
    data = {
        "cid": info["cid"], "formula": info["formula"], "molar_mass": info["molar_mass"],
        "cas": info["cas"], "smiles": smiles, "hazards": info["hazards"],
        "reactivity": reactivity, "structure_source": structure["source"],
        "structure_identifier_type": structure["identifier_type"],
        "structure_identifier": structure["identifier"],
        "structure_source_property": structure["source_property"],
        "structure_retrieved_at": structure["retrieved_at"],
        "structure_validation": structure["validation"],
        "structure_source_smiles": structure["source_smiles"],
        "structure_error": structure["error"],
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


def find_epa_toxval_records(identifier, *, casrn=None, dtxsid=None, limit=50):
    """Return EPA ToxValDB records for an exact DTXSID, CASRN, or chemical name.

    Identity matching is exact (case-insensitive for names). It does not infer
    identities from molecular formula or reaction co-occurrence.
    """
    raw = (identifier or "").strip()
    if not raw and not casrn and not dtxsid:
        return {"records": [], "total": 0, "match_basis": None}
    try:
        if dtxsid or re.fullmatch(r"DTXSID\d+", raw, re.IGNORECASE):
            clause, value, basis, identity_index = (
                "c.dtxsid=?", (dtxsid or raw).strip().upper(), "DTXSID",
                "idx_epa_toxval_chemical_dtxsid",
            )
        elif casrn or re.fullmatch(r"\d{2,7}-\d{2}-\d", raw):
            clause, value, basis, identity_index = (
                "c.casrn=?", (casrn or raw).strip(), "CASRN",
                "idx_epa_toxval_chemical_casrn",
            )
        elif raw:
            clause, value, basis, identity_index = (
                "c.name=? COLLATE NOCASE", raw, "exact chemical name",
                "idx_epa_toxval_chemical_name",
            )
        else:
            return {"records": [], "total": 0, "match_basis": None}

        con = connect()
        try:
            release = con.execute(
                "SELECT d.id FROM dataset_release d "
                "JOIN dataset_source s ON s.id=d.source_id "
                "WHERE s.source_key='epa-toxvaldb' AND d.release_version=? "
                "ORDER BY d.id DESC LIMIT 1", ("9.7.0",),
            ).fetchone()
            if not release:
                return {"records": [], "total": 0, "match_basis": basis}
            release_id = release[0]
            # Drive from the indexed EPA identity, then use the assertion's
            # chemical_id index. This avoids scanning the full release range.
            from_clause = (
                f"FROM epa_toxval_chemicals c INDEXED BY {identity_index} "
                "JOIN epa_toxval_assertions a INDEXED BY idx_epa_toxval_assertion_identity_fk "
                "ON a.chemical_id=c.id AND a.release_id=c.release_id "
                "WHERE c.release_id=? AND " + clause
            )
            params = (release_id, value)
            total = con.execute("SELECT COUNT(*) " + from_clause, params).fetchone()[0]
            rows = con.execute(
                "SELECT a.toxval_id,a.dtxsid,c.dtxsid AS identity_dtxsid,c.casrn,c.name,"
                "a.source,a.subsource,a.source_url,a.subsource_url,a.qc_status,a.qc_category,"
                "a.risk_assessment_class,a.human_eco,a.experimental_record,a.toxval_type,"
                "a.toxval_type_original,a.toxval_subtype,a.toxval_type_supercategory,"
                "a.toxval_type_definition,a.toxval_numeric,a.toxval_numeric_raw,"
                "a.toxval_numeric_original,a.toxval_numeric_original_raw,a.toxval_units,"
                "a.toxval_units_original,a.toxval_numeric_qualifier,"
                "a.toxval_numeric_qualifier_original,a.study_type,a.study_type_original,"
                "a.study_duration_class,a.study_duration_value,a.study_duration_units,"
                "a.species_common,a.species_latin,a.species_original,a.strain,a.sex,"
                "a.exposure_route,a.exposure_method,a.exposure_form,a.media,a.lifestage,"
                "a.generation,a.toxicological_effect,a.toxicological_effect_original,"
                "a.year,a.key_finding,a.identity_match_status,? AS release_version "
                + from_clause + " ORDER BY a.toxval_id LIMIT ?",
                ("9.7.0",) + params + (max(1, min(int(limit), 500)),),
            ).fetchall()
            records = [dict(row) for row in rows]
            if records:
                ids = [row["toxval_id"] for row in records]
                placeholders = ",".join("?" for _ in ids)
                references = con.execute(
                    "SELECT toxval_id,record_source_id,source,record_source_type,url,"
                    "document_name,long_ref,title,author,year,doi,guideline,glp,quality,"
                    "external_source_id,external_source_id_desc FROM epa_toxval_record_sources "
                    "WHERE release_id=? "
                    f"AND toxval_id IN ({placeholders}) ORDER BY priority,record_source_id",
                    (release_id, *ids),
                ).fetchall()
                reference_map = {}
                for reference in references:
                    reference_map.setdefault(reference["toxval_id"], []).append(dict(reference))
                for record in records:
                    record["references"] = reference_map.get(record["toxval_id"], [])
            return {"records": records, "total": total, "match_basis": basis}
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return {"records": [], "total": 0, "match_basis": None}
        raise


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
    """Search records containing both validated inputs as reactants/reagents.

    Results are allocated across source datasets so an early source cannot hide
    every other source. The total is an exact count of distinct matching source
    rows; a missing/invalid input raises instead of becoming a one-sided search.
    """
    input_fragments = []
    for label, smiles in (("Chemical 1", smiles_a), ("Chemical 2", smiles_b)):
        try:
            molecule = Chem.MolFromSmiles(smiles) if smiles else None
        except Exception:
            molecule = None
        fragments = key_fragments(smiles) if molecule is not None else []
        if molecule is None or not fragments:
            raise ReactionSearchError(label, "a valid molecular structure is unavailable")
        input_fragments.append(fragments)
    frags = list(dict.fromkeys(input_fragments[0] + input_fragments[1]))
    con = connect()
    try:
        counts = {}
        for f in frags:
            counts[f] = con.execute(
                "SELECT COUNT(*) FROM rxn_molecules WHERE smiles=? AND role IN ('R','G')",
                (f,),
            ).fetchone()[0]
            if counts[f] == 0:           # one of them never appears: no match possible
                con.close()
                return [], 0
    except sqlite3.OperationalError:     # recorded reactions not imported yet
        con.close()
        return [], 0

    ordered = sorted(frags, key=lambda f: counts[f])     # rarest first = fastest
    exists = "".join(
        f" AND EXISTS (SELECT 1 FROM rxn_molecules mx{i} "
        f"WHERE mx{i}.rxn_id=m0.rxn_id AND mx{i}.smiles=? AND mx{i}.role IN ('R','G'))"
        for i in range(1, len(ordered))
    )
    base = (f"FROM rxn_molecules m0 JOIN rxn_records r ON r.id=m0.rxn_id "
            "WHERE m0.smiles=? AND m0.role IN ('R','G')" + exists)
    params = [ordered[0]] + ordered[1:]
    limit = max(0, min(int(limit), 100))
    per_source = con.execute(
        f"SELECT r.source,COUNT(DISTINCT r.id) AS total {base} GROUP BY r.source",
        params,
    ).fetchall()
    totals = {row["source"] or "Unknown": row["total"] for row in per_source}
    total = sum(totals.values())
    source_names = sorted(
        totals, key=lambda source: (0 if source == "USPTO" else 1 if source == "ORD-mined" else 2, source)
    )
    selected = []
    if limit and source_names:
        # Round-robin allocation keeps each source visible even when one source
        # dominates the ranking. Rows are fetched only up to the requested cap.
        allocation = {source: 0 for source in source_names}
        while sum(allocation.values()) < limit:
            progressed = False
            for source in source_names:
                if allocation[source] < totals[source] and sum(allocation.values()) < limit:
                    allocation[source] += 1
                    progressed = True
            if not progressed:
                break
        for source in source_names:
            take = allocation[source]
            if not take:
                continue
            rows = con.execute(
                "SELECT DISTINCT r.id AS rxn_id,r.reactants,r.reagents,r.products,r.year,"
                "r.patent,r.yield,r.source "
                f"{base} AND r.source=? ORDER BY r.n_mols ASC,r.year DESC,r.id LIMIT ?",
                params + [source, take],
            ).fetchall()
            selected.extend(dict(row) for row in rows)
    if selected:
        rxn_ids = sorted({row["rxn_id"] for row in selected})
        placeholders = ",".join("?" for _ in rxn_ids)
        table_names = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('ord_reaction_records','ord_reaction_participants')"
        )}
        if "ord_reaction_records" in table_names:
            ord_rows = con.execute(
                "SELECT id,rxn_id,source_record_id,dataset_id,source_doi,is_mined,identity_status "
                f"FROM ord_reaction_records WHERE rxn_id IN ({placeholders}) ORDER BY id", rxn_ids,
            ).fetchall()
        else:
            ord_rows = []
        ord_by_rxn = {}
        for ord_row in ord_rows:
            ord_by_rxn.setdefault(ord_row["rxn_id"], []).append(dict(ord_row))
        role_rows = con.execute(
            f"SELECT rxn_id,smiles,role FROM rxn_molecules WHERE rxn_id IN ({placeholders}) "
            "AND role IN ('R','G')", rxn_ids,
        ).fetchall()
        role_map = {}
        for role_row in role_rows:
            role_map.setdefault(role_row["rxn_id"], {}).setdefault(role_row["smiles"], set()).add(
                role_row["role"]
            )
        ord_ids = [row["id"] for rows in ord_by_rxn.values() for row in rows]
        participant_map = {}
        if ord_ids and "ord_reaction_participants" in table_names:
            ord_placeholders = ",".join("?" for _ in ord_ids)
            participant_rows = con.execute(
                "SELECT ord_record_id,standardized_smiles,source_role FROM ord_reaction_participants "
                f"WHERE ord_record_id IN ({ord_placeholders}) AND side='input'", ord_ids,
            ).fetchall()
            for participant in participant_rows:
                participant_map.setdefault(participant["ord_record_id"], []).append(dict(participant))
        for item in selected:
            ord_records = ord_by_rxn.get(item["rxn_id"], [])
            matching_ord = ord_records[0] if ord_records else {}
            for field in ("source_record_id", "dataset_id", "source_doi", "is_mined", "identity_status"):
                item[field] = matching_ord.get(field)
            roles = {0: set(), 1: set()}
            if item["source"] == "ORD-mined" and matching_ord:
                for participant in participant_map.get(matching_ord["id"], []):
                    participant_fragments = key_fragments(participant["standardized_smiles"])
                    for index, fragments in enumerate(input_fragments):
                        if set(participant_fragments) & set(fragments):
                            roles[index].add((participant["source_role"] or "unknown").lower())
            else:
                role_by_smiles = role_map.get(item["rxn_id"], {})
                for index, fragments in enumerate(input_fragments):
                    roles[index].update(
                        "reactant" if role == "R" else "reagent"
                        for fragment in fragments for role in role_by_smiles.get(fragment, ())
                    )
            item["input_roles"] = [sorted(roles[0]), sorted(roles[1])]
            item["evidence_relation"] = _reaction_evidence_relation(roles)
    con.close()
    return selected, total


def _reaction_evidence_relation(roles):
    first, second = roles[0], roles[1]
    if first & {"reactant", "reactants"} and second & {"reactant", "reactants"}:
        return "reactant-reactant"
    if ((first & {"reactant", "reactants"} and second & {"reagent", "reagents"}) or
            (second & {"reactant", "reactants"} and first & {"reagent", "reagents"})):
        return "reactant-reagent"
    if first and second:
        return "other-participant-roles"
    return "role-not-resolved"


def clear_saved_lookups():
    ensure_extra_tables()
    con = connect()
    for t in ("name_index", "chemical_cache", "product_cache"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
