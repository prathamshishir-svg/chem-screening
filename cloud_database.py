"""Safe bootstrap for an optional compressed, externally hosted SQLite snapshot.

The source checkout's chem.db is never replaced. A verified cloud snapshot is
installed under a checksum-specific filename and selected through CHEM_DB_PATH.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


CHUNK_SIZE = 1024 * 1024
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_REQUIRED_TABLES = {
    "chemicals", "family_rules", "group_rules", "reactions", "reaction_products",
    "templates", "transport_hazards", "pair_reactions", "pair_products", "aliases",
    "rxn_records", "rxn_molecules", "ord_reaction_records", "ord_reaction_participants",
    "ord_chemical_structures", "dataset_source", "dataset_release", "import_run",
    "epa_toxval_assertions",
}
_BOOTSTRAP_CACHE_KEY = None
_BOOTSTRAP_CACHE_PATH = None


class CloudDatabaseError(RuntimeError):
    """A configured database artifact was not safe to activate."""


def _configuration(secrets=None, environ=None):
    environ = os.environ if environ is None else environ
    secrets = secrets or {}
    section = secrets.get("database", {}) if hasattr(secrets, "get") else {}
    if not hasattr(section, "get"):
        section = {}
    return {
        "url": environ.get("CHEM_DB_URL") or section.get("url"),
        "asset_sha256": (environ.get("CHEM_DB_ASSET_SHA256") or
                         environ.get("CHEM_DB_SHA256") or
                         section.get("asset_sha256") or section.get("sha256")),
        "database_sha256": (environ.get("CHEM_DB_DATABASE_SHA256") or
                            section.get("database_sha256")),
        "asset_size_bytes": (environ.get("CHEM_DB_ASSET_SIZE_BYTES") or
                             section.get("asset_size_bytes")),
        "expected_reaction_count": (environ.get("CHEM_DB_EXPECTED_REACTION_COUNT") or
                                     section.get("expected_reaction_count")),
    }


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_database(path, expected_reactions=None):
    path = Path(path)
    if not path.is_file():
        return False, "file_missing"
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            return False, "integrity_check_failed"
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            return False, "missing_tables:" + ",".join(missing)
        if expected_reactions is not None:
            count = connection.execute("SELECT COUNT(*) FROM rxn_records").fetchone()[0]
            if count != expected_reactions:
                return False, "reaction_count_mismatch"
        return True, "ok"
    except (OSError, sqlite3.Error, ValueError):
        return False, "sqlite_validation_failed"
    finally:
        if connection is not None:
            connection.close()


def _log_database_counts(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rxn_records)")}
        if "source" in columns:
            rows = connection.execute(
                "SELECT source,COUNT(*) FROM rxn_records GROUP BY source ORDER BY source"
            ).fetchall()
            total = sum(row[1] for row in rows)
            print(f"Verified reaction database rows: {total}; source counts: {rows}", flush=True)
        else:
            total = connection.execute("SELECT COUNT(*) FROM rxn_records").fetchone()[0]
            print(f"Verified reaction database rows: {total}", flush=True)
    finally:
        connection.close()


def _download(url, destination):
    destination = Path(destination)
    offset = destination.stat().st_size if destination.exists() else 0
    headers = {"User-Agent": "chem-screening-cloud-db/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudDatabaseError(f"Database asset download failed ({type(exc).__name__}).") from exc

    with response:
        status = getattr(response, "status", response.getcode())
        range_header = response.headers.get("Content-Range", "")
        resume = bool(offset and status == 206 and range_header.startswith(f"bytes {offset}-"))
        mode = "ab" if resume else "wb"
        with destination.open(mode) as output:
            shutil.copyfileobj(response, output, length=CHUNK_SIZE)
            output.flush()
            os.fsync(output.fileno())


def _required_config(config):
    url = config["url"]
    if not url:
        return None
    parsed_url = urllib.parse.urlparse(str(url))
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise CloudDatabaseError("Database artifact URL must use HTTPS.")
    if not config["asset_sha256"] or not _SHA256.fullmatch(str(config["asset_sha256"])):
        raise CloudDatabaseError("Configure database.asset_sha256 as a 64-character SHA-256.")
    database_hash = config["database_sha256"]
    if database_hash and not _SHA256.fullmatch(str(database_hash)):
        raise CloudDatabaseError("database.database_sha256 must be a 64-character SHA-256.")
    try:
        size = int(config["asset_size_bytes"]) if config["asset_size_bytes"] else None
        reactions = (int(config["expected_reaction_count"])
                     if config["expected_reaction_count"] else None)
    except (TypeError, ValueError) as exc:
        raise CloudDatabaseError("Database size and reaction count settings must be integers.") from exc
    return {**config, "url": str(url), "asset_sha256": str(config["asset_sha256"]).lower(),
            "database_sha256": str(database_hash).lower() if database_hash else None,
            "asset_size_bytes": size, "expected_reaction_count": reactions}


def bootstrap_database(root, *, secrets=None, environ=None):
    """Download and activate the configured database; return None if unconfigured.

    Existing valid database files are reused. The original chem.db is never
    overwritten; downloaded databases use a filename derived from their hash.
    """
    global _BOOTSTRAP_CACHE_KEY, _BOOTSTRAP_CACHE_PATH
    root = Path(root).resolve()
    config = _required_config(_configuration(secrets, environ))
    if config is None:
        return None
    cache_key = (str(root), config["url"], config["asset_sha256"],
                 config["database_sha256"], config["expected_reaction_count"])
    if (_BOOTSTRAP_CACHE_KEY == cache_key and _BOOTSTRAP_CACHE_PATH and
            _BOOTSTRAP_CACHE_PATH.is_file()):
        return _BOOTSTRAP_CACHE_PATH

    local_database = root / "chem.db"
    if config["database_sha256"] and local_database.is_file():
        if _sha256(local_database) == config["database_sha256"]:
            valid, _ = _validate_database(local_database, config["expected_reaction_count"])
            if valid:
                _log_database_counts(local_database)
                _BOOTSTRAP_CACHE_KEY, _BOOTSTRAP_CACHE_PATH = cache_key, local_database
                return local_database

    cache = root / ".chem_database_cache"
    cache.mkdir(parents=True, exist_ok=True)
    asset = cache / ("chem.db.gz-" + config["asset_sha256"][:16])
    database = cache / ("chem.db-" + (config["database_sha256"] or
                                      config["asset_sha256"])[:16])
    if database.exists():
        valid, reason = _validate_database(database, config["expected_reaction_count"])
        if (valid and (not config["database_sha256"] or
                       _sha256(database) == config["database_sha256"])):
            _log_database_counts(database)
            _BOOTSTRAP_CACHE_KEY, _BOOTSTRAP_CACHE_PATH = cache_key, database
            return database
        raise CloudDatabaseError(
            f"Existing checksum-specific database was not replaced ({reason})."
        )

    partial_asset = asset.with_name(asset.name + ".part")
    if not asset.exists():
        if partial_asset.exists() and config["asset_size_bytes"] is not None and \
                partial_asset.stat().st_size == config["asset_size_bytes"] and \
                _sha256(partial_asset) == config["asset_sha256"]:
            os.replace(partial_asset, asset)
        else:
            _download(config["url"], partial_asset)
        size = asset.stat().st_size if asset.exists() else partial_asset.stat().st_size
        if config["asset_size_bytes"] is not None and size != config["asset_size_bytes"]:
            raise CloudDatabaseError("Downloaded asset size does not match configuration.")
        verified_asset = asset if asset.exists() else partial_asset
        if _sha256(verified_asset) != config["asset_sha256"]:
            raise CloudDatabaseError("Downloaded asset SHA-256 does not match configuration.")
        if verified_asset == partial_asset:
            os.replace(partial_asset, asset)
    elif _sha256(asset) != config["asset_sha256"]:
        raise CloudDatabaseError("Cached asset SHA-256 does not match configuration.")

    partial_database = database.with_name(database.name + ".part")
    if partial_database.exists():
        # A previous process may have been interrupted while decompressing.
        partial_database.unlink()
    digest = hashlib.sha256()
    try:
        with gzip.open(asset, "rb") as source, partial_database.open("wb") as target:
            for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
    except (OSError, EOFError) as exc:
        raise CloudDatabaseError(f"Database asset decompression failed ({type(exc).__name__}).") from exc
    if config["database_sha256"] and digest.hexdigest() != config["database_sha256"]:
        raise CloudDatabaseError("Decompressed database SHA-256 does not match configuration.")
    valid, reason = _validate_database(partial_database, config["expected_reaction_count"])
    if not valid:
        raise CloudDatabaseError(f"Downloaded database failed validation ({reason}).")
    try:
        # Atomic rename to a previously absent, checksum-specific destination.
        os.link(partial_database, database)
    except FileExistsError as exc:
        raise CloudDatabaseError("Database appeared during setup; refusing to replace it.") from exc
    except OSError as exc:
        raise CloudDatabaseError(f"Could not activate verified database ({type(exc).__name__}).") from exc
    partial_database.unlink()
    _log_database_counts(database)
    _BOOTSTRAP_CACHE_KEY, _BOOTSTRAP_CACHE_PATH = cache_key, database
    return database
