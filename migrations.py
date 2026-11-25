"""Transactional, versioned, additive SQLite migrations.

The legacy ``migrate_to_db.py`` initializer is intentionally not used here: it
refreshes core tables. This module applies only numbered additive migrations.
"""
from __future__ import annotations

import hashlib
import argparse
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "chem.db"
_MIGRATION_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
_EXPECTED_TABLES = {
    "schema_migrations": {"version", "name", "checksum", "applied_at"},
    "dataset_source": {
        "id", "source_key", "name", "publisher", "canonical_url", "api_url",
        "license_name", "license_url", "license_terms", "attribution", "description",
        "created_at", "updated_at",
    },
    "dataset_release": {
        "id", "source_id", "release_key", "release_version", "release_date",
        "source_url", "manifest_url", "retrieved_at", "license_name", "license_url",
        "attribution", "artifact_name", "artifact_size_bytes", "checksum_algorithm",
        "content_checksum", "manifest_checksum", "source_row_count", "imported_row_count",
        "rejected_row_count", "status", "created_at", "updated_at",
    },
    "import_run": {
        "id", "release_id", "idempotency_key", "status", "source_row_count",
        "processed_row_count", "inserted_row_count", "updated_row_count",
        "rejected_row_count", "skipped_row_count", "checkpoint_json", "error_summary",
        "started_at", "updated_at", "finished_at",
    },
    "ord_reaction_records": {
        "id", "release_id", "dataset_id", "dataset_name", "dataset_description",
        "source_record_id", "source_doi", "is_mined", "reaction_smiles", "identity_key",
        "identity_status", "rxn_id", "raw_proto", "provenance_json", "imported_at",
    },
    "ord_chemical_structures": {
        "id", "canonical_smiles", "representative_raw_smiles", "inchi_key",
        "molecular_formula", "molecular_weight", "calculated_by", "first_release_id",
        "first_source_record_id", "created_at",
    },
    "ord_reaction_participants": {
        "id", "ord_record_id", "structure_id", "side", "source_role", "participant_index",
        "identifier_type", "identifier_value", "identifier_details", "raw_component",
        "standardized_smiles",
    },
    "epa_toxval_chemicals": {
        "id", "release_id", "source_chemical_id", "casrn", "name", "dtxsid",
        "local_match_type", "local_match_value", "raw_record_json",
    },
    "epa_toxval_species": {
        "release_id", "source_species_id", "common_name", "latin_name", "ecotox_group",
        "raw_record_json",
    },
    "epa_toxval_types": {
        "release_id", "source_type_id", "toxval_type", "toxval_type_supercategory",
        "definition", "raw_record_json",
    },
    "epa_toxval_assertions": {
        "id", "release_id", "toxval_id", "source_hash", "chemical_source_id",
        "chemical_id", "identity_match_status", "dtxsid", "source", "qc_status",
        "experimental_record", "toxval_type", "toxval_numeric", "toxval_numeric_raw",
        "toxval_units", "toxval_numeric_qualifier", "study_type", "species_id",
        "species_common", "species_latin", "exposure_route", "toxicological_effect",
        "raw_record_json",
    },
    "epa_toxval_record_sources": {
        "id", "release_id", "toxval_id", "record_source_id", "source", "url",
        "long_ref", "title", "author", "year", "doi", "guideline",
        "external_source_id", "raw_record_json",
    },
}


class MigrationError(RuntimeError):
    """The schema is not in a safe state for the requested migration."""


def _migration_files():
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if not match:
            raise MigrationError(f"Unexpected migration filename: {path.name}")
        version = int(match.group(1))
        sql = path.read_text(encoding="utf-8")
        files.append({
            "version": version,
            "name": match.group(2),
            "path": path,
            "sql": sql,
            "checksum": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        })
    versions = [item["version"] for item in files]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("Migration versions must be sequential starting at 001")
    return files


def _statements(sql):
    """Split the current DDL-only migration files without executescript commits."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement and not all(
                not part.strip() or part.lstrip().startswith("--")
                for part in statement.splitlines()
            ):
                yield statement
    if buffer.strip():
        raise MigrationError("Migration SQL ends with an incomplete statement")


def _connect_readonly(db_path):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise MigrationError(f"Database does not exist: {path}")
    uri_path = quote(path.as_posix(), safe="/:\\")
    con = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _table_names(con):
    return {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _unique_column_sets(con, table):
    result = set()
    primary_key = [row[1] for row in sorted(
        (row for row in con.execute(f'PRAGMA table_info("{table}")') if row[5]),
        key=lambda row: row[5],
    )]
    if primary_key:
        result.add(tuple(primary_key))
    for index in con.execute(f'PRAGMA index_list("{table}")'):
        if index[2]:
            columns = tuple(row[2] for row in con.execute(f'PRAGMA index_info("{index[1]}")'))
            result.add(columns)
    return result


def _validate_applied_schema(con):
    names = _table_names(con)
    missing = set(_EXPECTED_TABLES) - names
    if missing:
        raise MigrationError("Applied migration is missing tables: " + ", ".join(sorted(missing)))
    for table, expected in _EXPECTED_TABLES.items():
        actual = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}
        absent = expected - actual
        if absent:
            raise MigrationError(
                f"Applied migration table {table} is missing columns: " + ", ".join(sorted(absent))
            )
    indexes = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    expected_indexes = {
        "idx_dataset_release_source_status", "idx_import_run_release_status",
        "idx_ord_reaction_identity", "idx_ord_reaction_record_rxn",
        "idx_ord_participant_structure", "idx_ord_participant_smiles",
        "idx_epa_toxval_chemical_dtxsid", "idx_epa_toxval_chemical_casrn",
        "idx_epa_toxval_chemical_name", "idx_epa_toxval_chemical_local_match",
        "idx_epa_toxval_assertion_identity", "idx_epa_toxval_assertion_endpoint",
        "idx_epa_toxval_assertion_source_id", "idx_epa_toxval_assertion_identity_fk",
        "idx_epa_toxval_record_sources_toxval", "idx_epa_toxval_record_sources_doi",
    }
    expected_indexes.add("idx_import_run_one_active_release")
    if expected_indexes - indexes:
        raise MigrationError("Applied migration is missing required indexes")
    foreign_keys = {
        (row[2], row[3], row[4])
        for table in (
            "dataset_release", "import_run", "ord_reaction_records",
            "ord_chemical_structures", "ord_reaction_participants",
            "epa_toxval_chemicals", "epa_toxval_species", "epa_toxval_types",
            "epa_toxval_assertions", "epa_toxval_record_sources",
        )
        for row in con.execute(f'PRAGMA foreign_key_list("{table}")')
    }
    if not {
        ("dataset_source", "source_id", "id"),
        ("dataset_release", "release_id", "id"),
        ("dataset_release", "first_release_id", "id"),
        ("rxn_records", "rxn_id", "id"),
        ("ord_reaction_records", "ord_record_id", "id"),
        ("ord_chemical_structures", "structure_id", "id"),
        ("dataset_release", "release_id", "id"),
        ("epa_toxval_chemicals", "chemical_id", "id"),
        ("epa_toxval_assertions", "release_id", "release_id"),
        ("epa_toxval_assertions", "toxval_id", "toxval_id"),
    }.issubset(foreign_keys):
        raise MigrationError("Applied migration has missing provenance foreign keys")
    expected_unique = {
        "schema_migrations": {("version",), ("name",)},
        "dataset_source": {("source_key",)},
        "dataset_release": {("release_key",), ("source_id", "release_version")},
        "import_run": {("release_id", "idempotency_key")},
        "ord_reaction_records": {("release_id", "source_record_id")},
        "ord_chemical_structures": {("canonical_smiles",)},
        "ord_reaction_participants": {("ord_record_id", "side", "participant_index")},
        "epa_toxval_chemicals": {("release_id", "source_chemical_id")},
        "epa_toxval_species": {("release_id", "source_species_id")},
        "epa_toxval_types": {("release_id", "source_type_id")},
        "epa_toxval_assertions": {("release_id", "toxval_id")},
        "epa_toxval_record_sources": {("release_id", "record_source_id")},
    }
    for table, required in expected_unique.items():
        if not required.issubset(_unique_column_sets(con, table)):
            raise MigrationError(f"Applied migration has missing unique constraints on {table}")


def _plan_on_connection(con, migration_files):
    names = _table_names(con)
    applied = {}
    if "schema_migrations" in names:
        columns = {row[1] for row in con.execute('PRAGMA table_info("schema_migrations")')}
        if not _EXPECTED_TABLES["schema_migrations"].issubset(columns):
            raise MigrationError("Existing schema_migrations table has an incompatible schema")
        if not {("version",), ("name",)}.issubset(_unique_column_sets(con, "schema_migrations")):
            raise MigrationError("Existing schema_migrations table lacks required unique constraints")
        applied = {
            row["version"]: dict(row)
            for row in con.execute("SELECT version, name, checksum, applied_at FROM schema_migrations")
        }
    known = {item["version"]: item for item in migration_files}
    unknown = set(applied) - set(known)
    if unknown:
        raise MigrationError("Database contains migration versions absent from this project")
    for version, record in applied.items():
        migration = known[version]
        if record["name"] != migration["name"] or record["checksum"] != migration["checksum"]:
            raise MigrationError(f"Applied migration {version:03d} differs from its recorded checksum")
    pending = [item for item in migration_files if item["version"] not in applied]
    if pending:
        pending_tables = set()
        for migration in pending:
            for statement in _statements(migration["sql"]):
                match = re.match(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?([A-Za-z_][A-Za-z0-9_]*)"?',
                                 statement, re.IGNORECASE)
                if match:
                    pending_tables.add(match.group(1))
        collisions = pending_tables & names
        if collisions:
            raise MigrationError(
                "Untracked tables conflict with additive migration names: " + ", ".join(sorted(collisions))
            )
    if applied and not pending:
        _validate_applied_schema(con)
    return {
        "applied_versions": sorted(applied),
        "pending_versions": [item["version"] for item in pending],
        "pending_names": [item["name"] for item in pending],
    }


def migration_status(db_path):
    """Read-only dry-run plan; does not create or modify any file/database state."""
    migration_files = _migration_files()
    # Also validate syntax in a disposable in-memory database.
    memory = sqlite3.connect(":memory:")
    try:
        memory.execute("BEGIN IMMEDIATE")
        for migration in migration_files:
            for statement in _statements(migration["sql"]):
                memory.execute(statement)
        memory.rollback()
    except sqlite3.Error as exc:
        memory.rollback()
        raise MigrationError(f"Migration SQL validation failed: {type(exc).__name__}") from exc
    finally:
        memory.close()
    con = _connect_readonly(db_path)
    try:
        return _plan_on_connection(con, migration_files)
    finally:
        con.close()


def migrate_database(db_path, dry_run=False):
    """Apply pending additive migrations atomically, or return a read-only plan."""
    if dry_run:
        return migration_status(db_path)
    migration_files = _migration_files()
    path = Path(db_path).resolve()
    if not path.is_file():
        raise MigrationError(f"Database does not exist: {path}")
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        plan = _plan_on_connection(con, migration_files)
        for migration in migration_files:
            if migration["version"] not in plan["pending_versions"]:
                continue
            for statement in _statements(migration["sql"]):
                con.execute(statement)
            con.execute(
                "INSERT INTO schema_migrations(version, name, checksum) VALUES (?,?,?)",
                (migration["version"], migration["name"], migration["checksum"]),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return migration_status(path)


def validate_migration_state(db_path):
    """Read-only validation of the installed migration registry and schema."""
    plan = migration_status(db_path)
    if plan["pending_versions"]:
        raise MigrationError("Pending migrations remain: " + ", ".join(map(str, plan["pending_versions"])))
    con = _connect_readonly(db_path)
    try:
        _validate_applied_schema(con)
        return {"valid": True, **plan}
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate or apply additive SQLite migrations")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--dry-run", action="store_true", help="show pending migrations without writes (default)")
    actions.add_argument("--validate", action="store_true", help="validate the applied migration state read-only")
    actions.add_argument("--apply", action="store_true", help="apply pending additive migrations")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    args = parser.parse_args(argv)
    if args.validate:
        result = validate_migration_state(args.db)
    else:
        result = migrate_database(args.db, dry_run=not args.apply)
    print("Migration state:", result)


if __name__ == "__main__":
    main()
