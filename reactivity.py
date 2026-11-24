import requests

# Section headings we try to read. Missing ones are skipped.
HEADINGS = [
    "Reactive Group",
    "Reactivity Profile",
    "Air and Water Reactions",
    "Hazardous Reactivities and Incompatibilities",
]


def _collect_strings(node, out):
    """Walk the JSON and collect every text value found."""
    if isinstance(node, dict):
        for item in node.get("StringWithMarkup", []):
            text = item.get("String", "").strip()
            if text and text not in out:
                out.append(text)
        for v in node.values():
            _collect_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_strings(v, out)


def get_reactivity(cid):
    """Returns {heading: [text, ...]} for the headings that have data."""
    result = {}
    for heading in HEADINGS:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        try:
            r = requests.get(url, params={"heading": heading}, timeout=20)
            if r.status_code != 200:
                continue
            texts = []
            _collect_strings(r.json(), texts)
            if texts:
                result[heading] = texts
        except Exception:
            continue
    return result