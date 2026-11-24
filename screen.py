from lookup import lookup_chemical
from compatibility import check_pair
from severity import adjust_severity


def ask_text(prompt, default):
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw if raw else default


def ask_float(prompt, default):
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            print("Please type a number.")


def ask_chemical(label):
    print(f"\n--- {label} ---")
    name = input("Chemical name: ").strip()
    info = lookup_chemical(name)
    if not info:
        print("Not found on PubChem.")
        return None
    return {
        "name": name,
        "cas": info["cas"],
        "quantity": ask_float("Quantity (number)", 100),
        "unit": ask_text("Unit (mL, L, g, kg)", "mL"),
        "concentration": ask_float("Concentration in %", 100),
        "state": ask_text("Physical state (liquid/solid/gas/powder)", "liquid").lower(),
    }


if __name__ == "__main__":
    a = ask_chemical("Chemical 1")
    b = ask_chemical("Chemical 2") if a else None

    if a and b:
        temp = ask_float("\nTemperature in C", 25)
        base_level, message = check_pair(a["cas"], b["cas"])
        final_level, notes = adjust_severity(base_level, [a, b], temp)

        print("\n===== HAZARD REPORT =====")
        print(f"Base result:   {base_level}")
        print(f"Final result:  {final_level}")
        print(f"Reaction:      {message}")
        print("\nCondition notes:")
        for n in notes:
            print("  -", n)
        if not notes:
            print("  (none)")
        print("\nThis is a screening aid. Always check official safety data sheets.")