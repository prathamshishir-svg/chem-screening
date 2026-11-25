"""Download and verify the separately published full chem.db artifact."""

import argparse
from pathlib import Path

from database_distribution import (
    DatabaseDistributionError,
    install_database,
    load_release_config,
)


ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the verified chem-screening database")
    parser.add_argument("--manifest", type=Path, default=ROOT / "database_release.json")
    parser.add_argument("--destination", type=Path, default=ROOT / "chem.db")
    parser.add_argument("--url", help="HTTPS artifact URL; otherwise read CHEM_DB_URL or the manifest")
    parser.add_argument("--sha256", help="Expected SHA-256; otherwise read CHEM_DB_SHA256 or the manifest")
    args = parser.parse_args(argv)
    try:
        config = load_release_config(args.manifest, url=args.url, sha256=args.sha256)
        result = install_database(config, args.destination)
    except DatabaseDistributionError as exc:
        parser.exit(1, f"Database setup stopped safely: {exc}\n")
    print("Verified database already present." if result == "already_present"
          else f"Verified database installed at {args.destination.resolve()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
