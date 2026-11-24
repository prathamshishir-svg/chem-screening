import re
import time
import requests
import pubchempy as pcp


def get_ghs_hazards(cid, retries=2):
    """Fetch GHS hazard statements for a compound from PubChem."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    data = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params={"heading": "GHS Classification"}, timeout=20)
            if r.status_code == 200:
                data = r.json()
                break
            if r.status_code == 404:
                return []
        except Exception:
            pass
        time.sleep(1.5)

    if data is None:
        return []

    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("Name") == "GHS Hazard Statements":
                for item in node.get("Value", {}).get("StringWithMarkup", []):
                    text = item.get("String", "")
                    text = re.sub(r"\s*\[.*?\]", "", text).strip()
                    if text and text not in found:
                        found.append(text)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def lookup_chemical(name, retries=3):
    """Look up a chemical by name. Retries on temporary PubChem errors.
    Returns a dict, or None if not found or PubChem stays unavailable."""
    for attempt in range(retries):
        try:
            results = pcp.get_compounds(name, "name")
            if not results:
                return None

            c = results[0]

            cas = None
            for syn in c.synonyms or []:
                if re.fullmatch(r"\d{2,7}-\d{2}-\d", syn):
                    cas = syn
                    break

            return {
                "searched_name": name,
                "cid": c.cid,
                "formula": c.molecular_formula,
                "molar_mass": c.molecular_weight,
                "cas": cas,
                "hazards": get_ghs_hazards(c.cid),
            }
        except Exception as e:
            print(f"PubChem error (attempt {attempt + 1}/{retries}): {e}")
            time.sleep(2 * (attempt + 1))

    return None


if __name__ == "__main__":
    name = input("Enter a chemical name: ")
    info = lookup_chemical(name)

    if info is None:
        print("Not found, or PubChem is temporarily unavailable. Try again.")
    else:
        print("\n--- Result ---")
        print("Formula:   ", info["formula"])
        print("Molar mass:", info["molar_mass"], "g/mol")
        print("CAS number:", info["cas"])
        print("PubChem ID:", info["cid"])
        print("Hazards:")
        if info["hazards"]:
            for h in info["hazards"]:
                print("  -", h)
        else:
            print("  No hazard data found (this does NOT mean it is safe).")