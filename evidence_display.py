"""Small presentation helpers for source-preserving reaction and hazard evidence."""

import re


_NUMBER = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def classify_reported_yield(raw_value):
    """Classify a source yield for display without changing its original value."""
    if raw_value is None or not str(raw_value).strip():
        return {"status": "missing", "raw": raw_value, "message": None}

    raw_text = str(raw_value)
    values = []
    for token in _NUMBER.findall(raw_text):
        try:
            values.append(float(token))
        except ValueError:
            continue

    if not values:
        return {
            "status": "unvalidated",
            "raw": raw_value,
            "message": (f"Source yield could not be checked as a percentage (raw value: {raw_text}); "
                        "it is not treated as a validated yield."),
        }

    if any(value < 0 or value > 100 for value in values):
        return {
            "status": "anomalous",
            "raw": raw_value,
            "message": (f"Anomalous source yield: {raw_text} (raw value preserved). "
                        "A yield must be within 0-100%; this value is not treated as a valid yield."),
        }

    return {
        "status": "reported",
        "raw": raw_value,
        "message": (f"Source-reported yield: {raw_text}. The numeric value is within 0–100%; "
                    "the source report has not been independently verified."),
    }


def _source_labels(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        label = value.get("name") or value.get("title") or value.get("url")
        return [str(label).strip()] if label else []
    if isinstance(value, (list, tuple, set)):
        labels = []
        for item in value:
            labels.extend(_source_labels(item))
        return labels
    return [str(value).strip()]


def deduplicate_hazard_statements(hazards, default_source="PubChem PUG-View GHS Classification"):
    """Merge identical hazard text while retaining every available source label."""
    merged = {}
    for item in hazards or []:
        if isinstance(item, dict):
            text = (item.get("statement") or item.get("hazard_statement") or
                    item.get("text") or item.get("String") or item.get("name"))
            sources = []
            for field in ("sources", "source", "provenance"):
                sources.extend(_source_labels(item.get(field)))
        else:
            text = item
            sources = []
        if text is None:
            continue
        display_text = re.sub(r"\s+", " ", str(text)).strip()
        if not display_text:
            continue
        key = display_text.casefold()
        entry = merged.setdefault(key, {"statement": display_text, "sources": []})
        for source in sources or [default_source]:
            if source and source.casefold() not in {existing.casefold() for existing in entry["sources"]}:
                entry["sources"].append(source)
    return list(merged.values())


def describe_recorded_input_roles(relation):
    """Explain participant-role matches without claiming the pair reacted."""
    descriptions = {
        "reactant-reactant": (
            "Both inputs are explicitly listed as reactants in this record. "
            "This participant evidence alone does not establish that either was consumed "
            "or that the pair caused the recorded products."
        ),
        "reactant-reagent": (
            "The inputs are listed as a reactant and a reagent. A reagent may not be consumed; "
            "their co-occurrence does not establish a reaction between the pair."
        ),
        "other-participant-roles": (
            "The inputs occur in this record, but at least one has another participant role. "
            "This is not direct reactant-reactant evidence and does not establish a reaction."
        ),
        "role-not-resolved": (
            "The inputs occur in this record, but participant roles could not be resolved. "
            "Co-occurrence alone does not establish that the pair reacted."
        ),
    }
    return descriptions.get(relation, descriptions["role-not-resolved"])


def recorded_source_label(source):
    """Return a source category label that keeps patent and ORD records distinct."""
    if source == "USPTO":
        return "USPTO patent-extracted (text-mined) record"
    if source == "ORD-mined":
        return "Open Reaction Database (ORD) record"
    return f"{source} recorded reaction" if source else "Recorded reaction source not identified"


def recorded_reaction_display(record, index):
    """Build labels for a record while leaving every source field untouched."""
    source = record.get("source")
    source_label = recorded_source_label(source)
    if source == "ORD-mined":
        title = f"ORD record {record.get('source_record_id') or index}"
    elif source == "USPTO":
        patent = record.get("patent") or "patent ID not reported"
        year = record.get("year") or "year unknown"
        title = f"USPTO patent-extracted record {patent}, {year}"
    else:
        title = f"{source_label} {index}"

    yield_evidence = classify_reported_yield(record.get("yield"))
    if yield_evidence["status"] == "anomalous":
        title += " [anomalous source yield]"
    return {
        "title": title,
        "source_label": source_label,
        "yield": yield_evidence,
        "role_assessment": describe_recorded_input_roles(record.get("evidence_relation")),
    }
