"""Checks that every code file has the functions the app needs.
Run:  python check_files.py"""
import importlib
import os

REQUIRED = {
    "db": ["connect", "db_ready", "get_chemical", "get_product", "check_pair", "check_groups",
           "family_of", "get_reactions", "load_templates", "get_transport", "find_recorded",
           "clear_saved_lookups"],
    "engine": ["predict"],
    "severity": ["adjust_severity"],
    "fallback": ["find_mentions", "precautions"],
    "aliases": ["resolve"],
    "bridge": ["get_smiles", "describe_fragment"],
    "chemkeys": ["canon_fragment", "key_fragments", "parse_reaction_smiles"],
    "lookup": ["lookup_chemical", "get_ghs_hazards"],
    "reactivity": ["get_reactivity"],
    "ai_explain": ["get_ai_analysis", "ai_ready", "formulas_match", "clear_ai_answers"],
    "ai_verify": ["equation_balance", "verify_answer", "decide_status"],
    "pair_store": ["key_for", "save_pair_answer", "get_pair_reactions", "pair_has_rows", "worst_level"],
    "pair_fill": ["fill_pair"],
}

problems = 0
for module, names in REQUIRED.items():
    path = module + ".py"
    if not os.path.exists(path):
        print(f"MISSING FILE   {path}")
        problems += 1
        continue
    lines = sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
    try:
        mod = importlib.import_module(module)
    except Exception as e:
        print(f"CANNOT LOAD    {path}  ({type(e).__name__}: {e})")
        problems += 1
        continue
    missing = [n for n in names if not hasattr(mod, n)]
    if missing:
        print(f"INCOMPLETE     {path} ({lines} lines) is missing: {', '.join(missing)}")
        problems += 1
    else:
        print(f"ok             {path} ({lines} lines)")

print()
print("All files are complete." if not problems else
      f"{problems} file(s) need fixing: paste the full code for them again, save, and re-run.")
