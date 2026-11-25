import os
from pathlib import Path

import streamlit as st

from cloud_database import CloudDatabaseError, bootstrap_database

try:
    _cloud_secrets = st.secrets
except Exception:
    _cloud_secrets = {}

try:
    _cloud_database_path = bootstrap_database(Path(__file__).resolve().parent,
                                              secrets=_cloud_secrets)
except CloudDatabaseError as exc:
    _cloud_database_path = None
    _cloud_database_error = type(exc).__name__
else:
    _cloud_database_error = None

if _cloud_database_path is not None:
    os.environ["CHEM_DB_PATH"] = str(_cloud_database_path)
    print(f"Verified full reaction database selected: {_cloud_database_path}")

import sqlite3
from rdkit import Chem
from db import (DB_FILE, db_ready, get_chemical, get_product, check_pair, check_groups,
                family_of, get_reactions, clear_saved_lookups, get_transport,
                find_recorded, find_epa_toxval_records, ReactionSearchError)
from engine import predict
from severity import adjust_severity
from fallback import find_mentions, precautions
from aliases import resolve
from chemkeys import split_source_references
from ai_explain import formulas_match
from pair_store import key_for, get_pair_reactions, pair_has_rows, worst_level
from pair_fill import fill_pair
import timing
from migrations import migrate_database
from evidence_display import deduplicate_hazard_statements, recorded_reaction_display
from epa_diagnostics import collect_epa_diagnostics

st.set_page_config(page_title="Chemical Hazard Screening", page_icon="🧪")
if _cloud_database_error:
    st.warning("The optional full reaction database is unavailable; using the existing "
               f"CSV-based database setup. ({_cloud_database_error})")
st.title("🧪 AI Chemical Compatibility & Hazard Screening")
st.caption("Screening aid only. Always check official safety data sheets.")

if not db_ready():
    try:
        from migrate_to_db import main as migrate_legacy_database

        migrate_legacy_database()

    except Exception as e:
        st.error("Database initialization failed.")
        st.exception(e)
        st.stop()

try:
    migrate_database(DB_FILE)
except Exception as e:
    st.error("Database schema migration failed.")
    st.exception(e)
    st.stop()

con = sqlite3.connect(DB_FILE)

try:
    try:
        count = con.execute(
            "SELECT COUNT(*) FROM transport_hazards"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
finally:
    con.close()
if count == 0:
    adr_file = Path(__file__).resolve().parent / "adr.csv"

    if adr_file.exists():
        from import_transport import main as import_adr
        import_adr(str(adr_file))
    else:
        st.warning("ADR transport dataset is unavailable.")

if not db_ready():
    st.error("Database initialization was unsuccessful.")
    st.stop()


ICON = {"CRITICAL": "🟥", "HIGH": "🟧", "MEDIUM": "🟨", "LOW": "🟩"}
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SHOWN_STATUSES = ("reviewed", "ai_checked")

transport_rows = []   # collected for the summary table at the end


def show_hazard_statements(hazards, limit=None):
    """Render unique hazard text with its source labels retained."""
    evidence = deduplicate_hazard_statements(hazards)
    if limit is not None:
        evidence = evidence[:limit]
    if not evidence:
        return False

    source_sets = [tuple(source.casefold() for source in item["sources"]) for item in evidence]
    same_sources = all(sources == source_sets[0] for sources in source_sets)
    if same_sources:
        sources = "; ".join(evidence[0]["sources"])
        st.caption(f"Hazard statements (identical text grouped). Source: {sources}.")
    else:
        st.caption("Hazard statements (identical text grouped; provenance shown per statement).")

    for item in evidence:
        st.write("- " + item["statement"])
        if not same_sources:
            st.caption("Source(s): " + "; ".join(item["sources"]))
    return True


def show_transport(cas, substance, role):
    """Print ADR transport info for a CAS number and remember it for the summary table."""
    st.markdown("**Transport classification (ADR / UNECE)**")
    if not cas:
        st.write("No CAS number on record, so it cannot be matched to the transport list.")
        return
    entries = get_transport(cas)
    if not entries:
        st.write(f"No ADR entry found for CAS {cas}. The list only covers some chemicals, "
                 "so this does NOT mean it is unregulated.")
        return
    for e in entries:
        line = f"- UN {e['un_number']}  |  Class {e['hazard_class']}"
        if e["meaning"]:
            line += f" ({e['meaning']})"
        if e["packing_group"]:
            line += f"  |  Packing group {e['packing_group']}"
        if e["labels"]:
            line += f"  |  Labels {e['labels']}"
        st.write(line)
        transport_rows.append({
            "Substance": substance, "Role": role, "UN No.": e["un_number"],
            "Class": e["hazard_class"], "Hazard": e["meaning"],
            "Packing group": e["packing_group"], "Labels": e["labels"],
        })


def render_epa_toxval_evidence(chemical_name, result):
    """Show source-backed EPA toxicity evidence separately from GHS and compatibility."""
    st.markdown("**EPA toxicity evidence (ToxValDB v9.7.0)**")
    if not result["total"]:
        st.write(f"No EPA ToxValDB v9.7.0 toxicity records found for {chemical_name}.")
        return
    st.caption(
        f"{result['total']:,} exact {result['match_basis']} match(es); "
        f"showing {len(result['records']):,}. These are toxicity evidence, not GHS classifications "
        "or compatibility predictions. No GHS class or compatibility severity is inferred from them."
    )
    for record in result["records"]:
        endpoint = record.get("toxval_type") or record.get("toxval_subtype") or "Toxicity endpoint"
        numeric = record.get("toxval_numeric_raw")
        if numeric in (None, "", "-"):
            numeric = "Not reported"
        qualifier = record.get("toxval_numeric_qualifier")
        units = record.get("toxval_units")
        reported_value = (f"{qualifier or ''}{numeric}" + (f" {units}" if units and units != "-" else ""))
        header = f"{endpoint}: {reported_value} — {record.get('source') or 'source not reported'}"
        with st.expander(header):
            st.write(f"**EPA ToxValDB v{record.get('release_version')}:** {record.get('toxval_id')}")
            st.write(f"**Endpoint:** {endpoint}")
            if record.get("toxval_subtype") and record.get("toxval_subtype") != "-":
                st.write(f"**Endpoint subtype:** {record['toxval_subtype']}")
            if record.get("toxval_type_supercategory"):
                st.write(f"**Endpoint category:** {record['toxval_type_supercategory']}")
            st.write(f"**Reported value:** {reported_value}")
            original_value = record.get("toxval_numeric_original_raw")
            if original_value not in (None, "", "-") and original_value != numeric:
                st.write(f"**Original source value:** {record.get('toxval_numeric_qualifier_original') or ''}"
                         f"{original_value} {record.get('toxval_units_original') or ''}")
            experimental = record.get("experimental_record")
            if not experimental or experimental == "-":
                category = "Not reported"
            elif str(experimental).strip().lower() == "experimental":
                category = "Experimental record (EPA source flag)"
            elif "not experimental" in str(experimental).strip().lower():
                category = "Non-experimental EPA value (source flag: not experimental)"
            else:
                category = f"EPA record flag: {experimental} (category not inferred)"
            st.write(f"**Record category:** {category}")
            if record.get("species_common") or record.get("species_latin") or record.get("species_original"):
                species = record.get("species_common") or record.get("species_latin") or record.get("species_original")
                if record.get("species_latin") and species != record.get("species_latin"):
                    species += f" ({record['species_latin']})"
                st.write(f"**Species:** {species}")
            for label, field in (("Study type", "study_type"), ("Study duration", "study_duration_class"),
                                 ("Exposure route", "exposure_route"), ("Exposure method", "exposure_method"),
                                 ("Effect", "toxicological_effect"), ("Population", "population"),
                                 ("Year", "year"), ("Risk assessment class", "risk_assessment_class")):
                value = record.get(field)
                if value and value != "-":
                    st.write(f"**{label}:** {value}")
            if record.get("qc_status"):
                if str(record["qc_status"]).lower().startswith("fail"):
                    st.warning(f"EPA source QC status: {record['qc_status']}")
                else:
                    st.write(f"**EPA source QC status:** {record['qc_status']}")
            if record.get("source_url"):
                st.write(f"**Source URL:** {record['source_url']}")
            if record.get("subsource_url") and record["subsource_url"] != record.get("source_url"):
                st.write(f"**Subsource URL:** {record['subsource_url']}")
            for ref in record.get("references", []):
                st.markdown("**Record reference**")
                title = ref.get("title") or ref.get("long_ref") or ref.get("document_name")
                if title:
                    st.write(title)
                if ref.get("author") or ref.get("year"):
                    st.write(" ".join(value for value in (ref.get("author"), ref.get("year")) if value))
                if ref.get("doi"):
                    st.write(f"DOI: {ref['doi']}")
                if ref.get("url"):
                    st.write(f"Reference URL: {ref['url']}")
                if ref.get("guideline") and ref["guideline"] != "-":
                    st.write(f"Guideline: {ref['guideline']}")


def render_atrazine_epa_diagnostic(chemical_name, casrn, result, *, limit=25):
    """Show temporary read-only diagnostics only for an Atrazine lookup."""
    if (str(chemical_name or "").strip().casefold() != "atrazine"
            and str(casrn or "").strip() != "1912-24-9"):
        return
    diagnostic = collect_epa_diagnostics(
        DB_FILE,
        {"identifier": chemical_name, "casrn": casrn, "limit": limit},
        result,
    )
    with st.expander("Developer diagnostic — EPA ToxValDB (Atrazine)", expanded=False):
        st.json(diagnostic)


def render_product_epa_evidence(name, product):
    """Show EPA toxicity separately for a product with a resolvable identity."""
    if not product.get("cid") and not product.get("cas"):
        with st.expander(f"EPA toxicity evidence for {name}"):
            st.write("EPA lookup was not attempted because this product has no verified chemical "
                     "record or CAS number.")
        return
    result = find_epa_toxval_records(name, casrn=product.get("cas"), limit=10)
    with st.expander(f"EPA toxicity evidence for {name}"):
        render_epa_toxval_evidence(name, result)


def render_pair_entries(entries, expand_first):
    """Reaction entries stored in the database for this exact pair."""
    st.subheader("Reactions in the database for this pair")
    if all(e["status"] == "reviewed" for e in entries):
        st.write("Every entry below has been reviewed by a person.")
    else:
        st.write("Entries marked ✅ have been reviewed by a person. The others have **not been "
                 "reviewed yet**: treat them as leads to verify before relying on them.")
    for i, e in enumerate(entries, start=1):
        if e["status"] == "reviewed":
            badge, level_word = "✅ Reviewed", "level"
        elif e["status"] == "ai_checked":
            badge, level_word = "Not yet reviewed", "estimated level"
        else:
            badge, level_word = "⚠️ Unconfirmed", "estimated level"
        header = (f"{ICON.get(e['level'], '⬜')} Entry {i}: {e['title']}  "
                  f"({level_word}: {e['level']}, {badge})")
        with st.expander(header, expanded=(i == 1 and expand_first)):
            if e["equation"]:
                st.markdown("**Equation**")
                st.code(e["equation"], language=None)
            if e["when_text"]:
                st.markdown(f"**Favoured when:** {e['when_text']}")
            if e["notes"]:
                st.write(e["notes"])
            st.markdown("**Products and their hazards**")
            for p in e["products"][:4]:
                heading = p["name"] + (f"  |  {p['formula']}" if p["formula"] else "")
                if p["state"]:
                    heading += f"  ({p['state']})"
                st.markdown(f"##### {heading}")
                if p["note"]:
                    st.write("Notes: " + p["note"])
                prod = get_product("name", p["name"])
                if prod["cid"] is None:
                    st.caption("⚠️ This product name could not be matched to a known compound. "
                               "Treat this product as unconfirmed.")
                    continue
                match = formulas_match(p["formula"], prod["formula"])
                if match is False:
                    st.caption(f"⚠️ The formula given ({p['formula']}) differs from the record for "
                               f"this name ({prod['formula']}). The wrong compound may be named.")
                elif match is True:
                    st.caption("✓ Name and formula confirmed.")
                if not show_hazard_statements(prod["hazards"], limit=6):
                    st.caption("No official hazard statements found. This is not proof of safety.")
                render_product_epa_evidence(prod["name"], prod)
                show_transport(prod["cas"], p["name"], "Product (database entry)")


with st.sidebar:
    st.subheader("Saved lookups")
    st.caption("Chemicals you look up are saved in the database so they load "
               "instantly next time.")
    if st.button("Clear saved lookups"):
        clear_saved_lookups()
        st.success("Cleared.")
    st.divider()
    n_recorded = st.slider("Recorded reactions to show", 1, 100, 3)
    show_extra = st.checkbox("Show general analysis when no curated rule is available", value=False)


def chemical_form(label, key):
    st.subheader(label)
    name = st.text_input("Chemical name", key=key + "name")
    col1, col2 = st.columns(2)
    qty = col1.number_input("Quantity", min_value=0.0, value=100.0, key=key + "qty")
    unit = col2.selectbox("Unit", ["mL", "L", "g", "kg"], key=key + "unit")
    conc = st.number_input("Concentration (%)", min_value=0.0, max_value=100.0,
                           value=100.0, key=key + "conc")
    state = st.selectbox("Physical state", ["liquid", "solid", "gas", "powder"],
                         key=key + "state")
    return {"name": name.strip(), "quantity": qty, "unit": unit,
            "concentration": conc, "state": state}


left, right = st.columns(2)
with left:
    chem_a = chemical_form("Chemical 1", "a_")
with right:
    chem_b = chemical_form("Chemical 2", "b_")

temp = st.number_input("Temperature (°C)", value=25.0)

if st.button("Run screening", type="primary"):
    screening_started = timing.start_timer()
    if not chem_a["name"] or not chem_b["name"]:
        st.warning("Please enter both chemical names.")
        timing.log_duration("Total screening", screening_started)
        st.stop()

    with st.spinner("Looking up chemicals..."):
        lookup_started = timing.start_timer()
        try:
            info_a = get_chemical(resolve(chem_a["name"]))
        finally:
            timing.log_duration("Chemical identification and cache lookup (input A)",
                                lookup_started)
        lookup_started = timing.start_timer()
        try:
            info_b = get_chemical(resolve(chem_b["name"]))
        finally:
            timing.log_duration("Chemical identification and cache lookup (input B)",
                                lookup_started)

    if not info_a or not info_b:
        st.error("Could not look up one of the chemicals. Check the spelling, "
                 "or the data service may be busy. Wait a minute and try again.")
        for chemical, info in ((chem_a, info_a), (chem_b, info_b)):
            casrn = info.get("cas") if info else None
            evidence = find_epa_toxval_records(chemical["name"], casrn=casrn, limit=25)
            with st.expander(f"EPA toxicity evidence for {chemical['name']}", expanded=True):
                render_epa_toxval_evidence(chemical["name"], evidence)
            render_atrazine_epa_diagnostic(chemical["name"], casrn, evidence)
        timing.log_duration("Total screening", screening_started)
        st.stop()

    chem_a["cas"] = info_a["cas"]
    chem_b["cas"] = info_b["cas"]
    structure_valid = {}
    for label, chemical, info in (("Chemical 1", chem_a, info_a), ("Chemical 2", chem_b, info_b)):
        try:
            valid = bool(info.get("smiles") and Chem.MolFromSmiles(info["smiles"]) is not None)
        except Exception:
            valid = False
        structure_valid[label] = valid
        if not valid:
            st.error(
                f"{label} ({chemical['name']}) was identified, but a structure usable by RDKit "
                "could not be verified. Two-input reaction structure searches and template "
                "predictions are skipped; this is different from a search with zero matches."
            )
            if info.get("structure_source_smiles"):
                st.caption(f"Source {info.get('structure_source_property') or 'SMILES'} "
                           f"from {info.get('structure_source') or 'reported source'} was retained "
                           "for traceability, but RDKit rejected it.")
        elif info.get("structure_source"):
            source_detail = info.get("structure_source_detail")
            provenance = (f"; retrieved {info['structure_retrieved_at']}"
                          if info.get("structure_retrieved_at") else "")
            st.caption(
                f"{label} structure: RDKit-valid; source {info['structure_source']}"
                f"{(' (' + info['structure_identifier_type'] + ' ' + str(info['structure_identifier']) + ')') if info.get('structure_identifier') else ''}"
                f"{provenance}. {source_detail or ''}"
            )
    react_a, react_b = info_a["reactivity"], info_b["reactivity"]
    groups_a = react_a.get("Reactive Group", [])
    groups_b = react_b.get("Reactive Group", [])

    with st.spinner("Predicting reactions..."):
        predicted = (predict(info_a["smiles"], info_b["smiles"], groups_a, groups_b)
                     if all(structure_valid.values()) else [])

    base_level, message = check_pair(chem_a["cas"], chem_b["cas"])
    source = "Curated rule"
    if base_level == "UNKNOWN":
        g_level, g_msg = check_groups(groups_a, groups_b)
        if g_level != "UNKNOWN":
            base_level, message = g_level, g_msg
            source = "Reactive-group match (less specific)"
        elif predicted:
            worst = max(predicted, key=lambda r: ORDER.index(r["severity"]))
            base_level = worst["severity"]
            message = f"Predicted reaction: {worst['name']}."
            source = "Rule-based prediction (verify)"
        else:
            source = "No rule matched"

    fam_a, fam_b = family_of(chem_a["cas"]), family_of(chem_b["cas"])
    reactions = get_reactions(fam_a, fam_b)
    rules_cover = (base_level != "UNKNOWN") or bool(reactions) or bool(predicted)

    mentions_a = find_mentions(react_a, chem_b["name"])
    mentions_b = find_mentions(react_b, chem_a["name"])

    recorded_error = None
    if all(structure_valid.values()):
        try:
            recorded, rec_total = find_recorded(info_a["smiles"], info_b["smiles"], n_recorded)
        except ReactionSearchError as exc:
            recorded, rec_total, recorded_error = [], None, exc
    else:
        recorded, rec_total = [], None
    rec_count_text = (f"{rec_total:,}" if rec_total is not None
                      else "not searched (structure validation failed)")

    # ---------- entries stored for this exact pair; create them in the background if missing ----------
    pair_a = {"name": resolve(chem_a["name"]), "cas": info_a["cas"], "formula": info_a["formula"],
              "hazards": info_a["hazards"], "groups": groups_a}
    pair_b = {"name": resolve(chem_b["name"]), "cas": info_b["cas"], "formula": info_b["formula"],
              "hazards": info_b["hazards"], "groups": groups_b}
    key_a, key_b = key_for(pair_a), key_for(pair_b)
    if all(structure_valid.values()) and not rules_cover and not pair_has_rows(key_a, key_b):
        with st.spinner("Searching the database (a new pair can take up to a minute)..."):
            fill_pair(pair_a, pair_b, {"curated": [], "predicted": [],
                                       "recorded_count": rec_total})
    entries = get_pair_reactions(key_a, key_b, SHOWN_STATUSES)
    reviewed_level = worst_level([e for e in entries if e["status"] == "reviewed"])

    if base_level == "UNKNOWN" and reviewed_level:
        base_level = reviewed_level
        message = "Reviewed database entry for this pair."
        source = "Database entry (reviewed)"

    show_banner = rules_cover or bool(reviewed_level)
    show_details = rules_cover or show_extra or not entries
    final_level, notes = adjust_severity(base_level, [chem_a, chem_b], temp)

    # ---------- Report ----------
    st.header("Hazard Report")
    if show_banner:
        summary = f"Final result: {final_level}  (base: {base_level})"
        if final_level in ("HIGH", "CRITICAL"):
            st.error(summary)
        elif final_level in ("MEDIUM", "UNKNOWN"):
            st.warning(summary)
        else:
            st.success(summary)
        st.write(message)
        st.caption(f"Basis for this result: {source}")
        glance = (f"**Results found for this pair:**  {len(reactions)} curated  |  "
                  f"{len(predicted)} predicted  |  {len(entries)} database entries  |  "
                  f"{rec_count_text} recorded (source datasets)")
        st.info(glance + ". Scroll down for the details of each.")
    elif entries:
        best = worst_level(entries)
        st.caption("**Result pending review.** The database entries below have not been reviewed "
                   "by a person yet, so no final level is given. Highest estimated level among "
                   f"them: {best or 'unknown'}.")
    else:
        st.info("No reaction for this pair could be found in the database yet. "
                   "This does NOT mean the pair is safe. Check the safety data sheets, and "
                   "try again later.")

    # ---------- Database entries for this pair ----------
    if entries:
        render_pair_entries(entries, expand_first=not reactions)

    # ---------- Curated + predicted reactions ----------
    if rules_cover:
        st.subheader(f"Possible reactions: {chem_a['name']} + {chem_b['name']}")
        if reactions:
            st.write(f"{len(reactions)} curated reaction(s) found. Which one happens "
                     "depends on proportions, pH and temperature, so treat all as possible.")
            for i, rx in enumerate(reactions, start=1):
                header = f"{ICON.get(rx['severity'], '')} Reaction {i}: {rx['title']}  ({rx['severity']})"
                with st.expander(header, expanded=(i == 1)):
                    st.markdown("**Equation**")
                    st.code(rx["equation"], language=None)
                    st.markdown(f"**Favoured when:** {rx['when']}")
                    st.markdown("**Products and their hazards**")
                    for p in rx["products"]:
                        st.markdown(f"##### {p['name']}  ({p['state']})")
                        st.write(p["note"])
                        if p["lookup"]:
                            prod = get_product("name", p["lookup"])
                            hz = prod["hazards"]
                            if not show_hazard_statements(hz, limit=6) and not prod.get("cid"):
                                st.warning("This product could not be resolved to a verified "
                                           "chemical record; hazard information is unavailable.")
                            elif not hz and prod.get("cid"):
                                st.caption("No official hazard statements retrieved. "
                                           "This is not proof of safety.")
                            render_product_epa_evidence(prod["name"], prod)
                            show_transport(prod["cas"], p["name"], "Product (curated)")
        else:
            st.info("No curated reaction entry for this pair.")

        st.subheader("Predicted reactions (rule-based, verify before use)")
        if predicted:
            st.write(f"{len(predicted)} reaction type(s) match the structures of these "
                     "chemicals. These are predictions from reaction templates, not "
                     "recorded experiments.")
            for i, rx in enumerate(predicted, start=1):
                header = f"{ICON.get(rx['severity'], '')} Prediction {i}: {rx['name']}  ({rx['severity']})"
                with st.expander(header, expanded=(i == 1 and not reactions)):
                    st.markdown(f"**Conditions:** {rx['conditions']}")
                    st.markdown(f"**By-product:** {rx['byproduct']}")
                    st.markdown("**Predicted products and their hazards**")
                    shown = set()
                    for prod_smiles in rx["products"]:
                        for frag in prod_smiles.split("."):
                            if frag in shown:
                                continue
                            shown.add(frag)
                            d = get_product("smiles", frag)
                            title = d["name"]
                            if d["formula"]:
                                title += f"  |  {d['formula']}"
                            st.markdown(f"##### {title}")
                            st.caption(f"Structure (SMILES): {frag}")
                            if not show_hazard_statements(d["hazards"], limit=6) and not d.get("cid"):
                                st.warning("This product could not be resolved to a verified "
                                           "chemical record; hazard information is unavailable.")
                            elif not d["hazards"] and d.get("cid"):
                                st.write("No official hazard statements found. "
                                         "This is not proof of safety.")
                            render_product_epa_evidence(d["name"], d)
                            show_transport(d["cas"], d["name"], "Product (predicted)")
        else:
            if not all(structure_valid.values()):
                st.info("Reaction templates were not searched because an input structure "
                        "could not be verified.")
            else:
                st.write("No reaction template matched the structures of these two chemicals. "
                         "This does NOT mean they are safe together.")

    # ---------- Recorded reaction-source records ----------
    if show_details or recorded or rec_total is None:
        st.subheader("Recorded reaction records")
        if recorded:
            st.write(f"{rec_count_text} matching source record(s); showing {len(recorded)} "
                     "with source-aware allocation. A record containing both inputs does not "
                     "prove that the inputs reacted with each other. Equivalent reactions from "
                     "different source records are not merged and may overlap.")
            for i, rc in enumerate(recorded, start=1):
                is_ord_mined = rc.get("source") == "ORD-mined"
                display = recorded_reaction_display(rc, i)
                title = display["title"]
                with st.expander(title, expanded=(i == 1 and not reactions and not predicted)):
                    st.caption(f"Evidence source: {display['source_label']}.")
                    yield_evidence = display["yield"]
                    if yield_evidence["message"]:
                        if yield_evidence["status"] == "anomalous":
                            st.warning(yield_evidence["message"])
                        else:
                            st.caption(yield_evidence["message"])
                    if is_ord_mined:
                        if rc.get("is_mined"):
                            st.caption(
                                "ORD source marks this record as mined from reaction SMILES. "
                                "It is not independent experimental verification; input role labels "
                                "do not prove each input was consumed."
                            )
                        else:
                            st.caption(
                                "ORD source record. The dataset source and participant roles are "
                                "preserved; this record is not by itself proof that the inputs react."
                            )
                        actual_dois, other_references = split_source_references(rc.get("source_doi"))
                        for doi in actual_dois:
                            st.write(f"Source DOI: {doi}")
                        for reference in other_references:
                            st.write(f"ORD source reference identifier (not a DOI): {reference}")
                        st.write(f"ORD dataset: {rc.get('dataset_id') or 'unknown'}")
                    elif rc.get("source") == "USPTO":
                        st.caption("USPTO reactions are extracted from patent text. The record may "
                                   "contain extraction errors and is not independent verification.")
                    st.write(f"**Participant role assessment:** {display['role_assessment']}")
                    if rc.get("input_roles"):
                        st.write(f"Chemical 1 role(s): {', '.join(rc['input_roles'][0]) or 'not resolved'}")
                        st.write(f"Chemical 2 role(s): {', '.join(rc['input_roles'][1]) or 'not resolved'}")
                    st.markdown("**Reaction (reactants > reagents > products)**")
                    st.code(f"{rc['reactants']}  >  {rc['reagents'] or '-'}  >  {rc['products']}",
                            language=None)
                    st.markdown("**Products and their hazards**")
                    for frag in rc["products"].split(".")[:3]:
                        d = get_product("smiles", frag)
                        heading = d["name"] + (f"  |  {d['formula']}" if d["formula"] else "")
                        st.markdown(f"##### {heading}")
                        st.caption(f"Structure (SMILES): {frag}")
                        if not show_hazard_statements(d["hazards"], limit=4) and not d.get("cid"):
                            st.warning("This product could not be resolved to a verified "
                                       "chemical record; hazard information is unavailable.")
                        elif not d["hazards"] and d.get("cid"):
                            st.write("No official hazard statements found. "
                                     "This is not proof of safety.")
                        render_product_epa_evidence(d["name"], d)
                        show_transport(d["cas"], d["name"], "Product (recorded)")
            if any(row.get("source") == "USPTO" for row in recorded):
                st.caption("USPTO records are patent-extracted and text-mined (D. Lowe, 2017); "
                           "extracted participant roles, yields and conditions may contain errors.")
        else:
            if recorded_error:
                st.error(f"Recorded reaction search was not run: {recorded_error.input_label} "
                         "has no verified RDKit-usable structure.")
            elif rec_total is None:
                st.info("Recorded reaction search was skipped because at least one input structure "
                        "could not be verified. This is not a zero-result search.")
            else:
                st.write("No matching record was found in the currently imported reaction datasets. "
                         "Dataset coverage is incomplete; absence does not mean the chemicals "
                         "are safe together or cannot react.")

    # ---------- General analysis ----------
    if show_details and not reactions:
        st.subheader("General analysis")
        st.markdown("**Reactive groups**")
        st.write(f"- {chem_a['name']}: " + (", ".join(groups_a) if groups_a else "none on record"))
        st.write(f"- {chem_b['name']}: " + (", ".join(groups_b) if groups_b else "none on record"))

        st.markdown("**What the published reactivity data says about this pair**")
        if mentions_a or mentions_b:
            for s in mentions_a:
                st.write(f"- ({chem_a['name']} record) {s}")
            for s in mentions_b:
                st.write(f"- ({chem_b['name']} record) {s}")
        else:
            st.write("Neither record mentions the other chemical or its class directly.")

        st.markdown("**General precautions for handling these together**")
        shown_notes = []
        for n in precautions(info_a["hazards"]) + precautions(info_b["hazards"]):
            if n not in shown_notes:
                shown_notes.append(n)
        if shown_notes:
            for n in shown_notes:
                st.write("- " + n)
        else:
            st.write("- No hazard statements on record. Treat both as unknown and "
                     "start with very small quantities in a fume hood.")
        st.write("- Do not mix without checking the safety data sheets for both chemicals.")

    if show_banner:
        st.subheader("Effect of your conditions")
        if notes:
            for n in notes:
                st.write("- " + n)
        else:
            st.write("No condition adjustments.")

    st.subheader("Chemical data")
    for chem, info, react in ((chem_a, info_a, react_a), (chem_b, info_b, react_b)):
        cas_label = info["cas"] or "not reported"
        with st.expander(f"{chem['name']}  |  {info['formula']}  |  CAS {cas_label}"):
            st.markdown("**Hazard statements**")
            structure_to_show = info.get("smiles") or info.get("structure_source_smiles")
            if structure_to_show:
                st.markdown("**Structure (source SMILES)**")
                st.code(structure_to_show, language=None)
                if info.get("structure_source_property"):
                    st.caption(f"Property: {info['structure_source_property']}; "
                               "source representation is shown as retrieved, not locally normalized.")
            if not show_hazard_statements(info["hazards"]):
                st.write("No hazard statements found. This is not proof of safety.")

            evidence = find_epa_toxval_records(chem["name"], casrn=info.get("cas"), limit=25)
            render_epa_toxval_evidence(chem["name"], evidence)
            render_atrazine_epa_diagnostic(chem["name"], info.get("cas"), evidence)

            st.markdown("**Reactivity information**")
            if react:
                for heading, texts in react.items():
                    st.markdown(f"*{heading}*")
                    for t in texts[:5]:
                        st.write("- " + t)
            else:
                st.write("No reactivity data found. This is not proof of stability.")

            show_transport(info["cas"], chem["name"], "Input")

    with st.expander("Data sources"):
        st.write("Chemical identity and hazard data are compiled from public "
                 "chemical databases. Open the records below to verify.")
        for chem, info in ((chem_a, info_a), (chem_b, info_b)):
            if info.get("cid"):
                st.markdown(
                    f"- {chem['name']}: "
                    f"[PubChem record](https://pubchem.ncbi.nlm.nih.gov/compound/{info['cid']})")
            elif info.get("data_source") == "ORD structure-only record":
                st.markdown(
                    f"- {chem['name']}: ORD structure {info.get('source_record_id')} "
                    f"([dataset](https://github.com/open-reaction-database/ord-data)); "
                    "name, CAS number, and hazard record unavailable from this source row.")
            else:
                st.write(f"- {chem['name']}: no linked external chemical record")

    st.subheader("Transport hazard table (ADR / UNECE)")
    unique_rows, seen = [], set()
    for r in transport_rows:
        k = (r["Substance"], r["Role"], r["UN No."])
        if k not in seen:
            seen.add(k)
            unique_rows.append(r)
    if unique_rows:
        st.dataframe(unique_rows, width="stretch", hide_index=True)
    else:
        st.write("None of the chemicals or products shown above matched an ADR entry.")
    st.caption("Source: ADR Table A as published through the EU. CAS numbers in that "
               "list are representative, not exhaustive.")

    st.subheader("Uncertainty warnings")
    warnings = []
    for chem in (chem_a, chem_b):
        if family_of(chem["cas"]) is None:
            warnings.append(f"{chem['name']} is not in our direct rules table.")
    if source.startswith("Reactive-group"):
        warnings.append("Result came from reactive-group matching, which is less "
                        "precise than a direct rule.")
    if source.startswith("Rule-based"):
        warnings.append("Result came from reaction templates. Products are predicted, "
                        "not recorded, and conditions may differ in practice.")
    if any(e["status"] == "ai_checked" for e in entries):
        warnings.append("Some database entries for this pair have not been reviewed by a person yet.")
    if any(e["status"] == "needs_review" for e in entries):
        warnings.append("Some database entries for this pair could not be fully confirmed by the "
                        "automatic checks.")
    if not reactions and not predicted and not recorded and not entries:
        warnings.append("No products could be listed for this pair.")
    if base_level == "UNKNOWN":
        warnings.append("No verified rule exists for this pair. Treat it as potentially unsafe.")
    warnings.append("Verify everything against official safety data sheets.")
    for w in warnings:
        st.write("- " + w)
    timing.log_duration("Total screening", screening_started)
