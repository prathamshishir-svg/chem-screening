"""Reaction predictor. Its reaction patterns are rows in the database,
so adding a reaction type never needs a code change."""
import os

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit import RDLogger

from db import load_templates, DB_FILE

RDLogger.DisableLog("rdApp.*")

_cache = {"mtime": None, "templates": []}


def _templates():
    """Reload and compile templates whenever the database file changes."""
    mtime = os.path.getmtime(DB_FILE)
    if _cache["mtime"] != mtime:
        compiled = []
        for t in load_templates():
            try:
                t["rxn"] = AllChem.ReactionFromSmarts(t["smarts"])
                compiled.append(t)
            except Exception:
                continue  # skip a malformed pattern instead of crashing
        _cache["templates"] = compiled
        _cache["mtime"] = mtime
    return _cache["templates"]


def _is_oxidizer(groups):
    return any(g.strip().lower().startswith("oxidizing agents") for g in groups)


def _mol(smiles):
    return Chem.MolFromSmiles(smiles) if smiles else None


def _run(rxn, reactants):
    found = set()
    try:
        outcomes = rxn.RunReactants(reactants)
    except Exception:
        return []
    for outcome in outcomes:
        parts, ok = [], True
        for p in outcome:
            try:
                Chem.SanitizeMol(p)
                parts.append(Chem.MolToSmiles(p))
            except Exception:
                ok = False
                break
        if ok and parts:
            found.add(".".join(sorted(parts)))
    return sorted(found)


def predict(smiles_a, smiles_b, groups_a=None, groups_b=None):
    groups_a, groups_b = groups_a or [], groups_b or []
    a, b = _mol(smiles_a), _mol(smiles_b)
    if a is None or b is None:
        return []

    results = []
    for t in _templates():
        rxn = t["rxn"]
        n = rxn.GetNumReactantTemplates()

        if t["needs_oxidizer"]:
            outs = []
            if _is_oxidizer(groups_b):
                outs += _run(rxn, (a,))
            if _is_oxidizer(groups_a):
                outs += _run(rxn, (b,))
        elif n == 1:
            outs = _run(rxn, (a,)) + _run(rxn, (b,))
        else:
            outs = _run(rxn, (a, b)) + _run(rxn, (b, a))

        outs = sorted(set(outs))
        if outs:
            results.append({"name": t["name"], "products": outs,
                            "byproduct": t["byproduct"], "conditions": t["conditions"],
                            "severity": t["severity"]})
    return results
