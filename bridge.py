import re

import pubchempy as pcp
from lookup import get_ghs_hazards

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def get_smiles(cid):
    """Get a SMILES structure string for a PubChem compound ID."""
    c = None
    for _ in range(2):
        try:
            c = pcp.Compound.from_cid(cid)
            break
        except Exception:
            continue
    if c is None:
        return None
    for attr in ("smiles", "isomeric_smiles", "connectivity_smiles", "canonical_smiles"):
        try:
            value = getattr(c, attr, None)
        except Exception:
            value = None
        if value:
            return value
    return None


def describe_fragment(frag):
    """Name, formula, CAS and hazard statements for one product (given as SMILES)."""
    empty = {"smiles": frag, "name": frag, "formula": None, "cid": None,
             "cas": None, "hazards": []}
    try:
        results = pcp.get_compounds(frag, "smiles")
    except Exception:
        return empty
    if not results or results[0].cid is None:
        return empty

    c = results[0]
    name, cas = None, None
    try:
        syns = c.synonyms or []
        if syns:
            name = syns[0]
        for syn in syns:
            if CAS_RE.match(syn):
                cas = syn
                break
    except Exception:
        pass
    name = name or c.iupac_name or frag

    return {
        "smiles": frag,
        "name": name,
        "formula": c.molecular_formula,
        "cid": c.cid,
        "cas": cas,
        "hazards": get_ghs_hazards(c.cid),
    }
