"""Read-only EPA ToxValDB diagnostics for the developer-only app panel."""

import sqlite3
from pathlib import Path


def redact_database_path(database_path, *, app_root=None):
    """Return a useful database location without exposing host/user directories."""
    path = Path(database_path).expanduser().resolve()
    root = Path(app_root or Path(__file__).resolve().parent).expanduser().resolve()
    try:
        return "<APP_ROOT>/" + path.relative_to(root).as_posix()
    except ValueError:
        pass

    home = Path.home().resolve()
    try:
        return "<HOME>/" + path.relative_to(home).as_posix()
    except ValueError:
        return "<REDACTED_PATH>/" + path.name


def collect_epa_diagnostics(database_path, lookup_arguments, lookup_result):
    """Collect counts and release metadata without changing the selected DB."""
    diagnostic = {
        "active_database_path": redact_database_path(database_path),
        "epa_release_version": None,
        "epa_release_status": "not found",
        "total_epa_assertions": None,
        "release_epa_assertions": None,
        "atrazine_cas_identity_matches": None,
        "lookup_assertions_returned": int((lookup_result or {}).get("total", 0)),
        "lookup_records_returned": len((lookup_result or {}).get("records", [])),
        "lookup_match_basis": (lookup_result or {}).get("match_basis"),
        "lookup_arguments": {
            key: lookup_arguments.get(key)
            for key in ("identifier", "casrn", "dtxsid", "limit")
            if key in lookup_arguments
        },
        "database_diagnostic_status": "ok",
    }

    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        diagnostic["database_diagnostic_status"] = "database file not found"
        return diagnostic

    connection = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=5
        )
        connection.row_factory = sqlite3.Row
        release = connection.execute(
            "SELECT d.id, d.release_version, d.status "
            "FROM dataset_release d JOIN dataset_source s ON s.id=d.source_id "
            "WHERE s.source_key=? AND d.release_version=? "
            "ORDER BY d.id DESC LIMIT 1",
            ("epa-toxvaldb", "9.7.0"),
        ).fetchone()
        diagnostic["total_epa_assertions"] = connection.execute(
            "SELECT COUNT(*) FROM epa_toxval_assertions"
        ).fetchone()[0]

        if release:
            release_id = release["id"]
            diagnostic["epa_release_version"] = release["release_version"]
            diagnostic["epa_release_status"] = release["status"]
            diagnostic["release_epa_assertions"] = connection.execute(
                "SELECT COUNT(*) FROM epa_toxval_assertions WHERE release_id=?",
                (release_id,),
            ).fetchone()[0]
            diagnostic["atrazine_cas_identity_matches"] = connection.execute(
                "SELECT COUNT(*) FROM epa_toxval_chemicals "
                "WHERE release_id=? AND casrn=? COLLATE NOCASE "
                "AND name=? COLLATE NOCASE",
                (release_id, "1912-24-9", "Atrazine"),
            ).fetchone()[0]
    except sqlite3.Error as exc:
        # Class names are safe to display; SQLite messages can contain paths.
        diagnostic["database_diagnostic_status"] = type(exc).__name__
    finally:
        if connection is not None:
            connection.close()
    return diagnostic
