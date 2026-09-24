import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { PageHeader, Panel, evidenceShort } from '../components/bits';
import { ActivityTrend, ScoreDistribution } from '../components/charts';
import { WalletTable } from '../components/WalletTable';
import { fmtDate, fmtInt, fmtPct } from '../format';
import { useCases } from '../state/cases';
import { useDataset, useTransfers } from '../state/data';
import type { EvidenceLevel } from '../data/types';

const cellLink = (scope: 'flagged' | 'normal', level: EvidenceLevel) =>
  `/anomalies?${new URLSearchParams({ ...(scope === 'normal' ? { scope } : {}), evidence: level })}`;

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
            State of the current analysis run{period ? ` · transactions ${period}` : ''} · synthetic data
          </>
        }
      />

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
          <WalletTable rows={topByPriority} />
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
    </div>
  );
}
