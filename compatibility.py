from lookup import lookup_chemical

# Step A: which "family" each chemical belongs to (keyed by CAS number)
GROUPS = {
    "7681-52-9": ("Sodium hypochlorite (bleach)", "hypochlorite"),
    "7664-41-7": ("Ammonia", "ammonia"),
    "64-19-7":   ("Acetic acid (vinegar)", "acid"),
    "7647-01-0": ("Hydrochloric acid", "acid"),
    "7664-93-9": ("Sulfuric acid", "acid"),
    "7697-37-2": ("Nitric acid", "oxidizing_acid"),
    "7722-84-1": ("Hydrogen peroxide", "oxidizer"),
    "143-33-9":  ("Sodium cyanide", "cyanide"),
    "64-17-5":   ("Ethanol", "organic_flammable"),
    "67-64-1":   ("Acetone", "organic_flammable"),
    "1310-73-2": ("Sodium hydroxide", "strong_base"),
    "7440-23-5": ("Sodium metal", "water_reactive"),
    "7732-18-5": ("Water", "water"),
    "7647-14-5": ("Sodium chloride (salt)", "inert"),
}

# Step B: which families are dangerous together
RULES = {
    frozenset(["hypochlorite", "ammonia"]): (
        "HIGH", "Produces toxic chloramine gas."),
    frozenset(["hypochlorite", "acid"]): (
        "HIGH", "Releases toxic chlorine gas."),
    frozenset(["hypochlorite", "organic_flammable"]): (
        "HIGH", "Heat and toxic by-products; fire risk."),
    frozenset(["hypochlorite", "oxidizer"]): (
        "MEDIUM", "Rapid oxygen release and heat."),
    frozenset(["oxidizer", "organic_flammable"]): (
        "HIGH", "Fire or explosion risk."),
    frozenset(["oxidizing_acid", "organic_flammable"]): (
        "CRITICAL", "Violent reaction, fire or explosion risk."),
    frozenset(["acid", "cyanide"]): (
        "CRITICAL", "Releases hydrogen cyanide, a highly toxic gas."),
    frozenset(["oxidizing_acid", "cyanide"]): (
        "CRITICAL", "Releases toxic gas; violent reaction."),
    frozenset(["acid", "strong_base"]): (
        "MEDIUM", "Strong heat release; splashing and boiling risk."),
    frozenset(["water_reactive", "water"]): (
        "CRITICAL", "Releases flammable hydrogen and heat; fire risk."),
    frozenset(["water_reactive", "acid"]): (
        "CRITICAL", "Violent reaction; flammable hydrogen released."),
}


def check_pair(cas_a, cas_b):
    a = GROUPS.get(cas_a)
    b = GROUPS.get(cas_b)

    if a is None or b is None:
        return ("UNKNOWN",
                "No data for one or both chemicals. Treat as potentially unsafe.")

    key = frozenset([a[1], b[1]])
    if key in RULES:
        return RULES[key]

    if "inert" in key:
        return ("NO KNOWN HAZARD",
                "No hazard found in our rules. This is not a guarantee.")

    return ("UNKNOWN",
            "No rule for this pair. Treat as potentially unsafe.")


if __name__ == "__main__":
    n1 = input("First chemical:  ")
    n2 = input("Second chemical: ")

    c1 = lookup_chemical(n1)
    c2 = lookup_chemical(n2)

    if not c1 or not c2:
        print("Could not find one of the chemicals on PubChem.")
    else:
        print(f"\n{n1} -> CAS {c1['cas']}")
        print(f"{n2} -> CAS {c2['cas']}")
        level, message = check_pair(c1["cas"], c2["cas"])
        print(f"\nRESULT: {level}")
        print(message)