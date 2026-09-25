"""
SYNTHETIC network observations (IP, device, session) for the demo dataset.

These are NOT blockchain data and NOT real people or networks. Bitcoin transactions carry no IP address, device
or location; a real investigation would obtain such observations from a separate, lawful source. Here they are
generated so the graph and the clustering can show how blockchain activity could be correlated with separately
obtained network observations.

Everything is deterministic (seeded), uses only RFC 5737 documentation address ranges (192.0.2.0/24,
198.51.100.0/24, 203.0.113.0/24, which are never assigned to real hosts), and does not use the analysis results:
which wallets share a device is random and has no relation to how a wallet is scored.

Model
  * Every wallet operates from one synthetic DEVICE. About 15% of wallets are placed in small groups (2-5) that
    share a device, which is what produces shared-infrastructure relationships.
  * A device has 1-3 IPs from the documentation ranges (a few devices share an IP, like NAT).
  * A transaction is observed from its sender's device with probability 0.8 and from its receiver's device with
    probability 0.5. Observations of one device close in time (gap <= 2 h) belong to one SESSION.
  * observation_id = "obs-<transaction_id>-S" (sender side) or "-R" (receiver side).
"""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_SEED = 148
ORIGIN = "synthetic_network_observation"
DOCUMENTATION_NETWORKS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
IP_POOL = [str(h) for net in DOCUMENTATION_NETWORKS for h in net.hosts()]          # 762 addresses
USER_AGENTS = ["SynthWallet/1.0 (Windows)", "SynthWallet/1.0 (macOS)", "SynthWallet/1.0 (Linux)", "SynthMobile/2.3 (Android)", "SynthMobile/2.3 (iOS)"]
NETWORK_TYPES = ["broadband", "mobile", "vpn"]
NETWORK_TYPE_WEIGHTS = [0.6, 0.3, 0.1]
REGIONS = [f"SYN-REGION-{i:02d}" for i in range(1, 9)]          # not real places

SENDER_COVERAGE = 0.8
RECEIVER_COVERAGE = 0.5
SHARED_WALLET_FRACTION = 0.15       # share of wallets that sit in a multi-wallet group
GROUP_SIZES = (2, 5)
NAT_SHARE = 0.06                    # share of devices that reuse another device's IP
SESSION_GAP = timedelta(hours=2)

CSV_COLUMNS = ["observation_id", "transaction_id", "wallet_address", "ip_address", "device_id", "user_agent", "network_type",
               "session_id", "geo_region", "observed_at", "origin", "is_synthetic"]


def is_documentation_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in DOCUMENTATION_NETWORKS)


def _u(seed: int, *parts: object) -> float:
    """A deterministic pseudo-random number in [0, 1) from the seed and some identifying parts."""
    digest = hashlib.sha256("|".join(str(p) for p in (seed, *parts)).encode()).hexdigest()
    return int(digest[:12], 16) / 16**12


@dataclass
class Device:
    device_id: str
    ips: list[str]
    user_agent: str
    network_type: str
    geo_region: str


@dataclass
class _Session:
    session_id: str
    last_at: datetime


@dataclass
class ObservationGenerator:
    seed: int = DEFAULT_SEED
    wallet_device: dict[str, str] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    sessions: dict[str, _Session] = field(default_factory=dict)          # device_id -> its current session
    used_ips: set[str] = field(default_factory=set)
    next_device_no: int = 1

    # ---- profiles ------------------------------------------------------------------------------------
    def assign_initial_profiles(self, wallets: Iterable[str]) -> None:
        """Batch mode: give every wallet a device, with some wallets sharing one. Deterministic for a given wallet set."""
        ordered = sorted(set(wallets))
        rng = random.Random(self.seed)
        shuffled = ordered[:]
        rng.shuffle(shuffled)
        n_shared = int(len(shuffled) * SHARED_WALLET_FRACTION)
        pool, groups = shuffled[:n_shared], []
        while len(pool) >= GROUP_SIZES[0]:
            size = min(rng.randint(*GROUP_SIZES), len(pool))
            if len(pool) - size == 1:                       # never leave a lone wallet behind a group
                size += 1
            groups.append(pool[:size])
            pool = pool[size:]
        grouped = {w for g in groups for w in g}
        operators = groups + [[w] for w in ordered if w not in grouped]
        operators.sort(key=lambda g: min(g))                # stable device numbering

        ip_pool = IP_POOL[:]
        rng.shuffle(ip_pool)
        for members in operators:
            device = self._new_device(rng, ip_pool)
            for w in members:
                self.wallet_device[w] = device.device_id
        devices = list(self.devices.values())
        for d in rng.sample(devices, round(NAT_SHARE * len(devices))):          # a few devices reuse another device's IP
            other = rng.choice([o for o in devices if o.device_id != d.device_id])
            d.ips[0] = other.ips[0]
        self.used_ips = {ip for d in self.devices.values() for ip in d.ips}

    def _new_device(self, rng: random.Random, ip_pool: list[str]) -> Device:
        n_ips = rng.choice([1, 1, 2, 3])
        ips = [ip_pool.pop() if ip_pool else rng.choice(IP_POOL) for _ in range(n_ips)]
        device = Device(
            device_id=f"dev-{self.next_device_no:04d}", ips=ips, user_agent=rng.choice(USER_AGENTS),
            network_type=rng.choices(NETWORK_TYPES, NETWORK_TYPE_WEIGHTS)[0], geo_region=rng.choice(REGIONS))
        self.devices[device.device_id] = device
        self.next_device_no += 1
        return device

    def ensure_wallet(self, wallet: str) -> None:
        """Live mode: a wallet never seen before gets its own new device (deterministic for this seed and wallet)."""
        if wallet in self.wallet_device:
            return
        rng = random.Random(f"{self.seed}|{wallet}")
        free = [ip for ip in IP_POOL if ip not in self.used_ips]
        rng.shuffle(free)
        device = self._new_device(rng, free)
        self.used_ips.update(device.ips)
        self.wallet_device[wallet] = device.device_id

    # ---- observations --------------------------------------------------------------------------------
    def observe(self, transaction_id: str, timestamp: datetime, sender: str, receiver: str) -> list[dict]:
        """Observation rows (0, 1 or 2) for one transaction, from the sender's and/or receiver's device."""
        rows = []
        for role, wallet, coverage in (("S", sender, SENDER_COVERAGE), ("R", receiver, RECEIVER_COVERAGE)):
            if _u(self.seed, "coverage", transaction_id, role) >= coverage:
                continue
            self.ensure_wallet(wallet)
            device = self.devices[self.wallet_device[wallet]]
            ip = device.ips[0] if len(device.ips) == 1 or _u(self.seed, "ip", transaction_id, role) < 0.85 else \
                device.ips[1 + int(_u(self.seed, "ip2", transaction_id, role) * (len(device.ips) - 1))]
            observed_at = timestamp + timedelta(seconds=1 + int(_u(self.seed, "delay", transaction_id, role) * 30))
            session = self.sessions.get(device.device_id)
            if session is None or observed_at - session.last_at > SESSION_GAP:
                session = _Session(f"sess-{device.device_id}-{observed_at:%Y%m%d%H%M%S}", observed_at)
                self.sessions[device.device_id] = session
            session.last_at = max(session.last_at, observed_at)
            rows.append({
                "observation_id": f"obs-{transaction_id}-{role}", "transaction_id": transaction_id, "wallet_address": wallet,
                "ip_address": ip, "device_id": device.device_id, "user_agent": device.user_agent, "network_type": device.network_type,
                "session_id": session.session_id, "geo_region": device.geo_region, "observed_at": observed_at,
                "origin": ORIGIN, "is_synthetic": True,
            })
        return rows


def generate_dataset(transactions: Sequence[tuple[str, datetime, str, str]], seed: int = DEFAULT_SEED) -> list[dict]:
    """
    transactions: (transaction_id, timestamp, sender, receiver) in chronological order.
    Returns the observation rows of the whole dataset. Same input and seed always give the same output.
    """
    gen = ObservationGenerator(seed)
    gen.assign_initial_profiles([w for _, _, s, r in transactions for w in (s, r)])
    rows: list[dict] = []
    for tid, ts, sender, receiver in transactions:
        rows.extend(gen.observe(tid, ts, sender, receiver))
    return rows


# ---- CSV -------------------------------------------------------------------------------------------------
def write_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "observed_at": r["observed_at"].strftime("%Y-%m-%d %H:%M:%S"), "is_synthetic": "true"})


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError(f"Unexpected columns {reader.fieldnames}; expected {CSV_COLUMNS}")
        rows = []
        for n, r in enumerate(reader, start=2):
            try:
                rows.append({**r, "observed_at": datetime.strptime(r["observed_at"], "%Y-%m-%d %H:%M:%S"), "is_synthetic": r["is_synthetic"].strip().lower() == "true"})
            except ValueError as e:
                raise ValueError(f"line {n}: {e}") from e
    return rows
