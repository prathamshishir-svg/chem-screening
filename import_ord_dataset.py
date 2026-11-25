"""Stream a published ORD Parquet dataset into source-aware SQLite tables.

Uses PyArrow row groups and the official ord-schema protobuf definitions. It
never downloads data and never deletes/replaces existing reaction records.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors
from ord_schema.proto import reaction_pb2

from chemkeys import parse_reaction_smiles
from dataset_imports import (
    commit_import_batch,
    create_or_resume_import_run,
    register_dataset_release,
    register_dataset_source,
    set_import_run_status,
)
from import_uspto import make_key
from migrations import migrate_database, validate_migration_state


SOURCE_KEY = "open-reaction-database"
SOURCE_NAME = "Open Reaction Database"
SOURCE_URL = "https://github.com/open-reaction-database/ord-data"
LICENSE_NAME = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = "Open Reaction Database; dataset-level attribution retained per ORD metadata"
BATCH_SIZE = 2000


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _enum_name(message, field_name, number):
    enum_type = message.DESCRIPTOR.fields_by_name[field_name].enum_type
    value = enum_type.values_by_number.get(int(number))
    return value.name if value else f"UNKNOWN_{number}"


def _reaction_smiles(reaction):
    for identifier in reaction.identifiers:
        if _enum_name(identifier, "type", identifier.type) == "REACTION_SMILES":
            if ">>" in identifier.value:
                return identifier.value
    return None


def _component_smiles(component):
    for identifier in component.identifiers:
        kind = _enum_name(identifier, "type", identifier.type)
        if kind in ("SMILES", "CANONICAL_SMILES") and identifier.value:
            return identifier.value, kind
    return None, None


def _standardize_structure(raw_smiles):
    mol = Chem.MolFromSmiles(raw_smiles)
    if mol is None:
        return None
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    return mol, canonical


def _property_record(raw_smiles, canonical, mol):
    formula = rdMolDescriptors.CalcMolFormula(mol)
    weight = float(Descriptors.MolWt(mol))
    try:
        inchikey = Chem.MolToInchiKey(mol) or None
    except Exception:
        inchikey = None
    return (canonical, raw_smiles, inchikey, formula, weight,
            f"RDKit {rdBase.rdkitVersion} (calculated from source structure)")


def _participant_rows(reaction, ord_record_id, release_id, source_record_id,
                      next_structure_id, structure_ids, structures):
    rows = []
    position = 0

    def collect(side, components):
        nonlocal position
        for component in components:
            smiles, smiles_type = _component_smiles(component)
            molecule = _standardize_structure(smiles) if smiles else None
            mol, standardized = molecule if molecule else (None, None)
            structure_id = None
            if smiles and standardized:
                structure_id = structure_ids.get(standardized)
                if structure_id is None:
                    structure_id = next_structure_id[0]
                    next_structure_id[0] += 1
                    structure_ids[standardized] = structure_id
                    structure = _property_record(smiles, standardized, mol)
                    if structure:
                        structures.append((structure_id,) + structure +
                                          (release_id, source_record_id))
                    else:
                        structure_ids.pop(standardized, None)
                        structure_id = None
            rows.append((ord_record_id, structure_id, side,
                         _enum_name(component, "reaction_role", component.reaction_role),
                         position,
                         smiles_type,
                         smiles,
                         next((identifier.details for identifier in component.identifiers
                               if identifier.value == smiles), None),
                         component.SerializeToString(deterministic=True), standardized))
            position += 1

    for key in sorted(reaction.inputs):
        collect("input", reaction.inputs[key].components)
    for outcome in reaction.outcomes:
        for product in outcome.products:
            collect("product", [product])
    return rows


def import_dataset(dataset_path, database_path, *, batch_size=BATCH_SIZE):
    dataset_path = Path(dataset_path).resolve()
    database_path = Path(database_path).resolve()
    if not dataset_path.is_file():
        raise FileNotFoundError(dataset_path)
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    # Applying only numbered additive migrations; no base-table refresh occurs.
    migrate_database(database_path)
    validate_migration_state(database_path)

    parquet_file = pq.ParquetFile(dataset_path)
    metadata = parquet_file.metadata.metadata or {}
    dataset_id = metadata.get(b"ord.dataset_id", b"").decode("utf-8")
    dataset_name = metadata.get(b"ord.name", b"").decode("utf-8")
    dataset_description = metadata.get(b"ord.description", b"").decode("utf-8")
    if not dataset_id or not dataset_name:
        raise ValueError("ORD dataset footer lacks its dataset ID or name")
    total_rows = parquet_file.metadata.num_rows
    checksum = _sha256(dataset_path)
    release_key = f"ord-data:{dataset_id}"
    con = sqlite3.connect(database_path)
    try:
        existing_release = con.execute(
            "SELECT retrieved_at FROM dataset_release WHERE release_key=?", (release_key,)
        ).fetchone()
    finally:
        con.close()
    retrieved_at = (existing_release[0] if existing_release else
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
    dataset_folder = dataset_id.removeprefix("ord_dataset-")[:2]
    file_url = ("https://huggingface.co/datasets/open-reaction-database/ord-data/resolve/main/"
                f"data/{dataset_folder}/{dataset_path.name}")

    source_result = register_dataset_source(
        database_path, source_key=SOURCE_KEY, name=SOURCE_NAME,
        publisher="Open Reaction Database Consortium", canonical_url=SOURCE_URL,
        api_url="https://huggingface.co/datasets/open-reaction-database/ord-data",
        license_name=LICENSE_NAME, license_url=LICENSE_URL,
        attribution=ATTRIBUTION, description="Official ORD Parquet dataset repository.",
    )
    release_result = register_dataset_release(
        database_path, source_id=source_result["id"],
        release_key=release_key, release_version=dataset_id,
        release_date=None, source_url=file_url, retrieved_at=retrieved_at,
        license_name=LICENSE_NAME, license_url=LICENSE_URL,
        attribution=f"{ATTRIBUTION}; {dataset_name}", artifact_name=dataset_path.name,
        artifact_size_bytes=dataset_path.stat().st_size,
        content_checksum=checksum, source_row_count=total_rows,
    )
    release_id = release_result["id"]
    run_key = f"{dataset_id}:{checksum}"
    run = create_or_resume_import_run(
        database_path, release_id=release_id, idempotency_key=run_key,
        source_row_count=total_rows,
    )
    run_id = run["id"]
    checkpoint = json.loads(run["checkpoint_json"])
    last_checkpoint = checkpoint
    start_row = int(checkpoint.get("next_row", 0))
    processed = run["processed_row_count"]
    inserted = run["inserted_row_count"]
    rejected = run["rejected_row_count"]
    skipped = run["skipped_row_count"]

    con = sqlite3.connect(database_path)
    try:
        existing_key_to_id = {
            int(key): int(row_id) for key, row_id in con.execute(
                "SELECT rkey,id FROM rxn_records WHERE rkey IS NOT NULL"
            )
        }
        ord_key_to_id = {
            int(key): int(rxn_id) for key, rxn_id in con.execute(
                "SELECT identity_key,rxn_id FROM ord_reaction_records "
                "WHERE release_id=? AND identity_key IS NOT NULL AND rxn_id IS NOT NULL",
                (release_id,),
            )
        }
        structure_ids = {
            smiles: int(structure_id) for structure_id, smiles in con.execute(
                "SELECT id,canonical_smiles FROM ord_chemical_structures"
            )
        }
        next_rxn_id = con.execute("SELECT COALESCE(MAX(id),0)+1 FROM rxn_records").fetchone()[0]
        next_ord_id = con.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM ord_reaction_records"
        ).fetchone()[0]
        next_structure_id = [con.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM ord_chemical_structures"
        ).fetchone()[0]]
    finally:
        con.close()

    reaction_rows = []
    molecule_rows = []
    ord_rows = []
    participant_rows = []
    structure_rows = []
    batch_keys = set()
    last_row = start_row
    invalid_batch = duplicate_batch = inserted_batch = 0

    def flush(next_row):
        nonlocal reaction_rows, molecule_rows, ord_rows, participant_rows, structure_rows
        nonlocal batch_keys, invalid_batch, duplicate_batch, inserted_batch
        nonlocal last_checkpoint
        if not ord_rows:
            return
        counts = {
            "processed_row_count": processed,
            "inserted_row_count": inserted,
            "updated_row_count": 0,
            "rejected_row_count": rejected,
            "skipped_row_count": skipped,
        }

        def write_batch(connection):
            if structure_rows:
                connection.executemany(
                    "INSERT INTO ord_chemical_structures "
                    "(id,canonical_smiles,representative_raw_smiles,inchi_key,molecular_formula,"
                    "molecular_weight,calculated_by,first_release_id,first_source_record_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", structure_rows,
                )
            if reaction_rows:
                connection.executemany(
                    "INSERT INTO rxn_records "
                    "(id,reactants,reagents,products,year,patent,yield,source,n_mols,rkey) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", reaction_rows,
                )
            connection.executemany(
                "INSERT INTO ord_reaction_records "
                "(id,release_id,dataset_id,dataset_name,dataset_description,source_record_id,"
                "source_doi,is_mined,reaction_smiles,identity_key,identity_status,rxn_id,"
                "raw_proto,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ord_rows,
            )
            if participant_rows:
                connection.executemany(
                    "INSERT INTO ord_reaction_participants "
                    "(ord_record_id,structure_id,side,source_role,participant_index,"
                    "identifier_type,identifier_value,identifier_details,raw_component,"
                    "standardized_smiles) VALUES (?,?,?,?,?,?,?,?,?,?)", participant_rows,
                )
            if molecule_rows:
                connection.executemany(
                    "INSERT INTO rxn_molecules(rxn_id,smiles,role) VALUES (?,?,?)", molecule_rows,
                )

        commit_import_batch(
            database_path, run_id=run_id,
            expected_checkpoint=last_checkpoint,
            next_checkpoint={"next_row": next_row}, counts=counts,
            write_batch=write_batch,
        )
        last_checkpoint = {"next_row": next_row}
        reaction_rows = []
        molecule_rows = []
        ord_rows = []
        participant_rows = []
        structure_rows = []
        batch_keys = set()
        invalid_batch = duplicate_batch = inserted_batch = 0

    row_index = 0
    try:
        for arrow_batch in parquet_file.iter_batches(batch_size=batch_size, columns=["reaction_id", "reaction"]):
            ids = arrow_batch.column(0).to_pylist()
            blobs = arrow_batch.column(1).to_pylist()
            for source_record_id, blob in zip(ids, blobs):
                current_index = row_index
                row_index += 1
                if current_index < start_row:
                    continue
                reaction = reaction_pb2.Reaction.FromString(blob)
                reaction_id = source_record_id or reaction.reaction_id
                rxn_smiles = _reaction_smiles(reaction)
                parsed = parse_reaction_smiles(rxn_smiles) if rxn_smiles else None
                key = make_key(*parsed) if parsed else None
                status = "unparsed"
                rxn_id = None
                if key is not None:
                    if key in batch_keys or key in ord_key_to_id:
                        status = "duplicate_in_source"
                        rxn_id = ord_key_to_id.get(key)
                        duplicate_batch += 1
                    elif key in existing_key_to_id:
                        status = "matched_existing"
                        rxn_id = existing_key_to_id[key]
                        ord_key_to_id[key] = rxn_id
                        duplicate_batch += 1
                    else:
                        status = "new_unique"
                        rxn_id = next_rxn_id
                        next_rxn_id += 1
                        reactants, reagents, products = parsed
                        input_molecules = list(dict.fromkeys(reactants + reagents))
                        reaction_rows.append((rxn_id, ".".join(reactants), ".".join(reagents),
                                              ".".join(products), None, None, None,
                                              "ORD-mined", len(input_molecules), key))
                        molecule_rows.extend((rxn_id, smiles,
                                              "G" if smiles in reagents else "R")
                                             for smiles in input_molecules)
                        existing_key_to_id[key] = rxn_id
                        ord_key_to_id[key] = rxn_id
                        batch_keys.add(key)
                        inserted += 1
                        inserted_batch += 1
                else:
                    rejected += 1
                    invalid_batch += 1

                ord_record_id = next_ord_id
                next_ord_id += 1
                dois = [doi for doi in reaction.provenance.doi if doi]
                provenance = json.dumps({
                    "source_doi": dois,
                    "is_mined": bool(reaction.provenance.is_mined),
                    "ord_schema_version": metadata.get(b"ord.schema_version", b"").decode("utf-8"),
                }, sort_keys=True)
                ord_rows.append((ord_record_id, release_id, dataset_id, dataset_name,
                                 dataset_description, reaction_id, ";".join(dois) or None,
                                 int(bool(reaction.provenance.is_mined)), rxn_smiles, key, status,
                                 rxn_id, reaction.SerializeToString(deterministic=True), provenance))
                collected = _participant_rows(
                    reaction, ord_record_id, release_id, reaction_id,
                    next_structure_id, structure_ids, structure_rows,
                )
                participant_rows.extend(collected)
                processed += 1
                skipped += int(status in ("matched_existing", "duplicate_in_source"))
                last_row = current_index + 1
                if len(ord_rows) >= batch_size:
                    flush(last_row)
                    print(f"ORD import progress: {processed:,}/{total_rows:,}; "
                          f"new={inserted:,}; duplicate/matched={skipped:,}; rejected={rejected:,}",
                          flush=True)
        flush(last_row)
        set_import_run_status(database_path, run_id=run_id, status="completed")
    except Exception as exc:
        parquet_file.close()
        try:
            set_import_run_status(
                database_path, run_id=run_id, status="failed",
                safe_error_summary=type(exc).__name__,
            )
        except Exception:
            pass
        raise

    parquet_file.close()

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "total_rows": total_rows,
        "processed_rows": processed,
        "new_unique_reactions": inserted,
        "matched_or_duplicate_rows": skipped,
        "rejected_or_unparsed_rows": rejected,
        "release_id": release_id,
        "import_run_id": run_id,
        "sha256": checksum,
    }


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: python import_ord_dataset.py DATASET.parquet DB_PATH [BATCH_SIZE]")
    print(import_dataset(sys.argv[1], sys.argv[2],
                         batch_size=int(sys.argv[3]) if len(sys.argv) == 4 else BATCH_SIZE))
