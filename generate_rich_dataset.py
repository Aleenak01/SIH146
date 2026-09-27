"""
generate_rich_dataset.py
------------------------
Builds the RICH synthetic transaction dataset (Phase 1) from the existing raw dataset, without changing it.

    .\\.venv\\Scripts\\python.exe generate_rich_dataset.py            # writes dataset/rich/ (deterministic, seed 149)
    .\\.venv\\Scripts\\python.exe generate_rich_dataset.py --verify   # generates twice and checks the output is identical

Reads (read-only):  dataset/synthetic_bitcoin_transactions.csv  and the two GeoIP files in data/geoip/
Writes to dataset/rich/:
    synthetic_rich_transactions.jsonl   primary file, one record per existing transfer (5,000)
    synthetic_rich_transactions.csv     same records, list fields as JSON strings in the cells
    sample.json / sample.jsonl / sample.csv / sample.xml     20 records each, for tests and docs
    ground_truth_address_owner.csv      address -> owning legacy wallet. FOR VALIDATION ONLY: nothing in the backend or analysis reads it.

IMPORTANT (also in dataset/rich/README.md):
  * Everything is SYNTHETIC. IP addresses are randomly assigned synthetic values sampled from public ranges; they are not
    observed traffic and no real person or network did anything. Ports, countries and ASNs describe those synthetic addresses.
  * `sender_wallet`, `receiver_wallet` and `amount_btc` exist ONLY so the existing wallet-level pipeline can be fed a flat view
    (timestamp, sender, receiver, amount, input count, output count). Later detectors must not use them: they would give away the
    answer that address-level analysis is supposed to find. The same holds for ground_truth_address_owner.csv.

One rich record per existing transfer, same order and same legacy ids (syn-000001 ... chronological, exactly as the backend importer
numbers them). Design:
  * Addresses are opaque synthetic ids (s1... p2pkh, s3... p2sh, sy1q... p2wpkh/p2wsh, sy1p... p2tr), deterministic and shuffled, not
    derived from wallet numbers. Each wallet owns 1-4 addresses (2-4 if it ever sends with change).
  * Value balance is exact in satoshis: sum(inputs) = sum(outputs) + fee. There is no UTXO ledger: inputs are not linked to earlier outputs.
  * The payment to the receiver equals the original amount_btc; input_count and output_count equal the original counts exactly
    (a repeated input address means several coins spent from one address). All other outputs are change outputs to addresses of the sender
    that are not spent in that transaction (the least recently used ones first). Wallets with a single address never send with change.
  * About 17% of all transactions spend 2-3 DIFFERENT addresses of the same sender wallet (raw material for common-input analysis later).
  * Network flow: src_ip is a client IP of the sender (each wallet has a pool of 1-3 IPs; about 15% of wallets share an IP with another
    wallet), dst_ip is one of 8 synthetic node IPs, src_port is ephemeral, dst_port is 8333 in about 90% of records.
    geo_country / asn / asn_org come from a real lookup of src_ip in the DB-IP files (IP geolocation by DB-IP.com, CC BY 4.0).
    All IPs are public IPv4 addresses sampled from ranges in the ASN database, from a fixed set of 8 countries and 32 ASNs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import math
import random
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.config import load_settings                       # noqa: E402
from backend.ingestion.synthetic_csv import SyntheticCSVSource  # noqa: E402
from backend.services.geoip import GeoIP                        # noqa: E402

SEED = 149
RAW_CSV = ROOT / "dataset" / "synthetic_bitcoin_transactions.csv"
OUT_DIR = ROOT / "dataset" / "rich"
SAT = 100_000_000
NOTICE = ("SYNTHETIC DATA. IP addresses are randomly assigned synthetic values sampled from public ranges; they are not observed "
          "traffic and no real person or network did anything.")

FIELDS = ["timestamp", "txid", "legacy_transaction_id", "sender_wallet", "receiver_wallet", "amount_btc", "fee_btc", "script_type",
          "input_addresses", "input_amounts", "output_addresses", "output_amounts", "src_ip", "dst_ip", "src_port", "dst_port",
          "geo_country", "asn", "asn_org"]

SCRIPT_TYPES = ["p2pkh", "p2sh", "p2wpkh", "p2wsh", "p2tr"]
SCRIPT_WEIGHTS = [20, 12, 45, 8, 15]
ADDR_SHAPE = {"p2pkh": ("s1", 32), "p2sh": ("s3", 32), "p2wpkh": ("sy1q", 38), "p2wsh": ("sy1q", 58), "p2tr": ("sy1p", 58)}
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

COUNTRIES = {"US": 30, "DE": 15, "GB": 12, "NL": 10, "SG": 10, "JP": 8, "BR": 8, "FR": 7}     # country -> weight (wallets' home country)
ASNS_PER_COUNTRY = 4
N_NODES = 8
OTHER_DST_PORTS = [8332, 8334, 18333, 28333, 38333]
MULTI_INPUT_SHARE = 0.17
SHARED_IP_SHARE = 0.15


# ------------------------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------------------------
def stream(label: str) -> random.Random:
    """An independent deterministic random stream per phase, so changing one phase never shifts another."""
    return random.Random(f"sih146-rich-{SEED}-{label}")


def make_address(index: int, script_type: str) -> str:
    prefix, length = ADDR_SHAPE[script_type]
    raw = hashlib.shake_128(f"sih146-rich-{SEED}-address-{index}".encode()).digest(length)
    return prefix + "".join(BECH32[b % 32] for b in raw)


def sats(x: float) -> int:
    return int(round(x * SAT))


def btc(n: int) -> float:
    return round(n / SAT, 8)


def split_total(rng: random.Random, total: int, parts: int) -> list[int]:
    """`parts` positive integers (each >= 1) that sum exactly to `total`."""
    if parts == 1:
        return [total]
    weights = [rng.random() + 0.05 for _ in range(parts)]
    spare = total - parts
    shares = [1 + int(spare * w / sum(weights)) for w in weights]
    shares[-1] += total - sum(shares)
    assert all(s >= 1 for s in shares) and sum(shares) == total
    return shares


def load_transfers() -> list[dict]:
    """The existing transfers exactly as the backend importer sees them (same order, same syn- ids)."""
    items = SyntheticCSVSource(RAW_CSV).fetch()
    out = []
    for it in items:
        t = it.transaction
        out.append({"legacy_id": t.transaction_id, "ts": t.timestamp, "sender": t.sender_wallet, "receiver": t.receiver_wallet,
                    "amount": t.amount_btc, "amount_sats": sats(t.amount_btc), "n_in": t.input_count, "n_out": t.output_count})
    for t in out:
        assert abs(t["amount_sats"] / SAT - t["amount"]) < 1e-12, "raw amount has more than 8 decimals"
    return out


# ------------------------------------------------------------------------------------------------------------------
# infrastructure: real public IP ranges taken from the ASN database, countries and ASNs from real lookups
# ------------------------------------------------------------------------------------------------------------------
_ASN_NETS: dict[int, list[tuple[int, int]]] | None = None


def asn_networks(asn_path: Path) -> dict[int, list[tuple[int, int]]]:
    """{asn: [(first address as int, size)]} for public IPv4 ranges of /24 or larger. Iterated once (about 15-60 s)."""
    global _ASN_NETS
    if _ASN_NETS is None:
        import maxminddb

        nets: dict[int, list[tuple[int, int]]] = defaultdict(list)
        with maxminddb.open_database(str(asn_path)) as reader:
            for net, rec in reader:
                if net.version != 4 or net.prefixlen > 24 or not net.is_global or not rec:
                    continue
                asn = rec.get("autonomous_system_number")
                if asn:
                    nets[int(asn)].append((int(net.network_address), net.num_addresses))
        _ASN_NETS = dict(nets)
    return _ASN_NETS


class Infra:
    def __init__(self, geo: GeoIP, asn_path: Path) -> None:
        self.geo = geo
        nets = asn_networks(asn_path)
        rng = stream("infra")
        order = sorted(nets)
        rng.shuffle(order)
        self.asns_by_country: dict[str, list[int]] = {c: [] for c in COUNTRIES}
        self.nets = nets
        for asn in order:
            if all(len(v) >= ASNS_PER_COUNTRY for v in self.asns_by_country.values()):
                break
            if len(nets[asn]) < 3:
                continue
            first, size = nets[asn][0]
            info = geo.lookup(str(ipaddress.IPv4Address(first + size // 2)))
            country = info["country_code"]
            if country in self.asns_by_country and len(self.asns_by_country[country]) < ASNS_PER_COUNTRY and info["asn"] == asn and info["asn_org"]:
                self.asns_by_country[country].append(asn)
        missing = [c for c, v in self.asns_by_country.items() if len(v) < ASNS_PER_COUNTRY]
        if missing:
            raise SystemExit(f"Could not find {ASNS_PER_COUNTRY} ASNs for {missing}; are the GeoIP files complete?")
        self.used_ips: set[str] = set()

    def sample_ip(self, rng: random.Random, country: str) -> str:
        """A public IPv4 address from one of this country's ASNs whose lookup confirms that country and ASN."""
        for _ in range(200):
            asn = rng.choice(self.asns_by_country[country])
            first, size = rng.choice(self.nets[asn])
            ip = str(ipaddress.IPv4Address(first + rng.randrange(1, max(size - 1, 2))))
            if ip in self.used_ips or not ipaddress.ip_address(ip).is_global:
                continue
            info = self.geo.lookup(ip)
            if info["country_code"] == country and info["asn"] == asn:
                self.used_ips.add(ip)
                return ip
        raise SystemExit(f"Could not sample an address for {country}")


# ------------------------------------------------------------------------------------------------------------------
# generation
# ------------------------------------------------------------------------------------------------------------------
def generate(geo: GeoIP, asn_path: Path) -> dict:
    transfers = load_transfers()
    wallets = sorted({t["sender"] for t in transfers} | {t["receiver"] for t in transfers})
    needs_change = {t["sender"] for t in transfers if t["n_out"] >= 2}

    # ---- address pools (opaque, shuffled ids) ---------------------------------------------------------------------
    rng = stream("addresses")
    pool_size = {w: (rng.choice([2, 3, 3, 4, 4]) if w in needs_change else rng.choice([1, 1, 2, 3, 4])) for w in wallets}
    wallet_type = {w: rng.choices(SCRIPT_TYPES, SCRIPT_WEIGHTS)[0] for w in wallets}
    slots = [(w, i) for w in wallets for i in range(pool_size[w])]
    rng.shuffle(slots)
    pools: dict[str, list[tuple[str, str]]] = defaultdict(list)            # wallet -> [(address, script_type)]
    owner: dict[str, tuple[str, str]] = {}
    for index, (w, _) in enumerate(slots):
        st = wallet_type[w] if rng.random() < 0.85 else rng.choices(SCRIPT_TYPES, SCRIPT_WEIGHTS)[0]
        addr = make_address(index, st)
        assert addr not in owner
        pools[w].append((addr, st))
        owner[addr] = (w, st)

    # ---- IP pools, shared IPs and node IPs ---------------------------------------------------------------------------
    infra = Infra(geo, asn_path)
    rng = stream("ips")
    countries, weights = list(COUNTRIES), list(COUNTRIES.values())
    home = {w: rng.choices(countries, weights)[0] for w in wallets}
    ip_pool: dict[str, list[str]] = {}
    for w in wallets:
        n = rng.choices([1, 2, 3], [55, 30, 15])[0]
        ip_pool[w] = [infra.sample_ip(rng, home[w]) for _ in range(n)]
    n_share = round(SHARED_IP_SHARE * len(wallets)) // 2 * 2
    chosen = rng.sample(wallets, n_share)
    for a, b in zip(chosen[0::2], chosen[1::2]):                            # b also uses a's main address (its most used one)
        shared = ip_pool[a][0]
        ip_pool[b] = [shared] + [ip for ip in ip_pool[b] if ip != shared][:2]
    nodes = [infra.sample_ip(rng, rng.choices(countries, weights)[0]) for _ in range(N_NODES)]
    node_weights = [max(1, int(30 / (i + 1))) for i in range(N_NODES)]

    # ---- which transactions spend several different addresses of the sender ------------------------------------------------
    rng = stream("plan")
    feasible = []
    for i, t in enumerate(transfers):
        p = len(pools[t["sender"]])
        max_distinct = min(t["n_in"], p - (1 if t["n_out"] >= 2 else 0), 3)
        if t["n_in"] >= 2 and max_distinct >= 2:
            feasible.append((i, max_distinct))
    target = round(MULTI_INPUT_SHARE * len(transfers))
    picked = dict(rng.sample(feasible, min(target, len(feasible))))

    # ---- the transactions ---------------------------------------------------------------------------------------------------
    rng = stream("tx")
    last_used: dict[str, int] = {}                                           # address -> counter of its last use (never used = absent)
    counter = 0
    records = []
    for i, t in enumerate(transfers):
        sender_pool = [a for a, _ in pools[t["sender"]]]
        receiver_pool = pools[t["receiver"]]
        n_in, n_out = t["n_in"], t["n_out"]

        if i in picked:
            k = rng.randint(2, picked[i])
            distinct = rng.sample(sender_pool, k)
            inputs = distinct + rng.choices(distinct, k=n_in - k)
            rng.shuffle(inputs)
        else:
            inputs = [rng.choice(sender_pool)] * n_in
        pay_addr, script_type = rng.choice(receiver_pool)

        n_change = n_out - 1
        change_addrs: list[str] = []
        if n_change:
            spent = set(inputs)
            candidates = [a for a in sender_pool if a not in spent] or sender_pool
            for _ in range(n_change):
                best = min(candidates, key=lambda a: (last_used.get(a, -1), sender_pool.index(a)))
                change_addrs.append(best)
                counter += 1
                last_used[best] = counter
        for a in inputs + [pay_addr]:
            counter += 1
            last_used[a] = counter

        vsize = 10 + 68 * n_in + 31 * n_out
        feerate = min(200, max(1, int(rng.lognormvariate(math.log(12), 0.7))))
        fee = vsize * feerate
        change_total = max(n_change, int(t["amount_sats"] * rng.uniform(0.05, 1.2))) if n_change else 0
        change_parts = split_total(rng, change_total, n_change) if n_change else []
        in_parts = split_total(rng, t["amount_sats"] + change_total + fee, n_in)

        outputs = [(pay_addr, t["amount_sats"])] + list(zip(change_addrs, change_parts))
        rng.shuffle(outputs)

        src_ip = rng.choices(ip_pool[t["sender"]], [60, 30, 10][: len(ip_pool[t["sender"]])])[0]
        info = geo.lookup(src_ip)
        assert info["country_code"] and info["asn"], f"no GeoIP data for {src_ip}"
        records.append({
            "timestamp": t["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "txid": hashlib.sha256(f"sih146-rich-{SEED}|{t['legacy_id']}|{t['ts'].isoformat()}".encode()).hexdigest(),
            "legacy_transaction_id": t["legacy_id"], "sender_wallet": t["sender"], "receiver_wallet": t["receiver"],
            "amount_btc": t["amount"], "fee_btc": btc(fee), "script_type": script_type,
            "input_addresses": inputs, "input_amounts": [btc(x) for x in in_parts],
            "output_addresses": [a for a, _ in outputs], "output_amounts": [btc(x) for _, x in outputs],
            "src_ip": src_ip, "dst_ip": rng.choices(nodes, node_weights)[0],
            "src_port": rng.randint(32768, 60999), "dst_port": 8333 if rng.random() < 0.90 else rng.choice(OTHER_DST_PORTS),
            "geo_country": info["country_code"], "asn": info["asn"], "asn_org": info["asn_org"],
        })
    return {"records": records, "transfers": transfers, "owner": owner, "ip_pool": ip_pool, "nodes": nodes, "pools": pools}


# ------------------------------------------------------------------------------------------------------------------
# writing
# ------------------------------------------------------------------------------------------------------------------
def to_jsonl(records: list[dict]) -> str:
    return "".join(json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)


def csv_text(records: list[dict]) -> str:
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in records:
        w.writerow([json.dumps(r[k], separators=(",", ":")) if isinstance(r[k], list) else r[k] for k in FIELDS])
    return buf.getvalue()


def xml_text(records: list[dict]) -> str:
    root = ET.Element("transactions")
    for r in records:
        tx = ET.SubElement(root, "transaction")
        for k in FIELDS:
            el = ET.SubElement(tx, k)
            if isinstance(r[k], list):
                child = "address" if k.endswith("addresses") else "amount"
                for v in r[k]:
                    ET.SubElement(el, child).text = str(v)
            else:
                el.text = str(r[k])
    ET.indent(root)
    return f"<?xml version='1.0' encoding='utf-8'?>\n<!-- {NOTICE} -->\n" + ET.tostring(root, encoding="unicode") + "\n"


def sample_indices(records: list[dict]) -> list[int]:
    multi = [i for i, r in enumerate(records) if len(set(r["input_addresses"])) >= 2]
    many_out = [i for i, r in enumerate(records) if len(r["output_addresses"]) >= 3]
    chosen = set(list(range(8)) + multi[:6] + many_out[:6])
    i = 8
    while len(chosen) < 20:                                    # the three groups can overlap: top up with the next records
        chosen.add(i)
        i += 1
    return sorted(chosen)


def write_all(g: dict) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = g["records"]
    sample = [records[i] for i in sample_indices(records)]
    files = {
        "synthetic_rich_transactions.jsonl": to_jsonl(records),
        "synthetic_rich_transactions.csv": csv_text(records),
        "sample.json": json.dumps([{k: r[k] for k in FIELDS} for r in sample], indent=2, ensure_ascii=False) + "\n",
        "sample.jsonl": to_jsonl(sample),
        "sample.csv": csv_text(sample),
        "sample.xml": xml_text(sample),
        "ground_truth_address_owner.csv": "address,owner_wallet,script_type\n" + "".join(
            f"{a},{w},{st}\n" for a, (w, st) in sorted(g["owner"].items(), key=lambda kv: (kv[1][0], kv[0]))),
    }
    for name, text in files.items():
        (OUT_DIR / name).write_text(text, encoding="utf-8", newline="")
    return files


# ------------------------------------------------------------------------------------------------------------------
# validation
# ------------------------------------------------------------------------------------------------------------------
def validate(g: dict, geo: GeoIP, files: dict[str, str]) -> None:
    records, transfers, owner = g["records"], g["transfers"], g["owner"]
    n = len(records)
    problems: list[str] = []
    print("\n=== Validation summary ===")
    print(f"records: {n} (source transfers: {len(transfers)})")
    if n != len(transfers):
        problems.append("record count differs from the source")

    # re-read what was written, so the checks cover the files and not only memory
    rows = [json.loads(line) for line in files["synthetic_rich_transactions.jsonl"].splitlines()]
    with open(OUT_DIR / "synthetic_rich_transactions.csv", newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    print(f"jsonl rows: {len(rows)}; csv rows: {len(csv_rows)}; csv matches jsonl: "
          f"{all(json.loads(c['input_addresses']) == r['input_addresses'] and json.loads(c['output_amounts']) == r['output_amounts'] and c['txid'] == r['txid'] for c, r in zip(csv_rows, rows))}")

    bad_balance = sum(1 for r in rows if sum(sats(x) for x in r["input_amounts"]) != sum(sats(x) for x in r["output_amounts"]) + sats(r["fee_btc"]))
    print(f"value balance (inputs = outputs + fee, exact in satoshis): {n - bad_balance}/{n} balanced")
    if bad_balance:
        problems.append("balance")

    mismatch = 0
    bad_owner = 0
    for r, t in zip(rows, transfers):
        flat = (r["timestamp"], r["sender_wallet"], r["receiver_wallet"], r["amount_btc"], len(r["input_addresses"]), len(r["output_addresses"]))
        want = (t["ts"].strftime("%Y-%m-%dT%H:%M:%SZ"), t["sender"], t["receiver"], t["amount"], t["n_in"], t["n_out"])
        if flat != want or r["legacy_transaction_id"] != t["legacy_id"]:
            mismatch += 1
        paid = [a for a, x in zip(r["output_addresses"], r["output_amounts"]) if sats(x) == t["amount_sats"] and owner[a][0] == t["receiver"]]
        change_ok = all(owner[a][0] == t["sender"] and a not in r["input_addresses"] for a, x in zip(r["output_addresses"], r["output_amounts"]) if a not in paid)
        if len(paid) != 1 or not change_ok or any(owner[a][0] != t["sender"] for a in r["input_addresses"]):
            bad_owner += 1
    print(f"flatten round-trip to (timestamp, sender, receiver, amount_btc, input_count, output_count): {n - mismatch}/{n} identical to the original transfers")
    print(f"ownership (inputs and change belong to the sender, exactly one payment to the receiver): {n - bad_owner}/{n} ok")
    if mismatch or bad_owner:
        problems.append("round trip / ownership")

    no_geo = sum(1 for r in rows if not (r["geo_country"] and r["asn"] and r["asn_org"]))
    geo_diff = sum(1 for r in rows if (lambda i: (i["country_code"], i["asn"], i["asn_org"]))(geo.lookup(r["src_ip"])) != (r["geo_country"], r["asn"], r["asn_org"]))
    print(f"geo coverage: {n - no_geo}/{n} records have country/ASN/org; {n - geo_diff}/{n} equal a fresh lookup of src_ip")
    countries = Counter(r["geo_country"] for r in rows)
    asns = {r["asn"] for r in rows}
    print(f"countries used: {len(countries)} {dict(countries.most_common())}; distinct ASNs used: {len(asns)}")
    if no_geo or geo_diff:
        problems.append("geo")

    multi = sum(1 for r in rows if len(set(r["input_addresses"])) >= 2)
    multi23 = sum(1 for r in rows if 2 <= len(set(r["input_addresses"])) <= 3)
    print(f"transactions spending 2-3 different addresses of one sender: {multi23}/{n} = {100 * multi23 / n:.1f}% (any count >= 2: {multi})")
    ip_wallets: dict[str, set] = defaultdict(set)
    for r in rows:
        ip_wallets[r["src_ip"]].add(r["sender_wallet"])
    pool_share = defaultdict(set)
    for w, ips in g["ip_pool"].items():
        for ip in ips:
            pool_share[ip].add(w)
    sharing = {w for ws in pool_share.values() if len(ws) > 1 for w in ws}
    seen_share = {w for ws in ip_wallets.values() if len(ws) > 1 for w in ws}
    print(f"wallets sharing an IP with another wallet: {len(sharing)}/{len(g['ip_pool'])} = {100 * len(sharing) / len(g['ip_pool']):.1f}% (visible in the records: {len(seen_share)})")
    d8333 = sum(1 for r in rows if r["dst_port"] == 8333)
    print(f"dst_port 8333: {100 * d8333 / n:.1f}%; src_port range {min(r['src_port'] for r in rows)}-{max(r['src_port'] for r in rows)}; distinct dst_ip (nodes): {len({r['dst_ip'] for r in rows})}")
    per_wallet = Counter(len(v) for v in g["pools"].values())
    print(f"addresses per wallet: {dict(sorted(per_wallet.items()))}; total addresses: {len(owner)}; script types: {dict(Counter(r['script_type'] for r in rows))}")
    fees = sorted(sats(r["fee_btc"]) for r in rows)
    print(f"fee (sat): min {fees[0]}, median {fees[n // 2]}, max {fees[-1]}; txids unique: {len({r['txid'] for r in rows}) == n}")
    if len({r["txid"] for r in rows}) != n:
        problems.append("txid not unique")
    print("sha256:", {k: hashlib.sha256(v.encode()).hexdigest()[:16] for k, v in files.items() if k.startswith("synthetic")})
    if problems:
        raise SystemExit(f"VALIDATION FAILED: {problems}")
    print("All checks passed.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="generate twice and confirm the output is byte-identical")
    args = ap.parse_args()
    settings = load_settings()
    geo = GeoIP.from_settings(settings)
    if not (geo.status()["complete"]):
        raise SystemExit("GeoIP files are missing (data/geoip/): the rich dataset needs real lookups. " + geo.status()["message"])
    print(NOTICE)
    first = generate(geo, settings.geoip_asn_db)
    files = write_all(first)
    validate(first, geo, files)
    if args.verify:
        again = generate(geo, settings.geoip_asn_db)
        same = to_jsonl(again["records"]) == files["synthetic_rich_transactions.jsonl"] and csv_text(again["records"]) == files["synthetic_rich_transactions.csv"]
        print(f"\nDeterminism check (generated a second time in memory): {'IDENTICAL' if same else 'DIFFERENT'}")
        if not same:
            raise SystemExit(1)
    print(f"\nWrote {len(files)} files to {OUT_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
