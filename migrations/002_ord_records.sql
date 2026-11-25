-- Source-specific ORD preservation. Generic reaction columns remain a search
-- projection only; raw ORD protobuf records and original participant roles stay here.
CREATE TABLE ord_reaction_records (
    id                  INTEGER PRIMARY KEY,
    release_id          INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    dataset_id          TEXT NOT NULL,
    dataset_name        TEXT,
    dataset_description TEXT,
    source_record_id    TEXT NOT NULL,
    source_doi          TEXT,
    is_mined            INTEGER NOT NULL DEFAULT 0 CHECK (is_mined IN (0,1)),
    reaction_smiles     TEXT,
    identity_key        INTEGER,
    identity_status     TEXT NOT NULL CHECK (identity_status IN
                        ('new_unique','matched_existing','duplicate_in_source','unparsed')),
    rxn_id              INTEGER REFERENCES rxn_records(id) ON DELETE RESTRICT,
    raw_proto           BLOB NOT NULL,
    provenance_json     TEXT NOT NULL DEFAULT '{}',
    imported_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (release_id, source_record_id),
    CHECK (length(dataset_id) > 0),
    CHECK (length(source_record_id) > 0),
    CHECK (length(raw_proto) > 0)
);

CREATE TABLE ord_chemical_structures (
    id                      INTEGER PRIMARY KEY,
    canonical_smiles        TEXT NOT NULL UNIQUE,
    representative_raw_smiles TEXT NOT NULL,
    inchi_key               TEXT,
    molecular_formula       TEXT,
    molecular_weight        REAL,
    calculated_by           TEXT NOT NULL,
    first_release_id        INTEGER NOT NULL REFERENCES dataset_release(id) ON DELETE RESTRICT,
    first_source_record_id  TEXT NOT NULL,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(canonical_smiles) > 0),
    CHECK (length(representative_raw_smiles) > 0),
    CHECK (length(calculated_by) > 0)
);

CREATE TABLE ord_reaction_participants (
    id                  INTEGER PRIMARY KEY,
    ord_record_id       INTEGER NOT NULL REFERENCES ord_reaction_records(id) ON DELETE RESTRICT,
    structure_id        INTEGER REFERENCES ord_chemical_structures(id) ON DELETE RESTRICT,
    side                TEXT NOT NULL CHECK (side IN ('input','product')),
    source_role         TEXT NOT NULL,
    participant_index   INTEGER NOT NULL CHECK (participant_index >= 0),
    identifier_type     TEXT,
    identifier_value    TEXT,
    identifier_details  TEXT,
    raw_component       BLOB NOT NULL,
    standardized_smiles TEXT,
    UNIQUE (ord_record_id, side, participant_index),
    CHECK (length(source_role) > 0),
    CHECK (length(raw_component) > 0)
);

CREATE INDEX idx_ord_reaction_identity ON ord_reaction_records(identity_key, identity_status);
CREATE INDEX idx_ord_reaction_record_rxn ON ord_reaction_records(rxn_id);
CREATE INDEX idx_ord_participant_structure ON ord_reaction_participants(structure_id, side, source_role);
CREATE INDEX idx_ord_participant_smiles ON ord_reaction_participants(standardized_smiles, side);
