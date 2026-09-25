"""
Command-line helpers.

    .\\.venv\\Scripts\\python.exe -m backend.cli init-db
    .\\.venv\\Scripts\\python.exe -m backend.cli import-csv [--replace]
    .\\.venv\\Scripts\\python.exe -m backend.cli stats
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from .analysis.clustering import refresh_clusters
from .config import load_settings
from .database import Database
from .ingestion.base import DatasetError
from .models import Base, NetworkObservation, Transaction, Wallet
from .services import network as network_service
from .services.importer import ImportConflict, import_synthetic_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the SQLite database and any missing tables")
    imp = sub.add_parser("import-csv", help="import the existing synthetic dataset (safe to repeat)")
    imp.add_argument("--replace", action="store_true", help="remove ALL existing synthetic data first (use after regenerating the dataset)")
    sub.add_parser("generate-network", help="generate the SYNTHETIC network-observation dataset CSV from the imported transactions")
    imn = sub.add_parser("import-network", help="import the synthetic network-observation CSV (generates it first if it does not exist)")
    imn.add_argument("--replace", action="store_true", help="remove existing synthetic observations first")
    sub.add_parser("stats", help="show what the database holds")
    args = parser.parse_args(argv)

    settings = load_settings()
    db = Database(settings.database_url)
    db.init_db()
    try:
        if args.command == "init-db":
            print(f"Database ready: {settings.db_path}")
            print("Tables: " + ", ".join(sorted(Base.metadata.tables)))
        elif args.command == "import-csv":
            r = import_synthetic_csv(db, settings.dataset_csv, replace=args.replace)
            print(f"Imported {r.inserted} transactions ({r.skipped_existing} already present) from {r.file}; "
                  f"{r.wallets_total} synthetic wallets in the database.")
        elif args.command == "generate-network":
            n = network_service.generate_csv(db, settings.network_csv, settings.network_seed)
            print(f"Wrote {n} SYNTHETIC network observations to {settings.network_csv} (documentation IP ranges only).")
        elif args.command == "import-network":
            if not settings.network_csv.is_file():
                n = network_service.generate_csv(db, settings.network_csv, settings.network_seed)
                print(f"Generated {n} synthetic observations -> {settings.network_csv}")
            r = network_service.import_csv(db, settings.network_csv, replace=args.replace)
            print(f"Imported {r.inserted} synthetic observations ({r.skipped_existing} already present, {len(r.rejected)} rejected); {r.observations_total} in the database.")
            c = refresh_clusters(db, "synthetic")
            print(f"Clusters: {c.network_clusters} shared-network-observation, {c.transaction_communities} transaction communities.")
        elif args.command == "stats":
            with db.session() as s:
                print(f"Database: {settings.db_path}")
                for name, model in (("transactions", Transaction), ("wallets", Wallet)):
                    for source, n in s.execute(select(model.source, func.count()).group_by(model.source)):
                        print(f"  {name:<13}{source:<14}{n}")
                print(f"  {'observations':<13}{'synthetic':<14}{s.scalar(select(func.count()).select_from(NetworkObservation)) or 0}")
    except (DatasetError, ImportConflict) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
