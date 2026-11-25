# Additive dataset import foundation

This phase adds the versioned SQLite metadata needed to register external sources and track resumable imports. It does not download or import chemistry records. The application keeps its existing curated tables, screens, UI, caches, reactions, and human review state.

## Tables

- `schema_migrations` stores the numbered migration and SHA-256 of its SQL. Migration 001 is `migrations/001_dataset_provenance.sql`. Applied migration files are immutable; future schema changes must be new sequential migrations.
- `dataset_source` stores a stable source key, publisher, canonical/API URLs, license name/URL/terms, attribution, and description.
- `dataset_release` records a source release/version, retrieval time, source/manifest URLs, artifact name/size, content and manifest checksums, source and imported/rejected row counts, and release status. The release key is globally unique, and `(source_id, release_version)` is unique.
- `import_run` records an idempotency key, current state, row counters, JSON checkpoint, safe error summary, and start/update/finish timestamps. `(release_id, idempotency_key)` is unique. Counters are cumulative totals; resuming a run does not clear its checkpoint.

Only one pending/running/paused import run may exist for a release at a time. This avoids concurrent runs overwriting release-level progress totals.

Foreign keys use `ON DELETE RESTRICT`; there is no cascade deletion of source history. Existing tables are not referenced or rewritten by this migration.

## Migration operations

The migration uses regular transactional `CREATE TABLE`/`CREATE INDEX` statements, not `executescript`, so SQLite can roll back the complete DDL transaction on failure. It does not use or change `PRAGMA user_version`; the dedicated migration ledger avoids silently claiming an existing database version convention. Each migration checksum is checked on later starts, and applied schema columns, indexes, and foreign keys are validated.

```powershell
# Read-only: validate migration SQL in memory and inspect the target DB.
venv\Scripts\python.exe migrations.py --dry-run

# Apply pending numbered migrations to the selected database.
venv\Scripts\python.exe migrations.py --apply

# Read-only validation that all project migrations are applied and structurally valid.
venv\Scripts\python.exe migrations.py --validate

# Use an explicit database path for a disposable test/staging DB.
venv\Scripts\python.exe migrations.py --dry-run --db C:\path\to\staging.db
```

The application calls the additive migrator after its existing legacy initialization branch. The legacy initializer itself is unchanged. It should not be run to update an already populated database: `migrate_to_db.py` refreshes existing core tables and is not an additive importer.

## Importer contract

Future importers should register immutable source and release metadata once using `dataset_imports.register_dataset_source` and `register_dataset_release`. Repeating a registration with the same key and identical metadata returns the existing ID; reusing a stable key with different metadata raises `RegistryConflictError` and requires an explicit reviewed metadata update.

Use a deterministic `idempotency_key` for a logical import attempt (for example, an importer name plus its run configuration). `create_or_resume_import_run` returns the existing run if interrupted and preserves its checkpoint and counters. A completed run stays complete; a cancelled run cannot be resumed. Paused or failed runs can resume with the same key.

For each bounded source batch, call `commit_import_batch` with:

- the expected previous checkpoint;
- the next checkpoint (a JSON object, such as a source cursor or partition/row-group offset);
- cumulative row totals;
- a `write_batch(connection)` callback that writes source records using source-specific unique IDs.

The source rows and checkpoint/counters commit in one SQLite transaction. If the transaction fails, neither the writes nor progress marker persist. If a caller repeats a successfully committed batch using the same next checkpoint and totals, the helper returns `False` without calling the writer again. A stale/concurrent checkpoint is rejected. For true row-level idempotency, each importer must also enforce a unique key such as `(dataset_release_id, source_record_id)` in its future source-record table; the three foundation tables do not contain chemistry records.

Use `set_import_run_status` to pause, fail, or complete while preserving the checkpoint. Only store a sanitized `safe_error_summary`; do not put secrets, credentials, full requests, or sensitive source payloads there. Failed/paused runs retain their cumulative counts and cursor.

The helpers do not make network requests, choose source licenses, standardize structures, or write chemistry records on their own. Importers must stream files in bounded batches, audit rejected rows, preserve raw source values and provenance, and never merge experimental records with predictions or AI hypotheses.

## Planned EPA CompTox and ORD use

### EPA CompTox pilot

Register a source such as `epa_comptox` with EPA publisher, official dashboard/download/API URLs, EPA license/attribution statement, and intended endpoint scope. Register each official dataset release separately (for example, DSSTox identity and a selected ToxValDB snapshot); do not combine study-level observations and predictions under one evidence label. Record the official release/version, retrieval timestamp, exact artifact URL/name/size, checksum, source-provided row count, and license snapshot in `dataset_release`.

Create a deterministic import run for each release and importer configuration. Stream CSV/TSV records in batches; checkpoint a stable row offset or source identifier only after the corresponding chemical identity/hazard records commit. Preserve DTXSID and source row identifiers, authority/jurisdiction, measured-versus-predicted status, endpoint, units, and source fields in future source-aware chemical/hazard tables. Do not import a full EPA corpus before a small pilot establishes storage and match quality.

### Open Reaction Database pilot

Register ORD as its own source, then register each selected ORD dataset and version as a distinct release because licensing/provenance and evidence details may vary by dataset. Capture dataset ID/name, source repository or official mirror URL, CC BY-SA attribution and share-alike terms, artifact checksum/size, and expected record count.

Use a stable source record key based on ORD dataset ID plus reaction ID. Stream Parquet row groups/protobuf records; checkpoint `(dataset_id, row_group, row_offset)` only in the same transaction as the participant/reaction batch. Preserve separate participant roles (reactant, reagent, catalyst, solvent, product, unknown), reported conditions, measurements, raw record references, and per-record provenance in later reaction tables. Do not assume all records are equivalent experimental evidence; retain dataset-specific evidence descriptions and review the redistribution obligations before publishing derived database content.

## Safety and operational boundaries

- This migration adds only four metadata/registry tables and three indexes; it does not touch the CSV files or existing chemistry tables.
- The test suite uses disposable temporary databases. No test should call the live `chem.db` migrator.
- Database writes are protected by SQLite transactions and foreign-key enforcement. A verified pre-change project-files backup was made before implementation; the large database files were excluded from that file backup and must be preserved separately.
- A failed migration transaction can be retried after correcting the cause. A conflicting pre-existing table name or a changed applied migration checksum stops the operation rather than attempting reconciliation.
- Do not begin EPA or ORD data ingestion until Phase 2 is explicitly approved.
