"""Small transactional helpers for future source-aware dataset importers.

These helpers do not download data or import chemistry records. A future
importer's data writes and checkpoint update must share ``commit_import_batch``
so retrying after interruption cannot advance the checkpoint without its rows.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ImportStateError(RuntimeError):
    """The requested import state transition would be unsafe or ambiguous."""


class RegistryConflictError(ImportStateError):
    """A stable source/release key was reused with different metadata."""


def _connect(db_path):
    con = sqlite3.connect(Path(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def register_dataset_source(db_path, *, source_key, name, publisher, canonical_url,
                            api_url=None, license_name=None, license_url=None,
                            license_terms=None, attribution=None, description=None):
    """Register immutable source metadata; repeat calls with identical data are safe."""
    source_key = _nonempty(source_key, "source_key").lower()
    values = {
        "source_key": source_key,
        "name": _nonempty(name, "name"),
        "publisher": _nonempty(publisher, "publisher"),
        "canonical_url": _nonempty(canonical_url, "canonical_url"),
        "api_url": api_url,
        "license_name": license_name,
        "license_url": license_url,
        "license_terms": license_terms,
        "attribution": attribution,
        "description": description,
    }
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM dataset_source WHERE source_key=? COLLATE NOCASE", (source_key,)
        ).fetchone()
        if row is None:
            cursor = con.execute(
                "INSERT INTO dataset_source "
                "(source_key,name,publisher,canonical_url,api_url,license_name,license_url,"
                "license_terms,attribution,description) VALUES (?,?,?,?,?,?,?,?,?,?)",
                tuple(values.values()),
            )
            source_id, created = cursor.lastrowid, True
        else:
            differing = [key for key, value in values.items() if row[key] != value]
            if differing:
                raise RegistryConflictError(
                    "dataset_source key already exists with different metadata: "
                    + ", ".join(differing)
                )
            source_id, created = row["id"], False
        con.commit()
        return {"id": source_id, "created": created}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def register_dataset_release(db_path, *, source_id, release_key, release_version,
                             source_url, retrieved_at, release_date=None,
                             manifest_url=None, license_name=None, license_url=None,
                             attribution=None, artifact_name=None,
                             artifact_size_bytes=None, checksum_algorithm="sha256",
                             content_checksum=None, manifest_checksum=None,
                             source_row_count=None):
    """Register an immutable release descriptor, idempotent by release key."""
    release_key = _nonempty(release_key, "release_key")
    release_version = _nonempty(release_version, "release_version")
    values = {
        "source_id": int(source_id),
        "release_key": release_key,
        "release_version": release_version,
        "release_date": release_date,
        "source_url": _nonempty(source_url, "source_url"),
        "manifest_url": manifest_url,
        "retrieved_at": _nonempty(retrieved_at, "retrieved_at"),
        "license_name": license_name,
        "license_url": license_url,
        "attribution": attribution,
        "artifact_name": artifact_name,
        "artifact_size_bytes": artifact_size_bytes,
        "checksum_algorithm": _nonempty(checksum_algorithm, "checksum_algorithm").lower(),
        "content_checksum": content_checksum,
        "manifest_checksum": manifest_checksum,
        "source_row_count": source_row_count,
    }
    if values["checksum_algorithm"] != "sha256":
        raise ValueError("This migration currently accepts SHA-256 checksums only")
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM dataset_release WHERE release_key=?", (release_key,)
        ).fetchone()
        if row is None:
            cursor = con.execute(
                "INSERT INTO dataset_release "
                "(source_id,release_key,release_version,release_date,source_url,manifest_url,"
                "retrieved_at,license_name,license_url,attribution,artifact_name,"
                "artifact_size_bytes,checksum_algorithm,content_checksum,manifest_checksum,"
                "source_row_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(values.values()),
            )
            release_id, created = cursor.lastrowid, True
        else:
            differing = [key for key, value in values.items() if row[key] != value]
            if differing:
                raise RegistryConflictError(
                    "dataset_release key already exists with different metadata: "
                    + ", ".join(differing)
                )
            release_id, created = row["id"], False
        con.commit()
        return {"id": release_id, "created": created}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _checkpoint(value):
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise ValueError("checkpoint must be a JSON object")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def create_or_resume_import_run(db_path, *, release_id, idempotency_key,
                                source_row_count=None):
    """Return the same run for a repeated key, preserving its counts/checkpoint."""
    idempotency_key = _nonempty(idempotency_key, "idempotency_key")
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT OR IGNORE INTO import_run(release_id,idempotency_key,source_row_count) "
            "VALUES (?,?,?)", (int(release_id), idempotency_key, source_row_count),
        )
        row = con.execute(
            "SELECT * FROM import_run WHERE release_id=? AND idempotency_key=?",
            (int(release_id), idempotency_key),
        ).fetchone()
        if row is None:
            raise ImportStateError("Could not create or find import run")
        if source_row_count is not None and row["source_row_count"] not in (None, source_row_count):
            raise RegistryConflictError("Import run key was reused with a different source row count")
        status = row["status"]
        if status == "cancelled":
            raise ImportStateError("Cancelled import runs cannot be resumed")
        if status in ("pending", "paused", "failed"):
            con.execute(
                "UPDATE import_run SET status='running', source_row_count=COALESCE(source_row_count,?), "
                "started_at=COALESCE(started_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (source_row_count, row["id"]),
            )
        con.commit()
        row = con.execute("SELECT * FROM import_run WHERE id=?", (row["id"],)).fetchone()
        return dict(row)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


_COUNTER_FIELDS = (
    "processed_row_count", "inserted_row_count", "updated_row_count",
    "rejected_row_count", "skipped_row_count",
)


def commit_import_batch(db_path, *, run_id, expected_checkpoint, next_checkpoint,
                        counts, write_batch):
    """Commit source rows and their checkpoint in one transaction.

    ``counts`` contains cumulative absolute totals. A retry of an already
    committed batch (same next checkpoint and totals) is a no-op. The callback
    receives the active SQLite connection so importer writes roll back together
    with progress metadata if any part fails.
    """
    if not callable(write_batch):
        raise TypeError("write_batch must be callable")
    old_checkpoint = _checkpoint(expected_checkpoint)
    new_checkpoint = _checkpoint(next_checkpoint)
    if set(counts) != set(_COUNTER_FIELDS):
        raise ValueError("counts must contain exactly: " + ", ".join(_COUNTER_FIELDS))
    totals = {key: int(counts[key]) for key in _COUNTER_FIELDS}
    if any(value < 0 for value in totals.values()):
        raise ValueError("row counts cannot be negative")

    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM import_run WHERE id=?", (int(run_id),)).fetchone()
        if row is None:
            raise ImportStateError("Import run does not exist")
        if row["checkpoint_json"] == new_checkpoint and all(row[key] == totals[key] for key in _COUNTER_FIELDS):
            con.rollback()
            return False
        if row["status"] != "running":
            raise ImportStateError(f"Cannot add a batch to an import run in {row['status']} state")
        if row["checkpoint_json"] != old_checkpoint:
            raise ImportStateError("Import checkpoint changed; refusing a stale or concurrent batch")
        if any(totals[key] < row[key] for key in _COUNTER_FIELDS):
            raise ImportStateError("Cumulative import row counts cannot decrease")
        write_batch(con)
        con.execute(
            "UPDATE import_run SET checkpoint_json=?, processed_row_count=?, inserted_row_count=?, "
            "updated_row_count=?, rejected_row_count=?, skipped_row_count=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_checkpoint, totals["processed_row_count"], totals["inserted_row_count"],
             totals["updated_row_count"], totals["rejected_row_count"],
             totals["skipped_row_count"], int(run_id)),
        )
        con.execute(
            "UPDATE dataset_release SET status='importing', imported_row_count=?, "
            "rejected_row_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=("
            "SELECT release_id FROM import_run WHERE id=?)",
            (totals["inserted_row_count"] + totals["updated_row_count"],
             totals["rejected_row_count"], int(run_id)),
        )
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def set_import_run_status(db_path, *, run_id, status, safe_error_summary=None):
    """Pause/fail/complete a run without discarding its checkpoint or counts."""
    allowed = {"paused", "failed", "completed", "cancelled"}
    if status not in allowed:
        raise ValueError("status must be one of: " + ", ".join(sorted(allowed)))
    if safe_error_summary is not None:
        safe_error_summary = str(safe_error_summary)[:1000]
    con = _connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT release_id,status FROM import_run WHERE id=?", (int(run_id),)).fetchone()
        if row is None:
            raise ImportStateError("Import run does not exist")
        if row["status"] in ("completed", "cancelled"):
            if row["status"] == status:
                con.rollback()
                return False
            raise ImportStateError(f"Terminal import run cannot transition from {row['status']}")
        if status == "completed" and row["status"] != "running":
            raise ImportStateError("Only a running import can be completed")
        con.execute(
            "UPDATE import_run SET status=?, error_summary=?, updated_at=CURRENT_TIMESTAMP, "
            "finished_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN CURRENT_TIMESTAMP "
            "ELSE NULL END WHERE id=?",
            (status, safe_error_summary, status, int(run_id)),
        )
        if status == "completed":
            con.execute(
                "UPDATE dataset_release SET status='imported', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (row["release_id"],),
            )
        elif status == "failed":
            con.execute(
                "UPDATE dataset_release SET status='failed', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (row["release_id"],),
            )
        con.commit()
        return True
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
