import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dataset_imports
import migrations


class DatasetImportFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "disposable.db"
        con = sqlite3.connect(self.db_path)
        con.executescript(
            "CREATE TABLE chemicals (cas TEXT PRIMARY KEY, name TEXT);"
            "CREATE TABLE pair_reactions (id INTEGER PRIMARY KEY, pair_key TEXT, "
            "status TEXT, checks TEXT);"
            "CREATE TABLE rxn_records (id INTEGER PRIMARY KEY, patent TEXT);"
            "INSERT INTO chemicals VALUES ('64-17-5','ethanol');"
            "INSERT INTO pair_reactions VALUES (1,'a|b','reviewed','approved');"
            "INSERT INTO pair_reactions VALUES (2,'c|d','rejected','human rejection');"
            "INSERT INTO rxn_records VALUES (1,'US123');"
        )
        con.commit()
        con.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _snapshot_legacy(self):
        con = sqlite3.connect(self.db_path)
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("chemicals", "pair_reactions", "rxn_records")
        }
        decisions = con.execute(
            "SELECT id,pair_key,status,checks FROM pair_reactions ORDER BY id"
        ).fetchall()
        con.close()
        return counts, decisions

    def _registered_release(self):
        source = dataset_imports.register_dataset_source(
            self.db_path,
            source_key="epa_comptox",
            name="EPA CompTox",
            publisher="U.S. EPA",
            canonical_url="https://comptox.epa.gov/dashboard/",
            license_name="EPA open data",
            attribution="U.S. EPA",
        )
        release = dataset_imports.register_dataset_release(
            self.db_path,
            source_id=source["id"],
            release_key="epa_comptox:test-release",
            release_version="test-release",
            source_url="https://example.invalid/release.csv",
            retrieved_at="2026-11-24T00:00:00Z",
            content_checksum="a" * 64,
            source_row_count=10,
        )
        return source, release

    def test_dry_run_is_read_only_and_migration_is_additive_and_repeatable(self):
        before = self._snapshot_legacy()
        dry_run = migrations.migrate_database(self.db_path, dry_run=True)
        self.assertEqual(dry_run["pending_versions"], [1, 2, 3])
        con = sqlite3.connect(self.db_path)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0],
            0,
        )
        con.close()

        first = migrations.migrate_database(self.db_path)
        second = migrations.migrate_database(self.db_path)
        self.assertEqual(first["pending_versions"], [])
        self.assertEqual(second["applied_versions"], [1, 2, 3])
        self.assertTrue(migrations.validate_migration_state(self.db_path)["valid"])
        self.assertEqual(self._snapshot_legacy(), before)

    def test_partial_ddl_failure_rolls_back_and_can_be_retried(self):
        before = self._snapshot_legacy()
        with patch.object(migrations, "_statements", return_value=iter([
            "CREATE TABLE rollback_marker (id INTEGER PRIMARY KEY);",
            "THIS IS INVALID SQL;",
        ])):
            with self.assertRaises(sqlite3.OperationalError):
                migrations.migrate_database(self.db_path)
        con = sqlite3.connect(self.db_path)
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        self.assertNotIn("rollback_marker", names)
        self.assertNotIn("schema_migrations", names)
        self.assertEqual(self._snapshot_legacy(), before)

    def test_new_additive_migration_can_follow_an_already_applied_release(self):
        before = self._snapshot_legacy()
        all_migrations = migrations._migration_files()
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            for migration in all_migrations[:2]:
                for statement in migrations._statements(migration["sql"]):
                    con.execute(statement)
                con.execute(
                    "INSERT INTO schema_migrations(version,name,checksum) VALUES (?,?,?)",
                    (migration["version"], migration["name"], migration["checksum"]),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        self.assertEqual(migrations.migration_status(self.db_path)["applied_versions"], [1, 2])

        next_release = migrations.migrate_database(self.db_path)
        self.assertEqual(next_release["applied_versions"], [1, 2, 3])
        self.assertTrue(migrations.validate_migration_state(self.db_path)["valid"])
        self.assertEqual(self._snapshot_legacy(), before)

        migrations.migrate_database(self.db_path)
        self.assertTrue(migrations.validate_migration_state(self.db_path)["valid"])
        self.assertEqual(self._snapshot_legacy(), before)

    def test_registry_keys_are_idempotent_and_conflicts_are_rejected(self):
        migrations.migrate_database(self.db_path)
        source, release = self._registered_release()
        same_source = dataset_imports.register_dataset_source(
            self.db_path,
            source_key="EPA_COMPTOX",
            name="EPA CompTox",
            publisher="U.S. EPA",
            canonical_url="https://comptox.epa.gov/dashboard/",
            license_name="EPA open data",
            attribution="U.S. EPA",
        )
        self.assertEqual((same_source["id"], same_source["created"]), (source["id"], False))
        with self.assertRaises(dataset_imports.RegistryConflictError):
            dataset_imports.register_dataset_source(
                self.db_path,
                source_key="epa_comptox",
                name="Different source metadata",
                publisher="U.S. EPA",
                canonical_url="https://comptox.epa.gov/dashboard/",
                license_name="EPA open data",
                attribution="U.S. EPA",
            )
        self.assertTrue(release["created"])

    def test_run_resume_batch_atomicity_and_replay_idempotency(self):
        migrations.migrate_database(self.db_path)
        _, release = self._registered_release()
        run = dataset_imports.create_or_resume_import_run(
            self.db_path, release_id=release["id"], idempotency_key="ord-pilot:config-1",
            source_row_count=10,
        )
        con = sqlite3.connect(self.db_path)
        con.execute("CREATE TABLE imported_test_rows (source_id TEXT PRIMARY KEY, value TEXT)")
        con.commit()
        con.close()
        initial_counts = {field: 0 for field in dataset_imports._COUNTER_FIELDS}

        def failing_writer(connection):
            connection.execute("INSERT INTO imported_test_rows VALUES ('row-1','value')")
            raise RuntimeError("simulated batch failure")

        with self.assertRaisesRegex(RuntimeError, "simulated batch failure"):
            dataset_imports.commit_import_batch(
                self.db_path, run_id=run["id"], expected_checkpoint={},
                next_checkpoint={"cursor": "row-1"}, counts={
                    **initial_counts, "processed_row_count": 1, "inserted_row_count": 1,
                }, write_batch=failing_writer,
            )
        con = sqlite3.connect(self.db_path)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM imported_test_rows").fetchone()[0], 0)
        con.close()

        resumed = dataset_imports.create_or_resume_import_run(
            self.db_path, release_id=release["id"], idempotency_key="ord-pilot:config-1",
            source_row_count=10,
        )
        self.assertEqual(resumed["id"], run["id"])
        self.assertEqual(resumed["checkpoint_json"], "{}")

        counts = {**initial_counts, "processed_row_count": 10, "inserted_row_count": 10}
        writer_calls = []

        def successful_writer(connection):
            writer_calls.append(True)
            connection.executemany(
                "INSERT INTO imported_test_rows VALUES (?, 'value')",
                [(f"row-{index}",) for index in range(1, 11)],
            )

        saved = dataset_imports.commit_import_batch(
            self.db_path, run_id=run["id"], expected_checkpoint={},
            next_checkpoint={"cursor": "row-1"}, counts=counts, write_batch=successful_writer,
        )
        replay = dataset_imports.commit_import_batch(
            self.db_path, run_id=run["id"], expected_checkpoint={},
            next_checkpoint={"cursor": "row-1"}, counts=counts, write_batch=successful_writer,
        )
        self.assertTrue(saved)
        self.assertFalse(replay)
        self.assertEqual(len(writer_calls), 1)
        con = sqlite3.connect(self.db_path)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM imported_test_rows").fetchone()[0], 10)
        con.close()

        resumed = dataset_imports.create_or_resume_import_run(
            self.db_path, release_id=release["id"], idempotency_key="ord-pilot:config-1",
            source_row_count=10,
        )
        self.assertEqual(resumed["checkpoint_json"], '{"cursor":"row-1"}')
        self.assertEqual(resumed["inserted_row_count"], 10)
        with self.assertRaises(dataset_imports.ImportStateError):
            dataset_imports.commit_import_batch(
                self.db_path, run_id=run["id"], expected_checkpoint={},
                next_checkpoint={"cursor": "row-2"}, counts=counts, write_batch=successful_writer,
            )
        self.assertTrue(dataset_imports.set_import_run_status(
            self.db_path, run_id=run["id"], status="completed"))
        self.assertFalse(dataset_imports.set_import_run_status(
            self.db_path, run_id=run["id"], status="completed"))

    def test_foreign_key_and_metadata_validation(self):
        migrations.migrate_database(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            dataset_imports.register_dataset_release(
                self.db_path,
                source_id=999,
                release_key="orphan",
                release_version="v1",
                source_url="https://example.invalid/data",
                retrieved_at="2026-11-24T00:00:00Z",
            )
        self.assertEqual(migrations.migration_status(self.db_path)["pending_versions"], [])


if __name__ == "__main__":
    unittest.main()
