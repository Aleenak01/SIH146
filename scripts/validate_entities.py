"""
Offline validation of Phase 2's common-input-ownership entity clustering against the KNOWN ground truth of the
synthetic rich dataset (dataset/rich/ground_truth_address_owner.csv). This script is NOT part of the backend or the
analysis pipeline: nothing in backend/ reads the ground-truth file. It exists purely to measure how well the
heuristic performs, on data whose true wallet-address ownership we generated and therefore know.

It builds its own throw-away scratch database (a temp file); it never touches data/sih146.db.

Usage (from the project root):
    .\\.venv\\Scripts\\python.exe scripts\\validate_entities.py
"""

from __future__ import annotations

import csv
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from backend.analysis.correlation import refresh_correlation  # noqa: E402
from backend.analysis.entities import refresh_entities  # noqa: E402
from backend.database import Database  # noqa: E402
from backend.ingestion.rich_formats import parse, validate_records  # noqa: E402
from backend.models import AddressEntityMember  # noqa: E402
from backend.services.geoip import GeoIP  # noqa: E402
from backend.services.importer import import_synthetic_csv  # noqa: E402
from backend.services.rich_import import import_rich  # noqa: E402

GROUND_TRUTH = ROOT / "dataset" / "rich" / "ground_truth_address_owner.csv"
RICH_FILE = ROOT / "dataset" / "rich" / "synthetic_rich_transactions.jsonl"
RAW_CSV = ROOT / "dataset" / "synthetic_bitcoin_transactions.csv"


def load_ground_truth() -> dict[str, str]:
    with GROUND_TRUTH.open(newline="", encoding="utf-8") as f:
        return {row["address"]: row["owner_wallet"] for row in csv.DictReader(f)}


def build_scratch_db() -> Database:
    tmp_dir = Path(tempfile.mkdtemp(prefix="sih146_validate_entities_"))
    db = Database(f"sqlite:///{(tmp_dir / 'scratch.db').as_posix()}")
    db.init_db()
    import_synthetic_csv(db, RAW_CSV)
    raw, parse_rejected = parse(RICH_FILE.read_bytes(), "jsonl")
    valid, invalid = validate_records(raw)
    assert not parse_rejected, f"unexpected parse rejects: {parse_rejected[:3]}"
    assert not invalid, f"unexpected validation rejects: {invalid[:3]}"
    res = import_rich(db, valid, GeoIP())   # no GeoIP files needed for this check; geo fields are validated elsewhere
    assert res.details_total == len(valid), f"expected {len(valid)} tx_details rows, got {res.details_total}"
    return db


def main() -> int:
    truth = load_ground_truth()
    true_wallets = set(truth.values())
    print(f"Ground truth: {len(truth)} addresses, {len(true_wallets)} true wallets.")

    db = build_scratch_db()
    er = refresh_entities(db)
    refresh_correlation(db)
    print(f"Entities built: {er.entities} ({er.addresses_total} addresses); "
          f"{er.created} created, {er.updated} updated, {er.removed} removed.")

    with db.session() as s:
        rows = list(s.execute(select(AddressEntityMember.entity_id, AddressEntityMember.address)))
    entity_addrs: dict[str, list[str]] = defaultdict(list)
    for eid, addr in rows:
        entity_addrs[eid].append(addr)
    addr_to_entity = {a: eid for eid, addrs in entity_addrs.items() for a in addrs}

    # ---- purity: for each entity, share of its addresses owned by its single most common true wallet -------------
    purities: list[float] = []
    merged_wrong = 0
    for addrs in entity_addrs.values():
        owners = Counter(truth[a] for a in addrs if a in truth)
        if not owners:
            continue
        _, top_n = owners.most_common(1)[0]
        purities.append(top_n / len(addrs))
        if len(owners) > 1:
            merged_wrong += 1

    # ---- completeness: for each true wallet, share of its addresses that ended in its single largest entity -------
    addrs_by_wallet: dict[str, list[str]] = defaultdict(list)
    for addr, wallet in truth.items():
        addrs_by_wallet[wallet].append(addr)
    completeness: list[float] = []
    fragmented = 0
    for wallet, addrs in addrs_by_wallet.items():
        entities_here = Counter(addr_to_entity.get(a) for a in addrs if addr_to_entity.get(a) is not None)
        if not entities_here:
            completeness.append(0.0)
            fragmented += 1
            continue
        _, top_n = entities_here.most_common(1)[0]
        completeness.append(top_n / len(addrs))
        if top_n < len(addrs) or len(entities_here) > 1:
            fragmented += 1

    n_true_wallets = len(addrs_by_wallet)
    print("\n=== Entity purity (share of an entity's addresses owned by ONE true wallet) ===")
    print(f"  entities measured: {len(purities)}")
    if purities:
        print(f"  mean purity: {sum(purities) / len(purities):.3f}")
    print(f"  entities mixing addresses of 2+ true wallets (merged-wrong): {merged_wrong}")

    print("\n=== Wallet completeness (share of a wallet's addresses that ended up in ONE entity) ===")
    print(f"  true wallets measured: {n_true_wallets}")
    if completeness:
        print(f"  mean completeness: {sum(completeness) / len(completeness):.3f}")
    print(f"  wallets left fragmented across 2+ entities or with an unclustered address: {fragmented} "
          f"({fragmented / n_true_wallets:.1%})")

    print("\n=== Counts ===")
    print(f"  true wallets: {len(true_wallets)}")
    print(f"  entities found: {len(entity_addrs)}")
    print("\nReminder: only multi-input transactions merge addresses under this heuristic; a wallet whose addresses "
          "were never spent together in one transaction stays fragmented by design, not by a bug.")
    db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
