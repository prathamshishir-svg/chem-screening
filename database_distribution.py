"""Verified, non-destructive installation of the optional full SQLite database."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


CHUNK_SIZE = 1024 * 1024
REQUIRED_TABLES = {
    "chemicals",
    "rxn_records",
    "ord_reaction_records",
    "epa_toxval_assertions",
}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class DatabaseDistributionError(RuntimeError):
    """A release could not safely be installed."""


def load_release_config(path: Path, *, url: str | None = None,
                        sha256: str | None = None) -> dict:
    """Read the published artifact manifest, allowing explicit environment/CLI overrides."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatabaseDistributionError(f"Release manifest not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DatabaseDistributionError(f"Could not read release manifest: {exc.__class__.__name__}") from exc

    artifact_url = url or os.environ.get("CHEM_DB_URL") or config.get("url")
    expected_hash = sha256 or os.environ.get("CHEM_DB_SHA256") or config.get("sha256")
    if not artifact_url or not expected_hash:
        raise DatabaseDistributionError(
            "The database release is not published/configured yet. Set CHEM_DB_URL and "
            "CHEM_DB_SHA256 after an artifact has passed redistribution and integrity review."
        )
    parsed = urllib.parse.urlparse(artifact_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise DatabaseDistributionError("Database artifact URL must use HTTPS.")
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise DatabaseDistributionError("Expected SHA-256 must contain exactly 64 hexadecimal characters.")
    size = config.get("size_bytes")
    if size is not None and (not isinstance(size, int) or size <= 0):
        raise DatabaseDistributionError("Manifest size_bytes must be a positive integer or null.")
    return {"url": artifact_url, "sha256": expected_hash.lower(), "size_bytes": size,
            "release": config.get("release")}


def validate_database(path: Path) -> tuple[bool, str]:
    """Read-only SQLite integrity and required application schema validation."""
    if not path.is_file():
        return False, "file_missing"
    con = None
    try:
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True, timeout=30)
        check = con.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            return False, "sqlite_quick_check_failed"
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            return False, "missing_required_tables:" + ",".join(missing)
        for table in ("chemicals", "rxn_records", "ord_reaction_records", "epa_toxval_assertions"):
            if con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None:
                return False, f"required_table_empty:{table}"
        return True, "ok"
    except (OSError, sqlite3.Error, ValueError):
        return False, "sqlite_open_or_validation_failed"
    finally:
        if con is not None:
            con.close()


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _download_resumable(url: str, partial: Path) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "chem-screening-database-setup/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DatabaseDistributionError(f"Database download failed ({exc.__class__.__name__}).") from exc
    with response:
        status = getattr(response, "status", response.getcode())
        append = bool(offset and status == 206 and
                      response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"))
        mode = "ab" if append else "wb"
        with partial.open(mode) as output:
            shutil.copyfileobj(response, output, length=CHUNK_SIZE)
            output.flush()
            os.fsync(output.fileno())


def install_database(config: dict, destination: Path) -> str:
    """Install only into a missing destination; never replace an existing file."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        valid, reason = validate_database(destination)
        if valid:
            return "already_present"
        raise DatabaseDistributionError(
            f"Refusing to overwrite existing database ({reason}). Move it aside manually after backup."
        )

    partial = destination.with_name(destination.name + ".download.part")
    try:
        _download_resumable(config["url"], partial)
        digest, size = _sha256_and_size(partial)
        if size <= 0:
            raise DatabaseDistributionError("Downloaded database artifact is empty.")
        if config.get("size_bytes") is not None and size != config["size_bytes"]:
            raise DatabaseDistributionError("Downloaded size does not match the release manifest.")
        if digest != config["sha256"]:
            raise DatabaseDistributionError("Downloaded SHA-256 does not match the release manifest.")
        valid, reason = validate_database(partial)
        if not valid:
            raise DatabaseDistributionError(f"Downloaded database failed validation ({reason}).")
        try:
            # Same-volume hard-link creation is atomic and fails if another file exists.
            os.link(partial, destination)
        except FileExistsError as exc:
            raise DatabaseDistributionError("A database appeared during setup; refusing to overwrite it.") from exc
        except OSError as exc:
            raise DatabaseDistributionError(
                f"Could not atomically install verified database ({exc.__class__.__name__}); "
                "the verified download remains in the .part file."
            ) from exc
        partial.unlink()
        return "installed"
    except Exception:
        # Keep a partial download so a later invocation can resume it.
        raise
