"""Safely append a validated ORD staging import to the local application DB."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from migrations import migrate_database, validate_migration_state


_UNCHANGED_TABLES = (
    "chemicals", "chemical_cache", "name_index", "pair_reactions", "pair_products",
)


def _differences(con, left, right, table, where=""):
    clause = f" WHERE {where}" if where else ""
    columns = [row[1] for row in con.execute(f"PRAGMA {left}.table_info('{table}')")]
    projection = ",".join('"' + column + '"' for column in columns)
    forward = con.execute(
        f"SELECT COUNT(*) FROM (SELECT {projection} FROM {left}.\"{table}\"{clause} "
        f"EXCEPT SELECT {projection} FROM {right}.\"{table}\"{clause})"
    ).fetchone()[0]
    reverse = con.execute(
        f"SELECT COUNT(*) FROM (SELECT {projection} FROM {right}.\"{table}\"{clause} "
        f"EXCEPT SELECT {projection} FROM {left}.\"{table}\"{clause})"
    ).fetchone()[0]
    return forward, reverse


def promote(database_path, staging_path, backup_path):
    database_path, staging_path, backup_path = map(
        lambda value: Path(value).resolve(), (database_path, staging_path, backup_path)
    )
    if len({database_path, staging_path, backup_path}) != 3:
        raise ValueError("live, staging, and backup databases must be separate files")
    if not all(path.is_file() for path in (database_path, staging_path, backup_path)):
        raise FileNotFoundError("live, staging, and verified backup database files are required")

    # This migration only adds tables/indexes. It never rebuilds legacy tables.
    migrate_database(database_path)
    validate_migration_state(database_path)
    con = sqlite3.connect(database_path, timeout=120)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("ATTACH DATABASE ? AS stage", (str(staging_path),))
        con.execute("ATTACH DATABASE ? AS safety", (str(backup_path),))

        # Refuse promotion unless the source DB still matches the pre-change backup.
        for table in _UNCHANGED_TABLES:
            if _differences(con, "main", "safety", table) != (0, 0):
                raise RuntimeError(f"Live database differs from backup in {table}")
        if _differences(con, "main", "stage", "rxn_records", "id <= 1083071") != (0, 0):
            raise RuntimeError("Original reaction records in staging no longer match the live DB")
        if _differences(con, "main", "stage", "rxn_molecules", "rxn_id <= 1083071") != (0, 0):
            raise RuntimeError("Original reaction molecules in staging no longer match the live DB")

        for table in ("dataset_source", "dataset_release", "import_run",
                      "ord_reaction_records", "ord_chemical_structures",
                      "ord_reaction_participants"):
            if con.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0]:
                raise RuntimeError(f"Live destination table {table} is not empty; refusing merge")

        if con.execute("SELECT COUNT(*) FROM stage.import_run WHERE status='completed' ").fetchone()[0] != 1:
            raise RuntimeError("Staging DB must contain exactly one completed import run")
        if con.execute("SELECT COUNT(*) FROM stage.ord_reaction_records").fetchone()[0] != 409035:
            raise RuntimeError("Unexpected staged ORD source-row count")

        con.execute("BEGIN IMMEDIATE")
        try:
            for table in ("dataset_source", "dataset_release", "import_run",
                          "ord_chemical_structures", "rxn_records",
                          "ord_reaction_records", "ord_reaction_participants",
                          "rxn_molecules"):
                if table in ("rxn_records", "rxn_molecules"):
                    if table == "rxn_records":
                        con.execute(
                            "INSERT INTO main.rxn_records "
                            "SELECT * FROM stage.rxn_records WHERE source='ORD-mined'"
                        )
                    else:
                        con.execute(
                            "INSERT INTO main.rxn_molecules "
                            "SELECT m.* FROM stage.rxn_molecules m JOIN stage.rxn_records r "
                            "ON r.id=m.rxn_id WHERE r.source='ORD-mined'"
                        )
                else:
                    con.execute(f'INSERT INTO main."{table}" SELECT * FROM stage."{table}"')
            con.commit()
        except Exception:
            con.rollback()
            raise

        for table in _UNCHANGED_TABLES:
            if _differences(con, "main", "safety", table) != (0, 0):
                raise RuntimeError(f"Post-merge preservation check failed for {table}")
        if _differences(con, "main", "safety", "rxn_records", "id <= 1083071") != (0, 0):
            raise RuntimeError("Post-merge original reaction preservation check failed")
        if _differences(con, "main", "safety", "rxn_molecules", "rxn_id <= 1083071") != (0, 0):
            raise RuntimeError("Post-merge original reaction molecule preservation check failed")
        fk_issues = con.execute("PRAGMA main.foreign_key_check").fetchall()
        if fk_issues:
            raise RuntimeError(f"Post-merge foreign key check failed ({len(fk_issues)} issues)")
    finally:
        con.close()

    validate_migration_state(database_path)
    check = sqlite3.connect(database_path)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Post-merge integrity check failed: {integrity}")
        result = {
            table: check.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in ("chemicals", "rxn_records", "rxn_molecules", "ord_reaction_records",
                          "ord_chemical_structures", "ord_reaction_participants")
        }
        result["integrity_check"] = integrity
    finally:
        check.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", default="chem.db")
    parser.add_argument("staging", nargs="?", default="chem_staging.db")
    parser.add_argument("backup")
    args = parser.parse_args()
    print(promote(args.database, args.staging, args.backup))
