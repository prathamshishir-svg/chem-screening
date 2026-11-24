"""Automatic checks for reactions proposed by the AI.

A reaction passes only if: its equation balances atom-for-atom, every product name is a real
compound in the public database, and the product's formula agrees with the AI's formula.
Passing these checks does NOT prove the reaction happens; it only filters out obvious errors."""
import re
from fractions import Fraction

from ai_explain import formula_counts, formulas_match

ARROW = re.compile(r"\s*(?:<->|<=>|\u21cc|\u21c4|-->|->|\u2192|\u27f6|=>|=)\s*")
STATE = re.compile(r"\((?:aq|g|l|s)\)", re.I)
IGNORED_TERMS = {"heat", "energy", "light", "hv", "\u03b4", "heating", "warm", "catalyst"}


def _side_counts(side):
    """Total element counts on one side of an equation, or None if it cannot be read."""
    total = {}
    for term in re.split(r"\s+\+\s+", side.strip()):
        term = STATE.sub("", term).strip()
        if not term:
            return None
        if term.lower() in IGNORED_TERMS:
            continue
        coeff, formula = Fraction(1), term
        m = re.match(r"^(\d+(?:/\d+)?)\s*(.+)$", term)
        if m and re.match(r"[A-Z(]", m.group(2).strip()):
            coeff, formula = Fraction(m.group(1)), m.group(2).strip()
        counts = formula_counts(formula)
        if counts is None:
            return None
        for el, n in counts.items():
            total[el] = total.get(el, 0) + coeff * n
    return total


def equation_balance(equation):
    """True if every step balances, False if any step does not, None if it cannot be checked."""
    if not equation or not equation.strip():
        return None
    results = []
    for step in [s for s in re.split(r"[;\n]", equation) if s.strip()]:
        parts = ARROW.split(step.strip(), maxsplit=1)
        if len(parts) != 2:
            results.append(None)
            continue
        left, right = _side_counts(parts[0]), _side_counts(parts[1])
        if left is None or right is None:
            results.append(None)
        else:
            results.append(left == right)
    if False in results:
        return False
    if not results or None in results:
        return None
    return True


def verify_answer(answer, product_lookup):
    """Check every reaction. product_lookup(name) must return a dict with cid, formula, cas."""
    out = []
    for rx in answer["reactions"]:
        products = []
        for p in rx["products"][:4]:
            d = product_lookup(p["name"])
            matched = d.get("cid") is not None
            products.append({
                "name": p["name"], "matched": matched,
                "formula_ok": formulas_match(p["formula"], d.get("formula")) if matched else None,
                "cas": d.get("cas"), "db_formula": d.get("formula"),
            })
        out.append({"balanced": equation_balance(rx["equation"]), "products": products})
    return out


def decide_status(answer, checks):
    """One (status, reasons) per reaction: 'ai_checked' if everything passed, else 'needs_review'."""
    decisions = []
    for i, (rx, res) in enumerate(zip(answer["reactions"], checks), start=1):
        reasons = []
        if res["balanced"] is False:
            reasons.append("equation is not balanced")
        elif res["balanced"] is None:
            reasons.append("equation could not be checked")
        if not res["products"]:
            reasons.append("no products listed")
        for p in res["products"]:
            if not p["matched"]:
                reasons.append(f"product '{p['name']}' not found in the public database")
            elif p["formula_ok"] is False:
                reasons.append(f"formula of '{p['name']}' differs from the database record")
        if answer.get("confidence") == "low":
            reasons.append("the AI's own confidence is low")
        decisions.append(("ai_checked" if not reasons else "needs_review", reasons))
    return decisions
