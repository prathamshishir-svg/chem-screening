# Each rule: (group A prefix, group B prefix, level, message)
# Matching is case-insensitive and checks whether a chemical's group name STARTS with the prefix.
GROUP_RULES = [
    ("acids,", "cyanides", "CRITICAL", "Releases hydrogen cyanide, a highly toxic gas."),
    ("acids, strong", "sulfides", "HIGH", "Releases toxic, flammable hydrogen sulfide gas."),
    ("acids, strong", "bases, strong", "MEDIUM", "Strong heat release; splashing and boiling risk."),
    ("acids, strong", "bases, weak", "MEDIUM", "Heat release on neutralization."),
    ("acids, strong", "amines", "MEDIUM", "Heat release on neutralization."),
    ("acids, strong", "reducing agents, strong", "HIGH", "Violent reaction; may release flammable hydrogen."),
    ("acids, strong oxidizing", "alcohols", "CRITICAL", "Violent reaction; fire or explosion risk."),
    ("acids, strong oxidizing", "hydrocarbons", "CRITICAL", "Violent reaction; fire or explosion risk."),
    ("oxidizing agents, strong", "reducing agents, strong", "CRITICAL", "Fire or explosion risk."),
    ("oxidizing agents, strong", "alcohols", "HIGH", "Fire or explosion risk."),
    ("oxidizing agents, strong", "ketones", "HIGH", "Fire or explosion risk."),
    ("oxidizing agents, strong", "aldehydes", "HIGH", "Fire or explosion risk."),
    ("oxidizing agents, strong", "ethers", "HIGH", "Fire or explosion risk; peroxide hazards."),
    ("oxidizing agents, strong", "hydrocarbons", "HIGH", "Fire or explosion risk."),
    ("oxidizing agents, strong", "combustible and flammable", "HIGH", "Fire or explosion risk."),
    ("oxidizing agents, strong", "amines", "HIGH", "Violent reaction; fire risk."),
    ("oxidizing agents, strong", "cyanides", "HIGH", "Violent reaction; toxic gas risk."),
    ("water reactive", "water and aqueous", "HIGH", "Reacts with water; heat, fire or toxic gas risk."),
    ("metals, alkali", "water and aqueous", "CRITICAL", "Releases flammable hydrogen and heat; fire risk."),
    ("metals, alkali", "acids,", "CRITICAL", "Violent reaction; flammable hydrogen released."),
]

ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _has(groups, prefix):
    return any(g.strip().lower().startswith(prefix) for g in groups)


def check_groups(groups_a, groups_b):
    """Returns (level, message). Picks the most severe matching rule."""
    best = None
    for pa, pb, level, msg in GROUP_RULES:
        hit = (_has(groups_a, pa) and _has(groups_b, pb)) or \
              (_has(groups_a, pb) and _has(groups_b, pa))
        if hit and (best is None or ORDER.index(level) > ORDER.index(best[0])):
            best = (level, msg)
    if best:
        return best
    return ("UNKNOWN", "No reactive-group rule matched. Treat as potentially unsafe.")