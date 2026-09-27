"""
Relationship graph for the investigator UI, built from the transaction data and (synthetic) network observations.

Node types   wallet · transaction · ip_observation · device · session
Edge types   sent_to · received_from · observed_from · associated_with · same_device · same_session

  wallet view (default)      wallet --sent_to--> wallet             transfers between two wallets (aggregated: count, BTC)
  transaction view           wallet --sent_to--> transaction        the sender of a transaction
  (node_types includes       wallet --received_from--> transaction  the receiver of a transaction
   'transaction')
  infrastructure             transaction --observed_from--> ip_observation   (wallet --observed_from--> ip in the wallet view)
  (SYNTHETIC; needs          ip_observation --associated_with--> device
   ip_observation/device/    session --associated_with--> device
   session in node_types)    transaction --associated_with--> session   (wallet --associated_with--> session in the wallet view)
  derived, wallet-wallet     same_device / same_session  between two included wallets observed on the same device / in the same session

`ip_observation` is one node per observed IP address (so shared IPs appear as shared nodes). Infrastructure nodes only
exist for SYNTHETIC data and are labelled so; real Bitcoin transactions never have them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..analysis.service import latest_run
from ..errors import AppError
from ..models import (
    AnomalyResult, EntityClusterMember, FusionResult, InvestigativeLead, NetworkObservation, Transaction, Wallet,
)
from ..schemas import SYNTHETIC_NETWORK_NOTE, GraphEdgeOut, GraphNodeOut, GraphOut

NODE_TYPES = ("wallet", "transaction", "ip_observation", "device", "session")
EDGE_TYPES = ("sent_to", "received_from", "observed_from", "associated_with", "same_device", "same_session")
INFRA = {"ip_observation", "device", "session"}
HARD_MAX_NODES = 500


@dataclass
class GraphRequest:
    focus_type: str                 # wallet | transaction | cluster | leads
    focus_id: str | None = None
    depth: int = 1
    node_types: set[str] = field(default_factory=lambda: {"wallet"})
    edge_types: set[str] | None = None
    max_nodes: int = 150
    max_transactions: int = 150
    lead_limit: int = 20
    seeds: list[str] | None = None      # focus_type 'wallets': an explicit wallet list (used for a case)


def parse_csv_param(value: str | None, allowed: tuple[str, ...], name: str) -> set[str] | None:
    if value is None or not value.strip():
        return None
    items = {v.strip() for v in value.split(",") if v.strip()}
    bad = items - set(allowed)
    if bad:
        raise AppError(422, "validation_error", f"Unknown {name}: {', '.join(sorted(bad))}. Allowed: {', '.join(allowed)}.")
    return items


def wallet_neighbor_links(session: Session, frontier: set[str], known: set[str]) -> dict[str, Counter]:
    """
    For every wallet outside `known` that has at least one transfer (either direction) to/from a wallet in
    `frontier`, {candidate_wallet: Counter({frontier_wallet: transfer_count})} -- the breakdown of which frontier
    wallet(s) it is linked through, and how many transfers each link represents.

    Shared by this module's wallet-graph expansion (below) and analysis/risk_propagation.py, so both walk the
    wallet transfer graph exactly the same way instead of two independent implementations.
    """
    links: dict[str, Counter] = defaultdict(Counter)
    for snd, rcv in session.execute(select(Transaction.sender_wallet, Transaction.receiver_wallet).where(
            or_(Transaction.sender_wallet.in_(frontier), Transaction.receiver_wallet.in_(frontier)))):
        for a, b in ((snd, rcv), (rcv, snd)):
            if a in frontier and b not in known:
                links[b][a] += 1
    return links


def _wallet_link_totals(by_candidate: dict[str, Counter]) -> Counter:
    """
    Aggregate transfer count per candidate, built up in the same first-seen order as `wallet_neighbor_links` produced
    them -- so `Counter.most_common()`'s tie-break (insertion order) is identical to what build_graph computed before
    this helper existed. A pure extraction, not a behaviour change.
    """
    total: Counter = Counter()
    for candidate, per_parent in by_candidate.items():
        total[candidate] = sum(per_parent.values())
    return total


def build_graph(session: Session, req: GraphRequest) -> GraphOut:
    node_types = set(req.node_types) | {"wallet"}
    max_nodes = min(req.max_nodes, HARD_MAX_NODES)
    truncated, omitted = False, Counter()

    # ---- 1. seeds -------------------------------------------------------------------------------------------
    focus_tx: Transaction | None = None
    if req.focus_type == "wallet":
        if session.get(Wallet, req.focus_id) is None:
            raise AppError(404, "not_found", f"No wallet {req.focus_id!r}.")
        seeds = [req.focus_id]
    elif req.focus_type == "transaction":
        focus_tx = session.get(Transaction, req.focus_id)
        if focus_tx is None:
            raise AppError(404, "not_found", f"No transaction {req.focus_id!r}.")
        seeds = [focus_tx.sender_wallet, focus_tx.receiver_wallet]
    elif req.focus_type == "cluster":
        seeds = sorted(session.scalars(select(EntityClusterMember.entity_id).where(
            EntityClusterMember.cluster_id == req.focus_id, EntityClusterMember.entity_type == "wallet")))
        if not seeds:
            raise AppError(404, "not_found", f"No cluster {req.focus_id!r}.")
    elif req.focus_type == "wallets":
        seeds = list(req.seeds or [])
    elif req.focus_type == "leads":
        seeds = list(session.scalars(select(InvestigativeLead.wallet_address).order_by(InvestigativeLead.priority_rank).limit(req.lead_limit)))
    else:
        raise AppError(422, "validation_error", f"Unknown focus {req.focus_type!r}.")
    seeds = seeds[:max_nodes]

    # ---- 2. expand the wallet set by transfer links ----------------------------------------------------------
    wallets: list[str] = list(dict.fromkeys(seeds))
    known = set(wallets)
    frontier = set(wallets)
    for _ in range(max(0, req.depth)):
        if not frontier:
            break
        links = _wallet_link_totals(wallet_neighbor_links(session, frontier, known))
        added = []
        for w, _n in links.most_common():
            if len(wallets) >= max_nodes:
                omitted["wallet"] += 1
                truncated = True
            else:
                wallets.append(w)
                known.add(w)
                added.append(w)
        frontier = set(added)
    wallet_set = set(wallets)

    nodes: dict[str, GraphNodeOut] = {}
    edges: dict[str, GraphEdgeOut] = {}

    def add_edge(etype: str, source: str, target: str, **data):
        eid = f"{etype}:{source}->{target}"
        if eid not in edges:
            edges[eid] = GraphEdgeOut(id=eid, source=source, target=target, type=etype, data=data)

    # ---- 3. wallet nodes (with their analysis, if any) ------------------------------------------------------------
    info = _wallet_info(session, wallets)
    for w in wallets:
        i = info.get(w, {})
        nodes[f"wallet:{w}"] = GraphNodeOut(id=f"wallet:{w}", type="wallet", label=w, data={
            "address": w, "is_focus": w in seeds and req.focus_type in ("wallet", "wallets"), **i})

    # ---- 4. transfers between the included wallets --------------------------------------------------------------------
    tx_rows = list(session.execute(select(Transaction).where(Transaction.sender_wallet.in_(wallet_set), Transaction.receiver_wallet.in_(wallet_set))
                                   .order_by(Transaction.timestamp.desc(), Transaction.transaction_id.desc())))
    tx_rows = [r[0] for r in tx_rows]
    included_tx: list[Transaction] = []
    if "transaction" in node_types:
        included_tx = tx_rows[: req.max_transactions]
        if focus_tx is not None and focus_tx not in included_tx:
            included_tx = [focus_tx] + included_tx[: req.max_transactions - 1]
        if len(tx_rows) > len(included_tx):
            truncated = True
            omitted["transaction"] += len(tx_rows) - len(included_tx)
        for t in included_tx:
            tid = f"tx:{t.transaction_id}"
            nodes[tid] = GraphNodeOut(id=tid, type="transaction", label=t.transaction_id, data={
                "transaction_id": t.transaction_id, "timestamp": t.timestamp.isoformat() + "Z", "amount_btc": t.amount_btc, "source": t.source,
                "is_focus": focus_tx is not None and t.transaction_id == focus_tx.transaction_id})
            add_edge("sent_to", f"wallet:{t.sender_wallet}", tid, amount_btc=t.amount_btc)
            add_edge("received_from", f"wallet:{t.receiver_wallet}", tid, amount_btc=t.amount_btc)
    else:
        agg: dict[tuple[str, str], dict] = {}
        for t in tx_rows:
            a = agg.setdefault((t.sender_wallet, t.receiver_wallet), {"transfers": 0, "total_btc": 0.0, "first": t.timestamp, "last": t.timestamp})
            a["transfers"] += 1
            a["total_btc"] += t.amount_btc
            a["first"], a["last"] = min(a["first"], t.timestamp), max(a["last"], t.timestamp)
        for (snd, rcv), a in agg.items():
            add_edge("sent_to", f"wallet:{snd}", f"wallet:{rcv}", transfers=a["transfers"], total_btc=round(a["total_btc"], 8),
                     first_at=a["first"].isoformat() + "Z", last_at=a["last"].isoformat() + "Z")

    # ---- 5. synthetic network observations: infrastructure nodes/edges and derived wallet-wallet edges ----------------------------
    obs = list(session.scalars(select(NetworkObservation).where(NetworkObservation.wallet_address.in_(wallet_set)).order_by(NetworkObservation.observed_at)))
    tx_ids = {t.transaction_id for t in included_tx}
    show_ip, show_device, show_session = "ip_observation" in node_types, "device" in node_types, "session" in node_types
    ip_count, dev_info, sess_info = Counter(), {}, {}
    dev_wallets, sess_wallets = defaultdict(set), defaultdict(set)
    for o in obs:
        if o.device_id:
            dev_wallets[o.device_id].add(o.wallet_address)
        if o.session_id:
            sess_wallets[o.session_id].add(o.wallet_address)
    infra_nodes = 0

    def room_for(node_id: str, kind: str) -> bool:
        """Infrastructure nodes are capped at max_nodes so a busy wallet cannot flood the picture."""
        nonlocal infra_nodes, truncated
        if node_id in nodes:
            return True
        if infra_nodes >= max_nodes:
            truncated = True
            omitted[kind] += 1
            return False
        infra_nodes += 1
        return True

    for o in obs:
        in_tx_view = "transaction" in node_types
        if in_tx_view and o.transaction_id not in tx_ids:
            continue
        anchor = f"tx:{o.transaction_id}" if in_tx_view else f"wallet:{o.wallet_address}"
        if show_ip and o.ip_address and room_for(f"ip:{o.ip_address}", "ip_observation"):
            ipn = f"ip:{o.ip_address}"
            ip_count[o.ip_address] += 1
            nodes.setdefault(ipn, GraphNodeOut(id=ipn, type="ip_observation", label=o.ip_address, data={"ip_address": o.ip_address, "synthetic": True, "note": SYNTHETIC_NETWORK_NOTE}))
            add_edge("observed_from", anchor, ipn)
        if show_device and o.device_id and room_for(f"device:{o.device_id}", "device"):
            dn = f"device:{o.device_id}"
            if dn not in nodes:
                nodes[dn] = GraphNodeOut(id=dn, type="device", label=o.device_id, data={
                    "device_id": o.device_id, "user_agent": o.user_agent, "network_type": o.network_type, "geo_region": o.geo_region,
                    "wallet_count": len(dev_wallets[o.device_id]), "synthetic": True, "note": SYNTHETIC_NETWORK_NOTE})
            if show_ip and o.ip_address:
                add_edge("associated_with", f"ip:{o.ip_address}", dn)
            if show_session and o.session_id:
                add_edge("associated_with", f"session:{o.session_id}", dn)
        if show_session and o.session_id and room_for(f"session:{o.session_id}", "session"):
            sn = f"session:{o.session_id}"
            if sn not in nodes:
                nodes[sn] = GraphNodeOut(id=sn, type="session", label=o.session_id, data={"session_id": o.session_id, "started_at": None, "synthetic": True, "note": SYNTHETIC_NETWORK_NOTE})
            sess_info.setdefault(o.session_id, []).append(o.observed_at)
            add_edge("associated_with", anchor, sn)
    for ip, n in ip_count.items():
        nodes[f"ip:{ip}"].data["observation_count"] = n
    for sid, times in sess_info.items():
        nodes[f"session:{sid}"].data.update({"started_at": min(times).isoformat() + "Z", "ended_at": max(times).isoformat() + "Z", "observation_count": len(times)})
    # derived wallet-wallet edges
    for kind, groups in (("same_device", dev_wallets), ("same_session", sess_wallets)):
        for ident, ws in groups.items():
            ordered = sorted(ws)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1:]:
                    add_edge(kind, f"wallet:{a}", f"wallet:{b}", **({"device_id": ident} if kind == "same_device" else {"session_id": ident}), synthetic=True)

    # ---- 6. filters ----------------------------------------------------------------------------------------------------------
    if req.edge_types is not None:
        edges = {k: v for k, v in edges.items() if v.type in req.edge_types}
    edges = {k: v for k, v in edges.items() if v.source in nodes and v.target in nodes}

    node_list = list(nodes.values())
    return GraphOut(
        focus={"type": "case" if req.focus_type == "wallets" else req.focus_type, "id": req.focus_id}, nodes=node_list, edges=list(edges.values()), truncated=truncated,
        omitted=dict(omitted),
        counts={"nodes": dict(Counter(n.type for n in node_list)), "edges": dict(Counter(e.type for e in edges.values()))},
        notes=[SYNTHETIC_NETWORK_NOTE, "Bitcoin transactions do not contain IP addresses, device identifiers or locations; those come from a separate observation source.",
               "A relationship in this graph shows connected activity or shared synthetic infrastructure, not common ownership or wrongdoing."],
    )


def _wallet_info(session: Session, wallets: list[str]) -> dict[str, dict]:
    """Analysis fields for wallet nodes (empty until an analysis has run)."""
    if not wallets:
        return {}
    out: dict[str, dict] = {w: {} for w in wallets}
    counts = Counter()
    for snd, rcv in session.execute(select(Transaction.sender_wallet, Transaction.receiver_wallet).where(or_(Transaction.sender_wallet.in_(wallets), Transaction.receiver_wallet.in_(wallets)))):
        counts[snd] += 1
        counts[rcv] += 1
    for w in wallets:
        out[w]["transaction_count"] = counts[w]
    sources = dict(session.execute(select(Wallet.address, Wallet.source).where(Wallet.address.in_(wallets))).all())
    for w in wallets:
        out[w]["source"] = sources.get(w)
    run = latest_run(session, "synthetic")
    if run is not None:
        for f in session.scalars(select(FusionResult).where(FusionResult.run_id == run.run_id, FusionResult.wallet_address.in_(wallets))):
            out[f.wallet_address].update({"priority_rank": f.priority_rank, "priority_level": f.priority_level, "combined_score": f.combined_score})
        for a in session.scalars(select(AnomalyResult).where(AnomalyResult.run_id == run.run_id, AnomalyResult.wallet_address.in_(wallets))):
            out[a.wallet_address]["ml_prediction"] = a.anomaly_prediction
        leads = set(session.scalars(select(InvestigativeLead.wallet_address).where(InvestigativeLead.wallet_address.in_(wallets))))
        for w in wallets:
            out[w]["is_lead"] = w in leads
    return out
