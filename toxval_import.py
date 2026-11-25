"""Resumable importer for the normalized EPA ToxValDB MySQL dump.

The input is one official ZIP member: res_toxval_v97_0.sql.gz.  This module
streams the dump; it does not install MySQL or load the full release into RAM.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dataset_imports import (
    commit_import_batch,
    create_or_resume_import_run,
    register_dataset_release,
    register_dataset_source,
    set_import_run_status,
)
from migrations import migrate_database, validate_migration_state


SOURCE_KEY = "epa-toxvaldb"
SOURCE_NAME = "EPA Toxicity Values Database (ToxValDB)"
RELEASE_VERSION = "9.7.0"
SOURCE_URL = "https://www.epa.gov/comptox-tools/downloadable-computational-toxicology-data"
ZENODO_URL = "https://zenodo.org/records/17088058"
ARCHIVE_FILE_URL = (
    "https://zenodo.org/records/17088058/files/"
    "ToxValDB%20v97_0%20Release.zip?download=1"
)
LICENSE_NAME = "CC0 1.0 Universal (EPA open data; no copyright restrictions)"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ATTRIBUTION = "U.S. EPA, ToxValDB v9.7.0; Zenodo record 17088058"
TABLES = {
    "source_chemical", "species", "toxval_type_dictionary", "toxval", "record_source",
}
PHASES = (
    ("identities", ("source_chemical", "species", "toxval_type_dictionary")),
    ("toxicity_records", ("toxval",)),
    ("record_sources", ("record_source",)),
)
EXPECTED_TABLE_COUNTS = {
    "source_chemical": 303381,
    "species": 28761,
    "toxval_type_dictionary": 270,
    "toxval": 249071,
    "record_source": 306057,
}
_INSERT_RE = re.compile(r"^INSERT INTO `([^`]+)` VALUES (.*);\s*$")
_CREATE_RE = re.compile(r"^CREATE TABLE `([^`]+)`")
_COLUMN_RE = re.compile(r"^\s+`([^`]+)`\s+")
_DTXSID_RE = re.compile(r"^DTXSID\d+$", re.IGNORECASE)
_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decode_mysql_string(raw: str) -> str:
    """Decode MySQL dump backslash escapes without Python/SQLite reinterpretation."""
    escapes = {
        "0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t",
        "Z": "\x1a", "\\": "\\", "'": "'", '"': '"',
    }
    result = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            following = raw[index + 1]
            result.append(escapes.get(following, following))
            index += 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def parse_mysql_values(statement: str) -> list[tuple]:
    """Parse the literal VALUES tuples emitted by mysqldump.

    Supports quoted strings, escaped quotes/backslashes, NULLs, numeric values,
    and more than one tuple per INSERT statement.
    """
    marker = statement.find(" VALUES ")
    if marker < 0:
        raise ValueError("INSERT statement has no VALUES clause")
    text = statement[marker + len(" VALUES "):].rstrip().removesuffix(";")
    rows = []
    position = 0
    while position < len(text):
        while position < len(text) and text[position] in " ,\r\n\t":
            position += 1
        if position >= len(text):
            break
        if text[position] != "(":
            raise ValueError("unexpected token before MySQL VALUES tuple")
        position += 1
        values = []
        token = []
        quoted = False
        quote_closed = False
        while position < len(text):
            char = text[position]
            if quoted:
                if char == "\\" and position + 1 < len(text):
                    token.extend((char, text[position + 1]))
                    position += 2
                    continue
                if char == "'":
                    if position + 1 < len(text) and text[position + 1] == "'":
                        token.append("'")
                        position += 2
                        continue
                    quoted = False
                    quote_closed = True
                    position += 1
                    continue
                token.append(char)
                position += 1
                continue

            if char == "'" and not token and not quote_closed:
                quoted = True
                position += 1
                continue
            if char == "," or char == ")":
                raw = "".join(token).strip()
                if quoted:
                    raise ValueError("unterminated SQL string literal")
                if quote_closed:
                    value = _decode_mysql_string("".join(token))
                elif raw.upper() == "NULL":
                    value = None
                elif raw == "":
                    value = ""
                else:
                    try:
                        value = int(raw)
                    except ValueError:
                        try:
                            value = Decimal(raw)
                        except InvalidOperation as exc:
                            raise ValueError(f"unsupported MySQL literal: {raw[:80]}") from exc
                values.append(value)
                token = []
                quote_closed = False
                position += 1
                if char == ")":
                    rows.append(tuple(values))
                    break
                continue
            if quote_closed and char not in " \t\r\n":
                raise ValueError("unexpected data after quoted SQL string")
            token.append(char)
            position += 1
        else:
            raise ValueError("unterminated MySQL VALUES tuple")
    return rows


def _columns_by_table(sql_path: Path) -> dict[str, list[str]]:
    columns = {table: [] for table in TABLES}
    current = None
    with gzip.open(sql_path, "rt", encoding="utf-8", errors="strict") as source:
        for line in source:
            if current:
                match = _COLUMN_RE.match(line)
                if match:
                    columns[current].append(match.group(1))
                elif line.strip().startswith(") ENGINE="):
                    current = None
                continue
            match = _CREATE_RE.match(line)
            if match and match.group(1) in TABLES:
                current = match.group(1)
    missing = [table for table, names in columns.items() if not names]
    if missing:
        raise ValueError("ToxValDB dump lacks table definitions: " + ", ".join(missing))
    return columns


def _iter_table_rows(sql_path: Path, table: str, columns: list[str]):
    count = 0
    with gzip.open(sql_path, "rt", encoding="utf-8", errors="strict") as source:
        for line_number, line in enumerate(source, start=1):
            match = _INSERT_RE.match(line)
            if not match or match.group(1) != table:
                continue
            rows = parse_mysql_values(line.rstrip("\r\n"))
            for row in rows:
                if len(row) != len(columns):
                    raise ValueError(
                        f"{table} insert at line {line_number} has {len(row)} values; "
                        f"expected {len(columns)}"
                    )
                count += 1
                yield count, dict(zip(columns, row))


def _json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _meaningful(value):
    if value is None:
        return None
    text = str(value).strip()
    return None if text in ("", "-") else text


def _number(value):
    if value is None or str(value).strip() in ("", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _local_identity_match(con: sqlite3.Connection, casrn, name):
    casrn = _meaningful(casrn)
    name = _meaningful(name)
    if casrn:
        row = con.execute("SELECT cid FROM chemical_cache WHERE cas=? LIMIT 1", (casrn,)).fetchone()
        if row:
            return "pubchem_cas", str(row[0])
        row = con.execute("SELECT cas FROM chemicals WHERE cas=? LIMIT 1", (casrn,)).fetchone()
        if row:
            return "curated_cas", str(row[0])
    if name:
        row = con.execute(
            "SELECT cid FROM chemical_cache WHERE query_name=? COLLATE NOCASE LIMIT 1", (name,)
        ).fetchone()
        if row:
            return "pubchem_name", str(row[0])
        row = con.execute("SELECT cas FROM chemicals WHERE name=? COLLATE NOCASE LIMIT 1", (name,)).fetchone()
        if row:
            return "curated_name", str(row[0])
    return "unmatched", None


def _row_insert(table: str, columns: list[str], row: dict) -> tuple:
    return tuple(row.get(column) for column in columns)


def _identity_tuple(release_id, record, match_type, match_value):
    fields = (
        "source_chemical_id", "source", "raw_casrn", "raw_name", "cleaned_casrn",
        "cleaned_name", "casrn", "name", "dtxsid", "dtxrid", "quality", "flags",
        "parent_chemical_id", "chemical_id_rehash_orig",
    )
    return (release_id, str(record["chemical_id"]),
            *(record.get(field) for field in fields[1:]), match_type, match_value, _json(record))


def _species_tuple(release_id, row):
    keys = ("common_name", "latin_name", "kingdom", "phylum_division", "subphylum_div",
            "superclass", "tax_order", "family", "genus", "species", "subspecies",
            "variety", "ecotox_group", "habitat")
    return (release_id, int(row["species_id"]), *(row.get(key) for key in keys), _json(row))


def _type_tuple(release_id, row):
    keys = ("toxval_type", "display", "toxval_type_category", "toxval_type_supercategory",
            "fullname", "abbreviation", "definition", "definition_source", "units_default",
            "responsible_organization")
    return (release_id, int(row["toxval_type_dictionary_id"]), *(row.get(key) for key in keys), _json(row))


def _assertion_tuple(release_id, row, chemical_map, species_map, type_map):
    source_chemical_id = _meaningful(row.get("chemical_id"))
    chemical_identity = chemical_map.get(source_chemical_id) if source_chemical_id else None
    chemical_id = chemical_identity[0] if chemical_identity else None
    species_id = row.get("species_id")
    try:
        species = species_map.get(int(species_id)) if species_id is not None else None
    except (ValueError, TypeError):
        species = None
    toxval_type = _meaningful(row.get("toxval_type"))
    type_info = type_map.get(toxval_type, (None, None))
    # chemical_id always links to the EPA source identity when present. The
    # status separately reports whether that identity matched an existing
    # local PubChem-cache/curated identity by exact CASRN or exact name.
    status = ("matched" if chemical_identity and chemical_identity[1] != "unmatched"
              else "unmatched")
    values = [
        release_id, int(row["toxval_id"]), row.get("source_hash"), row.get("source_table"),
        row.get("chemical_id"), chemical_id, status, row.get("dtxsid"), row.get("source"),
        row.get("subsource"), row.get("source_url"), row.get("subsource_url"),
        row.get("details_text"), row.get("qc_status"), row.get("qc_category"),
        row.get("risk_assessment_class"), row.get("human_eco"), row.get("experimental_record"),
        row.get("toxval_type"), row.get("toxval_type_original"), row.get("toxval_subtype"),
        row.get("toxval_subtype_original"), type_info[0], type_info[1],
        _number(row.get("toxval_numeric")),
        None if row.get("toxval_numeric") is None else str(row.get("toxval_numeric")),
        _number(row.get("toxval_numeric_original")),
        None if row.get("toxval_numeric_original") is None else str(row.get("toxval_numeric_original")),
        row.get("toxval_units"), row.get("toxval_units_original"),
        row.get("toxval_numeric_qualifier"), row.get("toxval_numeric_qualifier_original"),
        row.get("study_type"), row.get("study_type_original"),
        row.get("study_duration_class"), row.get("study_duration_class_original"),
        _number(row.get("study_duration_value")), row.get("study_duration_value_original"),
        row.get("study_duration_units"), row.get("study_duration_units_original"),
        int(species_id) if species_id is not None else None, row.get("species_original"),
        species[0] if species else None, species[1] if species else None,
        species[2] if species else None, row.get("strain"), row.get("strain_original"),
        row.get("strain_group"), row.get("habitat"), row.get("sex"), row.get("sex_original"),
        row.get("toxicological_effect"), row.get("toxicological_effect_original"),
        row.get("population"), row.get("population_original"), row.get("exposure_route"),
        row.get("exposure_route_original"), row.get("exposure_method"),
        row.get("exposure_method_original"), row.get("exposure_form"),
        row.get("exposure_form_original"), row.get("media"), row.get("media_original"),
        row.get("lifestage"), row.get("lifestage_original"), row.get("generation"),
        row.get("generation_original"), row.get("year"), row.get("year_original"),
        _number(row.get("mw")), row.get("datestamp"), _number(row.get("source_source_id")),
        row.get("toxval_uuid"), row.get("toxval_hash"), row.get("study_group"),
        _number(row.get("visible")), row.get("key_finding"), row.get("supersource"), _json(row),
    ]
    return tuple(values)


def _reference_tuple(release_id, row):
    keys = (
        "toxval_id", "record_source_id", "record_source_hash", "record_source_uuid", "source",
        "record_source_type", "record_source_note", "record_source_level", "url",
        "document_name", "long_ref", "title", "author", "journal", "volume", "year",
        "issue", "page", "doi", "pmid", "guideline", "glp", "quality", "source_study_id",
        "clowder_doc_id", "clowder_doc_metadata", "priority", "external_source_id",
        "external_source_id_desc",
    )
    return (release_id, int(row["toxval_id"]), int(row["record_source_id"]),
            *(row.get(key) for key in keys[2:]), _json(row))


def _table_insert(connection, table, columns, rows):
    if not rows:
        return
    placeholders = ",".join("?" for _ in columns)
    names = ",".join('"' + column + '"' for column in columns)
    connection.executemany(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', rows
    )


def _dict_json_default(value):
    return str(value)


def _ensure_dump_columns(sql_path: Path) -> dict[str, list[str]]:
    return _columns_by_table(sql_path)


def _resolve_local_maps(database_path, release_id):
    con = sqlite3.connect(database_path)
    try:
        chemicals = {
            key: (int(value), match_type) for value, key, match_type in con.execute(
                "SELECT id,source_chemical_id,local_match_type "
                "FROM epa_toxval_chemicals WHERE release_id=?",
                (release_id,),
            )
        }
        species = {
            int(source_id): (common, latin, eco)
            for source_id, common, latin, eco in con.execute(
                "SELECT source_species_id,common_name,latin_name,ecotox_group "
                "FROM epa_toxval_species WHERE release_id=?", (release_id,),
            )
        }
        types = {}
        for toxval_type, supercategory, definition in con.execute(
            "SELECT toxval_type,toxval_type_supercategory,definition "
            "FROM epa_toxval_types WHERE release_id=?", (release_id,),
        ):
            if toxval_type and toxval_type not in types:
                types[toxval_type] = (supercategory, definition)
        return chemicals, species, types
    finally:
        con.close()


def import_toxval_dump(sql_dump_path, database_path, dictionary_path, *, batch_size=1000,
                       expected_counts=None):
    """Import the exact normalized dump in checkpointed additive batches.

    ``expected_counts`` can be supplied by tests with a tiny fixture. Production
    defaults are the official v9.7.0 row counts observed in the release dump.
    """
    sql_dump_path = Path(sql_dump_path).resolve()
    dictionary_path = Path(dictionary_path).resolve()
    database_path = Path(database_path).resolve()
    if not sql_dump_path.is_file() or not dictionary_path.is_file():
        raise FileNotFoundError("the verified SQL dump member and field dictionary are required")
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    expected_counts = dict(expected_counts or EXPECTED_TABLE_COUNTS)
    columns = _ensure_dump_columns(sql_dump_path)
    dump_checksum = _sha256(sql_dump_path)
    dictionary_checksum = _sha256(dictionary_path)

    migrate_database(database_path)
    validate_migration_state(database_path)
    con = sqlite3.connect(database_path)
    try:
        existing = con.execute(
            "SELECT retrieved_at FROM dataset_release WHERE release_key=?",
            (f"toxvaldb:{RELEASE_VERSION}",),
        ).fetchone()
    finally:
        con.close()
    from datetime import datetime, timezone
    retrieved_at = (existing[0] if existing else
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))

    source = register_dataset_source(
        database_path, source_key=SOURCE_KEY, name=SOURCE_NAME,
        publisher="U.S. Environmental Protection Agency, Center for Computational Toxicology and Exposure",
        canonical_url=SOURCE_URL, api_url=ZENODO_URL, license_name=LICENSE_NAME,
        license_url=LICENSE_URL,
        license_terms="EPA states CompTox downloadable data are open and free of copyright restrictions.",
        attribution=ATTRIBUTION,
        description="EPA normalized in vivo toxicity values and risk assessment values.",
    )
    release = register_dataset_release(
        database_path, source_id=source["id"],
        release_key=f"toxvaldb:{RELEASE_VERSION}", release_version=RELEASE_VERSION,
        release_date="2025-09-24", source_url=ZENODO_URL, manifest_url=SOURCE_URL,
        retrieved_at=retrieved_at, license_name=LICENSE_NAME, license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
        artifact_name="ToxValDB v97_0 Release.zip::Data MySQL Dump File/res_toxval_v97_0.sql.gz",
        artifact_size_bytes=sql_dump_path.stat().st_size, checksum_algorithm="sha256",
        content_checksum=dump_checksum, manifest_checksum=dictionary_checksum,
        source_row_count=expected_counts["toxval"],
    )
    run_key = f"{RELEASE_VERSION}:{dump_checksum}"
    run = create_or_resume_import_run(
        database_path, release_id=release["id"], idempotency_key=run_key,
        source_row_count=sum(expected_counts.get(table, 0) for table in (
            "source_chemical", "species", "toxval_type_dictionary", "toxval", "record_source"
        )),
    )
    run_id = run["id"]
    if run["status"] == "completed":
        return {
            "release_id": release["id"], "import_run_id": run_id,
            "counts": _imported_counts(database_path, release["id"]),
            "sha256": dump_checksum, "resumed": False,
        }

    checkpoint = json.loads(run["checkpoint_json"] or "{}")
    run_counts = {field: int(run[field]) for field in (
        "processed_row_count", "inserted_row_count", "updated_row_count",
        "rejected_row_count", "skipped_row_count",
    )}
    phase_names = [phase[0] for phase in PHASES]
    phase_index = phase_names.index(checkpoint.get("phase", phase_names[0]))
    starting_rows = checkpoint.get("next_rows", {}) if checkpoint else {}
    batch_rows: dict[str, list[tuple]] = {}
    current_phase_rows: dict[str, int] = {}
    counts_by_table = {name: 0 for name in expected_counts}
    if checkpoint:
        # Counts from prior committed phases are authoritative for resumed runs.
        with sqlite3.connect(database_path) as con:
            for table, sql_table in (
                ("source_chemical", "epa_toxval_chemicals"),
                ("species", "epa_toxval_species"),
                ("toxval_type_dictionary", "epa_toxval_types"),
                ("toxval", "epa_toxval_assertions"),
                ("record_source", "epa_toxval_record_sources"),
            ):
                counts_by_table[table] = con.execute(
                    f'SELECT COUNT(*) FROM "{sql_table}" WHERE release_id=?',
                    (release["id"],),
                ).fetchone()[0]

    def make_tuple(table, row):
        if table == "source_chemical":
            match_type, match_value = _local_identity_match(con_local, row.get("casrn"), row.get("name"))
            return _identity_tuple(release["id"], row, match_type, match_value)
        if table == "species":
            return _species_tuple(release["id"], row)
        if table == "toxval_type_dictionary":
            return _type_tuple(release["id"], row)
        if table == "toxval":
            return _assertion_tuple(release["id"], row, chemical_map, species_map, type_map)
        if table == "record_source":
            return _reference_tuple(release["id"], row)
        raise ValueError(f"unsupported ToxValDB table: {table}")

    def flush(next_checkpoint):
        rows_to_write = {name: rows for name, rows in batch_rows.items() if rows}
        if not rows_to_write and json.dumps(next_checkpoint, sort_keys=True,
                                            separators=(",", ":")) == json.dumps(
                                                last_checkpoint, sort_keys=True,
                                                separators=(",", ":")):
            return

        def write_batch(connection):
            for table, rows in rows_to_write.items():
                _table_insert(connection, TABLE_DESTINATIONS[table], TABLE_COLUMNS[table], rows)

        commit_import_batch(
            database_path, run_id=run_id, expected_checkpoint=last_checkpoint,
            next_checkpoint=next_checkpoint, counts=run_counts, write_batch=write_batch,
        )
        last_checkpoint.clear()
        # A checkpoint's ``next_rows`` often reuses current_phase_rows. Store a
        # detached copy so later parser progress cannot mutate the expected
        # checkpoint in memory before SQLite commits it.
        last_checkpoint.update(json.loads(json.dumps(next_checkpoint)))
        batch_rows.clear()

    TABLE_DESTINATIONS = {
        "source_chemical": "epa_toxval_chemicals",
        "species": "epa_toxval_species",
        "toxval_type_dictionary": "epa_toxval_types",
        "toxval": "epa_toxval_assertions",
        "record_source": "epa_toxval_record_sources",
    }
    TABLE_COLUMNS = {
        "source_chemical": ["release_id", "source_chemical_id", "source", "raw_casrn",
            "raw_name", "cleaned_casrn", "cleaned_name", "casrn", "name", "dtxsid",
            "dtxrid", "quality", "flags", "parent_chemical_id", "chemical_id_rehash_orig",
            "local_match_type", "local_match_value", "raw_record_json"],
        "species": ["release_id", "source_species_id", "common_name", "latin_name", "kingdom",
            "phylum_division", "subphylum_div", "superclass", "tax_order", "family", "genus",
            "species", "subspecies", "variety", "ecotox_group", "habitat", "raw_record_json"],
        "toxval_type_dictionary": ["release_id", "source_type_id", "toxval_type", "display",
            "toxval_type_category", "toxval_type_supercategory", "fullname", "abbreviation",
            "definition", "definition_source", "units_default", "responsible_organization",
            "raw_record_json"],
        "toxval": ["release_id", "toxval_id", "source_hash", "source_table", "chemical_source_id",
            "chemical_id", "identity_match_status", "dtxsid", "source", "subsource", "source_url",
            "subsource_url", "details_text", "qc_status", "qc_category", "risk_assessment_class",
            "human_eco", "experimental_record", "toxval_type", "toxval_type_original",
            "toxval_subtype", "toxval_subtype_original", "toxval_type_supercategory",
            "toxval_type_definition", "toxval_numeric", "toxval_numeric_raw",
            "toxval_numeric_original", "toxval_numeric_original_raw", "toxval_units",
            "toxval_units_original", "toxval_numeric_qualifier", "toxval_numeric_qualifier_original",
            "study_type", "study_type_original", "study_duration_class",
            "study_duration_class_original", "study_duration_value", "study_duration_value_original",
            "study_duration_units", "study_duration_units_original", "species_id", "species_original",
            "species_common", "species_latin", "species_supercategory", "strain", "strain_original",
            "strain_group", "habitat", "sex", "sex_original", "toxicological_effect",
            "toxicological_effect_original", "population", "population_original", "exposure_route",
            "exposure_route_original", "exposure_method", "exposure_method_original", "exposure_form",
            "exposure_form_original", "media", "media_original", "lifestage", "lifestage_original",
            "generation", "generation_original", "year", "year_original", "molecular_weight",
            "datestamp", "source_source_id", "toxval_uuid", "toxval_hash", "study_group", "visible",
            "key_finding", "supersource", "raw_record_json"],
        "record_source": ["release_id", "toxval_id", "record_source_id", "record_source_hash",
            "record_source_uuid", "source", "record_source_type", "record_source_note",
            "record_source_level", "url", "document_name", "long_ref", "title", "author", "journal",
            "volume", "year", "issue", "page", "doi", "pmid", "guideline", "glp", "quality",
            "source_study_id", "clowder_doc_id", "clowder_doc_metadata", "priority",
            "external_source_id", "external_source_id_desc", "raw_record_json"],
    }

    last_checkpoint = dict(checkpoint)
    # A small indexed mapping avoids keeping any toxicity values or references in RAM.
    con_local = sqlite3.connect(database_path)
    con_local.execute("PRAGMA query_only=ON")
    try:
        for phase_pos in range(phase_index, len(PHASES)):
            phase_name, tables = PHASES[phase_pos]
            if phase_name == "toxicity_records":
                # The identities/species/type dictionaries are now complete
                # (or were completed by a previous checkpoint). Resolve the
                # source-key maps once for this phase, not per assertion.
                chemical_map, species_map, type_map = _resolve_local_maps(
                    database_path, release["id"]
                )
            if phase_pos == phase_index and checkpoint.get("phase") not in (None, phase_name):
                raise ValueError("import checkpoint phase is inconsistent")
            current_phase_rows = dict(starting_rows) if phase_pos == phase_index else {}
            phase_seen = {table: 0 for table in tables}
            for table in tables:
                resume_at = int(current_phase_rows.get(table, 0))
                for row_number, source_row in _iter_table_rows(sql_dump_path, table, columns[table]):
                    phase_seen[table] = row_number
                    if row_number <= resume_at:
                        continue
                    batch_rows.setdefault(table, []).append(make_tuple(table, source_row))
                    run_counts["processed_row_count"] += 1
                    run_counts["inserted_row_count"] += 1
                    counts_by_table[table] += 1
                    if sum(map(len, batch_rows.values())) >= batch_size:
                        next_rows = dict(current_phase_rows)
                        next_rows[table] = row_number
                        last_checkpoint = dict(last_checkpoint)
                        flush({"phase": phase_name, "next_rows": next_rows})
                        current_phase_rows = next_rows
                        print(f"ToxValDB import: {phase_name}/{table} {row_number:,}; "
                              f"inserted={run_counts['inserted_row_count']:,}", flush=True)
                expected = expected_counts.get(table)
                if expected is not None and phase_seen[table] != expected:
                    raise ValueError(
                        f"ToxValDB {table} row count {phase_seen[table]} does not match expected {expected}"
                    )
                current_phase_rows[table] = phase_seen[table]
            next_phase = PHASES[phase_pos + 1][0] if phase_pos + 1 < len(PHASES) else "complete"
            last_checkpoint = dict(last_checkpoint)
            flush({"phase": next_phase, "next_rows": {}})
            starting_rows = {}
        if counts_by_table["toxval"] == 0 and run["processed_row_count"] == 0:
            raise ValueError("No toxicity assertions were imported")
        final = _imported_counts(database_path, release["id"])
        if final["epa_toxval_assertions"] != expected_counts.get("toxval"):
            raise ValueError("Imported toxicity assertion count does not match the release count")
        with sqlite3.connect(database_path) as con:
            con.execute(
                "UPDATE dataset_release SET imported_row_count=?, rejected_row_count=0, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (final["epa_toxval_assertions"], release["id"]),
            )
            con.commit()
        set_import_run_status(database_path, run_id=run_id, status="completed")
    except Exception as exc:
        try:
            set_import_run_status(database_path, run_id=run_id, status="failed",
                                  safe_error_summary=type(exc).__name__)
        except Exception:
            pass
        raise
    finally:
        con_local.close()

    return {
        "release_id": release["id"], "import_run_id": run_id,
        "counts": _imported_counts(database_path, release["id"]),
        "sha256": dump_checksum, "dictionary_sha256": dictionary_checksum,
        "resumed": bool(checkpoint),
    }


def _imported_counts(database_path, release_id):
    tables = {
        "epa_toxval_chemicals": "epa_toxval_chemicals",
        "epa_toxval_species": "epa_toxval_species",
        "epa_toxval_types": "epa_toxval_types",
        "epa_toxval_assertions": "epa_toxval_assertions",
        "epa_toxval_record_sources": "epa_toxval_record_sources",
    }
    with sqlite3.connect(database_path) as con:
        return {
            table: con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE release_id=?',
                               (release_id,)).fetchone()[0]
            for table in tables.values()
        }
