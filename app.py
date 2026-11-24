import streamlit as st
from db import (db_ready, get_chemical, get_product, check_pair, check_groups,
                family_of, get_reactions, clear_saved_lookups, get_transport,
                find_recorded)
from engine import predict
from severity import adjust_severity
from fallback import find_mentions, precautions
from aliases import resolve
from ai_explain import formulas_match
from pair_store import key_for, get_pair_reactions, pair_has_rows, worst_level
from pair_fill import fill_pair

st.set_page_config(page_title="Chemical Hazard Screening", page_icon="🧪")
st.title("🧪 AI Chemical Compatibility & Hazard Screening")
st.caption("Screening aid only. Always check official safety data sheets.")

if not db_ready():
    try:
        from migrate_to_db import main as migrate_database

        migrate_database()

    except Exception as e:
        st.error("Database initialization failed.")
        st.exception(e)
        st.stop()

if not db_ready():
    st.error("Database initialization was unsuccessful.")
    st.stop()


ICON = {"CRITICAL": "🟥", "HIGH": "🟧", "MEDIUM": "🟨", "LOW": "🟩"}
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SHOWN_STATUSES = ("reviewed", "ai_checked")

transport_rows = []   # collected for the summary table at the end


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
                if prod["hazards"]:
                    st.caption("Official hazard statements:")
                    for h in prod["hazards"][:6]:
                        st.write("- " + h)
                else:
                    st.caption("No official hazard statements found. This is not proof of safety.")
                show_transport(prod["cas"], p["name"], "Product (database entry)")


with st.sidebar:
    st.subheader("Saved lookups")
    st.caption("Chemicals you look up are saved in the database so they load "
               "instantly next time.")
    if st.button("Clear saved lookups"):
        clear_saved_lookups()
        st.success("Cleared.")
    st.divider()
    n_recorded = st.slider("Recorded reactions to show", 1, 10, 3)
    show_extra = st.checkbox("Also show patent records and general analysis for pairs "
                             "that have no curated rule", value=False)


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
    if not chem_a["name"] or not chem_b["name"]:
        st.warning("Please enter both chemical names.")
        st.stop()

    with st.spinner("Looking up chemicals..."):
        info_a = get_chemical(resolve(chem_a["name"]))
        info_b = get_chemical(resolve(chem_b["name"]))

    if not info_a or not info_b:
        st.error("Could not look up one of the chemicals. Check the spelling, "
                 "or the data service may be busy. Wait a minute and try again.")
        st.stop()

    chem_a["cas"] = info_a["cas"]
    chem_b["cas"] = info_b["cas"]
    react_a, react_b = info_a["reactivity"], info_b["reactivity"]
    groups_a = react_a.get("Reactive Group", [])
    groups_b = react_b.get("Reactive Group", [])

    with st.spinner("Predicting reactions..."):
        predicted = predict(info_a["smiles"], info_b["smiles"], groups_a, groups_b)

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

    recorded, rec_total = find_recorded(info_a["smiles"], info_b["smiles"], n_recorded)
    rec_count_text = f"{rec_total}+" if rec_total >= 1000 else str(rec_total)

    # ---------- entries stored for this exact pair; create them in the background if missing ----------
    pair_a = {"name": resolve(chem_a["name"]), "cas": info_a["cas"], "formula": info_a["formula"],
              "hazards": info_a["hazards"], "groups": groups_a}
    pair_b = {"name": resolve(chem_b["name"]), "cas": info_b["cas"], "formula": info_b["formula"],
              "hazards": info_b["hazards"], "groups": groups_b}
    key_a, key_b = key_for(pair_a), key_for(pair_b)
    if not rules_cover and not pair_has_rows(key_a, key_b):
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
                  f"{rec_count_text} recorded (patent literature)")
        st.info(glance + ". Scroll down for the details of each.")
    elif entries:
        best = worst_level(entries)
        st.warning("**Result pending review.** The database entries below have not been reviewed "
                   "by a person yet, so no final level is given. Highest estimated level among "
                   f"them: {best or 'unknown'}.")
    else:
        st.warning("No reaction for this pair could be found in the database yet. "
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
                            if hz:
                                st.caption("Official hazard statements:")
                                for h in hz[:6]:
                                    st.write("- " + h)
                            else:
                                st.caption("No official hazard statements retrieved. "
                                           "This is not proof of safety.")
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
                            if d["hazards"]:
                                for h in d["hazards"][:6]:
                                    st.write("- " + h)
                            else:
                                st.write("No official hazard statements found. "
                                         "This is not proof of safety.")
                            show_transport(d["cas"], d["name"], "Product (predicted)")
        else:
            st.write("No reaction template matched the structures of these two chemicals. "
                     "This does NOT mean they are safe together.")

    # ---------- Recorded reactions (patent literature) ----------
    if show_details:
        st.subheader("Recorded reactions (patent literature)")
        if recorded:
            st.write(f"{rec_count_text} recorded reaction(s) contain both chemicals as reactants "
                     f"or reagents. Showing the {len(recorded)} simplest, newest first. "
                     "This shows they were used together, not necessarily that they react "
                     "with each other.")
            for i, rc in enumerate(recorded, start=1):
                title = f"Record {i}: {rc['patent'] or 'patent'}, {rc['year'] or 'year unknown'}"
                if rc["yield"]:
                    title += f"  (reported yield {rc['yield']})"
                with st.expander(title, expanded=(i == 1 and not reactions and not predicted)):
                    st.markdown("**Reaction (reactants > reagents > products)**")
                    st.code(f"{rc['reactants']}  >  {rc['reagents'] or '-'}  >  {rc['products']}",
                            language=None)
                    st.markdown("**Products and their hazards**")
                    for frag in rc["products"].split(".")[:3]:
                        d = get_product("smiles", frag)
                        heading = d["name"] + (f"  |  {d['formula']}" if d["formula"] else "")
                        st.markdown(f"##### {heading}")
                        st.caption(f"Structure (SMILES): {frag}")
                        if d["hazards"]:
                            for h in d["hazards"][:4]:
                                st.write("- " + h)
                        else:
                            st.write("No official hazard statements found. "
                                     "This is not proof of safety.")
                        show_transport(d["cas"], d["name"], "Product (recorded)")
            st.caption("Source: text-mined US patent reactions (D. Lowe, 2017). Yields and "
                       "conditions are as extracted from patent text and may contain errors.")
        else:
            st.write("No recorded reaction contains both of these chemicals. Either the "
                     "patent data is not imported, or this pair does not appear in it. "
                     "This does NOT mean they are safe together or that they cannot react.")

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
        with st.expander(f"{chem['name']}  |  {info['formula']}  |  CAS {info['cas']}"):
            st.markdown("**Hazard statements**")
            if info["hazards"]:
                for h in info["hazards"]:
                    st.write("- " + h)
            else:
                st.write("No hazard statements found. This is not proof of safety.")

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
            st.markdown(
                f"- {chem['name']}: "
                f"[PubChem record](https://pubchem.ncbi.nlm.nih.gov/compound/{info['cid']})")

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
