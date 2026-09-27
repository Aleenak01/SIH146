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
from .ingestion.real_bitcoin import SourceNotConfigured, SourceUnavailable
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
    fr = sub.add_parser("fetch-real", help="OPTIONAL: read recent Bitcoin blocks from the configured Esplora API (needs SIH146_REAL_BITCOIN_ENABLED=true)")
    fr.add_argument("--blocks", type=int, default=1, help="how many of the newest blocks to read (default 1)")
    fr.add_argument("--heights", type=int, nargs="+", help="explicit block heights instead of the newest blocks")
    fr.add_argument("--max-tx-per-block", type=int, default=None, help="most transactions to read from each block")
    ir = sub.add_parser("import-rich", help="import rich (address-level) SYNTHETIC transaction records from a CSV / JSON / JSONL / XML file")
    ir.add_argument("path", help="file to import")
    ir.add_argument("--format", choices=["csv", "json", "jsonl", "xml"], default=None, help="file format (default: from the file extension)")
    an = sub.add_parser("analyze", help="run the analysis for one data source now")
    an.add_argument("--source", choices=["synthetic", "real_bitcoin"], default="synthetic")
    be = sub.add_parser("build-entities", help="build common-input-ownership address entities and network correlation from the rich (address-level) SYNTHETIC data (Phase 2)")
    be.add_argument("--source", choices=["synthetic", "real_bitcoin"], default="synthetic")
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
        elif args.command == "fetch-real":
            from .services import real_source

            state = real_source.RealSourceState()
            r = real_source.fetch_and_ingest(db, settings, state, blocks=args.blocks, heights=args.heights, max_tx_per_block=args.max_tx_per_block)
            print(f"Read {r['transactions_examined']} real transactions from {len(r['blocks'])} block(s) "
                  f"(heights {', '.join(str(b['height']) for b in r['blocks'])}) with {r['requests']} API requests.")
            print(f"Stored {r['inserted']} transfers as source=real_bitcoin ({r['duplicates']} already present, {r['rejected']} rejected). Skipped: {r['skipped'] or 'none'}.")
        elif args.command == "import-rich":
            from pathlib import Path

            from .ingestion.rich_formats import FormatError, parse, validate_records
            from .services.geoip import GeoIP
            from .services.rich_import import import_rich

            path = Path(args.path)
            fmt = args.format or path.suffix.lstrip(".").lower()
            try:
                raw, parse_rejected = parse(path.read_bytes(), fmt)
            except (FormatError, OSError) as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            valid, invalid = validate_records(raw)
            res = import_rich(db, valid, GeoIP.from_settings(settings), received=len(raw) + len(parse_rejected), rejected=list(parse_rejected) + invalid)
            print(f"{res.received} records read from {path.name} ({fmt}): {res.attached} attached to existing transactions, {res.created} transactions created, "
                  f"{res.duplicates} already imported, {len(res.rejected)} rejected, {len(res.warnings)} warnings. tx_details now holds {res.details_total} rows.")
            for r in sorted(res.rejected, key=lambda r: r["index"])[:10]:
                print(f"  rejected #{r['index']}: {r['error']}")
            print("Synthetic data: IP addresses are randomly assigned, not observed traffic.")
        elif args.command == "analyze":
            from .analysis.ml_bridge import AnalysisError
            from .analysis.service import run_analysis

            try:
                s = run_analysis(db, args.source, trigger="manual")
            except AnalysisError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            print(f"Analysed {s.transfer_count} {args.source} transfers: {s.scored_wallets} wallets scored, {s.unscored_wallets} not scored "
                  f"(too little activity), {s.anomalous_wallets} flagged, {s.leads_total} leads, {s.clusters_total} clusters.")
        elif args.command == "build-entities":
            from .analysis.correlation import refresh_correlation
            from .analysis.entities import refresh_entities

            er = refresh_entities(db, args.source)
            if not er.rich_data_available:
                print(f"No rich (address-level) data for source={args.source!r}. Import rich records first (see `import-rich`).")
            else:
                cr = refresh_correlation(db, args.source)
                print(f"Entities: {er.entities} ({er.created} created, {er.updated} updated, {er.removed} removed), {er.addresses_total} addresses.")
                print(f"Correlation: {cr.entity_ip_links} entity-IP links, {cr.entity_links} entity-entity links, {cr.findings} findings.")
                print("Synthetic data: this is a possible-link indicator from generated demo network data, not proof of anything.")
        elif args.command == "stats":
            with db.session() as s:
                print(f"Database: {settings.db_path}")
                for name, model in (("transactions", Transaction), ("wallets", Wallet)):
                    for source, n in s.execute(select(model.source, func.count()).group_by(model.source)):
                        print(f"  {name:<13}{source:<14}{n}")
                print(f"  {'observations':<13}{'synthetic':<14}{s.scalar(select(func.count()).select_from(NetworkObservation)) or 0}")
    except (DatasetError, ImportConflict, SourceNotConfigured, SourceUnavailable) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
