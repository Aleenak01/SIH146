import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { GeoSummary } from '../api/types';
import { PageHeader, Panel, evidenceShort } from '../components/bits';
import { ActivityTrend, ScoreDistribution } from '../components/charts';
import { WalletTable } from '../components/WalletTable';
import { fmtDate, fmtDateTime, fmtInt, fmtPct } from '../format';
import { useCases } from '../state/cases';
import { syntheticTx, useBackend, useConfidenceScores, useDataset, useTransfers } from '../state/data';
import type { EvidenceLevel } from '../data/types';

const cellLink = (scope: 'flagged' | 'normal', level: EvidenceLevel) =>
  `/anomalies?${new URLSearchParams({ scope, evidence: level })}`;

const LEVELS: EvidenceLevel[] = [
  'No specific rule triggered',
  'Single behavioural indicator',
  'Multiple behavioural indicators',
  'Multiple strong behavioural indicators',
];

/** A metric that leads to the section holding the records behind it. */
function Metric({ label, value, sub, to }: { label: string; value: string; sub: string; to: string }) {
  return (
    <Link to={to} className="metric metric-link" role="listitem" title={`Open ${label.toLowerCase()}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-sub">{sub}</div>
    </Link>
  );
}

export function Dashboard() {
  const { wallets, mlBoundary, population } = useDataset();
  const { transfers } = useTransfers();
  const { activeCases, statusOf } = useCases();
  const { mode, overview, monitor, analysis } = useBackend();
  const api = mode === 'api';
  const confidenceScores = useConfidenceScores();

  const s = useMemo(() => {
    const flagged = wallets.filter((w) => w.flagged);
    // Each transfer is "outgoing" for exactly one wallet, so this is the distinct-transfer count.
    const transferCount = wallets.reduce((n, w) => n + w.features.outgoing_count, 0);
    const records = wallets.reduce((n, w) => n + w.features.transaction_count, 0);
    return {
      flagged: flagged.length,
      flaggedWithRules: flagged.filter((w) => w.fusion.forensic_rule_count >= 2).length,
      transferCount,
      records,
      featureCount: Object.keys(population).length,
    };
  }, [wallets, population]);

  const unreviewedFlagged = wallets.filter((w) => w.flagged && statusOf(w.id) === 'Unreviewed').length;
  const leadCount = overview?.leads_total ?? wallets.filter((w) => w.lead?.isLead).length;
  const topByPriority = useMemo(
    () => [...wallets].sort((a, b) => a.priorityRank - b.priorityRank).slice(0, 10),
    [wallets],
  );

  // ML prediction x forensic evidence level, straight counts from the fusion output.
  const matrix = useMemo(
    () =>
      LEVELS.map((level) => {
        const inLevel = wallets.filter((w) => w.fusion.forensic_evidence_level === level);
        return { level, anomalous: inLevel.filter((w) => w.flagged).length, normal: inLevel.filter((w) => !w.flagged).length };
      }),
    [wallets],
  );
  const matrixMax = Math.max(...matrix.flatMap((m) => [m.anomalous, m.normal]), 1);

  const period = transfers
    ? `${fmtDate(Math.min(...transfers.map((t) => t.ts)))} – ${fmtDate(Math.max(...transfers.map((t) => t.ts)))}`
    : null;

  return (
    <div className="page">
      <PageHeader
        title="Dashboard"
        subtitle={
          <>
            {api ? 'State of the latest analysis run' : 'State of the bundled analysis output'}{period ? ` · transactions ${period}` : ''} · synthetic data
          </>
        }
      />

      {api && <MonitoringStrip />}

      {api ? (
        <div className="metrics metrics-6" role="list">
          <Metric to="/network" label="Total transactions" value={fmtInt(overview ? syntheticTx(overview) : transfers?.length ?? 0)} sub={`Distinct transfers · ${fmtInt(s.records)} wallet-level records`} />
          <Metric
            to="/anomalies?scope=all"
            label="Wallets monitored"
            value={fmtInt(overview?.by_source.synthetic?.wallets ?? wallets.length)}
            sub={`${fmtInt(wallets.length)} analysed${analysis && analysis.unscored > 0 ? ` · ${fmtInt(analysis.unscored)} too quiet to score` : ''}`}
          />
          <Metric to="/anomalies?scope=flagged" label="Anomalies" value={fmtInt(s.flagged)} sub={`ML-flagged · ${fmtPct((s.flagged / wallets.length) * 100)} of wallets`} />
          <Metric to="/anomalies" label="Leads" value={fmtInt(leadCount)} sub={monitor && monitor.new_leads_last_run > 0 ? `${monitor.new_leads_last_run} new in the last run` : 'Flagged or High priority'} />
          <Metric to="/cases" label="Active cases" value={fmtInt(activeCases)} sub={`${fmtInt(unreviewedFlagged)} flagged wallets not yet reviewed`} />
          <Metric to="/network?view=clusters" label="Clusters" value={fmtInt(overview?.clusters_total ?? 0)} sub="Related-entity groups (synthetic network data)" />
        </div>
      ) : (
        <div className="metrics" role="list">
          <Metric to="/network" label="Total transactions" value={fmtInt(s.records)} sub={`Wallet-level records · ${fmtInt(s.transferCount)} distinct transfers`} />
          <Metric to="/anomalies?scope=all" label="Wallets analyzed" value={fmtInt(wallets.length)} sub={`${s.featureCount} behavioural features each`} />
          <Metric
            to="/anomalies"
            label="Flagged wallets"
            value={fmtInt(s.flagged)}
            sub={`${fmtPct((s.flagged / wallets.length) * 100)} of wallets · ${s.flaggedWithRules} also trigger 2+ rules`}
          />
          <Metric to="/cases" label="Active cases" value={fmtInt(activeCases)} sub={`${fmtInt(unreviewedFlagged)} flagged wallets not yet reviewed`} />
        </div>
      )}

      <div className="grid-2">
        <ScoreDistribution wallets={wallets} boundary={mlBoundary} />
        <ActivityTrend transfers={transfers} wallets={wallets} />
      </div>

      <div className="grid-main-side">
        <Panel
          title="Priority queue"
          note="Top 10 wallets by combined result. A flag is a lead for review; opening a case is the investigator’s decision."
          actions={
            <Link to="/anomalies" className="link">
              All flagged wallets →
            </Link>
          }
        >
          <WalletTable rows={topByPriority} confidenceScores={api ? confidenceScores : undefined} />
        </Panel>

        <Panel
          title="ML and forensic evidence"
          note="Wallets by forensic evidence level and ML prediction; select a count to list those wallets. ML-normal wallets with several triggered rules, and the reverse, are where the two signals disagree."
        >
          <table className="data-table matrix">
            <thead>
              <tr>
                <th>Forensic evidence</th>
                <th className="num">ML anomalous</th>
                <th className="num">ML normal</th>
              </tr>
            </thead>
            <tbody>
              {matrix.map((m) => (
                <tr key={m.level}>
                  <th scope="row">{evidenceShort(m.level)}</th>
                  <td className="num mono cell-heat" style={{ ['--a' as string]: m.anomalous / matrixMax }}>
                    <Link to={cellLink('flagged', m.level)} className="cell-link">
                      {m.anomalous}
                    </Link>
                  </td>
                  <td className="num mono cell-heat" style={{ ['--a' as string]: m.normal / matrixMax }}>
                    <Link to={cellLink('normal', m.level)} className="cell-link">
                      {m.normal}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      {api && <GeoSummaryPanel />}
    </div>
  );
}

/** Dataset-wide top countries/ASNs across current leads (Phase 4 Part A), from synthetic GeoIP demo data. */
function GeoSummaryPanel() {
  const [summary, setSummary] = useState<GeoSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<GeoSummary>('/api/geo/summary').then(
      (s) => live && setSummary(s),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, []);

  return (
    <Panel
      title="Top countries in leads"
      note="Country and ASN breakdown of the synthetic GeoIP data attached to current investigative leads' transactions. Synthetic GeoIP demo data: IP addresses are randomly assigned for the demo, not observed network traffic."
    >
      {error && <p className="field-error">Could not load geo summary: {error}</p>}
      {!summary && !error && <div className="viz-loading">Loading…</div>}
      {summary && summary.top_countries.length === 0 && <p className="muted">No synthetic GeoIP data is attached to any current lead's transactions.</p>}
      {summary && summary.top_countries.length > 0 && (
        <div className="table-wrap">
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Country</th>
                <th className="num">Transactions</th>
              </tr>
            </thead>
            <tbody>
              {summary.top_countries.map((c) => (
                <tr key={c.country}>
                  <td className="mono">{c.country}</td>
                  <td className="num mono">{fmtInt(c.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {summary && summary.top_asns.length > 0 && (
        <p className="muted">
          Top networks (ASN): {summary.top_asns.map((a) => `${a.asn} (${a.asn_org ?? 'unknown org'}) · ${a.count}`).join(' · ')}
        </p>
      )}
    </Panel>
  );
}

/** Where the data comes from and how current it is. It never implies real-time monitoring of the Bitcoin network. */
function MonitoringStrip() {
  const { monitor, overview, analysis, online } = useBackend();
  if (!monitor) return null;
  const state = monitor.analysis_in_progress ? 'Analysing now…' : monitor.analysis_stale ? 'Newer transactions await analysis' : 'Analysis is up to date';
  return (
    <section className="panel monitor-strip" aria-label="Monitoring status">
      <div className="monitor-head">
        <span className={`status status-${monitor.status === 'active' && online ? 'case' : 'unreviewed'}`}>
          <span className="status-dot" aria-hidden="true" />
          Monitoring {monitor.status === 'active' ? 'active' : 'stopped'}
        </span>
        <span className="rule-tag">{monitor.data_source.label}</span>
        <span className="muted">{monitor.data_source.note}</span>
        <Link to="/settings" className="link monitor-link">
          Monitoring settings →
        </Link>
      </div>
      <dl className="monitor-grid">
        <div>
          <dt>Last transaction</dt>
          <dd>{monitor.last_transaction_at ? fmtDateTime(monitor.last_transaction_at) : '—'}</dd>
        </div>
        <div>
          <dt>Last analysis</dt>
          <dd>{analysis?.finishedAt ? fmtDateTime(analysis.finishedAt) : '—'}</dd>
        </div>
        <div>
          <dt>Analysis mode</dt>
          <dd>{monitor.auto_analysis ? `Automatic, micro-batch (checked every ${monitor.interval_seconds} s)` : 'Manual (automatic analysis is off)'}</dd>
        </div>
        <div>
          <dt>State</dt>
          <dd>{state}</dd>
        </div>
        <div>
          <dt>New leads in last run</dt>
          <dd>{monitor.new_leads_last_run}</dd>
        </div>
        <div>
          <dt>Network observations</dt>
          <dd>{fmtInt(overview?.network_observations_total ?? 0)} synthetic</dd>
        </div>
      </dl>
      {monitor.last_error && <p className="field-error">Monitor error: {monitor.last_error}</p>}
    </section>
  );
}
