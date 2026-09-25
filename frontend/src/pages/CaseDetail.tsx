import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download } from 'lucide-react';
import { api, qs } from '../api/client';
import type { ApiTransaction, CaseDetail as CaseDetailT, CaseItem, CasePriority, GraphOut, ItemType } from '../api/types';
import { CaseStatusPill, EvidenceCell, PageHeader, Panel, PredictionText, PriorityPill } from '../components/bits';
import { TransactionTable } from '../components/TransactionTable';
import { TRANSFER_COLUMNS, downloadCsv, downloadJson, stamp, transferRows } from '../data/export';
import { ruleLabel } from '../data/rules';
import type { EvidenceLevel, Transfer } from '../data/types';
import { fmtBtc, fmtDate, fmtDateTime, fmtInt, fmtScore } from '../format';
import { ApiGraph, graphCaption } from '../graph/ApiGraph';
import { useCases, type CaseStatus } from '../state/cases';
import { useConfirm } from '../state/confirm';
import { useBackend, useFlaggedIds } from '../state/data';

const STATUSES: CaseStatus[] = ['Open', 'Under investigation', 'Closed'];
const ITEM_LABEL: Record<ItemType, string> = { lead: 'Lead', wallet: 'Wallet', transaction: 'Transaction', cluster: 'Cluster' };

const pretty = (action: string) => {
  const s = action.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
};

/**
 * A single case from the backend: its items with the evidence saved when each was added (and today's values beside
 * it), notes, history, the related transactions and a relationship graph.
 */
export function CaseDetail() {
  const { caseId = '' } = useParams();
  const store = useCases();
  const backend = useBackend();
  const flaggedIds = useFlaggedIds();
  const { guard, confirm } = useConfirm();
  const [c, setC] = useState<CaseDetailT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [txs, setTxs] = useState<Transfer[] | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [infra, setInfra] = useState(false);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [addType, setAddType] = useState<ItemType>('wallet');
  const [addId, setAddId] = useState('');

  const load = useCallback(async () => {
    try {
      const d = await api.get<CaseDetailT>(`/api/cases/${encodeURIComponent(caseId)}`);
      setC(d);
      setMissing(false);
      setError(null);
      const page = await api.get<{ items: ApiTransaction[] }>(`/api/cases/${encodeURIComponent(caseId)}/transactions${qs({ limit: 500, order: 'desc' })}`);
      setTxs(
        page.items.map((t) => ({ id: t.transaction_id, ts: Date.parse(t.timestamp), from: t.sender_wallet, to: t.receiver_wallet, amountBtc: t.amount_btc, inputCount: t.input_count, outputCount: t.output_count })),
      );
    } catch (e) {
      if ((e as { status?: number }).status === 404) setMissing(true);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load, backend.analysis?.runId]);

  useEffect(() => {
    if (!c) return;
    let live = true;
    const types = infra ? 'wallet,device,ip_observation' : 'wallet';
    api.get<GraphOut>(`/api/cases/${encodeURIComponent(caseId)}/graph${qs({ node_types: types, max_nodes: infra ? 20 : 40 })}`).then(
      (g) => live && setGraph(g),
      () => live && setGraph(null),
    );
    return () => {
      live = false;
    };
    // The graph depends on the case's contents, not on every history entry.
  }, [caseId, infra, c?.items.length, backend.analysis?.runId]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!store.available) {
    return (
      <div className="page">
        <PageHeader back={<BackLink />} title="Case management is offline" subtitle="Cases are stored in the local backend, which is not connected." />
      </div>
    );
  }
  if (missing) {
    return (
      <div className="page">
        <PageHeader back={<BackLink />} title="Case not found" subtitle={`No case “${caseId}” exists in the backend database.`} />
      </div>
    );
  }
  if (!c) {
    return (
      <div className="page">
        <PageHeader back={<BackLink />} title={caseId} subtitle={error ?? 'Loading case…'} />
      </div>
    );
  }

  const rt = c.related_transactions as { total: number; value_moved_btc?: number; first_at?: string | null; last_at?: string | null };
  const counts = c.item_counts;

  const changeStatus = async (next: CaseStatus) => {
    if (next === c.status) return;
    const ok = await guard('confirmStatusChange', {
      title: 'Change case status',
      body: (
        <>
          Change <b>{c.case_id}</b> from <b>{c.status}</b> to <b>{next}</b>? The change is recorded in the case history.
          {next === 'Closed' ? ' A closed case no longer counts as active.' : ''}
        </>
      ),
      confirmLabel: `Set to ${next}`,
    });
    if (ok) await run(() => store.updateCase(c.case_id, { status: next }));
  };

  const exportJson = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export case',
      body: `Save ${c.case_id} (items, evidence saved at the time, notes and history) as a JSON file on this computer.`,
      confirmLabel: 'Export JSON',
    });
    if (ok) downloadJson(`sih146-${c.case_id}-${stamp()}.json`, { exportedAt: new Date().toISOString(), source: 'SIH146 local backend, synthetic data', case: c });
  };

  const exportTx = async () => {
    if (!txs) return;
    const ok = await guard('confirmExport', {
      title: 'Export case transactions',
      body: `Save the ${fmtInt(txs.length)} transactions related to ${c.case_id} as a CSV file on this computer.`,
      confirmLabel: 'Export CSV',
    });
    if (ok) downloadCsv(`sih146-${c.case_id}-transactions-${stamp()}.csv`, TRANSFER_COLUMNS, transferRows(txs));
  };

  const removeItem = async (it: CaseItem) => {
    const ok = await confirm({
      title: 'Remove from case',
      body: (
        <>
          Remove {ITEM_LABEL[it.item_type].toLowerCase()} <b>{it.item_id}</b> from {c.case_id}? The evidence saved for it is removed with it; the removal is recorded in the history.
        </>
      ),
      confirmLabel: 'Remove',
      danger: true,
    });
    if (ok) await run(() => store.removeItem(c.case_id, it.item_type, it.item_id));
  };

  return (
    <div className="page">
      <PageHeader
        back={<BackLink />}
        title={c.title}
        subtitle={
          <>
            <span className="mono">{c.case_id}</span> · Created {fmtDateTime(c.created_at)} · <CaseStatusPill status={c.status} />
            {c.assigned_to ? ` · Assigned to ${c.assigned_to}` : ''}
          </>
        }
        actions={
          <>
            <label className="ctl">
              <span>Status</span>
              <select value={c.status} disabled={busy} onChange={(e) => void changeStatus(e.target.value as CaseStatus)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label className="ctl">
              <span>Priority</span>
              <select value={c.priority ?? ''} disabled={busy} onChange={(e) => e.target.value && void run(() => store.updateCase(c.case_id, { priority: e.target.value as CasePriority }))}>
                {!c.priority && <option value="">Not set</option>}
                {(['High', 'Medium', 'Low'] as const).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn" onClick={exportJson}>
              <Download size={14} aria-hidden="true" /> Export case
            </button>
          </>
        }
      />
      {c.description && <p className="case-desc">{c.description}</p>}
      {actionError && <p className="field-error" role="alert">{actionError}</p>}

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Items in the case</div>
          <div className="metric-value">{fmtInt(c.items.length)}</div>
          <div className="metric-sub">
            {Object.entries(counts).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k}${n === 1 ? '' : 's'}`).join(' · ') || 'None yet'}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Related transactions</div>
          <div className="metric-value">{fmtInt(rt.total)}</div>
          <div className="metric-sub">Involving the case wallets (live)</div>
        </div>
        <div className="metric">
          <div className="metric-label">Value moved</div>
          <div className="metric-value mono">{fmtBtc(rt.value_moved_btc ?? 0)}</div>
          <div className="metric-sub">BTC across related transactions</div>
        </div>
        <div className="metric">
          <div className="metric-label">Activity period</div>
          <div className="metric-value metric-text">{rt.first_at && rt.last_at ? `${fmtDate(Date.parse(rt.first_at))} – ${fmtDate(Date.parse(rt.last_at))}` : '—'}</div>
          <div className="metric-sub">First to last related transfer</div>
        </div>
      </div>

      <Panel
        title="Evidence in this case"
        note="Each item shows the evidence saved when it was added; a note beside it appears if today’s analysis differs. Rules are behavioural indicators and an anomaly score is a statistical measure: neither establishes wrongdoing, and both require investigator review."
      >
        <div className="case-wallets">
          {c.items.map((it) => (
            <ItemCard
              key={`${it.item_type}:${it.item_id}`}
              item={it}
              busy={busy}
              onReviewed={() => run(() => store.markReviewed(c.case_id, it.item_type, it.item_id))}
              onRemove={() => removeItem(it)}
            />
          ))}
          {c.items.length === 0 && <p className="muted">This case holds no items yet.</p>}
        </div>
        <form
          className="add-item"
          onSubmit={(e) => {
            e.preventDefault();
            const id = addId.trim();
            if (id) void run(async () => { await store.addItem(c.case_id, addType, id); setAddId(''); });
          }}
        >
          <span>Add to case</span>
          <select value={addType} onChange={(e) => setAddType(e.target.value as ItemType)} aria-label="Item type">
            <option value="wallet">Wallet</option>
            <option value="lead">Lead</option>
            <option value="transaction">Transaction</option>
            <option value="cluster">Cluster</option>
          </select>
          <input value={addId} onChange={(e) => setAddId(e.target.value)} placeholder={addType === 'transaction' ? 'syn-000123' : addType === 'cluster' ? 'NET-0001' : 'wallet_…'} aria-label="Item ID" />
          <button type="submit" className="btn btn-sm" disabled={busy || !addId.trim()}>
            Add
          </button>
        </form>
      </Panel>

      <div className="grid-notes-history">
        <Panel title="Investigator notes" note="Saved with the case in the backend database.">
          {c.notes.length === 0 ? (
            <p className="muted">No notes yet.</p>
          ) : (
            <ul className="notes">
              {c.notes.map((n) => (
                <li key={n.entry_id}>
                  <div className="notes-meta">
                    {n.actor} · {fmtDateTime(n.at)}
                  </div>
                  <div className="notes-text">{n.detail}</div>
                </li>
              ))}
            </ul>
          )}
          <form
            className="note-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) void run(async () => { await store.addNote(c.case_id, note); setNote(''); });
            }}
          >
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Add an observation, a next step or a decision." aria-label="New note" />
            <button type="submit" className="btn btn-sm" disabled={!note.trim() || busy}>
              Add note
            </button>
          </form>
        </Panel>

        <Panel title="Investigation history" note="Every recorded change to this case, oldest first.">
          <ol className="history">
            {c.history.map((h) => (
              <li key={h.entry_id}>
                <div className="history-when">{fmtDateTime(h.at)}</div>
                <div>
                  <b>{pretty(h.action)}</b>
                  {h.detail && <span className="muted"> · {h.detail}</span>}
                  <div className="muted history-who">{h.actor}</div>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <Panel
        title="Relationship graph"
        note="The case wallets, the members of any attached clusters and the parties of attached transactions, with the transfers between them. Select a wallet to open its investigation."
        actions={
          <label className="check">
            <input type="checkbox" checked={infra} onChange={(e) => setInfra(e.target.checked)} />
            Show synthetic network infrastructure (IP, device)
          </label>
        }
      >
        {graph ? (
          <>
            <ApiGraph graph={graph} height={560} />
            <p className="graph-foot">
              {graphCaption(graph)}. {infra ? 'IP addresses and devices are SYNTHETIC network observations generated for the demo, not blockchain data. ' : ''}Outlined wallets are ML-flagged.
            </p>
          </>
        ) : (
          <div className="viz-loading">Loading graph…</div>
        )}
      </Panel>

      <Panel
        title="Related transactions"
        note={`${fmtInt(rt.total)} transfers involving the case wallets, listed live from the database (up to 500 shown). Select a row for its full record.`}
        actions={
          <button type="button" className="btn btn-sm" onClick={exportTx} disabled={!txs || txs.length === 0}>
            <Download size={13} aria-hidden="true" /> Export CSV
          </button>
        }
      >
        {txs ? <TransactionTable rows={txs} flaggedIds={flaggedIds} pageSize={15} emptyText="No transactions are related to this case yet." /> : <div className="viz-loading">Loading transactions…</div>}
      </Panel>
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/cases" className="back">
      <ArrowLeft size={14} aria-hidden="true" /> Cases
    </Link>
  );
}

/** Snapshot evidence for a lead or wallet, in the same shape whichever way it was added. */
function walletEvidence(it: CaseItem): Record<string, any> | null {
  const s = it.evidence_snapshot;
  if (!s) return null;
  return s.kind === 'lead' ? s : s.analysis ?? null;
}

function ItemCard({ item, busy, onReviewed, onRemove }: { item: CaseItem; busy: boolean; onReviewed: () => void; onRemove: () => void }) {
  const s = item.evidence_snapshot;
  const cur = item.current;
  const isWallet = item.item_type === 'lead' || item.item_type === 'wallet';
  const ev = isWallet ? walletEvidence(item) : null;

  let body: React.ReactNode = null;
  if (isWallet && ev) {
    const scored = ev.scored !== false;
    const findings: { rule_id: string; evidence: string | null }[] = ev.findings ?? [];
    const changed =
      cur && cur.exists && scored && (cur.priority_rank !== ev.priority_rank || cur.ml_prediction !== ev.ml_prediction || Math.abs((cur.combined_score ?? 0) - (ev.combined_score ?? 0)) > 1e-9);
    body = (
      <>
        {scored ? (
          <div className="case-wallet-head">
            <Link to={`/wallets/${item.item_id}`} className="mono wallet-link">
              {item.item_id}
            </Link>
            <span>
              <PredictionText flagged={ev.ml_prediction === 'Anomalous'} /> · ML {fmtScore(ev.ml_score)}
            </span>
            <EvidenceCell level={ev.evidence_level as EvidenceLevel} />
            <span className="muted">
              {ev.forensic_rule_count} rule{ev.forensic_rule_count === 1 ? '' : 's'} · combined {fmtScore(ev.combined_score)} (#{ev.priority_rank})
            </span>
            {ev.priority_level && <PriorityPill level={ev.priority_level} />}
          </div>
        ) : (
          <div className="case-wallet-head">
            <Link to={`/wallets/${item.item_id}`} className="mono wallet-link">
              {item.item_id}
            </Link>
            <span className="muted">Not scored when added: {ev.unscored_reason ?? 'insufficient activity'}</span>
          </div>
        )}
        {scored && (ev.reasons?.length ?? 0) > 0 && (
          <ul className="reasons">
            {ev.reasons.map((r: string, i: number) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
        {scored && findings.length > 0 && (
          <ul className="rule-list">
            {findings.map((f) => (
              <li key={f.rule_id}>
                <div className="rule-name">
                  {ruleLabel(f.rule_id)} <span className="rule-tag">Rule triggered</span>
                </div>
                {f.evidence && <div className="rule-text">{f.evidence}</div>}
              </li>
            ))}
          </ul>
        )}
        {scored && findings.length === 0 && <p className="muted">No forensic rule was triggered for this wallet.</p>}
        {cur && !cur.exists && <p className="caveat">This wallet no longer exists in the database.</p>}
        {changed && (
          <p className="caveat changed">
            Today: {cur!.ml_prediction ?? 'not scored'} · rank #{cur!.priority_rank} · combined {cur!.combined_score !== null ? fmtScore(cur!.combined_score) : '—'} (review status {cur!.review_status}). The saved evidence above is unchanged.
          </p>
        )}
      </>
    );
  } else if (item.item_type === 'cluster' && s) {
    body = (
      <>
        <div className="case-wallet-head">
          <Link to={`/network?view=clusters&cluster=${item.item_id}`} className="mono wallet-link">
            {item.item_id}
          </Link>
          <span>
            {s.wallet_count} wallets · {s.method_label}
          </span>
          <span className="muted">
            {s.priority_summary?.lead_count ?? 0} leads · {s.transaction_count} transactions
          </span>
        </div>
        <p className="caveat">{s.note}</p>
        {cur?.exists && cur.wallet_count !== s.wallet_count && <p className="caveat changed">Today the cluster has {cur.wallet_count} wallets. The saved evidence above is unchanged.</p>}
        {cur && !cur.exists && <p className="caveat changed">This cluster id no longer exists (clusters are recomputed after each analysis).</p>}
      </>
    );
  } else if (item.item_type === 'transaction' && s) {
    const t = s.transaction ?? {};
    const obs: { observation_id: string; ip_address: string | null; device_id: string | null; observed_party: string | null }[] = s.synthetic_network_observations ?? [];
    body = (
      <>
        <div className="case-wallet-head">
          <span className="mono">{item.item_id}</span>
          <span>
            <Link to={`/wallets/${t.sender_wallet}`} className="mono wallet-link">{t.sender_wallet}</Link> → <Link to={`/wallets/${t.receiver_wallet}`} className="mono wallet-link">{t.receiver_wallet}</Link>
          </span>
          <span className="muted">{fmtBtc(t.amount_btc)} BTC · {t.timestamp ? fmtDateTime(t.timestamp) : ''}</span>
        </div>
        {obs.length > 0 && (
          <p className="muted">
            Synthetic network observations saved with it: {obs.map((o) => `${o.observed_party ?? ''} ${o.ip_address ?? ''} / ${o.device_id ?? ''}`.trim()).join(' · ')}
          </p>
        )}
      </>
    );
  } else {
    body = <p className="muted">No evidence was saved for this item.</p>;
  }

  return (
    <div className="case-wallet">
      <div className="item-bar">
        <span className="rule-tag">{ITEM_LABEL[item.item_type]}</span>
        <span className="muted">Added {fmtDateTime(item.added_at)}{s?.taken_at ? ` · evidence as of ${fmtDateTime(s.taken_at)}` : ''}</span>
        <span className="item-actions">
          <button type="button" className="btn btn-sm" onClick={onReviewed} disabled={busy}>
            Mark evidence reviewed
          </button>
          <button type="button" className="btn btn-sm btn-danger-outline" onClick={onRemove} disabled={busy}>
            Remove
          </button>
        </span>
      </div>
      {body}
    </div>
  );
}
