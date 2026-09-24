"""
generate_dataset.py
-------------------
Builds a SYNTHETIC raw Bitcoin transaction dataset (no real addresses, no real data).

Output : dataset/synthetic_bitcoin_transactions.csv
Columns: timestamp, wallet_address, amount_btc, direction,
         input_count, output_count, counterparty_wallet

How it works (short version)
  1. We create a network of wallets (small "communities" of wallets that trade with each other).
  2. We generate TRANSFERS (wallet A sends BTC to wallet B). Most are normal, some follow
     unusual patterns (rapid movement, fan-out, fan-in, ...).
  3. Every transfer is written as TWO rows so the data is logically consistent:
        A | Outgoing | counterparty B      and      B | Incoming | counterparty A
     (same timestamp, amount, input_count and output_count on both rows).
  4. Behaviour tags are only kept in memory to report percentages. They are NOT saved in the CSV.

This script only creates RAW data. No ML features, no scores, no labels.
"""

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
SEED = 42                       # fixed seed -> the same dataset every run
TOTAL_TRANSFERS = 5000          # each transfer = 2 rows -> 10,000 rows
N_NORMAL_WALLETS = 320          # ordinary wallets that make up the base network
COMMUNITY_SIZE = 16             # wallets are grouped in small communities
START = datetime(2025, 10, 1)
END = datetime(2026, 9, 24, 23, 59, 59)     # roughly one year of history, ending on 24 September 2026
N_DAYS = (END.date() - START.date()).days + 1   # 359 calendar days in the window

# Share of all transfers whose anchor time falls in each month (they need not be exact). The
# generators below place their patterns on an abstract 365-slot timeline; random_time() maps a slot
# onto a real day using these shares, so activity is balanced across the year and September 2026
# (only 24 days long) stays at about a tenth of the data at most.
MONTH_SHARE = {
    (2025, 10): 8, (2025, 11): 8, (2025, 12): 8,
    (2026, 1): 8, (2026, 2): 8, (2026, 3): 9, (2026, 4): 8, (2026, 5): 8, (2026, 6): 9,
    (2026, 7): 8, (2026, 8): 8, (2026, 9): 10,
}
TIMELINE_SLOTS = 365            # the abstract timeline the behaviours are written against
DAY_BURSTINESS = 0.5            # spread of the per-day activity level (0 = every day identical)

OUTPUT_DIR = "dataset"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "synthetic_bitcoin_transactions.csv")
COLUMNS = ["timestamp", "wallet_address", "amount_btc", "direction",
           "input_count", "output_count", "counterparty_wallet"]

rng = np.random.default_rng(SEED)

# ----------------------------------------------------------------------------
# Storage: wallets are plain integers while generating; they are renamed
# to wallet_001, wallet_002, ... (in shuffled order) at the very end.
# ----------------------------------------------------------------------------
transfers = []          # list of dicts: time, src, dst, amount, n_in, n_out, tag
_next_wallet = [0]


def new_wallet():
    """Create a brand-new wallet id."""
    _next_wallet[0] += 1
    return _next_wallet[0]


def clip_time(t):
    """Keep a timestamp inside the dataset time window."""
    return min(max(t, START), END)


def normal_counts():
    """Typical input/output counts for an ordinary transaction."""
    n_in = int(rng.choice([1, 2, 3, 4], p=[0.55, 0.25, 0.13, 0.07]))
    n_out = int(rng.choice([1, 2, 3], p=[0.30, 0.55, 0.15]))
    return n_in, n_out


def add_transfer(time, src, dst, amount, tag, n_in=None, n_out=None):
    """Record one transfer src -> dst."""
    if n_in is None or n_out is None:
        n_in, n_out = normal_counts()
    transfers.append({
        "time": clip_time(time), "src": src, "dst": dst,
        "amount": max(round(float(amount), 8), 0.00001),
        "n_in": n_in, "n_out": n_out, "tag": tag,
    })


# Busy hours are during the day, quiet at night.
_hour_p = np.array([1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 7, 7, 6, 5, 4, 3, 2, 1], float)
_hour_p /= _hour_p.sum()


def _build_day_map():
    """
    Map each abstract timeline slot (0..364) to a calendar day in [START, END].

    Each calendar day gets an activity level: a random burstiness factor, rescaled so the days of a
    month add up to that month's MONTH_SHARE. Slots are then placed by quantile, so a slot's relative
    position in the year (early / mid / late) is kept, while the number of transfers per month follows
    MONTH_SHARE and individual days are busier or quieter. A separate random generator is used, so the
    main generator's random draws (wallets, amounts, behaviours) are exactly what they were before.
    """
    day_rng = np.random.default_rng(SEED + 1)
    days = [START + timedelta(days=i) for i in range(N_DAYS)]
    level = np.exp(day_rng.normal(0.0, DAY_BURSTINESS, N_DAYS))
    density = np.zeros(N_DAYS)
    for key, share in MONTH_SHARE.items():
        idx = [i for i, d in enumerate(days) if (d.year, d.month) == key]
        density[idx] = share * level[idx] / level[idx].sum()
    assert abs(sum(MONTH_SHARE.values()) - 100) < 1e-9 and (density > 0).all()
    cumulative = np.cumsum(density) / density.sum()
    jitter = day_rng.random(TIMELINE_SLOTS * 64)            # position inside a slot, drawn once
    return cumulative, jitter


_day_cumulative, _slot_jitter = _build_day_map()
_jitter_pos = [0]


def slot_to_day(slot):
    """Calendar day index (0 = START) for a timeline slot."""
    j = _slot_jitter[_jitter_pos[0] % len(_slot_jitter)]
    _jitter_pos[0] += 1
    u = (slot + j) / TIMELINE_SLOTS
    return int(min(np.searchsorted(_day_cumulative, u, side="left"), N_DAYS - 1))


def random_time(day_from=0, day_to=TIMELINE_SLOTS - 1):
    """Random realistic timestamp between two timeline slots (day numbers of the year)."""
    day = slot_to_day(int(rng.integers(day_from, day_to + 1)))
    hour = int(rng.choice(24, p=_hour_p))
    return START + timedelta(days=day, hours=hour,
                             minutes=int(rng.integers(0, 60)), seconds=int(rng.integers(0, 60)))


# ----------------------------------------------------------------------------
# Step 1: the base wallet network
# ----------------------------------------------------------------------------
normal_wallets = [new_wallet() for _ in range(N_NORMAL_WALLETS)]

# Each wallet has its own typical amount ("median") and its own activity level.
median_amount = {w: float(np.exp(rng.normal(np.log(0.05), 1.0))) for w in normal_wallets}
activity = {w: float(np.exp(rng.normal(0, 0.8))) for w in normal_wallets}

# Each wallet has a few usual trading partners, mostly inside its own community.
friends = {}
for start in range(0, N_NORMAL_WALLETS, COMMUNITY_SIZE):
    community = normal_wallets[start:start + COMMUNITY_SIZE]
    for w in community:
        others = [c for c in community if c != w]
        chosen = list(rng.choice(others, size=int(rng.integers(3, 8)), replace=False))
        if rng.random() < 0.10:                       # occasional cross-community link
            chosen[0] = int(rng.choice(normal_wallets))
        friends[w] = [int(c) for c in chosen if c != w]


def other_wallet(w):
    """A random normal wallet different from w."""
    while True:
        c = int(rng.choice(normal_wallets))
        if c != w:
            return c


# ----------------------------------------------------------------------------
# Step 2: unusual behaviours (generated first so we know how many rows they use)
# ----------------------------------------------------------------------------
def gen_rapid_movement(n_chains=25):
    """Funds arrive at a wallet and are passed on again within minutes (multi-hop)."""
    for _ in range(n_chains):
        t = random_time(60, 300)
        amount = float(rng.uniform(0.5, 8))
        wallets = [int(rng.choice(normal_wallets))] + [new_wallet() for _ in range(int(rng.integers(2, 4)))]
        wallets.append(other_wallet(wallets[0]))              # final "cash-out" wallet
        for a, b in zip(wallets[:-1], wallets[1:]):
            add_transfer(t, a, b, amount, "rapid_fund_movement", int(rng.integers(1, 3)), int(rng.integers(1, 3)))
            t += timedelta(minutes=float(rng.uniform(2, 25)), seconds=int(rng.integers(0, 60)))
            amount *= float(rng.uniform(0.97, 0.995))        # small cut taken at each hop


def gen_dormant_activation(n_wallets=12):
    """Wallet is quiet for ~9 months, then suddenly becomes very active."""
    for _ in range(n_wallets):
        d = new_wallet()
        base = float(rng.uniform(0.01, 0.1))
        for _ in range(int(rng.integers(2, 4))):              # small early history (normal)
            t = random_time(0, 45)
            if rng.random() < 0.5:
                add_transfer(t, d, other_wallet(d), base * rng.lognormal(0, 0.3), "normal")
            else:
                add_transfer(t, other_wallet(d), d, base * rng.lognormal(0, 0.3), "normal")
        t = random_time(315, 330)                             # wakes up ~9 months later (late in the period)
        for _ in range(int(rng.integers(6, 9))):
            t += timedelta(minutes=float(rng.uniform(5, 120)))
            add_transfer(t, d, other_wallet(d), base * rng.uniform(8, 25), "dormant_activation")


def gen_high_frequency(n_wallets=8):
    """An existing wallet makes many transactions within about an hour."""
    for w in rng.choice(normal_wallets, size=n_wallets, replace=False):
        w = int(w)
        t = random_time(30, 330)
        amount = median_amount[w]
        for _ in range(int(rng.integers(11, 15))):
            t += timedelta(seconds=int(rng.integers(30, 420)))
            c = other_wallet(w)
            a = amount * float(rng.uniform(0.7, 1.3))
            if rng.random() < 0.7:
                add_transfer(t, w, c, a, "high_frequency")
            else:
                add_transfer(t, c, w, a, "high_frequency")


def gen_fan_out(n_sources=6):
    """One wallet receives funds, then sends to many different wallets within hours."""
    for _ in range(n_sources):
        src = new_wallet()
        t = random_time(30, 320)
        k = int(rng.integers(12, 19))
        total = float(rng.uniform(2, 10))
        add_transfer(t, other_wallet(src), src, total, "fan_out")
        t += timedelta(minutes=30)
        for dst in rng.choice(normal_wallets, size=k, replace=False):
            t += timedelta(minutes=float(rng.uniform(3, 25)))
            add_transfer(t, src, int(dst), total / k * rng.uniform(0.9, 1.1), "fan_out",
                         int(rng.integers(1, 3)), int(rng.integers(3, 8)))


def gen_fan_in(n_collectors=6):
    """Many wallets send to one wallet in a short time, which then forwards the total."""
    for _ in range(n_collectors):
        col = new_wallet()
        t = random_time(30, 320)
        k = int(rng.integers(12, 19))
        total = 0.0
        for s in rng.choice(normal_wallets, size=k, replace=False):
            t += timedelta(minutes=float(rng.uniform(5, 45)))
            a = float(rng.uniform(0.05, 0.6))
            total += a
            add_transfer(t, int(s), col, a, "fan_in", int(rng.integers(1, 3)), int(rng.integers(1, 3)))
        t += timedelta(hours=float(rng.uniform(1, 6)))
        add_transfer(t, col, other_wallet(col), total * 0.99, "fan_in",
                     int(rng.integers(5, 15)), int(rng.integers(1, 3)))


def gen_repeated_relationships(n_pairs=5):
    """Two wallets that are NOT usual partners trade with each other over and over."""
    for _ in range(n_pairs):
        a = int(rng.choice(normal_wallets))
        b = other_wallet(a)
        while b in friends[a] or a in friends[b]:
            b = other_wallet(a)
        t = random_time(30, 300)
        amount = float(rng.uniform(0.05, 0.5))
        for _ in range(int(rng.integers(14, 19))):
            t += timedelta(hours=float(rng.uniform(6, 20)))
            a2 = amount * float(rng.uniform(0.98, 1.02))
            if rng.random() < 0.8:
                add_transfer(t, a, b, a2, "repeated_relationship")
            else:
                add_transfer(t, b, a, a2, "repeated_relationship")


def gen_unusual_amounts(n=60):
    """A normal-looking transfer, but 15-50x bigger than that wallet's usual amount."""
    for _ in range(n):
        w = int(rng.choice(normal_wallets))
        c = int(rng.choice(friends[w]))
        add_transfer(random_time(), w, c, median_amount[w] * float(rng.uniform(15, 50)), "unusual_amount")


gen_rapid_movement()
gen_dormant_activation()
gen_high_frequency()
gen_fan_out()
gen_fan_in()
gen_repeated_relationships()
gen_unusual_amounts()

# ----------------------------------------------------------------------------
# Step 3: normal behaviour
# ----------------------------------------------------------------------------
def gen_merchant_hubs(n_hubs=3, per_hub=100):
    """Merchant-like wallets that receive from many different wallets, spread over the year."""
    for _ in range(n_hubs):
        hub = new_wallet()
        for _ in range(per_hub):
            s = int(rng.choice(normal_wallets))
            add_transfer(random_time(), s, hub, median_amount[s] * rng.lognormal(0, 0.35), "normal")


def gen_payroll_hubs(n_hubs=2, employees=8):
    """Payroll-like wallets that pay the same people every month (normal repeats)."""
    for _ in range(n_hubs):
        hub = new_wallet()
        staff = [int(x) for x in rng.choice(normal_wallets, size=employees, replace=False)]
        salary = {e: float(rng.uniform(0.02, 0.15)) for e in staff}
        for month in range(12):
            for e in staff:
                t = START + timedelta(days=month * 30 + int(rng.integers(0, 3)),
                                      hours=int(rng.integers(9, 17)), minutes=int(rng.integers(0, 60)))
                add_transfer(t, hub, e, salary[e] * rng.uniform(0.98, 1.02), "normal")


def gen_normal_chains(n_chains=40):
    """Slow multi-hop paths: A -> B -> C -> D with hours/days between hops."""
    for _ in range(n_chains):
        path = [int(x) for x in rng.choice(normal_wallets, size=int(rng.integers(3, 5)), replace=False)]
        t = random_time(0, 330)
        amount = median_amount[path[0]] * float(rng.lognormal(0, 0.3))
        for a, b in zip(path[:-1], path[1:]):
            add_transfer(t, a, b, amount, "normal")
            t += timedelta(hours=float(rng.uniform(6, 96)))
            amount *= float(rng.uniform(0.6, 0.95))


gen_merchant_hubs()
gen_payroll_hubs()
gen_normal_chains()

# Ordinary background activity fills the rest, mostly between usual partners.
n_remaining = TOTAL_TRANSFERS - len(transfers)
weights = np.array([activity[w] for w in normal_wallets])
weights /= weights.sum()
for _ in range(n_remaining):
    w = int(rng.choice(normal_wallets, p=weights))
    c = int(rng.choice(friends[w])) if rng.random() < 0.85 else other_wallet(w)
    amount = median_amount[w] * float(rng.lognormal(0, 0.35))
    if rng.random() < 0.5:
        add_transfer(random_time(), w, c, amount, "normal")
    else:
        add_transfer(random_time(), c, w, amount, "normal")

# ----------------------------------------------------------------------------
# Step 4: turn transfers into rows and rename wallets
# ----------------------------------------------------------------------------
used = sorted({t["src"] for t in transfers} | {t["dst"] for t in transfers})
shuffled = rng.permutation(len(used)) + 1               # shuffle so ids don't reveal roles
width = max(3, len(str(len(used))))
name = {w: f"wallet_{int(n):0{width}d}" for w, n in zip(used, shuffled)}

rows = []
for t in transfers:
    stamp = t["time"].strftime("%Y-%m-%d %H:%M:%S")
    common = (t["amount"], None, t["n_in"], t["n_out"])
    rows.append([stamp, name[t["src"]], t["amount"], "Outgoing", t["n_in"], t["n_out"], name[t["dst"]]])
    rows.append([stamp, name[t["dst"]], t["amount"], "Incoming", t["n_in"], t["n_out"], name[t["src"]]])

df = pd.DataFrame(rows, columns=COLUMNS)
df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

os.makedirs(OUTPUT_DIR, exist_ok=True)
df.to_csv(OUTPUT_FILE, index=False)
print(f"Saved {len(df)} rows to {OUTPUT_FILE}")

tag_counts = Counter(t["tag"] for t in transfers)


# ----------------------------------------------------------------------------
# Step 5: validation (reads the CSV back from disk)
# ----------------------------------------------------------------------------
def find_time_ordered_path(data, hops=3):
    """Look for a path like A -> B -> C -> D where each hop happens later than the previous one."""
    out = data[data["direction"] == "Outgoing"]
    edges = defaultdict(list)                            # wallet -> [(time, next_wallet)]
    for ts, w, c in zip(out["timestamp"], out["wallet_address"], out["counterparty_wallet"]):
        edges[w].append((ts, c))

    def walk(wallet, last_time, path):
        if len(path) == hops + 1:
            return path
        for ts, nxt in edges.get(wallet, []):
            if ts > last_time and nxt not in path:
                found = walk(nxt, ts, path + [nxt])
                if found:
                    return found
        return None

    for w in list(edges)[:400]:
        for ts, nxt in edges[w][:20]:
            found = walk(nxt, ts, [w, nxt])
            if found:
                return found
    return None


def validate(path):
    d = pd.read_csv(path)
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Rows                          : {len(d)}")
    print(f"Columns ({len(d.columns)})                   : {list(d.columns)}")
    print(f"Columns exactly as specified  : {list(d.columns) == COLUMNS}")
    print(f"Unique wallets (wallet_address): {d['wallet_address'].nunique()}")
    print(f"Unique counterparties          : {d['counterparty_wallet'].nunique()}")
    print(f"Missing values                : {int(d.isna().sum().sum())}")
    print(f"Invalid directions            : {int((~d['direction'].isin(['Incoming', 'Outgoing'])).sum())}")
    print(f"Amounts <= 0                  : {int((d['amount_btc'] <= 0).sum())}")
    print(f"Invalid input/output counts   : {int(((d['input_count'] < 1) | (d['output_count'] < 1)).sum())}")
    print(f"Amount range (BTC)            : {d['amount_btc'].min():.6f} to {d['amount_btc'].max():.4f}")

    ts = pd.to_datetime(d["timestamp"])
    print(f"Timestamps sorted             : {ts.is_monotonic_increasing}  ({ts.min()} -> {ts.max()})")
    within = (ts >= pd.Timestamp(START)).all() and (ts <= pd.Timestamp(END)).all()
    print(f"Within {START.date()} .. {END.date()} : {bool(within)}")
    per_month = ts.dt.to_period("M").value_counts().sort_index() / len(ts)
    print("Rows per month               : " + ", ".join(f"{m} {v:.1%}" for m, v in per_month.items()))

    fmt = d["wallet_address"].str.match(r"^wallet_\d+$").all() and d["counterparty_wallet"].str.match(r"^wallet_\d+$").all()
    print(f"Wallet id format ok           : {bool(fmt)}")
    print(f"Wallets seen in only 1 row    : {int((pd.concat([d['wallet_address']]).value_counts() < 2).sum())}")

    # Incoming/Outgoing consistency: every Outgoing row must have a mirrored Incoming row.
    out = d[d["direction"] == "Outgoing"]
    inc = d[d["direction"] == "Incoming"]
    a = Counter(zip(out["timestamp"], out["wallet_address"], out["counterparty_wallet"], out["amount_btc"]))
    b = Counter(zip(inc["timestamp"], inc["counterparty_wallet"], inc["wallet_address"], inc["amount_btc"]))
    print(f"Outgoing/Incoming mirrored    : {a == b}")

    pairs = out.groupby(["wallet_address", "counterparty_wallet"]).size()
    print(f"Repeated relationships        : {int((pairs >= 2).sum())} wallet pairs with 2+ transfers, "
          f"{int((pairs >= 10).sum())} pairs with 10+ transfers")

    path_found = find_time_ordered_path(d, hops=3)
    print(f"Multi-hop path (3 hops) found : {path_found is not None}")
    if path_found:
        print("   example                    : " + " -> ".join(path_found))

    total = sum(tag_counts.values())
    unusual = sum(v for k, v in tag_counts.items() if k != "normal")
    print("\nBehaviour mix from the generation process (per transfer, not saved in CSV):")
    for k, v in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<24}{v:>6}  ({v / total:6.2%})")
    print(f"   -> normal {1 - unusual / total:.1%} | unusual {unusual / total:.1%}")

    print("\nFirst 10 rows:")
    print(d.head(10).to_string(index=False))


validate(OUTPUT_FILE)
