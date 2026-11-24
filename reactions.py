# Reactions are keyed by chemical FAMILY pair (same families as compatibility.py).
# Each product: name, lookup (PubChem search name or None), state, note.

def P(name, lookup, state, note):
    return {"name": name, "lookup": lookup, "state": state, "note": note}


REACTIONS = {
    frozenset(["hypochlorite", "ammonia"]): [
        {
            "title": "Formation of monochloramine",
            "when": "Any mixing; fastest in neutral to alkaline solution.",
            "equation": "NaOCl + NH3 -> NH2Cl + NaOH",
            "severity": "HIGH",
            "products": [
                P("Monochloramine", "chloramine", "gas/vapor",
                  "Toxic irritant gas. Damages eyes, throat and lungs."),
                P("Sodium hydroxide", "sodium hydroxide", "aqueous",
                  "Corrosive. Causes skin burns and eye damage."),
            ],
        },
        {
            "title": "Further chlorination (excess hypochlorite)",
            "when": "Hypochlorite in excess, especially at lower pH.",
            "equation": "NH2Cl + NaOCl -> NHCl2 + NaOH ; NHCl2 + NaOCl -> NCl3 + NaOH",
            "severity": "CRITICAL",
            "products": [
                P("Dichloramine", None, "gas/vapor",
                  "Toxic, strongly irritating to the respiratory tract."),
                P("Nitrogen trichloride", "nitrogen trichloride", "oily liquid/vapor",
                  "Toxic and can explode, including from light, heat or shock."),
            ],
        },
        {
            "title": "Hydrazine formation (excess ammonia)",
            "when": "Ammonia in excess and alkaline conditions.",
            "equation": "NH2Cl + NH3 + NaOH -> N2H4 + NaCl + H2O",
            "severity": "HIGH",
            "products": [
                P("Hydrazine", "hydrazine", "liquid/vapor",
                  "Toxic, corrosive, flammable and a suspected carcinogen."),
            ],
        },
    ],
    frozenset(["hypochlorite", "acid"]): [
        {
            "title": "Release of hypochlorous acid",
            "when": "Mild acids (e.g. vinegar); the acid lowers the pH.",
            "equation": "NaOCl + H-A -> HOCl + Na-A",
            "severity": "MEDIUM",
            "products": [
                P("Hypochlorous acid", "hypochlorous acid", "aqueous",
                  "Strong oxidizer and irritant. Decomposes, releasing chlorine."),
            ],
        },
        {
            "title": "Release of chlorine gas",
            "when": "Stronger acids or lower pH (e.g. hydrochloric acid).",
            "equation": "NaOCl + 2 HCl -> Cl2 + NaCl + H2O",
            "severity": "HIGH",
            "products": [
                P("Chlorine", "chlorine", "gas",
                  "Highly toxic gas. Severe lung damage; oxidizer."),
            ],
        },
    ],
    frozenset(["hypochlorite", "organic_flammable"]): [
        {
            "title": "Oxidation of the organic liquid",
            "when": "Alcohols such as ethanol; exothermic.",
            "equation": "CH3CH2OH + NaOCl -> CH3CHO + NaCl + H2O",
            "severity": "HIGH",
            "products": [
                P("Acetaldehyde", "acetaldehyde", "liquid/vapor",
                  "Extremely flammable, irritant, suspected carcinogen."),
            ],
        },
        {
            "title": "Haloform reaction",
            "when": "Acetone (and, more slowly, ethanol) with excess hypochlorite.",
            "equation": "3 NaOCl + CH3COCH3 -> CHCl3 + CH3COONa + 2 NaOH",
            "severity": "HIGH",
            "products": [
                P("Chloroform", "chloroform", "liquid/vapor",
                  "Toxic, harms liver and kidneys, suspected carcinogen."),
                P("Sodium hydroxide", "sodium hydroxide", "aqueous",
                  "Corrosive. Causes skin burns and eye damage."),
            ],
        },
    ],
    frozenset(["hypochlorite", "oxidizer"]): [
        {
            "title": "Oxygen release",
            "when": "Any mixing with hydrogen peroxide.",
            "equation": "NaOCl + H2O2 -> NaCl + H2O + O2",
            "severity": "MEDIUM",
            "products": [
                P("Oxygen", None, "gas",
                  "Not toxic, but the rapid release causes foaming, heat and "
                  "splashing, and it feeds fires."),
                P("Sodium chloride", None, "aqueous", "Low hazard."),
            ],
        },
    ],
    frozenset(["oxidizer", "organic_flammable"]): [
        {
            "title": "Rapid oxidation of the organic liquid",
            "when": "Concentrated oxidizer; heat or contamination makes it worse.",
            "equation": "Organic + oxidizer -> CO2 + H2O + CO + heat",
            "severity": "HIGH",
            "products": [
                P("Carbon monoxide", "carbon monoxide", "gas",
                  "Toxic gas that prevents blood carrying oxygen."),
                P("Carbon dioxide", None, "gas", "Asphyxiant in enclosed spaces."),
            ],
        },
        {
            "title": "Unstable peroxide-type compounds",
            "when": "Strong oxidizer with certain organics, often with acid present.",
            "equation": "Organic + H2O2 -> organic peroxides (varies)",
            "severity": "CRITICAL",
            "products": [
                P("Organic peroxides", None, "solid/liquid",
                  "Can be shock, heat or friction sensitive and explode."),
            ],
        },
    ],
    frozenset(["oxidizing_acid", "organic_flammable"]): [
        {
            "title": "Oxidation and nitration",
            "when": "Concentrated nitric acid; violent, often with a delay.",
            "equation": "Organic + HNO3 -> oxidized/nitrated organics + NO2 + H2O",
            "severity": "CRITICAL",
            "products": [
                P("Nitrogen dioxide", "nitrogen dioxide", "gas",
                  "Brown, highly toxic gas. Causes delayed lung damage."),
                P("Nitrated organic compounds", None, "varies",
                  "Some are unstable or explosive."),
            ],
        },
    ],
    frozenset(["acid", "cyanide"]): [
        {
            "title": "Release of hydrogen cyanide",
            "when": "Any acid contacting a cyanide salt; faster with stronger acid.",
            "equation": "NaCN + HCl -> HCN + NaCl",
            "severity": "CRITICAL",
            "products": [
                P("Hydrogen cyanide", "hydrogen cyanide", "gas",
                  "Extremely toxic and flammable. Can be fatal in minutes."),
            ],
        },
    ],
    frozenset(["oxidizing_acid", "cyanide"]): [
        {
            "title": "Hydrogen cyanide and nitrogen oxides",
            "when": "Nitric acid with a cyanide salt; violent.",
            "equation": "NaCN + HNO3 -> HCN + NaNO3 (plus NOx from side reactions)",
            "severity": "CRITICAL",
            "products": [
                P("Hydrogen cyanide", "hydrogen cyanide", "gas",
                  "Extremely toxic and flammable. Can be fatal in minutes."),
                P("Nitrogen dioxide", "nitrogen dioxide", "gas",
                  "Brown, highly toxic gas. Causes delayed lung damage."),
            ],
        },
    ],
    frozenset(["acid", "strong_base"]): [
        {
            "title": "Neutralization",
            "when": "Any mixing; heat grows with concentration and quantity.",
            "equation": "H-A + NaOH -> Na-A + H2O + heat",
            "severity": "MEDIUM",
            "products": [
                P("A salt (depends on the acid)", None, "aqueous",
                  "Usually low hazard, but check the specific salt."),
                P("Water (hot)", None, "liquid",
                  "Mixture can boil and spatter corrosive liquid."),
            ],
        },
    ],
    frozenset(["water_reactive", "water"]): [
        {
            "title": "Reaction with water",
            "when": "Any contact with water or moisture.",
            "equation": "2 Na + 2 H2O -> 2 NaOH + H2 + heat",
            "severity": "CRITICAL",
            "products": [
                P("Hydrogen", "hydrogen", "gas",
                  "Extremely flammable; can ignite from the heat released."),
                P("Sodium hydroxide", "sodium hydroxide", "aqueous",
                  "Corrosive. Causes skin burns and eye damage."),
            ],
        },
    ],
    frozenset(["water_reactive", "acid"]): [
        {
            "title": "Violent reaction with acid",
            "when": "Any contact; more violent than with water.",
            "equation": "2 Na + 2 H-A -> 2 Na-A + H2 + heat",
            "severity": "CRITICAL",
            "products": [
                P("Hydrogen", "hydrogen", "gas",
                  "Extremely flammable; likely to ignite."),
            ],
        },
    ],
}


def get_reactions(family_a, family_b):
    """Returns the list of reactions for a family pair (may be empty)."""
    return REACTIONS.get(frozenset([family_a, family_b]), [])