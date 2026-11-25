# Chemical Data Expansion Audit and Implementation Plan

**Audit date:** 2026-11-24
**Branch inspected:** `research-engine`
**Scope:** Read-only inspection of the application data model, current CSV/SQLite contents, import and lookup paths, and public dataset documentation. This report is a plan only. No datasets were downloaded, no application code or CSV files were changed, and no database writes or migrations were performed.

## Executive summary

The project has two distinct layers of data today: a small set of curated rule/reaction CSVs, and a local SQLite database containing those curated records plus a large USPTO patent-reaction corpus and runtime caches/review state. The current database is about 511 MiB on disk and contains 1,083,071 patent reaction records and 5,129,681 reaction-molecule rows. This is a sound local starting point, but it is not yet a general multi-source research repository: source release/license metadata are sparse, chemical identity rows are few, and the large reaction lookup can retrieve molecules from reactant and reagent roles together.

Recommended order:

1. Add additive provenance and import-run support, then pilot a carefully selected EPA CompTox identity/hazard/property subset.
2. Pilot one or a few Open Reaction Database (ORD) datasets with their dataset-level provenance and licensing intact; expand only after evaluating storage, duplicate behavior, and query quality.
3. Improve the PubChem integration as an on-demand, cached identity/property source; do not attempt a full PubChem mirror.
4. Audit and, if useful, incrementally add compatible USPTO source partitions with source record identifiers and robust deduplication.
5. Add narrowly scoped, separately labelled sources such as Rhea for biochemical reactions. Link to CAMEO/NIOSH/NIST until bulk-use permissions and data packaging are clear.

Keep predictions and AI hypotheses in separate evidence categories from source-reported records. Do not use mere chemical co-occurrence as proof of a reaction or compatibility.

## Existing project data audit

### CSV files

Counts below are data rows excluding the header; sizes are the inspected local file sizes.

| File | Rows | Size | Current role |
|---|---:|---:|---|
| `data/chemicals.csv` | 14 | 521 B | Curated chemical identities/attributes |
| `data/family_rules.csv` | 11 | 802 B | Curated family-level compatibility rules |
| `data/group_rules.csv` | 20 | 1,624 B | Curated group-level compatibility rules |
| `data/reaction_products.csv` | 25 | 2,849 B | Curated reaction-product mappings |
| `data/reactions.csv` | 16 | 2,877 B | Curated reaction records |
| `data/templates.csv` | 10 | 1,903 B | RDKit reaction templates |

These are small, human-manageable seed/curation files. They should remain appropriate for curated inputs; millions of external records belong in a database or source-specific data store, never in CSVs loaded wholesale by the app.

`import_data.py` is designed for these files, not large incremental source ingestion. It uses replacement semantics, and its reaction loading path removes existing product rows for a reaction before loading the supplied products. `migrate_to_db.py` contains deletion of core tables and is not appropriate for additive data expansion. Neither script was run during this audit.

### SQLite database and schema

The local `chem.db` was inspected read-only. It is 535,818,240 bytes (about 511 MiB) and passed SQLite `integrity_check`. It contains these application tables and approximate row counts:

| Table | Rows | Purpose / observations |
|---|---:|---|
| `chemicals` | 14 | Curated chemical records |
| `aliases` | 19 | Curated aliases |
| `family_rules` | 11 | Curated family rules |
| `group_rules` | 20 | Curated group rules |
| `reactions` | 16 | Curated reactions |
| `reaction_products` | 25 | Products linked to curated reactions |
| `templates` | 10 | RDKit templates |
| `hazard_classes` | 15 | Curated hazard classifications |
| `transport_hazards` | 6,138 | Transport hazard entries |
| `chemical_cache` | 13 | Runtime chemical lookup cache |
| `product_cache` | 58 | Runtime product lookup cache |
| `name_index` | 13 | Name lookup index/cache |
| `ai_answers` | 10 | Cached AI analyses |
| `pair_reactions` | 5 | Per-pair generated/curated reaction state and review status |
| `pair_products` | 11 | Per-pair product state |
| `rxn_records` | 1,083,071 | Imported USPTO-derived reaction records |
| `rxn_molecules` | 5,129,681 | Molecule/role rows associated with reaction records |
| `recorded_reactions` | 0 | Legacy/unused table in this database |

SQLite also has its internal `sqlite_sequence` table. The primary USPTO tables use integer IDs and text fields for reaction sides, patent, yield, and source; `rxn_molecules` stores reaction ID, SMILES, and role. Existing USPTO rows span 1976–2016, have 103,978 distinct patent numbers, and currently have `source='USPTO'`. Reactant-side role `R` occurs 3,767,041 times and reagent-side role `G` 1,362,640 times. The original paragraph-level source record identifier and source release/import manifest are not retained in a dedicated provenance model.

The inspected schema has one declared foreign key, `reaction_products.reaction_id -> reactions.id`. There is no declared FK for `rxn_molecules.rxn_id`, `pair_products`, or caches. Existing explicit indexes include pair-key lookup, reaction-molecule reaction ID/SMILES lookup, and transport CAS lookup, in addition to primary-key autoindexes. There is no database-enforced uniqueness constraint on the USPTO source reaction key. `PRAGMA user_version` is 0. `pair_reactions` stores decision state in its status/check fields; there is no separate review-decision table in this database. Any future migration must preserve these rows and review state.

The current reaction lookup in `db.find_recorded` builds structure fragments and finds records by matching molecule strings. Its molecule join does not constrain `role`, so a hit may be a reactant or a reagent. It then returns patent/year/reaction fields and ranks results. A pathway must therefore be matched using participant roles and reaction-side/product evidence; the presence of both input structures anywhere in a patent record is insufficient.

### USPTO import and retrieval

`import_uspto.py` imports the Lowe patent-extracted reaction collection from RSMI/TSV-like inputs and archive formats, parses reactants/reagents/products, and stores molecule-role rows. It commits in batches (5,000 rows), but its current duplicate handling loads existing reaction keys into an in-memory set, has no DB-enforced unique source key, and drops/rebuilds molecule indexes around import. Its persistent provenance is largely a source label rather than a dataset release, exact source-file checksum, paragraph record ID, license record, or import-run manifest. It selects calculated yield before text-mined yield where available and stores the selected value as text; original fields should be retained separately in a future source-preserving importer.

The official Lowe/NextMove Figshare archive describes patent-text-mined US patent reactions from 1976 to September 2016 and is approximately 1.39 GB compressed. The records are extracted from patents; text-mined reaction records are not equivalent to independently verified experimental outcomes. The source notes duplicate application/grant records and potential atom-mapping issues. Its reported CC0 license makes it a suitable candidate for a provenance-preserving additive import, after inspecting exact files and identifying which partitions are absent locally.

### Existing PubChem integration

PubChem is currently used on demand through PubChemPy/PUG interfaces for chemical name/structure resolution, synonyms, CAS, formula, molecular mass, SMILES, and GHS hazard information. Results are cached locally in `chemical_cache`, `product_cache`, and `name_index`. Cache rows have a fetch time, but source record/version and field-level provenance are limited. Lookup paths can retry and sleep on errors; separate calls can repeat work when one lookup resolves through multiple functions.

PubChem is best treated as an external identity/property service with local caching and controlled refresh, not as a full database mirror. Its records aggregate contributions from many sources, so licensing and evidentiary quality need to be evaluated by source/field rather than assuming one uniform PubChem license. PubChem advises that PUG REST is not intended for millions of individual requests and recommends a rate limit of no more than five requests per second; use bulk/optimized routes for large selected subsets.

## Public data source comparison

Counts and release information below are the values visible in official documentation on the audit date; dataset counts change. Exact downloadable sizes are often release-specific and must be measured on a small pilot before a full import. “Experimental” below describes the origin of the data where supported, not a guarantee that every row is correct or directly applicable to a queried pair.

| Source | Coverage and approximate scale | Access, format, storage | License / redistribution | Evidence type and app value | Recommended import/use |
|---|---|---|---|---|---|
| **PubChem** ([statistics](https://pubchem.ncbi.nlm.nih.gov/docs/statistics/), [downloads](https://pubchem.ncbi.nlm.nih.gov/docs/downloads), [PUG REST guide](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest-tutorial)) | As of 2026-11-19: about 124.9M Compounds and 351.9M Substance records. | PUG REST/PUG-View, E-utilities, RDF and bulk downloads; SDF, XML, JSON, TSV and other formats. A complete local mirror is far beyond the current DB and could require tens to hundreds of GB depending on content/format. | Mixed contributor terms; do not assume every contributed field is under one unrestricted license. Track source/contributor and terms. Review the source-level license before redistributing fields. | Aggregated and contributed identities, structures, properties, synonyms and hazard data; valuable for identity/property lookup and hazard evidence, not a uniform experimental reaction corpus. | Continue on-demand access and cache only resolved compounds/fields needed. For a fixed curated subset, use bulk downloads and preserve source IDs. Avoid full mirroring. Respect documented rate limits. |
| **EPA CompTox Chemicals Dashboard / DSSTox / ToxValDB / ToxCast / ToxRefDB** ([Dashboard](https://comptox.epa.gov/dashboard/), [about](https://www.epa.gov/comptox-tools/comptox-chemicals-dashboard-about), [downloadable data](https://www.epa.gov/comptox-tools/downloadable-computational-toxicology-data), [APIs](https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis)) | Dashboard reported about 1.386M chemicals in Aug 2026. ToxValDB v9.7 documentation lists about 249,071 toxicity values for 41,855 chemicals; ToxRefDB covers thousands of studies and over 1,000 chemicals. | Downloadable DSSTox and toxicity/property files, plus web APIs; file formats vary by release (commonly CSV/TSV). APIs are free; API-key registration/contact may be required. Exact bulk sizes are release-dependent and should be measured. | EPA states its computational toxicology data are open and free of copyright restrictions for commercial and noncommercial use. Preserve EPA attribution, dataset/release/version and source fields anyway. | Mix of measured study/assay observations, curated/standardized values, and computational predictions. Strong for chemical identity backbone, environmental/physchem properties, and toxicology; not a direct reaction compatibility dataset. | **Highest priority**: pilot DSSTox identity mapping plus selected ToxValDB fields relevant to screening. Keep assay results, study summaries, and predictions distinguishable. Import release-by-release from official downloads. Avoid ingesting every assay endpoint until use cases and storage/query design are proven. |
| **Open Reaction Database (ORD)** ([site](https://open-reaction-database.org/), [data docs](https://docs.open-reaction-database.org/en/latest/data.html)) | Current site reports about 2.4M reactions across 93 datasets. | Dataset-level Parquet files in Git LFS and a Hugging Face mirror; each row serializes an ORD protobuf `Reaction`; Python `ord-schema` tools decode the records. Rich reaction details can include reactants, reagents, conditions, outcomes and measurements. | ORD data are CC BY-SA 4.0; attribution and share-alike terms apply to redistributed adapted database content. Check the specific dataset metadata and repository terms for each release. The schema/software license is separate. | Structured records from contributed reaction datasets, including lab and high-throughput experiments. Dataset-level provenance and evidence type vary; inspect each dataset rather than labelling all uniformly. Highly useful for reactions, conditions, and experimental outcomes. | **High priority, staged**: import one dataset with clear metadata and useful coverage, preserving its dataset ID and raw record. Keep it separate from USPTO patent-extracted evidence. Evaluate storage and quality before expanding beyond the pilot. |
| **USPTO / Lowe–NextMove patent reaction data** ([Figshare dataset](https://figshare.com/articles/dataset/Chemical_reactions_from_US_patents_1976-Sep2016_/5104873), [USPTO research datasets](https://www.uspto.gov/ip-policy/economic-research/research-datasets)) | Existing DB: 1.083M records. Lowe archive: about 1.39 GB compressed, with patent grants/applications and associated reaction extraction data over 1976–Sep 2016. | Figshare archive; CML and reaction SMILES/text tables. USPTO bulk patent sources are also public but are not reaction-labelled. | Figshare describes this dataset as CC0. Check individual files and retain source attribution. Patent documents themselves can have distinct legal considerations from the extracted dataset. | Patent text-mined reactions, not independently validated experiments. Useful evidence for reported patent pathways, patent IDs/years, and reported conditions/yields where captured, with text-mining uncertainty. | Audit exact local coverage and compare source IDs/checksums first. If adding partitions, import incrementally with patent number, paragraph/source record ID, original yield fields, extraction metadata, and deduplication. Never equate patent co-occurrence with pathway support. |
| **NIST Chemistry WebBook, SRD 69** ([WebBook](https://webbook.nist.gov/chemistry/), [NIST SRD terms](https://www.nist.gov/srd/public-law)) | Roughly 7,000+ compounds with thermochemical data, 8,000–9,000 reaction thermochemistry entries, and larger spectral/GC collections (including tens of thousands of spectra/chromatographic records). | Searchable web interface and per-record downloads; no unrestricted complete bulk package/API was identified in the reviewed pages. Data formats vary by property. | **Important restriction:** NIST states SRD material is copyrighted and may not be reproduced, stored, or transmitted without permission under the SRD Act. | Evaluated/compiled reference values, thermochemistry and spectra; useful for physical properties and reaction thermochemistry, not general synthetic pathway evidence or pair compatibility. | Do not bulk import absent written permission. Link to records or use permitted individual access. Seek NIST permission before storing/re-distributing data. |
| **CAMEO Chemicals** ([about](https://cameochemicals.noaa.gov/about), [terms](https://cameochemicals.noaa.gov/help/reference/terms_and_conditions.htm), [EPA CAMEO page](https://www.epa.gov/cameo/cameo-chemicals-software)) | Thousands of chemical datasheets and reactivity/mixture guidance entries. | Web application and downloadable/mobile/desktop CAMEO software; no clearly unrestricted complete structured bulk dataset was identified. | Software is free, but terms identify contributed/proprietary fields that cannot be duplicated without written permission, including CAS Registry data, some synonyms/formulas, NFPA ratings, AEGLs and ERPGs. | Curated/aggregated emergency-response hazards and reactivity guidance; especially relevant to compatibility and emergency safety, but not a set of experimentally demonstrated reaction pathways. | High safety relevance, but initially link/cite the official record. Request permission and perform field-level license review before ingesting any content. Exclude restricted fields unless licensed. |
| **Rhea** ([statistics](https://www.rhea-db.org/statistics), [downloads](https://www.rhea-db.org/help/download), [license](https://www.rhea-db.org/help/license-disclaimer)) | Release 143 (2026-11-04): about 19,005 unique reaction quartets and 15,555 participants; most are approved, with a small preliminary set. | Download formats include RDF, BioPAX, RXN, RD, TSV and structures. | CC BY 4.0, commercial reuse allowed with attribution under its license statement. | Expert-curated biochemical enzyme reactions, some preliminary; useful for metabolism/biochemistry but narrow for ordinary synthetic chemical compatibility. | Optional, separate biochemical reaction source. Preserve approval status, enzyme/EC information, organism/context and release; do not mix with general synthetic reaction evidence. |
| **NIOSH Pocket Guide** ([official guide](https://www.cdc.gov/niosh/npg/npg.html)) | Hundreds of workplace chemical entries/classes. | Web, PDFs and mobile access; the reviewed page did not establish an unrestricted full structured API/bulk export. | Bulk reuse terms were not confirmed in the reviewed official material. | Curated/compiled occupational safety data: exposure limits, IDLH, properties, protection, incompatibilities/reactivities and first aid. Useful for hazards and incompatibility context, not detailed synthetic reaction evidence. | Link to records and investigate CDC reuse/data access terms before any ingestion. If later permitted, import with original source/date and distinguish occupational limits from GHS classifications. |

**Other sources:** EPA ToxCast, ToxRefDB and ToxValDB are covered under CompTox; select toxicology tables rather than indiscriminately loading every assay. Paid/proprietary reaction and chemistry databases are outside the project constraints and are not recommended. Public patent full text is not itself a curated reaction dataset; extracting reaction records from it requires a separate validated pipeline and provenance.

## Storage and performance estimates

Known baseline: `chem.db` is about 511 MiB for 1.083M USPTO reaction records plus 5.13M molecule-role rows and the app's smaller tables. That is roughly 0.5 KB per reaction at the current schema and compression/content mix; it is only a rough observed ratio, not a prediction for richer datasets.

The following are planning bands, **not vendor-published sizes or measured import results**:

| Expansion | Rough planning storage | Basis and uncertainty |
|---|---:|---|
| One ORD pilot / selected ORD datasets | 0.2–1.0 GB for a small pilot; 1.5–4 GB for a broad, normalized 2.4M-record import | ORD records can include conditions, measurements, provenance and nested details, so the current compact USPTO row ratio likely understates storage. Exact Parquet and normalized SQLite sizes must be measured on a pilot. |
| Selected CompTox identity + hazard/property tables | 0.5–3 GB | Depends on selected endpoints, study-level detail, identifiers and retained raw fields. Official pages do not establish one universal data-package size. |
| Incremental USPTO source files | Source archive is about 1.39 GB compressed; database growth depends on which grant/application partitions are not already present and how duplicates are handled | Do not add source files already represented. Pilot an exact partition and measure. |
| PubChem full mirror | Not recommended; plan for at least tens of GB and potentially hundreds of GB depending on records and fields | 124.9M compounds plus much larger substance/contributor data; no local full-mirror estimate is justified without defining scope. |
| Rhea | Likely modest relative to the above due to ~19k reaction quartets | Still measure after selecting structures and metadata fields. |

A focused initial expansion could add around 2–7 GB if ORD and selected CompTox tables are both imported; this is a planning reserve only. Run a bounded pilot, measure actual database/WAL/index growth, and leave free disk headroom for temporary staging, backups, and SQLite maintenance. Do not attempt a large import on a constrained Streamlit Cloud local filesystem. Streamlit Community Cloud's ephemeral/local disk is not a suitable durable home for a multi-GB evolving corpus; for this plan, keep the current local SQLite workflow and evaluate persistent storage separately before production scale.

For SQLite, batch writes, keyset pagination, bounded transactions, WAL-aware disk planning, and indexing after bulk staging are important. Avoid `fetchall()` across millions of rows and Python sets containing every source key. Avoid loading complete datasets into memory. Add indexes only to support actual query patterns and verify with `EXPLAIN QUERY PLAN`; excessive indexes increase import time and disk use.

## Recommended additive data architecture

Add a provenance layer without replacing existing tables. Existing tables remain readable during a staged transition; new records enter new source-aware tables or explicitly additive columns/tables. Preserve every existing row and the existing `pair_reactions` status/check decisions.

### Core entities

| Entity/table (proposed) | Key fields and purpose |
|---|---|
| `dataset_source` | Stable source key, publisher, canonical URL, source-level license/terms URI, contact/notes. |
| `dataset_release` | Source FK, release/version/date, retrieval timestamp, source manifest, file name, file checksum, declared row counts, schema version and license snapshot. |
| `import_run` | Release FK, start/end, status, checkpoint/cursor, input/output/rejected counts, error summary and importer version. Supports resume and audit. |
| `chemical_entity` | Internal stable chemical ID and preferred display identity; does not assume name or CAS is globally unique. |
| `chemical_identifier` | Chemical ID, namespace/value (PubChem CID/SID, DTXSID, CAS RN, source ID), source/release, match method and confidence. Index `(namespace,value)`. CAS is an identifier, not a sole primary key. |
| `chemical_structure` | Raw structure, normalized canonical isomeric SMILES, InChI/InChIKey when available, standardizer/RDKit version, charge, stereo/salt/tautomer notes, source and validation result. Keep raw and standardized forms. |
| `reaction_record` | Source/release/source record ID, evidence class, raw record pointer/payload, reaction sides, year/patent/paragraph or ORD record ID, extraction/curation method, source confidence and import provenance. Unique on `(release_id, source_record_id)` when source IDs exist. |
| `reaction_participant` | Reaction ID, chemical/structure ID, side, role (`reactant`, `reagent`, `catalyst`, `solvent`, `product`, `unknown`), stoichiometry, participant order, raw source role. Index by structure and role. |
| `reaction_condition` | Reaction ID, field/value/unit, reported-vs-normalized state, exact source text/pointer, method and provenance. Missing conditions remain missing. |
| `hazard_assertion` | Chemical ID, hazard namespace/classification, statement/code, value, source/release/record, jurisdiction, effective/version date, evidence type, retrieval date, raw assertion and verification status. Keep official, curated, and AI-proposed assertions separate. |
| `physical_property_measurement` | Chemical ID, property/value/unit, method, temperature/pressure, uncertainty, phase, source record and evidence type. Do not collapse conditions or units. |
| `compatibility_rule` | Curated rule, scope/chemical group, outcome/severity reference, rationale, source/citation, reviewer and review status. AI hypotheses must not be stored as curated rules. |
| `reaction_template` | Existing curated/RDKit template identity, SMARTS, source, version, validation state and evidence category. Keep predictions separate from records. |
| `human_review_decision` | Immutable/revisioned human decision linked to pair/pathway/source record, reviewer, decision, timestamp and rationale. Existing embedded statuses remain preserved while the new model is introduced. |

All source-derived rows should retain source ID, dataset release, original record/field, retrieval/import time and license attribution. Store raw source artifacts outside Git, with checksums and restricted-access notes where applicable. Store large raw blobs as files or source-object references rather than duplicating all source payload text into indexed SQLite columns.

### Identity and deduplication rules

Use stable source identifiers first: PubChem CID/SID, EPA DTXSID, ORD dataset + record ID, patent + paragraph/reaction ID, and Rhea ID. Retain CAS and names as aliases, never as the only key. Maintain source-specific records even when two sources describe the same chemistry; a cross-source canonical match is a link, not a destructive merge.

Normalize structures with a recorded, versioned standardization pipeline. Preserve original structure strings and atom mapping; retain stereochemistry, charge, isotopes, salts, and mixture context. Do not canonicalize away stereochemistry or strip salts without an explicit use-case policy. Use InChIKey/canonical isomeric SMILES as candidate matching keys, not proof of identity for every tautomer/mixture. Every match should retain its method and reviewability.

Keep evidence categories explicit at query and display boundaries:

- patent-extracted record;
- structured laboratory/ORD record;
- curated reaction or compatibility rule;
- RDKit/template prediction;
- other computational prediction;
- AI-generated hypothesis;
- human decision.

Do not rank an AI hypothesis above source-supported evidence based on model confidence. Do not treat a source's mere mention of both input compounds as direct reaction evidence. A direct pathway match needs role-aware participant evidence and compatible product/pathway evidence; if those are not available, say no direct supporting record was identified.

## Safe import pipeline design

1. **Register and gate the source.** Record official download/API URL, release, terms/license, commercial redistribution permission, attribution, allowed fields, evidence type, date accessed and intended use. Stop if redistribution terms are unclear for a planned local persistent copy.
2. **Create an immutable source manifest.** Keep downloaded artifacts outside the app's CSV folder and Git. Store file checksums, byte sizes, expected release IDs, schema description and source-provided counts. Do not overwrite earlier release files.
3. **Stage and inspect a small sample.** Stream a small dataset slice. Validate schema, identifier coverage, role mappings, structure parse rate, license-sensitive columns, and estimated row/index growth. Record rejected rows with reasons; do not silently discard them.
4. **Import idempotently in bounded batches.** Use a `dataset_release` + stable source record key uniqueness constraint. Read with streaming/Parquet row groups and keyset checkpoints. Write bounded transactions and checkpoint only after commit. Use UPSERT only on newly owned source rows; never `REPLACE` a whole chemical/reaction or delete related rows as a side effect.
5. **Preserve provenance and evidence semantics.** Store source IDs and original/raw values alongside normalized values. Keep patent-extracted, laboratory, curated, model-predicted, and AI records separately queryable. Do not combine reported and inferred conditions/yields.
6. **Validate before promotion.** Compare source and imported counts; check duplicate source IDs, FK integrity, role distributions, parse/normalization counts, year/ID ranges, checksum, and sample round-trip. Validate no change in existing legacy table counts/hashes where practical, and specifically confirm reviewed pair state is unchanged.
7. **Promote additively.** Build any new source indexes after staging/import; run `ANALYZE`; test representative query plans and latency. Enable the new source in lookup behind a configuration/source flag only after quality review.
8. **Resume or roll back safely.** Resume from `import_run` checkpoint after a crash. To roll back, disable the release/source in application queries or remove only rows keyed to that release after an independently verified backup and explicit approval. Prefer a reversible `active=false` flag over deletion. Existing records and review decisions are outside the source-release rollback scope.
9. **Back up and verify.** Use SQLite's online backup API or a consistent snapshot before any eventual write; verify backup integrity and checksums. Preserve the original `chem.db`, do not rely on copying an actively written file. This audit did not create a backup because it performed no writes.

### SQLite-specific query and import improvements for a later implementation

- Add unique `(dataset_release_id, source_record_id)` keys to new source tables; add indexes on chemical identifier namespace/value, participant structure/role/reaction, reaction source/release/year/patent, and hazard chemical/source/classification.
- Consider a unique source key for future imported USPTO records only after assessing existing duplicates and building a non-destructive reconciliation report; do not add a constraint that could fail or mutate legacy rows without a migration plan.
- Make USPTO lookups role-aware and return participant roles. Index `(smiles, role, rxn_id)` or equivalent after query-plan testing. Keep reagent-only co-occurrence from being presented as direct reactant-reactant evidence.
- Replace in-memory all-key duplicate sets with database uniqueness and bounded staging joins. Do not drop production lookup indexes during incremental imports.
- Use `fetchmany`, row groups, keyset pagination, and batched `executemany`; avoid full corpus reads and large `IN` clauses.
- Update cache provenance incrementally and avoid repeated PubChem calls by resolving one canonical chemical lookup result per request and reusing it.
- Keep CSVs for the small curated seed corpus and human-edited rules/templates only.

## Prioritized implementation phases

### Phase 0 — Governance and baseline (no data writes)

Record current DB/table/index counts and checksums, document existing human review fields, license source list, and define evidence categories and chemical standardization policy. Identify exact USPTO source release/files represented locally. Keep an untouched backup before later additive migrations.

### Phase 1 — Provenance foundation (schema-only, additive)

Introduce versioned, backup-protected additive migrations for source/release/import-run tables and source-aware record identifiers. Do not rebuild existing tables or rewrite/delete current rows. Provide compatibility views/adapters so current app workflows continue to function. Add a dry-run migration report and rollback procedure before applying to the working database.

### Phase 2 — EPA CompTox pilot

Select DSSTox identity mapping and a bounded set of toxicity/property endpoints tied to current product/reactant screening. Import a single release, measure storage and query latency, validate links to existing chemical identifiers, and inspect conflicting sources. Keep assay observations and modeled values distinct. Advance only if the pilot improves coverage without overwhelming the UI/query model.

### Phase 3 — ORD pilot and reaction evidence

Select one or several well-documented datasets with useful coverage and compatible license obligations. Import source IDs, exact dataset metadata, reactants/reagents/catalysts/solvents/products, recorded conditions and measurements. Audit structure parsing and duplicated record handling. Provide per-pathway evidence details and explicit role-aware matching before broad ORD import.

### Phase 4 — Incremental USPTO provenance and retrieval quality

Reconcile local data against exact Lowe release partitions, preserve patent and paragraph identifiers, original yield fields, and extraction provenance, then add only absent records. Improve role-aware indexes and retrieval. Keep patent text extraction quality limits visible.

### Phase 5 — On-demand identity/property enrichment

Improve PubChem cache provenance, source-aware refresh, rate limiting and request deduplication. Import only a defined list of compounds or selected fields when bulk access is materially more efficient. Do not mirror all PubChem.

### Phase 6 — Specialized sources and ongoing updates

Optionally ingest Rhea in a separate biochemical category. Link NIOSH/CAMEO/NIST records unless data reuse permission supports local ingestion. Define release update cadence, source revalidation, cache expiry, duplicate monitoring and license review before each update.

## Licensing, scientific quality, and operational limits

- “Publicly accessible” does not automatically mean unrestricted redistribution. Maintain license and attribution at source and, where possible, field level.
- ORD's CC BY-SA terms can impose obligations on redistribution of adapted database content. Review legal compatibility with the application distribution model before importing broadly.
- NIST SRD 69 explicitly restricts reproduction/storage absent permission. CAMEO contains fields with third-party restrictions. Do not scrape or bulk-copy these sources under this plan.
- PubChem is an aggregation; determine the contributor/source and applicable terms for each reused dataset/field.
- Patent-text-mined data can contain false mappings, duplicates and extracted yields/conditions that require normalization and source-text traceability. Preserve raw values and label the extraction origin.
- ORD and Rhea have their own scope limitations; Rhea is biochemical. Do not imply general chemical compatibility from reaction presence.
- Hazards must remain attached to their authority, jurisdiction, version, substance identity resolution and source statement. Conflicting classifications should be shown with provenance; AI must never override official records.
- SQLite is appropriate for this local read-heavy application at current scale if imports are batched and indexed. Multi-GB corpora and concurrent cloud writes require separate persistent-storage planning; do not place the authoritative corpus in an ephemeral Streamlit Cloud filesystem.

## Official references

All links below were reviewed on **2026-11-24**. Source statistics are snapshots and may change.

- PubChem statistics, downloads and PUG REST usage: [Statistics](https://pubchem.ncbi.nlm.nih.gov/docs/statistics/), [Downloads](https://pubchem.ncbi.nlm.nih.gov/docs/downloads), [PUG REST tutorial](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest-tutorial)
- EPA CompTox: [Dashboard](https://comptox.epa.gov/dashboard/), [Dashboard overview](https://www.epa.gov/comptox-tools/comptox-chemicals-dashboard-about), [Downloadable computational toxicology data](https://www.epa.gov/comptox-tools/downloadable-computational-toxicology-data), [APIs](https://www.epa.gov/comptox-tools/computational-toxicology-and-exposure-apis)
- ORD: [ORD site](https://open-reaction-database.org/), [Data documentation](https://docs.open-reaction-database.org/en/latest/data.html)
- USPTO reaction data and public patent datasets: [Lowe dataset on Figshare](https://figshare.com/articles/dataset/Chemical_reactions_from_US_patents_1976-Sep2016_/5104873), [USPTO research datasets](https://www.uspto.gov/ip-policy/economic-research/research-datasets)
- NIST: [Chemistry WebBook](https://webbook.nist.gov/chemistry/), [Standard Reference Data legal notice](https://www.nist.gov/srd/public-law)
- CAMEO: [About](https://cameochemicals.noaa.gov/about), [Terms](https://cameochemicals.noaa.gov/help/reference/terms_and_conditions.htm), [EPA CAMEO page](https://www.epa.gov/cameo/cameo-chemicals-software)
- Rhea: [Statistics](https://www.rhea-db.org/statistics), [Downloads](https://www.rhea-db.org/help/download), [License](https://www.rhea-db.org/help/license-disclaimer)
- NIOSH: [Pocket Guide](https://www.cdc.gov/niosh/npg/npg.html)

## Audit conclusion

The strongest practical expansion is a provenance-first, additive design followed by selective CompTox hazard/property data and a measured ORD reaction pilot. Continue PubChem as a targeted online/cache service, and improve the existing USPTO retrieval semantics before adding more patent rows. Do not bulk-ingest NIST or CAMEO until permissions are resolved. The current database and curated CSVs should remain intact throughout all future phases.
