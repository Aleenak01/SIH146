import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { api, qs } from '../api/client';
import type { CoinJoinCandidatePage } from '../api/types';
import { EvidenceCell, PageHeader, Panel, PredictionText, StatusPill } from '../components/bits';
import { ClustersView } from '../components/ClustersView';
import { EntitiesView } from '../components/EntitiesView';
import { RelatedEntities } from '../components/RelatedEntities';
import { TransactionTable } from '../components/TransactionTable';
import { TRANSFER_COLUMNS, downloadCsv, stamp, transferRows } from '../data/export';
import type { Transfer } from '../data/types';
import { fmtBtc, fmtDate, fmtInt, fmtScore, fmtTs } from '../format';
import { buildGraph, tracePath, type PathResult } from '../graph/model';
import { WalletGraph } from '../graph/WalletGraph';
import { useCases } from '../state/cases';
import { useConfirm } from '../state/confirm';
import { useBackend, useDataset, useFlaggedIds, useTransfers } from '../state/data';

type View = 'transactions' | 'network' | 'clusters' | 'entities';

/** Query-string state, so every link elsewhere in the app can open this page pre-filtered. */
function useParamState() {
  const [sp, setSp] = useSearchParams();
  const get = (k: string) => sp.get(k) ?? '';
  // Read the live URL, not the last render's copy, so two quick changes cannot overwrite each other.
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setSp(next, { replace: true });
  };
  return { get, set, sp };
}

export function Network() {
  const { get, set } = useParamState();
  const { mode } = useBackend();
  const requested = get('view');
  const view: View =
    requested === 'network'
      ? 'network'
      : requested === 'clusters' && mode === 'api'
        ? 'clusters'
        : requested === 'entities' && mode === 'api'
          ? 'entities'
          : 'transactions';
  const { transfers, error } = useTransfers();

  return (
    <div className="page">
      <PageHeader
        title="Transactions / Network"
        subtitle={
          view === 'clusters'
            ? 'Groups of wallets connected by shared synthetic network observations or by dense transfer activity. Clusters are derived by the analysis and indicate connected activity, not common ownership or wrongdoing.'
            : view === 'entities'
              ? 'Groups of addresses inferred to be under common ownership by the common-input-ownership heuristic. Unlike clusters, an entity IS an ownership inference — a heuristic, not proof.'
              : 'Search the synthetic transaction dataset and explore who transacted with whom. Every row and every link comes from the dataset; nothing is inferred.'
        }
        actions={
          <div className="seg" role="tablist" aria-label="View">
            <button type="button" role="tab" aria-selected={view === 'transactions'} className={view === 'transactions' ? 'on' : ''} onClick={() => set({ view: null })}>
              Transactions
            </button>
            <button type="button" role="tab" aria-selected={view === 'network'} className={view === 'network' ? 'on' : ''} onClick={() => set({ view: 'network' })}>
              Network
            </button>
            {mode === 'api' && (
              <button type="button" role="tab" aria-selected={view === 'clusters'} className={view === 'clusters' ? 'on' : ''} onClick={() => set({ view: 'clusters' })}>
                Clusters
              </button>
            )}
            {mode === 'api' && (
              <button type="button" role="tab" aria-selected={view === 'entities'} className={view === 'entities' ? 'on' : ''} onClick={() => set({ view: 'entities' })}>
                Entities
              </button>
            )}
          </div>
        }
      />
      {view === 'clusters' && <ClustersView />}
      {view === 'entities' && <EntitiesView />}
      {view !== 'clusters' && view !== 'entities' && error && <div className="empty-block">Could not load transactions: {error}</div>}
      {view !== 'clusters' && view !== 'entities' && !error && !transfers && <div className="empty-block">Loading transactions…</div>}
      {view !== 'clusters' && view !== 'entities' && transfers && (view === 'transactions' ? <TransactionsView transfers={transfers} /> : <NetworkView transfers={transfers} />)}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------------------------
const DAY = 86_400_000;

/** Transaction ids flagged as CoinJoin-like candidates by the pattern detector (heuristic, never a certainty). */
function useCoinjoinIds(): Set<string> {
  const { mode } = useBackend();
  const [ids, setIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (mode !== 'api') return;
    let live = true;
    api.get<CoinJoinCandidatePage>(`/api/coinjoin-candidates${qs({ limit: 500 })}`).then(
      (p) => live && setIds(new Set(p.items.map((c) => c.transaction_id))),
      () => live && setIds(new Set()),
    );
    return () => {
      live = false;
    };
  }, [mode]);
  return ids;
}

function TransactionsView({ transfers }: { transfers: Transfer[] }) {
  const { get, set, sp } = useParamState();
  const flaggedIds = useFlaggedIds();
  const coinjoinIds = useCoinjoinIds();
  const { guard } = useConfirm();

  const q = get('q').trim().toLowerCase();
  const txId = get('tx').trim().toLowerCase(); // set by search results: a transaction ID (backend mode)
  const withWallet = get('with').trim().toLowerCase(); // set by links: only transfers between q and this wallet
  const sender = get('sender').trim().toLowerCase();
  const receiver = get('receiver').trim().toLowerCase();
  const min = get('min');
  const max = get('max');
  const start = get('start');
  const end = get('end');
  const flaggedOnly = get('flagged') === '1';

  const rows = useMemo(() => {
    const minV = min !== '' ? Number(min) : null;
    const maxV = max !== '' ? Number(max) : null;
    const t0 = start ? Date.parse(`${start}T00:00:00Z`) : null;
    const t1 = end ? Date.parse(`${end}T00:00:00Z`) + DAY : null;
    return transfers.filter(
      (t) =>
        (!txId || (t.id ?? '').toLowerCase().includes(txId)) &&
        (!q || t.from.includes(q) || t.to.includes(q)) &&
        (!withWallet || (t.from.includes(q) && t.to.includes(withWallet)) || (t.to.includes(q) && t.from.includes(withWallet))) &&
        (!sender || t.from.includes(sender)) &&
        (!receiver || t.to.includes(receiver)) &&
        (minV === null || t.amountBtc >= minV) &&
        (maxV === null || t.amountBtc <= maxV) &&
        (t0 === null || t.ts >= t0) &&
        (t1 === null || t.ts < t1) &&
        (!flaggedOnly || flaggedIds.has(t.from) || flaggedIds.has(t.to)),
    );
  }, [transfers, txId, q, withWallet, sender, receiver, min, max, start, end, flaggedOnly, flaggedIds]);

  const totalBtc = useMemo(() => rows.reduce((n, t) => n + t.amountBtc, 0), [rows]);
  const wallets = useMemo(() => {
    const s = new Set<string>();
    for (const t of rows) {
      s.add(t.from);
      s.add(t.to);
    }
    return s.size;
  }, [rows]);
  const filtered = ['tx', 'q', 'with', 'sender', 'receiver', 'min', 'max', 'start', 'end', 'flagged'].some((k) => sp.has(k));

  const exportCsv = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export transactions',
      body: `Save the ${fmtInt(rows.length)} transactions currently shown (${filtered ? 'filtered' : 'all'}) as a CSV file on this computer.`,
      confirmLabel: 'Export CSV',
    });
    if (!ok) return;
    downloadCsv(`sih146-transactions-${stamp()}.csv`, TRANSFER_COLUMNS, transferRows(rows));
  };

  return (
    <>
      <div className="filters">
        <label className="field-inline">
          <span>Wallet (sender or receiver)</span>
          <input value={get('q')} onChange={(e) => set({ q: e.target.value })} placeholder="e.g. wallet_350" />
        </label>
        <label className="field-inline">
          <span>Sender</span>
          <input value={get('sender')} onChange={(e) => set({ sender: e.target.value })} placeholder="wallet_…" />
        </label>
        <label className="field-inline">
          <span>Receiver</span>
          <input value={get('receiver')} onChange={(e) => set({ receiver: e.target.value })} placeholder="wallet_…" />
        </label>
        <label className="field-inline field-narrow">
          <span>Amount min (BTC)</span>
          <input type="number" min="0" step="any" value={min} onChange={(e) => set({ min: e.target.value })} />
        </label>
        <label className="field-inline field-narrow">
          <span>Amount max (BTC)</span>
          <input type="number" min="0" step="any" value={max} onChange={(e) => set({ max: e.target.value })} />
        </label>
        <label className="field-inline field-date">
          <span>From date</span>
          <input type="date" value={start} onChange={(e) => set({ start: e.target.value })} />
        </label>
        <label className="field-inline field-date">
          <span>To date</span>
          <input type="date" value={end} onChange={(e) => set({ end: e.target.value })} />
        </label>
        <label className="check">
          <input type="checkbox" checked={flaggedOnly} onChange={(e) => set({ flagged: e.target.checked ? '1' : null })} />
          Involving ML-flagged wallets
        </label>
        {txId && (
          <span className="chip">
            Transaction {get('tx')}
            <button type="button" aria-label="Remove transaction filter" onClick={() => set({ tx: null })}>
              ×
            </button>
          </span>
        )}
        {withWallet && (
          <span className="chip">
            Between {get('q')} and {get('with')}
            <button type="button" aria-label="Remove pair filter" onClick={() => set({ with: null })}>
              ×
            </button>
          </span>
        )}
        {filtered && (
          <button type="button" className="btn btn-sm" onClick={() => set({ tx: null, q: null, with: null, sender: null, receiver: null, min: null, max: null, start: null, end: null, flagged: null })}>
            Clear filters
          </button>
        )}
      </div>

      <div className="toolbar">
        <span className="toolbar-summary">
          <b>{fmtInt(rows.length)}</b> of {fmtInt(transfers.length)} transfers · {fmtBtc(totalBtc)} BTC · {fmtInt(wallets)} wallets
        </span>
        <button type="button" className="btn btn-sm toolbar-right" onClick={exportCsv} disabled={rows.length === 0}>
          <Download size={13} aria-hidden="true" /> Export {fmtInt(rows.length)} rows (CSV)
        </button>
      </div>

      <TransactionTable rows={rows} flaggedIds={flaggedIds} coinjoinIds={coinjoinIds} pageSize={25} emptyText="No transactions match these filters." />
    </>
  );
}

// ---------------------------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------------------------
interface Relationship {
  id: string;
  received: number; // transfers counterparty -> focus
  sent: number; // transfers focus -> counterparty
  btcReceived: number;
  btcSent: number;
  first: number;
  last: number;
}

function NetworkView({ transfers }: { transfers: Transfer[] }) {
  const { get, set } = useParamState();
  const { byId, wallets } = useDataset();
  const { statusOf } = useCases();
  const { guard } = useConfirm();
  const { mode } = useBackend();

  const topWallet = useMemo(() => wallets.reduce((a, b) => (b.priorityRank < a.priorityRank ? b : a)), [wallets]);
  const requested = get('wallet');
  const focusId = byId.has(requested) ? requested : topWallet.id;
  const usingDefault = !requested;
  const wallet = byId.get(focusId)!;

  const maxN = Number(get('n')) || 10;
  const links = get('links') === '1';

  const [showAll, setShowAll] = useState(false);
  const [draft, setDraft] = useState(focusId);
  const [draftFor, setDraftFor] = useState(focusId);
  if (draftFor !== focusId) {
    setDraftFor(focusId);
    setDraft(focusId);
    setShowAll(false);
  }
  const draftValid = byId.has(draft.trim());

  const graph = useMemo(() => buildGraph([focusId], transfers, byId, { maxCounterparties: maxN, linkCounterparties: links }), [focusId, transfers, byId, maxN, links]);

  // Every counterparty of the focus wallet, not only the ones drawn.
  const relationships = useMemo(() => {
    const m = new Map<string, Relationship>();
    for (const t of transfers) {
      const incoming = t.to === focusId;
      if (!incoming && t.from !== focusId) continue;
      const other = incoming ? t.from : t.to;
      let r = m.get(other);
      if (!r) m.set(other, (r = { id: other, received: 0, sent: 0, btcReceived: 0, btcSent: 0, first: t.ts, last: t.ts }));
      if (incoming) {
        r.received++;
        r.btcReceived += t.amountBtc;
      } else {
        r.sent++;
        r.btcSent += t.amountBtc;
      }
      r.first = Math.min(r.first, t.ts);
      r.last = Math.max(r.last, t.ts);
    }
    return [...m.values()].sort((a, b) => b.received + b.sent - (a.received + a.sent) || a.id.localeCompare(b.id));
  }, [transfers, focusId]);
  const repeated = relationships.filter((r) => r.received + r.sent >= 2).length;

  const exportRelationships = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export relationships',
      body: `Save the ${fmtInt(relationships.length)} counterparties of ${focusId} as a CSV file on this computer.`,
      confirmLabel: 'Export CSV',
    });
    if (!ok) return;
    downloadCsv(
      `sih146-relationships-${focusId}-${stamp()}.csv`,
      ['wallet', 'counterparty', 'transfers_received_from_counterparty', 'transfers_sent_to_counterparty', 'btc_received', 'btc_sent', 'first_transfer_utc', 'last_transfer_utc', 'counterparty_ml_prediction'],
      relationships.map((r) => [focusId, r.id, r.received, r.sent, r.btcReceived, r.btcSent, fmtTs(r.first), fmtTs(r.last), byId.get(r.id)?.fusion.ml_anomaly_prediction]),
    );
  };

  return (
    <>
      <div className="filters">
        <form
          className="field-inline field-grow"
          onSubmit={(e) => {
            e.preventDefault();
            if (draftValid) set({ wallet: draft.trim() });
          }}
        >
          <span>Focus wallet</span>
          <div className="inline-row">
            <input list="wallet-options" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="wallet_…" aria-invalid={!draftValid} />
            <button type="submit" className="btn btn-sm" disabled={!draftValid || draft.trim() === focusId}>
              Explore
            </button>
          </div>
          {!draftValid && draft.trim() !== '' && <em className="field-error">Not a wallet in the dataset</em>}
          <datalist id="wallet-options">
            {wallets.map((w) => (
              <option key={w.id} value={w.id} />
            ))}
          </datalist>
        </form>
        <label className="field-inline field-narrow">
          <span>Counterparties shown</span>
          <select value={maxN} onChange={(e) => set({ n: e.target.value })}>
            {[10, 20, 30].map((n) => (
              <option key={n} value={n}>
                {n} most active
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={links} onChange={(e) => set({ links: e.target.checked ? '1' : null })} />
          Show transfers between counterparties
        </label>
      </div>

      <div className="focus-bar">
        <Link to={`/wallets/${focusId}`} className="mono focus-id">
          {focusId}
        </Link>
        <span>
          <PredictionText flagged={wallet.flagged} /> · ML {fmtScore(wallet.fusion.ml_anomaly_score)}
        </span>
        <EvidenceCell level={wallet.fusion.forensic_evidence_level} />
        <StatusPill status={statusOf(focusId)} />
        <span className="focus-links">
          {usingDefault && <span className="muted">Starting from the highest-priority wallet</span>}
          <Link to={`/wallets/${focusId}`} className="btn btn-sm">
            Open investigation
          </Link>
          <Link to={`/network?view=transactions&q=${focusId}`} className="btn btn-sm">
            View transactions
          </Link>
        </span>
      </div>

      <Panel
        title="Relationship graph"
        note={
          <>
            Wallets that mostly send to {focusId} are on the left, wallets that mostly receive from it on the right. Arrows show the direction of the larger flow (double arrow: both ways); heavier lines
            are repeated transfers. Select a wallet to re-centre the graph on it.
          </>
        }
      >
        {graph.totalCounterparties === 0 ? (
          <p className="muted">This wallet has no counterparties in the dataset.</p>
        ) : (
          <>
            <WalletGraph model={graph} onSelect={(id) => set({ wallet: id })} />
            <p className="graph-foot">
              {graph.hiddenCounterparties > 0
                ? `Showing the ${fmtInt(graph.nodes.length - 1)} most active of ${fmtInt(graph.totalCounterparties)} counterparties (the table below lists all).`
                : `Showing all ${fmtInt(graph.totalCounterparties)} counterparties.`}{' '}
              Outlined wallets are ML-flagged.
            </p>
          </>
        )}
      </Panel>

      <PathTrace transfers={transfers} defaultFrom={focusId} />

      <Panel
        title="Relationships"
        note={`${fmtInt(relationships.length)} counterparties · ${fmtInt(repeated)} with two or more transfers (repeated relationships). Transfers are counted in each direction.`}
        actions={
          <button type="button" className="btn btn-sm" onClick={exportRelationships} disabled={relationships.length === 0}>
            <Download size={13} aria-hidden="true" /> Export CSV
          </button>
        }
      >
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Counterparty</th>
                <th>ML</th>
                <th className="num">Received from</th>
                <th className="num">Sent to</th>
                <th className="num">BTC in</th>
                <th className="num">BTC out</th>
                <th>First transfer</th>
                <th>Last transfer</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(showAll ? relationships : relationships.slice(0, 25)).map((r) => {
                const w = byId.get(r.id);
                return (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/wallets/${r.id}`} className="mono wallet-link">
                        {r.id}
                      </Link>
                      {r.received + r.sent >= 2 && <span className="rule-tag rule-tag-inline">repeated</span>}
                    </td>
                    <td>{w && <PredictionText flagged={w.flagged} />}</td>
                    <td className="num mono">{r.received}</td>
                    <td className="num mono">{r.sent}</td>
                    <td className="num mono">{fmtBtc(r.btcReceived)}</td>
                    <td className="num mono">{fmtBtc(r.btcSent)}</td>
                    <td>{fmtDate(r.first)}</td>
                    <td>{fmtDate(r.last)}</td>
                    <td className="row-actions">
                      <Link to={`/network?view=transactions&q=${focusId}&with=${r.id}`} className="link">
                        Transfers
                      </Link>
                      <Link to={`/network?view=network&wallet=${r.id}`} className="link">
                        Explore
                      </Link>
                    </td>
                  </tr>
                );
              })}
              {relationships.length === 0 && (
                <tr>
                  <td colSpan={9} className="empty">
                    No transfers involve this wallet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {relationships.length > 25 && (
          <p className="graph-foot table-more">
            <button type="button" className="btn btn-sm" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show fewer' : `Show all ${fmtInt(relationships.length)}`}
            </button>
            <span className="muted"> Sorted by number of transfers; showing {showAll ? 'all' : 'the 25 most active'}.</span>
          </p>
        )}
      </Panel>

      {mode === 'api' && <RelatedEntities wallet={wallet} />}
    </>
  );
}

// ---------------------------------------------------------------------------------------------
// Path tracing: how does one wallet connect to another?
// ---------------------------------------------------------------------------------------------
function PathTrace({ transfers, defaultFrom }: { transfers: Transfer[]; defaultFrom: string }) {
  const { byId } = useDataset();
  const [from, setFrom] = useState(defaultFrom);
  const [fromFor, setFromFor] = useState(defaultFrom);
  if (fromFor !== defaultFrom) {
    setFromFor(defaultFrom);
    setFrom(defaultFrom);
  }
  const [to, setTo] = useState('');
  const [result, setResult] = useState<{ ran: boolean; path: PathResult | null; from: string; to: string }>({ ran: false, path: null, from: '', to: '' });

  const f = from.trim();
  const t = to.trim();
  const valid = byId.has(f) && byId.has(t) && f !== t;

  return (
    <Panel
      title="Trace a connection"
      note="Finds the shortest chain of transfers linking two wallets. It follows sender → receiver where possible and otherwise ignores direction. Timestamps are not taken into account."
    >
      <form
        className="filters filters-flat"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) setResult({ ran: true, path: tracePath(f, t, transfers), from: f, to: t });
        }}
      >
        <label className="field-inline">
          <span>From</span>
          <input list="wallet-options" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="wallet_…" />
        </label>
        <label className="field-inline">
          <span>To</span>
          <input list="wallet-options" value={to} onChange={(e) => setTo(e.target.value)} placeholder="wallet_…" />
        </label>
        <button type="submit" className="btn btn-sm btn-align" disabled={!valid}>
          Trace
        </button>
      </form>
      {result.ran && (
        <div className="path-result">
          {!result.path ? (
            <p>No chain of transfers connects {result.from} and {result.to} in the dataset.</p>
          ) : (
            <>
              <p>
                {result.path.mode === 'directed'
                  ? `Directed path found: ${result.path.hops.length} hop${result.path.hops.length === 1 ? '' : 's'}, each transfer going from sender to receiver.`
                  : `No sender → receiver path exists. The wallets are connected in ${result.path.hops.length} hop${result.path.hops.length === 1 ? '' : 's'} when direction is ignored.`}
              </p>
              <div className="table-wrap">
                <table className="data-table compact">
                  <thead>
                    <tr>
                      <th>Hop</th>
                      <th>From</th>
                      <th>To</th>
                      <th className="num">Transfers →</th>
                      <th className="num">Transfers ←</th>
                      <th className="num">BTC</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.path.hops.map((h, i) => (
                      <tr key={i}>
                        <td className="mono">{i + 1}</td>
                        <td>
                          <Link to={`/wallets/${h.from}`} className="mono wallet-link">{h.from}</Link>
                        </td>
                        <td>
                          <Link to={`/wallets/${h.to}`} className="mono wallet-link">{h.to}</Link>
                        </td>
                        <td className="num mono">{h.forward}</td>
                        <td className="num mono">{h.backward}</td>
                        <td className="num mono">{fmtBtc(h.btc)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </Panel>
  );
}
