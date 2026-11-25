# Full reaction database on Streamlit Community Cloud

The app can optionally select a verified SQLite snapshot downloaded from a
public HTTPS release asset. With no database settings configured, it follows
the existing CSV bootstrap path. The local `chem.db` is never replaced.

## Prepared release asset

Prepared from the current local database using SQLite's online backup API;
the source database was opened read-only and its SHA-256 was unchanged after
the copy. The snapshot was integrity checked and then gzip-compressed.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `chem.db.gz` | 851,220,708 | `eda42f8030347a535b2e000faea76c8d9f382513fcd8811cd81d0b5f9b52da23` |
| Decompressed SQLite snapshot | 5,696,905,216 | `b32c27a0d53adef30a461ea51138f63b141e308c0757c3e342e3ee41918d136c` |

The snapshot contains 1,418,171 `rxn_records` rows: 1,083,071 USPTO and
335,100 ORD. It also contains 6,781,357 molecule-role rows. Exact counts for
every table are recorded in the ignored local file
`.deployment_artifacts/metadata.json`.

## Publish the asset

Do not add the database or compressed asset to normal Git history. The local
`.deployment_artifacts/` directory is Git-ignored. After the application code
is reviewed and approved for release, create a GitHub Release on the
`research-engine` code line and upload **only**
`.deployment_artifacts/chem.db.gz` with the asset name `chem.db.gz`.

For release tag `chem-db-2026-11-25`, the public download URL will be:

```text
https://github.com/prathamshishir-svg/chem-screening/releases/download/chem-db-2026-11-25/chem.db.gz
```

That URL is a planned stable asset URL; it will return a file only after the
release and asset are actually published. Do not configure Cloud until GitHub
shows the asset as uploaded and its displayed size matches 851,220,708 bytes.

## Streamlit Community Cloud settings

In the app's **Settings → Secrets**, add:

```toml
[database]
url = "https://github.com/prathamshishir-svg/chem-screening/releases/download/chem-db-2026-11-25/chem.db.gz"
asset_sha256 = "eda42f8030347a535b2e000faea76c8d9f382513fcd8811cd81d0b5f9b52da23"
database_sha256 = "b32c27a0d53adef30a461ea51138f63b141e308c0757c3e342e3ee41918d136c"
asset_size_bytes = 851220708
expected_reaction_count = 1418171
```

`asset_sha256` checks the downloaded gzip file. `database_sha256` checks the
decompressed SQLite file. The size and reaction count provide additional
checks. Secrets are optional: without `database.url`, the app uses its current
CSV-based initialization.

## Startup and safety behavior

On startup the app checks the release settings before importing the database
modules. It downloads the asset into `.chem_database_cache`, keeps a partial
download resumable, verifies the compressed checksum, decompresses to a
temporary file, verifies the database checksum and SQLite schema/integrity,
then activates it under a checksum-specific filename. It does not overwrite
`chem.db` or an existing checksum-specific database. Repeated Streamlit
reruns in the same process reuse the selected file; a new container may need to
download it again.

The downloaded snapshot includes the existing curated chemistry, hazard,
transport, pair review, cache, USPTO, ORD, and EPA data. The app selects the
snapshot as a single database, so it does not merge or replace any rows in the
CSV bootstrap database. If download or validation fails, startup displays a
warning and continues through the existing CSV bootstrap path.

Community Cloud's local storage is ephemeral: reboot, redeploy, or container
replacement can remove the downloaded files, after which the app downloads the
asset again. Streamlit currently documents up to 50 GB storage and up to
2.7 GB memory for Community Cloud apps; those limits can change. This snapshot
uses about 5.7 GB disk when extracted, plus 851 MB while retaining the gzip
cache. The release download can take time and uses app startup resources.

## Local validation

For an opt-in local check, environment variables are also accepted:

```powershell
$env:CHEM_DB_URL = "<public release asset URL>"
$env:CHEM_DB_ASSET_SHA256 = "<gzip SHA-256>"
$env:CHEM_DB_DATABASE_SHA256 = "<SQLite SHA-256>"
$env:CHEM_DB_ASSET_SIZE_BYTES = "851220708"
$env:CHEM_DB_EXPECTED_REACTION_COUNT = "1418171"
venv\Scripts\python.exe -m streamlit run app.py
```

No public deployment is considered verified until the release is uploaded,
these secrets are configured, the deployed app starts, and its Cloud logs
confirm the expected reaction count.
