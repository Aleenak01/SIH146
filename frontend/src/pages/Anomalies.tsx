import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Download } from 'lucide-react';
import { api, qs } from '../api/client';
import type { PeelingChainPage } from '../api/types';
import { PageHeader } from '../components/bits';
import { CreateCaseDialog } from '../components/CreateCaseDialog';
import { WalletTable, type SortKey, type SortState } from '../components/WalletTable';
import { WALLET_COLUMNS, downloadCsv, stamp, walletRows } from '../data/export';
import type { EvidenceLevel, WalletRecord } from '../data/types';
import { fmtInt } from '../format';
import { useCases, type ReviewStatus } from '../state/cases';
import { useConfirm } from '../state/confirm';
import { useBackend, useDataset } from '../state/data';
import { useSettings } from '../state/settings';

const PAGE_SIZE = 25;
const STATUS_ORDER: Record<ReviewStatus, number> = { Unreviewed: 0, 'Under Review': 1, 'Case Created': 2 };
const LEVELS: EvidenceLevel[] = [
  'No specific rule triggered',
  'Single behavioural indicator',
  'Multiple behavioural indicators',
  'Multiple strong behavioural indicators',
];

type Scope = 'leads' | 'flagged' | 'normal' | 'all';

/**
 * Anomalies and investigative leads. Filters live in the query string so the Dashboard (and bookmarks) can link
 * straight to a view. With the backend connected the default view is the lead queue (ML-flagged wallets plus any
 * wallet in the High priority band); without it the screen is the original ML-flagged list.
 */
export function Anomalies() {
  const { wallets } = useDataset();
  const { mode } = useBackend();
  const withLeads = mode === 'api';
  const { statusOf, available: casesAvailable, createCase } = useCases();
  const { guard } = useConfirm();
  const { settings } = useSettings();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [creating, setCreating] = useState<WalletRecord | null>(null);

  const rawScope = sp.get('scope');
  const scope: Scope = rawScope === 'all' || rawScope === 'normal' || rawScope === 'flagged' ? rawScope : withLeads ? 'leads' : 'flagged';
  const status = (sp.get('status') as ReviewStatus | null) ?? 'any';
  const evidence = (sp.get('evidence') as EvidenceLevel | null) ?? 'any';
  const priority = sp.get('priority') ?? 'any';
  const query = sp.get('q') ?? '';
  const peelingOnly = sp.get('peeling') === '1';

  const [sort, setSort] = useState<SortState>({ key: 'combined', dir: 'desc' });
  const [page, setPage] = useState(0);
  const [peelingWallets, setPeelingWallets] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!withLeads) return;
    let live = true;
    api.get<PeelingChainPage>(`/api/peeling-chains${qs({ limit: 500 })}`).then(
      (p) => {
        if (!live) return;
        const s = new Set<string>();
        for (const c of p.items) for (const w of c.wallets) s.add(w);
        setPeelingWallets(s);
      },
      () => live && setPeelingWallets(new Set()),
    );
    return () => {
      live = false;
    };
  }, [withLeads]);

  const setParam = (patch: Record<string, string | null>) => {
    setPage(0);
    const next = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) {
      if (v && v !== 'any') next.set(k, v);
      else next.delete(k);
    }
    setSp(next, { replace: true });
  };

  const flaggedCount = useMemo(() => wallets.filter((w) => w.flagged).length, [wallets]);
  const leadCount = useMemo(() => wallets.filter((w) => w.lead?.isLead).length, [wallets]);

  const inScope = (w: WalletRecord) => {
    switch (scope) {
      case 'leads': return !!w.lead?.isLead;
      case 'flagged': return w.flagged;
      case 'normal': return !w.flagged;
      case 'all': return true;
    }
  };

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const value = (w: WalletRecord): number | string => {
      switch (sort.key) {
        case 'wallet': return w.id;
        case 'ml': return w.fusion.ml_anomaly_score;
        case 'evidence':
        case 'rules': return w.fusion.forensic_rule_count;
        case 'combined': return w.fusion.combined_score;
        case 'priority': return w.priorityRank;
        case 'status': return STATUS_ORDER[statusOf(w.id)];
      }
    };
    const dir = sort.dir === 'asc' ? 1 : -1;
    return wallets
      .filter(
        (w) =>
          inScope(w) &&
          (status === 'any' || statusOf(w.id) === status) &&
          (evidence === 'any' || w.fusion.forensic_evidence_level === evidence) &&
          (priority === 'any' || w.lead?.priorityLevel === priority) &&
          (!q || w.id.includes(q)) &&
          (!peelingOnly || peelingWallets.has(w.id)),
      )
      .sort((a, b) => {
        const x = value(a);
        const y = value(b);
        const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number);
        return c * dir || a.priorityRank - b.priorityRank;
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wallets, scope, status, evidence, priority, query, peelingOnly, peelingWallets, sort, statusOf]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const visible = rows.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  const onSort = (key: SortKey) => {
    setPage(0);
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'wallet' || key === 'status' || key === 'priority' ? 'asc' : 'desc' }));
  };

  const exportCsv = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export anomaly results',
      body: `Save the ${fmtInt(rows.length)} wallets currently listed (all pages, current filters and sort) as a CSV file on this computer.`,
      confirmLabel: 'Export CSV',
    });
    if (ok) downloadCsv(`sih146-anomalies-${stamp()}.csv`, WALLET_COLUMNS, walletRows(rows, statusOf));
  };

  // Honour the "confirm before creating a case" preference: off means the case opens at once with a default title.
  const startCreate = async (w: WalletRecord) => {
    if (settings.confirmCreateCase) {
      setCreating(w);
      return;
    }
    const c = await createCase({ title: `Review of ${w.id}`, priority: w.lead?.priorityLevel ?? null, items: [{ type: w.lead?.isLead ? 'lead' : 'wallet', id: w.id }] });
    navigate('/cases', { state: { created: c.case_id } });
  };

  return (
    <div className="page">
      <PageHeader
        title="Anomalies"
        subtitle={
          withLeads
            ? `${fmtInt(leadCount)} investigative leads: the ${fmtInt(flaggedCount)} wallets the ML model flagged plus any wallet in the High priority band, ranked by the prototype combined result. A lead is a starting point for review, not a case: an investigator decides whether to open one.`
            : `${fmtInt(flaggedCount)} of ${fmtInt(wallets.length)} wallets were flagged for investigation by the ML model. A flag is a lead, not a case: an investigator decides whether to open one.`
        }
      />

      <div className="toolbar">
        <div className="seg" role="group" aria-label="Scope">
          {withLeads && (
            <button type="button" className={scope === 'leads' ? 'on' : ''} onClick={() => setParam({ scope: null })}>
              Leads ({fmtInt(leadCount)})
            </button>
          )}
          <button type="button" className={scope === 'flagged' ? 'on' : ''} onClick={() => setParam({ scope: withLeads ? 'flagged' : null })}>
            ML-flagged ({fmtInt(flaggedCount)})
          </button>
          <button type="button" className={scope === 'normal' ? 'on' : ''} onClick={() => setParam({ scope: 'normal' })}>
            Not flagged ({fmtInt(wallets.length - flaggedCount)})
          </button>
          <button type="button" className={scope === 'all' ? 'on' : ''} onClick={() => setParam({ scope: 'all' })}>
            All ({fmtInt(wallets.length)})
          </button>
        </div>
        {withLeads && (
          <label className="ctl">
            <span>Priority</span>
            <select value={priority} onChange={(e) => setParam({ priority: e.target.value })}>
              <option value="any">Any</option>
              <option value="High">High</option>
              <option value="Medium">Medium</option>
              <option value="Low">Low</option>
            </select>
          </label>
        )}
        <label className="ctl">
          <span>Evidence</span>
          <select value={evidence} onChange={(e) => setParam({ evidence: e.target.value })}>
            <option value="any">Any</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l.replace('behavioural ', '')}
              </option>
            ))}
          </select>
        </label>
        <label className="ctl">
          <span>Status</span>
          <select value={status} onChange={(e) => setParam({ status: e.target.value })}>
            <option value="any">Any</option>
            <option value="Unreviewed">Unreviewed</option>
            <option value="Under Review">Under Review</option>
            <option value="Case Created">Case Created</option>
          </select>
        </label>
        <input
          className="search"
          type="search"
          placeholder="Search wallet, e.g. wallet_350"
          aria-label="Search wallet"
          value={query}
          onChange={(e) => setParam({ q: e.target.value })}
        />
        {withLeads && (
          <label className="check">
            <input type="checkbox" checked={peelingOnly} onChange={(e) => setParam({ peeling: e.target.checked ? '1' : null })} />
            Part of a peeling chain
          </label>
        )}
        <span className="toolbar-count">
          {fmtInt(rows.length)} wallet{rows.length === 1 ? '' : 's'}
        </span>
        <button type="button" className="btn btn-sm" onClick={exportCsv} disabled={rows.length === 0}>
          <Download size={13} aria-hidden="true" /> Export CSV
        </button>
      </div>

      <WalletTable rows={visible} sort={sort} onSort={onSort} leads={withLeads} onCreateCase={casesAvailable ? startCreate : undefined} />

      {pages > 1 && (
        <div className="pager">
          <span>
            {fmtInt(current * PAGE_SIZE + 1)}–{fmtInt(Math.min(rows.length, (current + 1) * PAGE_SIZE))} of {fmtInt(rows.length)}
          </span>
          <button type="button" className="btn btn-sm" disabled={current === 0} onClick={() => setPage(current - 1)}>
            Previous
          </button>
          <button type="button" className="btn btn-sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
            Next
          </button>
        </div>
      )}

      {creating && (
        <CreateCaseDialog
          wallet={creating}
          onCancel={() => setCreating(null)}
          onCreated={(id) => {
            setCreating(null);
            navigate('/cases', { state: { created: id } });
          }}
        />
      )}
    </div>
  );
}
