import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, qs, Unreachable } from '../api/client';
import type { ApiTransaction, BulkAnalysis, ConfidenceScorePage, MonitorStatus, Overview } from '../api/types';
import { assembleDataset, loadDataset, loadTransfers, type Dataset } from '../data/loader';
import type { EvidenceLevel, FeatureRow, FusionRow, LeadInfo, Transfer } from '../data/types';

// ---------------------------------------------------------------------------------------------
// Where the app's data comes from.
//
//   api  the local backend (FastAPI + SQLite): the analysis, leads, clusters, cases and monitoring
//        it holds. Refreshed automatically when the monitor finishes a new analysis run.
//   csv  fallback when the backend is not running (or holds no analysis yet): the pipeline CSV
//        files bundled with the app. Read-only; cases and monitoring are unavailable in this mode.
//
// Nothing here talks to any service except the local backend.
// ---------------------------------------------------------------------------------------------

export type Mode = 'connecting' | 'api' | 'csv';
const POLL_MS = 8000;
const NO_RULE_TEXT = 'No behavioural rule was triggered for this wallet.';

export interface AnalysisInfo {
  runId: number | null;
  finishedAt: string | null;
  stale: boolean;
  fusionNote: string;
  forensicWeight: number | null;
  mlWeight: number | null;
  unscored: number;
}

export interface Backend {
  mode: Mode;
  /** API mode: the last status poll succeeded. False shows an "offline" notice; the last loaded data stays on screen. */
  online: boolean;
  /** CSV mode: why the backend is not being used. */
  reason: string | null;
  /** CSV mode: the backend answered but holds no analysis yet (offer to load the demo data). */
  emptyBackend: boolean;
  overview: Overview | null;
  monitor: MonitorStatus | null;
  analysis: AnalysisInfo | null;
  /** Reloads wallets (and transactions if their number changed), overview and monitor status. */
  reload: () => Promise<void>;
  /** Try to reach the backend again (from the CSV fallback). */
  reconnect: () => Promise<void>;
  /** Import the synthetic demo data into an empty backend and run the analysis. */
  loadDemoData: () => Promise<void>;
  busy: string | null;
}

interface Ctx {
  dataset: Dataset | null;
  transfers: Transfer[] | null;
  transfersError: string | null;
  backend: Backend;
  fatal: string | null;
}

const DataCtx = createContext<Ctx | null>(null);

// --- backend -> the shapes the screens already use ------------------------------------------------
function toDataset(b: BulkAnalysis): Dataset {
  const fusion: FusionRow[] = [];
  const features: FeatureRow[] = [];
  const ranks = new Map<string, number>();
  const leads = new Map<string, LeadInfo>();
  for (const w of b.wallets) {
    fusion.push({
      wallet_address: w.wallet_address,
      forensic_score: w.forensic_score,
      forensic_rule_count: w.forensic_rule_count,
      forensic_evidence_level: w.evidence_level as EvidenceLevel,
      forensic_triggered_rules: w.triggered_rules.length ? w.triggered_rules.join(';') : 'none',
      forensic_findings: w.findings.length ? w.findings.join(' ') : NO_RULE_TEXT,
      ml_anomaly_score: w.ml_score,
      ml_anomaly_prediction: w.ml_prediction,
      combined_score: w.combined_score,
      forensic_weight: b.forensic_weight ?? 0.4,
      ml_weight: b.ml_weight ?? 0.6,
      valid: true,
      warnings: '',
      errors: '',
    });
    features.push({ wallet_address: w.wallet_address, ...w.features } as unknown as FeatureRow);
    ranks.set(w.wallet_address, w.priority_rank);
    leads.set(w.wallet_address, { priorityLevel: w.priority_level, isLead: w.is_lead, reasons: w.reasons, caseIds: w.case_ids, review: w.review_status });
  }
  return assembleDataset(fusion, features, 'api', { ranks, leads });
}

async function fetchAllTransfers(): Promise<Transfer[]> {
  const page = (offset: number) => api.get<{ total: number; items: ApiTransaction[] }>(`/api/transactions${qs({ limit: 500, offset, sort: 'timestamp', order: 'asc', source: 'synthetic' })}`);
  const first = await page(0);
  const rest = await Promise.all(Array.from({ length: Math.max(0, Math.ceil(first.total / 500) - 1) }, (_, i) => page((i + 1) * 500)));
  const seen = new Map<string, Transfer>();
  for (const p of [first, ...rest])
    for (const t of p.items)
      seen.set(t.transaction_id, {
        id: t.transaction_id,
        ts: Date.parse(t.timestamp),
        from: t.sender_wallet,
        to: t.receiver_wallet,
        amountBtc: t.amount_btc,
        inputCount: t.input_count,
        outputCount: t.output_count,
      });
  return [...seen.values()].sort((a, b) => a.ts - b.ts);
}

/** Transactions of the synthetic source only: real Bitcoin data, if any was fetched, is kept out of these screens. */
export const syntheticTx = (o: Overview) => o.by_source.synthetic?.transactions ?? 0;

const analysisInfo = (b: BulkAnalysis): AnalysisInfo => ({
  runId: b.run_id,
  finishedAt: b.finished_at,
  stale: b.stale,
  fusionNote: b.fusion_note,
  forensicWeight: b.forensic_weight,
  mlWeight: b.ml_weight,
  unscored: b.unscored_wallets,
});

// --- provider --------------------------------------------------------------------------------------
export function DataProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>('connecting');
  const [online, setOnline] = useState(true);
  const [reason, setReason] = useState<string | null>(null);
  const [emptyBackend, setEmpty] = useState(false);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [transfers, setTransfers] = useState<Transfer[] | null>(null);
  const [transfersError, setTransfersError] = useState<string | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [monitor, setMonitor] = useState<MonitorStatus | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisInfo | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const modeRef = useRef<Mode>('connecting');
  const loadedRun = useRef<number | null>(null);
  const loadedTx = useRef<number | null>(null);
  const failures = useRef(0);
  const seq = useRef(0);
  const inFlight = useRef(false);

  const useCsv = useCallback((why: string | null, empty: boolean) => {
    try {
      setDataset(loadDataset());
      setFatal(null);
    } catch (e) {
      setFatal(e instanceof Error ? e.message : String(e));
    }
    modeRef.current = 'csv';
    loadedTx.current = null;
    setMode('csv');
    setReason(why);
    setEmpty(empty);
    setOverview(null);
    setMonitor(null);
    setAnalysis(null);
    loadTransfers().then(
      (t) => setTransfers(t),
      (e) => setTransfersError(e instanceof Error ? e.message : String(e)),
    );
  }, []);

  /** Loads everything from the backend. Returns false if the backend is unusable (caller falls back to CSV). */
  const loadFromApi = useCallback(async (): Promise<{ ok: true } | { ok: false; why: string; empty: boolean }> => {
    const my = ++seq.current;
    let bulk: BulkAnalysis;
    try {
      await api.get('/api/health', { timeoutMs: 4000 });
      bulk = await api.get<BulkAnalysis>('/api/analysis/wallets', { timeoutMs: 30000 });
    } catch (e) {
      return { ok: false, why: e instanceof Unreachable ? 'The backend is not running.' : `The backend returned an error: ${(e as Error).message}`, empty: false };
    }
    if (bulk.run_id === null || bulk.wallets.length === 0) return { ok: false, why: 'The backend is running but has not analysed any data yet.', empty: true };

    const [ov, mon] = await Promise.all([api.get<Overview>('/api/overview').catch(() => null), api.get<MonitorStatus>('/api/monitor/status').catch(() => null)]);
    let txs: Transfer[] | null = null;
    if (loadedTx.current === null || (ov && syntheticTx(ov) !== loadedTx.current)) {
      try {
        txs = await fetchAllTransfers();
      } catch (e) {
        setTransfersError(e instanceof Error ? e.message : String(e));
      }
    }
    if (my !== seq.current) return { ok: true }; // a newer load superseded this one
    setDataset(toDataset(bulk));
    setAnalysis(analysisInfo(bulk));
    setOverview(ov);
    setMonitor(mon);
    if (txs) {
      setTransfers(txs);
      setTransfersError(null);
      loadedTx.current = txs.length;
    }
    loadedRun.current = bulk.run_id;
    modeRef.current = 'api';
    setMode('api');
    setOnline(true);
    setReason(null);
    setEmpty(false);
    setFatal(null);
    failures.current = 0;
    return { ok: true };
  }, []);

  const connect = useCallback(async () => {
    const r = await loadFromApi();
    if (!r.ok) useCsv(r.why, r.empty);
  }, [loadFromApi, useCsv]);

  useEffect(() => {
    void connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reloads run one after another, so a change made while a reload is in flight is never skipped.
  const chain = useRef<Promise<void>>(Promise.resolve());
  const reload = useCallback(() => {
    const next = chain.current.then(async () => {
      if (modeRef.current !== 'api') return;
      inFlight.current = true;
      try {
        const r = await loadFromApi();
        if (!r.ok) setOnline(false);
      } finally {
        inFlight.current = false;
      }
    });
    chain.current = next.catch(() => undefined);
    return next;
  }, [loadFromApi]);

  // API mode: watch the monitor; when a newer analysis run (or new transactions) is available, load them.
  useEffect(() => {
    if (mode !== 'api') return;
    const id = window.setInterval(async () => {
      if (inFlight.current) return;
      try {
        const mon = await api.get<MonitorStatus>('/api/monitor/status', { timeoutMs: 5000 });
        const recovered = failures.current > 0;
        failures.current = 0;
        setOnline(true);
        setMonitor(mon);
        const newRun = mon.last_analysis_run_id !== null && mon.last_analysis_run_id !== loadedRun.current;
        const newTx = mon.transactions_total !== loadedTx.current;
        if ((newRun || (recovered && newTx)) && !mon.analysis_in_progress) await reload();
        else if (newTx) api.get<Overview>('/api/overview').then(setOverview).catch(() => undefined);
      } catch {
        failures.current += 1;
        if (failures.current >= 2) setOnline(false);
      }
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [mode, reload]);

  const reconnect = useCallback(async () => {
    setBusy('Connecting…');
    try {
      await connect();
    } finally {
      setBusy(null);
    }
  }, [connect]);

  const loadDemoData = useCallback(async () => {
    setBusy('Importing the synthetic demo data and running the analysis (about 20 seconds)…');
    try {
      await api.post('/api/import/synthetic-csv', {}, { timeoutMs: 120000 });
      await api.post('/api/import/synthetic-network', {}, { timeoutMs: 120000 });
      await api.post('/api/analysis/run', {}, { timeoutMs: 300000 }).catch((e) => {
        if ((e as { code?: string }).code !== 'analysis_in_progress') throw e; // the monitor may already be analysing
      });
      loadedTx.current = null;
      // The monitor may have started the analysis itself; wait for the first result rather than giving up.
      for (let i = 0; i < 60; i++) {
        const r = await loadFromApi();
        if (r.ok) return;
        if (!r.empty) throw new Error(r.why);
        await new Promise((res) => setTimeout(res, 3000));
      }
      throw new Error('the analysis did not finish in time; use Retry connection in a moment');
    } catch (e) {
      setReason(`Could not load the demo data: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }, [connect, loadFromApi]);

  const backend = useMemo<Backend>(
    () => ({ mode, online, reason, emptyBackend, overview, monitor, analysis, reload, reconnect, loadDemoData, busy }),
    [mode, online, reason, emptyBackend, overview, monitor, analysis, reload, reconnect, loadDemoData, busy],
  );
  const value = useMemo<Ctx>(() => ({ dataset, transfers, transfersError, backend, fatal }), [dataset, transfers, transfersError, backend, fatal]);

  if (fatal && !dataset) {
    return (
      <div className="fatal">
        <h1>Could not load pipeline output</h1>
        <p>{fatal}</p>
        <p>Start the backend (python -m backend), or make sure data/fusion_results.csv and data/wallet_behavior_features.csv exist in the project root.</p>
      </div>
    );
  }
  if (!dataset) {
    return (
      <div className="fatal" role="status">
        <h1>Connecting to the local backend…</h1>
        <p>Falling back to the bundled pipeline output if it is not running.</p>
      </div>
    );
  }
  return <DataCtx.Provider value={value}>{children}</DataCtx.Provider>;
}

function useCtx(): Ctx {
  const v = useContext(DataCtx);
  if (!v) throw new Error('data hooks must be used inside DataProvider');
  return v;
}

export function useDataset(): Dataset {
  return useCtx().dataset!;
}

/** Transfers (one row per transfer), loaded by the provider from the backend or the bundled CSV. */
export function useTransfers(): { transfers: Transfer[] | null; error: string | null } {
  const c = useCtx();
  return { transfers: c.transfers, error: c.transfersError };
}

export function useBackend(): Backend {
  return useCtx().backend;
}

/** Ids of the wallets the Isolation Forest flagged. */
export function useFlaggedIds(): Set<string> {
  const { wallets } = useDataset();
  return useMemo(() => new Set(wallets.filter((w) => w.flagged).map((w) => w.id)), [wallets]);
}

/**
 * The explainable confidence score (Phase 4 Part A) for every wallet that has one, fetched once from
 * `/api/confidence-scores`. Alongside, never instead of, the existing combined_score. Empty (not null) if the
 * backend is not connected or `compute-confidence` has not been run yet.
 */
export function useConfidenceScores(): Map<string, number> {
  const { mode, analysis } = useBackend();
  const [scores, setScores] = useState<Map<string, number>>(new Map());
  useEffect(() => {
    if (mode !== 'api') return;
    let live = true;
    api.get<ConfidenceScorePage>(`/api/confidence-scores${qs({ limit: 500 })}`).then(
      (p) => live && setScores(new Map(p.items.map((i) => [i.wallet_address, i.score]))),
      () => live && setScores(new Map()),
    );
    return () => {
      live = false;
    };
  }, [mode, analysis?.runId]);
  return scores;
}
