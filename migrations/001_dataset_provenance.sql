-- Additive provenance foundation. Do not edit after it has been applied;
-- later changes belong in a new numbered migration.
CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    checksum    TEXT NOT NULL CHECK (length(checksum) = 64),
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dataset_source (
    id              INTEGER PRIMARY KEY,
    source_key      TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name            TEXT NOT NULL,
    publisher       TEXT NOT NULL,
    canonical_url   TEXT NOT NULL,
    api_url         TEXT,
    license_name    TEXT,
    license_url     TEXT,
    license_terms   TEXT,
    attribution     TEXT,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(source_key)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(publisher)) > 0),
    CHECK (length(trim(canonical_url)) > 0)
);

CREATE TABLE dataset_release (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES dataset_source(id) ON DELETE RESTRICT,
    release_key         TEXT NOT NULL UNIQUE,
    release_version     TEXT NOT NULL,
    release_date        TEXT,
    source_url          TEXT NOT NULL,
    manifest_url        TEXT,
    retrieved_at        TEXT NOT NULL,
    license_name        TEXT,
    license_url         TEXT,
    attribution         TEXT,
    artifact_name       TEXT,
    artifact_size_bytes INTEGER CHECK (artifact_size_bytes IS NULL OR artifact_size_bytes >= 0),
    checksum_algorithm  TEXT NOT NULL DEFAULT 'sha256',
    content_checksum    TEXT,
    manifest_checksum   TEXT,
    source_row_count    INTEGER CHECK (source_row_count IS NULL OR source_row_count >= 0),
    imported_row_count  INTEGER NOT NULL DEFAULT 0 CHECK (imported_row_count >= 0),
    rejected_row_count  INTEGER NOT NULL DEFAULT 0 CHECK (rejected_row_count >= 0),
    status              TEXT NOT NULL DEFAULT 'registered'
                        CHECK (status IN ('registered', 'available', 'importing', 'imported', 'failed', 'retired')),
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, release_version),
    CHECK (length(trim(release_key)) > 0),
    CHECK (length(trim(release_version)) > 0),
    CHECK (length(trim(source_url)) > 0),
    CHECK (length(trim(retrieved_at)) > 0),
    CHECK (checksum_algorithm = 'sha256'),
    CHECK (content_checksum IS NULL OR
           (length(content_checksum) = 64 AND content_checksum NOT GLOB '*[^0-9a-fA-F]*')),
    CHECK (manifest_checksum IS NULL OR
           (length(manifest_checksum) = 64 AND manifest_checksum NOT GLOB '*[^0-9a-fA-F]*'))
);

CREATE TABLE import_run (
    id                  INTEGER PRIMARY KEY,
    release_id          INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    idempotency_key     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled')),
    source_row_count    INTEGER CHECK (source_row_count IS NULL OR source_row_count >= 0),
    processed_row_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_row_count >= 0),
    inserted_row_count  INTEGER NOT NULL DEFAULT 0 CHECK (inserted_row_count >= 0),
    updated_row_count   INTEGER NOT NULL DEFAULT 0 CHECK (updated_row_count >= 0),
    rejected_row_count  INTEGER NOT NULL DEFAULT 0 CHECK (rejected_row_count >= 0),
    skipped_row_count   INTEGER NOT NULL DEFAULT 0 CHECK (skipped_row_count >= 0),
    checkpoint_json     TEXT NOT NULL DEFAULT '{}',
    error_summary       TEXT,
    started_at          TEXT,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at         TEXT,
    UNIQUE (release_id, idempotency_key),
    CHECK (length(trim(idempotency_key)) > 0)
);

CREATE INDEX idx_dataset_release_source_status
    ON dataset_release(source_id, status, release_version);
CREATE INDEX idx_import_run_release_status
    ON import_run(release_id, status, id);
CREATE UNIQUE INDEX idx_import_run_one_active_release
    ON import_run(release_id) WHERE status IN ('pending', 'running', 'paused');
