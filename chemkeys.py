"""Shared helpers so that the USPTO importer and the app search turn molecules
into exactly the same text keys."""
from rdkit import Chem
from rdkit import RDLogger
import re

RDLogger.DisableLog("rdApp.*")
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


def split_source_references(value):
    """Separate valid DOI-shaped source citations from opaque source identifiers."""
    references = [item.strip() for item in (value or "").split(";") if item.strip()]
    dois = [item for item in references if _DOI_RE.fullmatch(item)]
    return dois, [item for item in references if item not in dois]


def canon_fragment(smiles):
    """Canonical, atom-map-free, stereo-free SMILES for ONE fragment. None if invalid."""
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(mol, isomericSmiles=False)


def _is_single_ion(smiles):
    """Single charged atoms such as [Na+] or [Cl-] (counter-ions)."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() != 1:
        return False
    atom = mol.GetAtomWithIdx(0)
    # hydroxide [OH-] and hydronium carry hydrogens, so they are real species, not counter-ions
    return atom.GetFormalCharge() != 0 and atom.GetTotalNumHs() == 0


def key_fragments(smiles):
    """The fragments that identify a chemical when searching reactions.
    Counter-ions are dropped (sodium hydroxide -> hydroxide), unless that leaves nothing."""
    if not smiles:
        return []
    frags = []
    for part in smiles.split("."):
        c = canon_fragment(part)
        if c and c not in frags:
            frags.append(c)
    kept = [f for f in frags if not _is_single_ion(f)]
    return kept or frags


def parse_reaction_smiles(text):
    """Split 'reactants>reagents>products' into three lists of canonical fragments.
    Returns None if the line is not a usable reaction.
    Reactant or product fragments that cannot be read make the whole reaction unusable;
    unreadable reagents are simply skipped."""
    token = text.strip().split(" ")[0]          # drop any trailing CXSMILES extension
    parts = token.split(">")
    if len(parts) != 3:
        return None

    def convert(block, must_all_parse):
        out = []
        for frag in [p for p in block.split(".") if p]:
            c = canon_fragment(frag)
            if c is None:
                if must_all_parse:
                    return None
                continue
            if c not in out:
                out.append(c)
        return out

    reactants = convert(parts[0], True)
    reagents = convert(parts[1], False)
    products = convert(parts[2], True)
    if not reactants or not products:
        return None
    return reactants, reagents, products
