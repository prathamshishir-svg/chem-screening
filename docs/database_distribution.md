# Distributing the expanded SQLite database

## Published release

The application opens `chem.db` from the project directory (`db.py`). Git intentionally excludes the database. The complete existing database is now published in the public Hugging Face dataset repository `prathamshishir-chem/chem-screening-database`.

- Immutable download URL: <https://huggingface.co/datasets/prathamshishir-chem/chem-screening-database/resolve/6a615626b4b8a07c4b14fba90b7cc6d342e8c3a8/chem.db>
- Size: `5,696,905,216` bytes
- SHA-256: `D9A4BBECB65996383A14A0EA2CA5E25631C3833E4CC06B4C2320947B154DA1B4`
- Database record: project owner confirmed redistribution review; artifact integrity was verified after a fresh download.

The published file is the unchanged local `chem.db`, uploaded through a temporary hard link. The original database remains at its project path.

## Free hosting evaluation

| Option | Relevant limits | Assessment |
|---|---|---|
| GitHub repository / Git LFS | Ordinary Git rejects files over 100 MB; Git LFS Free has a 2 GB per-file limit and 10 GiB storage plus 10 GiB/month download bandwidth. | Not suitable for this 5.7 GB uncompressed artifact; one fresh download consumes over half the free monthly bandwidth. GitHub release assets avoid LFS bandwidth accounting, but should not be assumed to accept a single 5.7 GB file and do not solve the dataset-license review. |
| Hugging Face public dataset | Public storage is best-effort for free accounts, with no published hard public quota; Hub policies reserve mitigation for abuse and ask large-dataset hosts to use suitable formats and enable community reuse. Downloads use Xet/CDN hosts. | The most practical no-billing public dataset host if the project publishes a proper dataset card, attribution/share-alike terms and a separately prepared artifact. Availability is best-effort, so do not promise a guaranteed mirror or unlimited download rate. |
| Cloudflare R2 Standard | 10 GB-month, 1 million Class A requests and 10 million Class B requests monthly free; Internet egress is free. | 5.7 GB fits storage allowance, but R2 requires an account/subscription setup and overages can be billed. It is not a zero-billing-risk choice unless account spending controls are configured. |

**Recommendation:** use a public Hugging Face dataset repository for a community-reusable artifact, subject to license clearance and acceptance of best-effort availability. Keep an independently verified official-source rebuild/import path documented as a fallback. If dependable uptime is required, R2 offers clearer capacity and free-egress terms but requires account/payment safeguards and is not guaranteed to remain free under high use.

## Attribution and integrity record

- ORD data are released under CC BY-SA 4.0. Include attribution, a link to the license, identify modifications/selection, and ensure the relevant adapted database content is offered under compatible share-alike terms.
- EPA ToxValDB is a compilation of information from multiple public datasets, databases and literature. Preserve EPA/source attribution and each source reference; inspect the actual v9.7.0 notices and underlying source terms before redistributing the combined artifact. Do not infer that every included upstream source is public domain solely because EPA hosts the compilation.
- USPTO/Lowe-derived reactions were previously registered as CC0 in project metadata. Retain source attribution and identify the exact source release represented.
- Review curated CSV content, transport data, chemical-cache fields, cached AI responses and `pair_reactions` / `pair_products` for redistribution rights and user-specific information. Exclude secrets and user-specific runtime state unless specifically cleared.
- The release artifact must be created from a copy, with an explicit inventory/count report and checksum. Never create the distributable by modifying the working database.

The project owner confirmed the redistribution review. Preserve ORD and EPA attribution and source references in future updates. The uploaded artifact passed SHA-256, SQLite `quick_check`, required-table, and fresh-download record-count checks.

## Install for a fresh clone

For a fresh clone, install the Python requirements and run:

```powershell
python -m pip install -r requirements.txt
python setup_database.py
python -m streamlit run app.py
```

The setup command reads the release URL, size and hash from `database_release.json`, streams the artifact to `chem.db.download.part`, resumes interrupted HTTP range downloads where supported, checks the declared byte size and SHA-256, runs SQLite `quick_check`, requires non-empty curated, USPTO, ORD and EPA tables, and installs through an atomic same-volume link. It does not install an unverified file. A mismatch leaves the partial file for diagnosis/retry and leaves `chem.db` absent.

Existing installs are safe: a valid local `chem.db` is accepted as-is and no network request is made. An invalid existing `chem.db` is never replaced; the command stops and asks the operator to back it up and move it manually. This avoids silently overwriting user data.

The installer requires HTTPS and a 64-character SHA-256. The database repository is public, so normal downloads need no Hugging Face token. The installer has no third-party runtime dependency. Do not place credentials in the URL, manifest, logs or Git.

The verified download contained 1,418,171 `rxn_records` (1,083,071 USPTO and 335,100 ORD-mined), 409,035 source-specific ORD records, and 249,071 EPA ToxValDB assertions. The application’s reaction lookup returned matching USPTO and ORD evidence, and its EPA lookup returned 94 records for a verified DTXSID.

## Operational notes

- Keep the exact database artifact immutable for each release; publish a new version and manifest hash for updates.
- For a multi-part archive, publish and verify a signed/hash-checked manifest for every part, assemble to a temporary output, then validate before installation. The current installer expects one SQLite database artifact.
- SQLite is distributed as a read-only-style local file but the app writes runtime caches/reviews. Users therefore need write access to their local project/database directory. Streamlit Community Cloud's ephemeral filesystem and repository size constraints make this full local database unsuitable for direct Cloud deployment; use a managed database/service for that scenario.
- The first download is several gigabytes. Report its expected size before transfer and preserve resumability. Users should have at least the artifact size plus temporary download space available.
