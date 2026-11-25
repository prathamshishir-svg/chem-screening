-- EPA ToxValDB v9.7.0 source-backed assertions and source identities.
-- These data are not GHS classifications and do not alter existing hazard tables.
CREATE TABLE epa_toxval_chemicals (
    id                       INTEGER PRIMARY KEY,
    release_id               INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    source_chemical_id       TEXT NOT NULL,
    source                   TEXT,
    raw_casrn                TEXT,
    raw_name                 TEXT,
    cleaned_casrn            TEXT,
    cleaned_name             TEXT,
    casrn                    TEXT,
    name                     TEXT,
    dtxsid                   TEXT,
    dtxrid                   TEXT,
    quality                  TEXT,
    flags                    TEXT,
    parent_chemical_id       TEXT,
    chemical_id_rehash_orig  TEXT,
    local_match_type         TEXT NOT NULL,
    local_match_value        TEXT,
    raw_record_json          TEXT NOT NULL,
    UNIQUE (release_id, source_chemical_id),
    CHECK (length(source_chemical_id) > 0),
    CHECK (local_match_type IN ('pubchem_cas','curated_cas','pubchem_name','curated_name','unmatched'))
);

CREATE TABLE epa_toxval_species (
    release_id          INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    source_species_id   INTEGER NOT NULL,
    common_name         TEXT,
    latin_name          TEXT,
    kingdom             TEXT,
    phylum_division     TEXT,
    subphylum_div       TEXT,
    superclass         TEXT,
    tax_order           TEXT,
    family              TEXT,
    genus               TEXT,
    species             TEXT,
    subspecies          TEXT,
    variety             TEXT,
    ecotox_group        TEXT,
    habitat             TEXT,
    raw_record_json     TEXT NOT NULL,
    PRIMARY KEY (release_id, source_species_id)
);

CREATE TABLE epa_toxval_types (
    release_id                    INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    source_type_id                INTEGER NOT NULL,
    toxval_type                   TEXT,
    display                       TEXT,
    toxval_type_category          TEXT,
    toxval_type_supercategory     TEXT,
    fullname                      TEXT,
    abbreviation                  TEXT,
    definition                    TEXT,
    definition_source             TEXT,
    units_default                 TEXT,
    responsible_organization      TEXT,
    raw_record_json               TEXT NOT NULL,
    PRIMARY KEY (release_id, source_type_id)
);

CREATE TABLE epa_toxval_assertions (
    id                           INTEGER PRIMARY KEY,
    release_id                   INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    toxval_id                    INTEGER NOT NULL,
    source_hash                  TEXT,
    source_table                 TEXT,
    chemical_source_id           TEXT,
    chemical_id                  INTEGER REFERENCES epa_toxval_chemicals(id) ON DELETE RESTRICT,
    identity_match_status        TEXT NOT NULL CHECK (identity_match_status IN ('matched','unmatched')),
    dtxsid                       TEXT,
    source                       TEXT,
    subsource                    TEXT,
    source_url                   TEXT,
    subsource_url                TEXT,
    details_text                 TEXT,
    qc_status                    TEXT,
    qc_category                  TEXT,
    risk_assessment_class        TEXT,
    human_eco                    TEXT,
    experimental_record          TEXT,
    toxval_type                  TEXT,
    toxval_type_original         TEXT,
    toxval_subtype               TEXT,
    toxval_subtype_original      TEXT,
    toxval_type_supercategory    TEXT,
    toxval_type_definition       TEXT,
    toxval_numeric               REAL,
    toxval_numeric_raw           TEXT,
    toxval_numeric_original      REAL,
    toxval_numeric_original_raw  TEXT,
    toxval_units                 TEXT,
    toxval_units_original        TEXT,
    toxval_numeric_qualifier     TEXT,
    toxval_numeric_qualifier_original TEXT,
    study_type                   TEXT,
    study_type_original          TEXT,
    study_duration_class         TEXT,
    study_duration_class_original TEXT,
    study_duration_value         REAL,
    study_duration_value_original TEXT,
    study_duration_units         TEXT,
    study_duration_units_original TEXT,
    species_id                   INTEGER,
    species_original             TEXT,
    species_common               TEXT,
    species_latin                TEXT,
    species_supercategory        TEXT,
    strain                       TEXT,
    strain_original              TEXT,
    strain_group                 TEXT,
    habitat                      TEXT,
    sex                          TEXT,
    sex_original                 TEXT,
    toxicological_effect         TEXT,
    toxicological_effect_original TEXT,
    population                   TEXT,
    population_original          TEXT,
    exposure_route               TEXT,
    exposure_route_original      TEXT,
    exposure_method              TEXT,
    exposure_method_original     TEXT,
    exposure_form                TEXT,
    exposure_form_original       TEXT,
    media                        TEXT,
    media_original               TEXT,
    lifestage                    TEXT,
    lifestage_original           TEXT,
    generation                   TEXT,
    generation_original          TEXT,
    year                         TEXT,
    year_original                TEXT,
    molecular_weight             REAL,
    datestamp                    TEXT,
    source_source_id             INTEGER,
    toxval_uuid                  TEXT,
    toxval_hash                  TEXT,
    study_group                  TEXT,
    visible                      INTEGER,
    key_finding                  TEXT,
    supersource                  TEXT,
    raw_record_json              TEXT NOT NULL,
    UNIQUE (release_id, toxval_id),
    CHECK (length(raw_record_json) > 1)
);

CREATE TABLE epa_toxval_record_sources (
    id                       INTEGER PRIMARY KEY,
    release_id               INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    toxval_id                INTEGER NOT NULL,
    record_source_id         INTEGER NOT NULL,
    record_source_hash       TEXT,
    record_source_uuid       TEXT,
    source                   TEXT,
    record_source_type       TEXT,
    record_source_note       TEXT,
    record_source_level      TEXT,
    url                      TEXT,
    document_name            TEXT,
    long_ref                 TEXT,
    title                    TEXT,
    author                   TEXT,
    journal                  TEXT,
    volume                   TEXT,
    year                     TEXT,
    issue                    TEXT,
    page                     TEXT,
    doi                      TEXT,
    pmid                     INTEGER,
    guideline                TEXT,
    glp                      TEXT,
    quality                  TEXT,
    source_study_id          INTEGER,
    clowder_doc_id           TEXT,
    clowder_doc_metadata     TEXT,
    priority                 INTEGER,
    external_source_id       TEXT,
    external_source_id_desc  TEXT,
    raw_record_json          TEXT NOT NULL,
    FOREIGN KEY (release_id, toxval_id)
        REFERENCES epa_toxval_assertions(release_id, toxval_id) ON DELETE RESTRICT,
    UNIQUE (release_id, record_source_id)
);

CREATE INDEX idx_epa_toxval_chemical_dtxsid
    ON epa_toxval_chemicals(release_id, dtxsid);
CREATE INDEX idx_epa_toxval_chemical_casrn
    ON epa_toxval_chemicals(release_id, casrn);
CREATE INDEX idx_epa_toxval_chemical_name
    ON epa_toxval_chemicals(release_id, name COLLATE NOCASE);
CREATE INDEX idx_epa_toxval_chemical_local_match
    ON epa_toxval_chemicals(local_match_type);
CREATE INDEX idx_epa_toxval_assertion_identity
    ON epa_toxval_assertions(release_id, dtxsid, chemical_id);
CREATE INDEX idx_epa_toxval_assertion_endpoint
    ON epa_toxval_assertions(release_id, toxval_type, toxval_subtype);
CREATE INDEX idx_epa_toxval_assertion_source_id
    ON epa_toxval_assertions(release_id, toxval_id);
CREATE INDEX idx_epa_toxval_assertion_identity_fk
    ON epa_toxval_assertions(chemical_id, identity_match_status);
CREATE INDEX idx_epa_toxval_record_sources_toxval
    ON epa_toxval_record_sources(release_id, toxval_id);
CREATE INDEX idx_epa_toxval_record_sources_doi
    ON epa_toxval_record_sources(doi);
