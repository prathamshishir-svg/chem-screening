from pint import UnitRegistry

ureg = UnitRegistry()
SCALE = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
LITER_DIM = ureg.Quantity(1, "liter").dimensionality
KG_DIM = ureg.Quantity(1, "kilogram").dimensionality


def to_base(quantity, unit):
    """Convert to liters or kilograms. Returns (amount, 'L' or 'kg') or (None, None)."""
    try:
        q = ureg.Quantity(quantity, unit)
        if q.dimensionality == LITER_DIM:
            return q.to("liter").magnitude, "L"
        if q.dimensionality == KG_DIM:
            return q.to("kilogram").magnitude, "kg"
    except Exception:
        pass
    return None, None


def adjust_severity(level, chemicals, temp_c):
    """chemicals: list of dicts with name, quantity, unit, concentration, state."""
    notes = []
    steps_up = 0

    for c in chemicals:
        name = c["name"]

        amount, base_unit = to_base(c["quantity"], c["unit"])
        if amount is None:
            notes.append(f"{name}: unit '{c['unit']}' not understood, quantity ignored.")
        elif amount >= 5:
            steps_up += 1
            notes.append(f"{name}: large quantity ({amount:g} {base_unit}) raises severity.")

        conc = c["concentration"]
        if conc >= 30:
            steps_up += 1
            notes.append(f"{name}: high concentration ({conc:g}%) raises severity.")
        elif 0 < conc <= 5:
            notes.append(f"{name}: dilute ({conc:g}%). Hazard may be reduced but is NOT removed.")

        if c["state"] in ("gas", "powder"):
            notes.append(f"{name}: {c['state']} mixes and spreads quickly. Take extra care.")

    if temp_c >= 40:
        steps_up += 1
        notes.append(f"Temperature {temp_c:g} C speeds up reactions and raises severity.")

    if level not in SCALE:
        notes.append(f"Conditions not applied because the base result is {level}.")
        return level, notes

    steps_up = min(steps_up, 2)
    new_index = min(SCALE.index(level) + steps_up, len(SCALE) - 1)
    return SCALE[new_index], notes