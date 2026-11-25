"""Turns everyday names (bleach, vinegar...) into proper chemical names,
using the aliases table in chem.db."""
import sqlite3
from database_path import get_database_path

DB_FILE = get_database_path()


def resolve(name):
    """Return the proper chemical name for an alias, or the name unchanged."""
    key = name.strip().lower()
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("CREATE TABLE IF NOT EXISTS aliases ("
                    "alias TEXT PRIMARY KEY, canonical TEXT NOT NULL)")
        row = con.execute("SELECT canonical FROM aliases WHERE alias=?", (key,)).fetchone()
        con.close()
    except Exception:
        return name
    return row[0] if row else name
