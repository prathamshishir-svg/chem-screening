from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

# Each template:
#   name, smarts (A.B>>product), byproduct text, conditions, severity,
#   needs_oxidizer: True means one chemical must be a strong oxidizer (by reactive group)
TEMPLATES = [
    {
        "name": "Fischer esterification",
        "smarts": "[CX3:1](=[O:2])[OX2H1:3].[CX4:4][OX2H1:5]>>[C:1](=[O:2])[O:5][C:4]",
        "byproduct": "water",
        "conditions": "Acid catalyst (e.g. sulfuric acid), heat; reversible.",
        "severity": "LOW",
    },
    {
        "name": "Amide formation from acyl chloride",
        "smarts": "[NX3;H2,H1;!$(NC=O):1].[CX3:2](=[O:3])[Cl]>>[N:1][C:2]=[O:3]",
        "byproduct": "hydrogen chloride (corrosive gas)",
        "conditions": "Fast and exothermic, usually with a base to absorb HCl.",
        "severity": "MEDIUM",
    },
    {
        "name": "Ester formation from acyl chloride",
        "smarts": "[OX2H1:1][CX4:2].[CX3:3](=[O:4])[Cl]>>[C:3](=[O:4])[O:1][C:2]",
        "byproduct": "hydrogen chloride (corrosive gas)",
        "conditions": "Fast and exothermic; releases HCl.",
        "severity": "MEDIUM",
    },
    {
        "name": "Imine formation (condensation)",
        "smarts": "[CX3;!$(C(=O)[!#6]):1](=[O:2])[#6:5].[NX3;H2;!$(NC=O):3]>>[C:1](=[N:3])[#6:5]",
        "byproduct": "water",
        "conditions": "Mild acid catalysis, removal of water helps.",
        "severity": "LOW",
    },
    {
        "name": "Saponification (ester hydrolysis by hydroxide)",
        "smarts": "[CX3:1](=[O:2])[OX2:3][CX4:4].[OH-]>>[C:1](=[O:2])[O-].[C:4][O:3]",
        "byproduct": "none (an alcohol and a carboxylate salt form)",
        "conditions": "Aqueous hydroxide, often warmed; exothermic.",
        "severity": "MEDIUM",
    },
    {
        "name": "Bromine addition to an alkene",
        "smarts": "[CX3:1]=[CX3:2].[Br:3][Br:4]>>[C:1]([Br:3])[C:2][Br:4]",
        "byproduct": "none",
        "conditions": "Room temperature; bromine is toxic and corrosive.",
        "severity": "HIGH",
    },
    {
        "name": "Aromatic nitration",
        "smarts": "[cH1:1].[N+:2](=[O:3])([O-:4])[OX2H1:5]>>[c:1][N+:2](=[O:3])[O-:4]",
        "byproduct": "water",
        "conditions": "Concentrated nitric acid with sulfuric acid. Strongly exothermic; "
                      "runaway and explosion risk, nitro products can be explosive.",
        "severity": "CRITICAL",
    },
    {
        "name": "Oxidation of a primary alcohol to an aldehyde",
        "smarts": "[CH2X4:1][OX2H1:2]>>[CH1X3:1]=[O:2]",
        "byproduct": "reduced form of the oxidizer",
        "conditions": "Needs an oxidizing agent; exothermic. Can go further to the acid.",
        "severity": "HIGH",
        "needs_oxidizer": True,
    },
    {
        "name": "Oxidation of a secondary alcohol to a ketone",
        "smarts": "[CX4H1:1]([#6:5])([#6:6])[OX2H1:2]>>[C:1]([#6:5])([#6:6])=[O:2]",
        "byproduct": "reduced form of the oxidizer",
        "conditions": "Needs an oxidizing agent; exothermic.",
        "severity": "HIGH",
        "needs_oxidizer": True,
    },
    {
        "name": "Oxidation of an aldehyde to a carboxylic acid",
        "smarts": "[CX3H1:1](=[O:2])[#6:5]>>[C:1](=[O:2])([#6:5])[OH]",
        "byproduct": "reduced form of the oxidizer",
        "conditions": "Needs an oxidizing agent; exothermic.",
        "severity": "HIGH",
        "needs_oxidizer": True,
    },
]

for _t in TEMPLATES:
    _t["rxn"] = AllChem.ReactionFromSmarts(_t["smarts"])


def _is_oxidizer(groups):
    return any(g.strip().lower().startswith("oxidizing agents") for g in groups)


def _mol(smiles):
    m = Chem.MolFromSmiles(smiles) if smiles else None
    return m


def _run(rxn, reactants):
    """Run a reaction; return a sorted list of unique product SMILES strings."""
    found = set()
    try:
        outcomes = rxn.RunReactants(reactants)
    except Exception:
        return []
    for outcome in outcomes:
        parts = []
        ok = True
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
    """Predict reactions between two chemicals.
    Returns a list of dicts: name, products (SMILES list), byproduct, conditions, severity.
    """
    groups_a = groups_a or []
    groups_b = groups_b or []
    a, b = _mol(smiles_a), _mol(smiles_b)
    if a is None or b is None:
        return []

    results = []
    for t in TEMPLATES:
        rxn = t["rxn"]
        n = rxn.GetNumReactantTemplates()

        if t.get("needs_oxidizer"):
            # one chemical is the oxidizer (by reactive group), the other is the substrate
            pairs = []
            if _is_oxidizer(groups_b):
                pairs.append((a, "A"))
            if _is_oxidizer(groups_a):
                pairs.append((b, "B"))
            outs = []
            for substrate, _ in pairs:
                outs += _run(rxn, (substrate,))
        elif n == 1:
            outs = _run(rxn, (a,)) + _run(rxn, (b,))
        else:
            outs = _run(rxn, (a, b)) + _run(rxn, (b, a))

        outs = sorted(set(outs))
        if outs:
            results.append({
                "name": t["name"],
                "products": outs,
                "byproduct": t["byproduct"],
                "conditions": t["conditions"],
                "severity": t["severity"],
            })
    return results


if __name__ == "__main__":
    tests = [
        ("acetic acid + ethanol", "CC(=O)O", "CCO", [], []),
        ("ethanol + hydrogen peroxide", "CCO", "OO", [], ["Oxidizing Agents, Strong"]),
        ("isopropanol + hydrogen peroxide", "CC(C)O", "OO", [], ["Oxidizing Agents, Strong"]),
        ("ethyl acetate + sodium hydroxide", "CCOC(C)=O", "[OH-].[Na+]", [], []),
        ("benzene + nitric acid", "c1ccccc1", "[N+](=O)(O)[O-]", [], []),
        ("ethylene + bromine", "C=C", "BrBr", [], []),
        ("acetyl chloride + methylamine", "CC(=O)Cl", "CN", [], []),
        ("acetaldehyde + methylamine", "CC=O", "CN", [], []),
        ("2-butanol + hydrogen peroxide", "CCC(C)O", "OO", [], ["Oxidizing Agents, Strong"]),
        ("benzaldehyde + hydrogen peroxide", "O=Cc1ccccc1", "OO", [], ["Oxidizing Agents, Strong"]),
        ("propanal + aniline", "CCC=O", "Nc1ccccc1", [], []),
        ("toluene + nitric acid", "Cc1ccccc1", "[N+](=O)(O)[O-]", [], []),
        ("ethanol + sodium chloride (expect none)", "CCO", "[Na+].[Cl-]", [], []),
    ]
    for label, sa, sb, ga, gb in tests:
        res = predict(sa, sb, ga, gb)
        print(f"\n{label}")
        if not res:
            print("  (no template matched)")
        for r in res:
            print(f"  - {r['name']} [{r['severity']}] -> {r['products']}")