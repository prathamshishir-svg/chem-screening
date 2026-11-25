import re
from datetime import datetime, timezone

import timing

import pubchempy as pcp
import requests
from rdkit import Chem
from lookup import get_ghs_hazards

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def get_smiles(cid):
    """Return a source-retrieved PubChem SMILES only when RDKit can parse it."""
    started = timing.start_timer()
    try:
        return _get_smiles(cid)
    finally:
        timing.log_duration("PubChem structure lookup", started)


def get_structure(cid, *, timeout=15, http_get=None):
    """Fetch a structure and retain its source/validation metadata in the result.

    PubChem's property response is authoritative for the returned structure. A
    structure that RDKit cannot parse is returned as unavailable for reaction
    matching, with the source value retained for diagnostics.
    """
    started = timing.start_timer()
    try:
        return _get_structure(cid, timeout=timeout, http_get=http_get)
    finally:
        timing.log_duration("PubChem structure lookup", started)


def _get_structure(cid, *, timeout=15, http_get=None):
    result = {
        "smiles": None, "source_smiles": None, "source": "PubChem PUG REST",
        "identifier_type": "PubChem CID", "identifier": str(cid),
        "source_property": None, "retrieved_at": None,
        "validation": "unavailable", "error": None,
    }
    if cid in (None, ""):
        result["error"] = "missing_pubchem_cid"
        return result
    get = http_get or requests.get
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/CanonicalSMILES,IsomericSMILES/JSON"
    try:
        response = get(url, timeout=timeout)
        if response.status_code != 200:
            result["error"] = f"pubchem_http_{response.status_code}"
            return result
        payload = response.json()
    except requests.Timeout:
        result["error"] = "pubchem_timeout"
        return result
    except requests.RequestException:
        result["error"] = "pubchem_network_error"
        return result
    except (ValueError, TypeError, AttributeError):
        result["error"] = "malformed_pubchem_response"
        return result

    try:
        rows = payload["PropertyTable"]["Properties"]
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ValueError("missing property row")
        row = rows[0]
    except (KeyError, TypeError, ValueError):
        result["error"] = "missing_pubchem_structure"
        return result

    property_name = next((key for key in
                          ("IsomericSMILES", "CanonicalSMILES", "SMILES", "ConnectivitySMILES")
                          if isinstance(row.get(key), str) and row[key].strip()), None)
    value = row[property_name] if property_name else None
    if value is None:
        result["error"] = "missing_pubchem_structure"
        return result
    result["source_smiles"] = value
    result["source_property"] = property_name
    result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    try:
        molecule = Chem.MolFromSmiles(value)
    except Exception:
        molecule = None
    if molecule is None:
        result["validation"] = "rdkit_rejected_source_structure"
        result["error"] = "rdkit_structure_validation_failed"
        return result
    result["smiles"] = value
    result["validation"] = "rdkit_valid"
    return result


def _get_smiles(cid):
    """Compatibility helper retained for existing callers and timing tests."""
    return _get_structure(cid)["smiles"]


def describe_fragment(frag):
    started = timing.start_timer()
    try:
        return _describe_fragment(frag)
    finally:
        timing.log_duration("PubChem product lookup (inclusive of GHS)", started)


def _describe_fragment(frag):
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
