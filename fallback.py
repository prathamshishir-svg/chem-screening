import re


def _sentences(texts):
    out = []
    for t in texts:
        for s in re.split(r"(?<=[.;])\s+", t):
            s = s.strip()
            if s:
                out.append(s)
    return out


def _terms(name):
    """Words to look for when another chemical is mentioned."""
    name = name.lower().strip()
    terms = {name}
    words = [w for w in re.split(r"[\s,\-]+", name) if len(w) >= 5]
    if words:
        terms.add(words[-1])  # e.g. 'hypochlorite' from 'sodium hypochlorite'
    return terms


def find_mentions(source_react, other_name):
    """Sentences in one chemical's reactivity text that mention the other chemical."""
    terms = _terms(other_name)
    found = []
    for heading, texts in source_react.items():
        if heading == "Reactive Group":
            continue
        for s in _sentences(texts):
            low = s.lower()
            if any(t in low for t in terms) and s not in found:
                found.append(s)
    return found[:5]


def precautions(hazard_statements):
    """General precautions from GHS hazard codes like 'H302: Harmful if swallowed'."""
    codes = set()
    for h in hazard_statements:
        m = re.match(r"(H\d{3})", h.strip())
        if m:
            codes.add(m.group(1))

    notes = []
    if codes & {"H200", "H201", "H202", "H203", "H204", "H205", "H240", "H241", "H242"}:
        notes.append("Explosive or self-reacting hazard. Keep away from heat, shock and friction.")
    if codes & {"H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228"}:
        notes.append("Flammable. Keep away from ignition sources.")
    if codes & {"H270", "H271", "H272"}:
        notes.append("Oxidizer. May cause or intensify fire; keep away from combustibles.")
    if codes & {"H260", "H261"}:
        notes.append("Reacts with water to release flammable gas.")
    if codes & {"H280", "H281"}:
        notes.append("Contains gas under pressure.")
    if codes & {"H290"}:
        notes.append("May corrode metals.")
    if codes & {"H300", "H310", "H330", "H301", "H311", "H331"}:
        notes.append("Toxic. Avoid inhalation, skin contact and swallowing.")
    if codes & {"H314", "H318"}:
        notes.append("Corrosive. Causes severe skin burns and eye damage.")
    if codes & {"H334", "H335", "H336"}:
        notes.append("Respiratory hazard. Use only with good ventilation.")
    if codes & {"H340", "H350", "H360"}:
        notes.append("Possible long-term health hazard (cancer, genetic or reproductive).")
    if codes & {"H400", "H410", "H411"}:
        notes.append("Harmful to aquatic life. Do not release to drains.")
    return notes