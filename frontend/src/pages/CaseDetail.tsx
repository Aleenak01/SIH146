import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download } from 'lucide-react';
import { CaseStatusPill, EvidenceCell, PageHeader, Panel, PredictionText } from '../components/bits';
import { TransactionTable } from '../components/TransactionTable';
import { TRANSFER_COLUMNS, downloadCsv, downloadJson, stamp, transferRows } from '../data/export';
import { ruleLabel, splitFindings } from '../data/rules';
import { fmtBtc, fmtDate, fmtDateTime, fmtInt, fmtScore } from '../format';
import { buildGraph } from '../graph/model';
import { WalletGraph } from '../graph/WalletGraph';
import { useCases, type CaseStatus, type CaseWallet } from '../state/cases';
import { useConfirm } from '../state/confirm';
import { useDataset, useFlaggedIds, useTransfers } from '../state/data';

const STATUSES: CaseStatus[] = ['Open', 'Under investigation', 'Closed'];

/**
 * A single case, read entirely from what the investigator saved (wallets, evidence snapshot,
 * related transactions, notes, history) plus the current transaction data for the network graph.
 */
export function CaseDetail() {
  const { caseId = '' } = useParams();
  const { getCase, setCaseStatus, addNote } = useCases();
  const { byId, mlBoundary } = useDataset();
  const { transfers } = useTransfers();
  const flaggedIds = useFlaggedIds();
  const { guard } = useConfirm();
  const [note, setNote] = useState('');

  const c = getCase(caseId);

  const walletIds = useMemo(() => c?.wallets.map((w) => w.address) ?? [], [c]);
  const graph = useMemo(
    () => (transfers && walletIds.length ? buildGraph(walletIds, transfers, byId, { maxCounterparties: 12, linkCounterparties: true }) : null),
    [transfers, walletIds, byId],
  );

  if (!c) {
    return (
      <div className="page">
        <PageHeader back={<BackLink />} title="Case not found" subtitle={`No case “${caseId}” exists in this browser's saved investigation data.`} />
      </div>
    );
  }

  const txs = c.relatedTransactions;
  const totalBtc = txs.reduce((n, t) => n + t.amountBtc, 0);
  const first = txs.length ? Math.min(...txs.map((t) => t.ts)) : null;
  const last = txs.length ? Math.max(...txs.map((t) => t.ts)) : null;
  const anomalous = c.wallets.filter((w) => w.snapshot.mlPrediction === 'Anomalous').length;

  const changeStatus = async (next: CaseStatus) => {
    if (next === c.status) return;
    const ok = await guard('confirmStatusChange', {
      title: 'Change case status',
      body: (
        <>
          Change <b>{c.id}</b> from <b>{c.status}</b> to <b>{next}</b>? The change is recorded in the case history.
          {next === 'Closed' ? ' A closed case no longer counts as active.' : ''}
        </>
      ),
      confirmLabel: `Set to ${next}`,
    });
    if (ok) setCaseStatus(c.id, next);
  };

  const exportJson = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export case',
      body: `Save ${c.id} (wallets, evidence snapshot, ${fmtInt(txs.length)} related transactions, notes and history) as a JSON file on this computer.`,
      confirmLabel: 'Export JSON',
    });
    if (ok)
      downloadJson(`sih146-${c.id}-${stamp()}.json`, {
        exportedAt: new Date().toISOString(),
        source: 'SIH146 frontend prototype, synthetic data',
        case: c,
      });
  };

  const exportTx = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export case transactions',
      body: `Save the ${fmtInt(txs.length)} transactions attached to ${c.id} as a CSV file on this computer.`,
      confirmLabel: 'Export CSV',
    });
    if (ok) downloadCsv(`sih146-${c.id}-transactions-${stamp()}.csv`, TRANSFER_COLUMNS, transferRows(txs));
  };

  return (
    <div className="page">
      <PageHeader
        back={<BackLink />}
        title={c.title}
        subtitle={
          <>
            <span className="mono">{c.id}</span> · Created {fmtDateTime(c.createdAt)} · <CaseStatusPill status={c.status} />
          </>
        }
        actions={
          <>
            <label className="ctl">
              <span>Status</span>
              <select value={c.status} onChange={(e) => changeStatus(e.target.value as CaseStatus)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
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

      <div className="metrics">
        <div className="metric">
          <div className="metric-label">Flagged wallets</div>
          <div className="metric-value">{fmtInt(c.wallets.length)}</div>
          <div className="metric-sub">{anomalous} ML-anomalous when the case was opened</div>
        </div>
        <div className="metric">
          <div className="metric-label">Related transactions</div>
          <div className="metric-value">{fmtInt(txs.length)}</div>
          <div className="metric-sub">Total attached to this case</div>
        </div>
        <div className="metric">
          <div className="metric-label">Value moved</div>
          <div className="metric-value mono">{fmtBtc(totalBtc)}</div>
          <div className="metric-sub">BTC across related transactions</div>
        </div>
        <div className="metric">
          <div className="metric-label">Activity period</div>
          <div className="metric-value metric-text">{first !== null && last !== null ? `${fmtDate(first)} – ${fmtDate(last)}` : '—'}</div>
          <div className="metric-sub">First to last related transfer</div>
        </div>
      </div>

      <Panel
        title="Flagged wallets and evidence"
        note="Evidence as recorded when the wallet was added to the case. Rules are behavioural indicators, and an anomaly score is a statistical measure; neither establishes wrongdoing, and both require investigator review."
      >
        <div className="case-wallets">
          {c.wallets.map((w) => (
            <WalletEvidence key={w.address} w={w} boundary={mlBoundary} />
          ))}
        </div>
      </Panel>

      <div className="grid-notes-history">
        <Panel title="Investigator notes" note="Saved with the case in this browser.">
          {c.notes.length === 0 ? (
            <p className="muted">No notes yet.</p>
          ) : (
            <ul className="notes">
              {c.notes.map((n) => (
                <li key={n.id}>
                  <div className="notes-meta">
                    {n.author} · {fmtDateTime(n.at)}
                  </div>
                  <div className="notes-text">{n.text}</div>
                </li>
              ))}
            </ul>
          )}
          <form
            className="note-form"
            onSubmit={(e) => {
              e.preventDefault();
              addNote(c.id, note);
              setNote('');
            }}
          >
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Add an observation, a next step or a decision." aria-label="New note" />
            <button type="submit" className="btn btn-sm" disabled={!note.trim()}>
              Add note
            </button>
          </form>
        </Panel>

        <Panel title="Investigation history" note="Every recorded change to this case, oldest first.">
          <ol className="history">
            {c.history.map((h, i) => (
              <li key={i}>
                <div className="history-when">{fmtDateTime(h.at)}</div>
                <div>
                  <b>{h.action}</b>
                  {h.detail && <span className="muted"> · {h.detail}</span>}
                  <div className="muted history-who">{h.actor}</div>
                </div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <Panel
        title="Wallet relationship graph"
        note={
          <>
            The case wallets in the centre with their most active counterparties either side, including transfers between those counterparties. Built from the transaction dataset. Select a wallet to open
            its investigation.
          </>
        }
      >
        {graph ? (
          <>
            <WalletGraph model={graph} height={620} />
            <p className="graph-foot">
              {graph.hiddenCounterparties > 0
                ? `Showing the ${fmtInt(graph.nodes.length - graph.focusIds.length)} most active of ${fmtInt(graph.totalCounterparties)} counterparties. `
                : `Showing all ${fmtInt(graph.totalCounterparties)} counterparties. `}
              Outlined wallets are ML-flagged.{' '}
              {walletIds.length === 1 && (
                <Link to={`/network?view=network&wallet=${walletIds[0]}`} className="link">
                  Explore further in Transactions / Network →
                </Link>
              )}
            </p>
          </>
        ) : (
          <div className="viz-loading">Loading transactions…</div>
        )}
      </Panel>

      <Panel
        title="Related transactions"
        note={`${fmtInt(txs.length)} transfers involving the case wallets, saved with the case. Select a row for its full record.`}
        actions={
          <button type="button" className="btn btn-sm" onClick={exportTx} disabled={txs.length === 0}>
            <Download size={13} aria-hidden="true" /> Export CSV
          </button>
        }
      >
        <TransactionTable rows={txs} flaggedIds={flaggedIds} pageSize={15} emptyText="No transactions were attached to this case." />
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

function WalletEvidence({ w, boundary }: { w: CaseWallet; boundary: number }) {
  const { byId } = useDataset();
  const live = byId.get(w.address);
  const s = w.snapshot;
  // Older saved cases have no stored explanation text; fall back to the current pipeline output for it.
  const findings = s.findings ?? live?.fusion.forensic_findings;
  const sentences = findings ? splitFindings(findings, s.triggeredRules) : null;

  return (
    <div className="case-wallet">
      <div className="case-wallet-head">
        <Link to={`/wallets/${w.address}`} className="mono wallet-link">
          {w.address}
        </Link>
        <span>
          <PredictionText flagged={s.mlPrediction === 'Anomalous'} /> · ML {fmtScore(s.mlScore)}
          <span className="muted"> (model boundary {fmtScore(boundary)})</span>
        </span>
        <EvidenceCell level={s.evidenceLevel as never} />
        <span className="muted">
          {s.ruleCount} rule{s.ruleCount === 1 ? '' : 's'} · combined {fmtScore(s.combinedScore)} (#{s.priorityRank})
        </span>
        <span className="muted case-wallet-added">Added {fmtDateTime(w.addedAt)}</span>
      </div>
      {s.triggeredRules.length === 0 ? (
        <p className="muted">No forensic rule was triggered for this wallet.</p>
      ) : (
        <ul className="rule-list">
          {s.triggeredRules.map((r, i) => (
            <li key={r}>
              <div className="rule-name">
                {ruleLabel(r)} <span className="rule-tag">Rule triggered</span>
              </div>
              {sentences && <div className="rule-text">{sentences[i]}</div>}
            </li>
          ))}
        </ul>
      )}
      {s.triggeredRules.length > 0 && !sentences && findings && <p className="rule-text">{findings}</p>}
      {!s.findings && s.triggeredRules.length > 0 && <p className="caveat">Explanation text taken from the current pipeline output.</p>}
    </div>
  );
}
