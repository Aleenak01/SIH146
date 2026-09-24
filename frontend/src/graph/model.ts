import type { Transfer, WalletRecord } from '../data/types';

// ---------------------------------------------------------------------------------------------
// Relationship graph data structure (framework-independent).
//   nodes = wallets, edges = aggregated transfers between two wallets.
// Built only from the transactions in the dataset: a wallet appears if it transacted with a focus
// wallet, and an edge exists only where at least one transfer exists between the two wallets.
// One or more focus wallets (a wallet under investigation, or all the wallets of a case) sit in the
// middle; their direct counterparties are drawn either side.
// ---------------------------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  role: 'focus' | 'counterparty';
  /** Senders (net flow towards the focus wallets) on the left, recipients on the right. */
  side: 'left' | 'center' | 'right';
  flagged: boolean;
  mlScore: number | null;
  /** Counterparties only: transfers to / from the focus wallets, and BTC moved in both directions. */
  toFocus?: number;
  fromFocus?: number;
  btc?: number;
}

export interface GraphEdge {
  id: string;
  source: string; // direction of the larger flow
  target: string;
  /** Transfers source -> target and (if any) target -> source. */
  forwardCount: number;
  backwardCount: number;
  forwardBtc: number;
  backwardBtc: number;
  /** 'focus' = touches a focus wallet; 'link' = between two counterparties. */
  kind: 'focus' | 'link';
}

export interface GraphModel {
  focusIds: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Counterparties that exist but are not drawn (kept the view readable). */
  hiddenCounterparties: number;
  totalCounterparties: number;
}

export interface GraphOptions {
  maxCounterparties?: number;
  /** Also draw transfers between two shown counterparties (not only those touching a focus wallet). */
  linkCounterparties?: boolean;
}

interface Agg {
  toFocus: number; // counterparty -> focus
  fromFocus: number; // focus -> counterparty
  btcTo: number;
  btcFrom: number;
}

export function buildGraph(
  focusIds: string[],
  transfers: Transfer[],
  byId: Map<string, WalletRecord>,
  { maxCounterparties = 10, linkCounterparties = false }: GraphOptions = {},
): GraphModel {
  const focus = new Set(focusIds);
  const agg = new Map<string, Agg>();
  const slot = (id: string) => {
    let a = agg.get(id);
    if (!a) agg.set(id, (a = { toFocus: 0, fromFocus: 0, btcTo: 0, btcFrom: 0 }));
    return a;
  };
  for (const t of transfers) {
    const fromIn = focus.has(t.from);
    const toIn = focus.has(t.to);
    if (fromIn && !toIn) {
      const a = slot(t.to);
      a.fromFocus++;
      a.btcFrom += t.amountBtc;
    } else if (toIn && !fromIn) {
      const a = slot(t.from);
      a.toFocus++;
      a.btcTo += t.amountBtc;
    }
  }

  const total = (a: Agg) => a.toFocus + a.fromFocus;
  const ranked = [...agg.entries()].sort((x, y) => total(y[1]) - total(x[1]) || y[1].btcTo + y[1].btcFrom - (x[1].btcTo + x[1].btcFrom) || x[0].localeCompare(y[0]));
  const shown = ranked.slice(0, maxCounterparties);
  const shownIds = new Set<string>([...focus, ...shown.map(([id]) => id)]);

  const info = (id: string) => {
    const w = byId.get(id);
    return { flagged: !!w?.flagged, mlScore: w?.fusion.ml_anomaly_score ?? null };
  };

  const nodes: GraphNode[] = focusIds.map((id) => ({ id, role: 'focus', side: 'center', ...info(id) }));
  for (const [id, a] of shown) {
    nodes.push({
      id,
      role: 'counterparty',
      side: a.toFocus >= a.fromFocus ? 'left' : 'right',
      ...info(id),
      toFocus: a.toFocus,
      fromFocus: a.fromFocus,
      btc: a.btcTo + a.btcFrom,
    });
  }

  // Aggregate every transfer between two drawn wallets into one edge per pair.
  const pairs = new Map<string, { a: string; b: string; ab: number; ba: number; abBtc: number; baBtc: number }>();
  for (const t of transfers) {
    if (!shownIds.has(t.from) || !shownIds.has(t.to)) continue;
    if (!focus.has(t.from) && !focus.has(t.to) && !linkCounterparties) continue;
    const [a, b] = t.from < t.to ? [t.from, t.to] : [t.to, t.from];
    const key = `${a}|${b}`;
    let p = pairs.get(key);
    if (!p) pairs.set(key, (p = { a, b, ab: 0, ba: 0, abBtc: 0, baBtc: 0 }));
    if (t.from === a) {
      p.ab++;
      p.abBtc += t.amountBtc;
    } else {
      p.ba++;
      p.baBtc += t.amountBtc;
    }
  }

  const edges: GraphEdge[] = [];
  for (const p of pairs.values()) {
    const kind: GraphEdge['kind'] = focus.has(p.a) || focus.has(p.b) ? 'focus' : 'link';
    // Orient by the larger flow; on a tie a counterparty is drawn as sending to the focus wallet.
    let aToB = p.ab > p.ba;
    if (p.ab === p.ba) aToB = focus.has(p.b) && !focus.has(p.a);
    const [source, target] = aToB ? [p.a, p.b] : [p.b, p.a];
    edges.push({
      id: `${source}->${target}`,
      source,
      target,
      forwardCount: aToB ? p.ab : p.ba,
      backwardCount: aToB ? p.ba : p.ab,
      forwardBtc: aToB ? p.abBtc : p.baBtc,
      backwardBtc: aToB ? p.baBtc : p.abBtc,
      kind,
    });
  }

  return { focusIds, nodes, edges, hiddenCounterparties: ranked.length - shown.length, totalCounterparties: ranked.length };
}

/** Direct counterparties of one wallet (the Phase 1 wallet-investigation view). */
export const buildEgoGraph = (focusId: string, transfers: Transfer[], byId: Map<string, WalletRecord>, options?: GraphOptions) =>
  buildGraph([focusId], transfers, byId, options);

// --- path tracing -----------------------------------------------------------------------------

export interface PathResult {
  /** 'directed' follows sender -> receiver; 'undirected' ignores direction. */
  mode: 'directed' | 'undirected';
  wallets: string[]; // from ... to
  /** For each hop: transfers from wallets[i] to wallets[i+1] and the reverse, and BTC moved. */
  hops: { from: string; to: string; forward: number; backward: number; btc: number }[];
}

/** Shortest chain of transfers linking two wallets (fewest hops); prefers a directed path. */
export function tracePath(from: string, to: string, transfers: Transfer[]): PathResult | null {
  if (from === to) return null;
  const out = new Map<string, Set<string>>();
  const und = new Map<string, Set<string>>();
  const add = (m: Map<string, Set<string>>, a: string, b: string) => {
    let s = m.get(a);
    if (!s) m.set(a, (s = new Set()));
    s.add(b);
  };
  for (const t of transfers) {
    add(out, t.from, t.to);
    add(und, t.from, t.to);
    add(und, t.to, t.from);
  }

  const bfs = (adj: Map<string, Set<string>>): string[] | null => {
    const prev = new Map<string, string | null>([[from, null]]);
    const queue = [from];
    for (let i = 0; i < queue.length; i++) {
      const cur = queue[i];
      if (cur === to) break;
      for (const nxt of adj.get(cur) ?? []) {
        if (!prev.has(nxt)) {
          prev.set(nxt, cur);
          queue.push(nxt);
        }
      }
    }
    if (!prev.has(to)) return null;
    const path: string[] = [];
    for (let c: string | null = to; c !== null; c = prev.get(c) ?? null) path.push(c);
    return path.reverse();
  };

  const directed = bfs(out);
  const wallets = directed ?? bfs(und);
  if (!wallets) return null;

  const hops = wallets.slice(0, -1).map((a, i) => {
    const b = wallets[i + 1];
    let forward = 0;
    let backward = 0;
    let btc = 0;
    for (const t of transfers) {
      if (t.from === a && t.to === b) {
        forward++;
        btc += t.amountBtc;
      } else if (t.from === b && t.to === a) {
        backward++;
        btc += t.amountBtc;
      }
    }
    return { from: a, to: b, forward, backward, btc };
  });
  return { mode: directed ? 'directed' : 'undirected', wallets, hops };
}
