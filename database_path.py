"""Shared path selection for the application's SQLite database."""

import os
from pathlib import Path


def get_database_path():
    configured = os.environ.get("CHEM_DB_PATH")
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str(Path(__file__).resolve().parent / "chem.db")
