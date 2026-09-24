import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { EvidenceCell, PageHeader, Panel, PredictionText, StatusPill } from '../components/bits';
import { CreateCaseDialog } from '../components/CreateCaseDialog';
import { FEATURE_GROUPS, RULES, TOTAL_RULES, parseRuleNames, ruleLabel, splitFindings } from '../data/rules';
import { median, percentileRank } from '../data/loader';
import type { FeatureKey, WalletRecord } from '../data/types';
import { fmtDateTime, fmtInt, fmtNum, fmtScore } from '../format';
import { buildEgoGraph } from '../graph/model';
import { WalletGraph } from '../graph/WalletGraph';
import { useCases } from '../state/cases';
import { useDataset, useTransfers } from '../state/data';
import { useSettings } from '../state/settings';

const HIGH_PCT = 90; // same reference point the forensic rules use (population 90th percentile)

export function WalletDetail() {
  const { walletId = '' } = useParams();
  const { byId, wallets, population, mlBoundary } = useDataset();
  const { transfers } = useTransfers();
  const { statusOf, caseFor, markUnderReview, markUnreviewed, createCase } = useCases();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const { settings } = useSettings();

  const wallet = byId.get(walletId);

  const graph = useMemo(
    () => (wallet && transfers ? buildEgoGraph(wallet.id, transfers, byId) : null),
    [wallet, transfers, byId],
  );

  if (!wallet) {
    return (
      <div className="page">
        <PageHeader title="Wallet not found" subtitle={`No wallet “${walletId}” exists in the analysed dataset.`} back={<BackLink />} />
      </div>
    );
  }

  const f = wallet.fusion;
  const status = statusOf(wallet.id);
  const existingCase = caseFor(wallet.id);
  const ruleNames = parseRuleNames(f.forensic_triggered_rules);
  const sentences = splitFindings(f.forensic_findings, ruleNames);
  const mlRank = wallets.filter((w) => w.fusion.ml_anomaly_score > f.ml_anomaly_score).length + 1;

  // Features where this wallet is in the top decile of the population (descriptive position only).
  const standout = FEATURE_GROUPS.flatMap((g) => g.features)
    .map((ft) => ({ ...ft, value: wallet.features[ft.key], pct: percentileRank(population[ft.key], wallet.features[ft.key]) }))
    .filter((x) => x.pct > HIGH_PCT)
    .sort((a, b) => b.pct - a.pct || a.label.localeCompare(b.label));

  const ruleFeatures = new Set<FeatureKey>(ruleNames.map((r) => RULES[r]?.feature).filter(Boolean) as FeatureKey[]);

  const headline = headlineFor(wallet);

  // Opens the review dialog, or (if the investigator switched that confirmation off in Settings)
  // creates the case straight away with a default title.
  const startCreate = () => {
    if (settings.confirmCreateCase || !transfers) {
      setCreating(true);
      return;
    }
    const related = transfers.filter((t) => t.from === wallet.id || t.to === wallet.id);
    const c = createCase({ wallet, title: '', note: '', transactions: related });
    navigate('/cases', { state: { created: c.id } });
  };

  return (
    <div className="page">
      <PageHeader
        back={<BackLink />}
        title={<span className="mono">{wallet.id}</span>}
        subtitle={
          <>
            Priority #{wallet.priorityRank} of {fmtInt(wallets.length)} by combined result · <StatusPill status={status} />
          </>
        }
        actions={
          existingCase ? (
            <Link to={`/cases/${existingCase.id}`} className="btn">
              Open {existingCase.id}
            </Link>
          ) : (
            <>
              {status === 'Under Review' ? (
                <button type="button" className="btn" onClick={() => markUnreviewed(wallet.id)}>
                  Mark unreviewed
                </button>
              ) : (
                <button type="button" className="btn" onClick={() => markUnderReview(wallet.id)}>
                  Mark under review
                </button>
              )}
              <button type="button" className="btn btn-primary" onClick={startCreate} disabled={!settings.confirmCreateCase && !transfers}>
                Create case
              </button>
            </>
          )
        }
      />

      <div className="metrics metrics-3">
        <div className="metric">
          <div className="metric-label">ML anomaly score</div>
          <div className="metric-value mono">{fmtScore(f.ml_anomaly_score)}</div>
          <div className="metric-sub">
            <PredictionText flagged={wallet.flagged} /> · rank {mlRank} of {fmtInt(wallets.length)}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Forensic evidence</div>
          <div className="metric-value metric-text">
            <EvidenceCell level={f.forensic_evidence_level} />
          </div>
          <div className="metric-sub">
            {f.forensic_rule_count} of {TOTAL_RULES} rules triggered
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Combined result</div>
          <div className="metric-value mono">{fmtScore(f.combined_score)}</div>
          <div className="metric-sub">
            {Math.round(f.ml_weight * 100)}% ML / {Math.round(f.forensic_weight * 100)}% forensic · prototype weights
          </div>
        </div>
      </div>

      <Panel title="Why was this wallet flagged?" className="why">
        <p className="why-headline">{headline}</p>
        <div className="grid-evidence">
          {/* ---------------- ML evidence ---------------- */}
          <div className="evidence-col">
            <h3>ML evidence</h3>
            <p>
              {wallet.flagged ? 'Anomalous behavior detected. ' : 'No anomalous behavior detected by the model. '}
              The Isolation Forest scored this wallet <b className="mono">{fmtScore(f.ml_anomaly_score)}</b>; the model treats scores above{' '}
              <span className="mono">{fmtScore(mlBoundary)}</span> as anomalous. Higher means the wallet is easier to separate from the rest of the
              population.
            </p>
            <ScoreScale wallets={wallets} score={f.ml_anomaly_score} boundary={mlBoundary} />

            <h4>Where this wallet ranks in the population</h4>
            {standout.length === 0 ? (
              <p className="muted">No feature places this wallet above the population 90th percentile.</p>
            ) : (
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th>Model feature</th>
                    <th className="num">Value</th>
                    <th className="num">Population position</th>
                  </tr>
                </thead>
                <tbody>
                  {standout.slice(0, 8).map((x) => (
                    <tr key={x.key}>
                      <td>{x.label}</td>
                      <td className="num mono">
                        {fmtNum(x.value, x.digits ?? 2)}
                        {x.unit ? <span className="unit"> {x.unit}</span> : null}
                      </td>
                      <td className="num mono">P{Math.round(x.pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="caveat">
              Isolation Forest does not attribute a score to individual features. This table only shows where the wallet&apos;s input values sit
              relative to the {fmtInt(wallets.length)} analysed wallets.
            </p>
          </div>

          {/* ---------------- Forensic evidence ---------------- */}
          <div className="evidence-col">
            <h3>Forensic evidence</h3>
            <p>
              {ruleNames.length === 0 ? (
                'No forensic rule was triggered for this wallet.'
              ) : (
                <>
                  {ruleNames.length === 1 ? '1 rule was' : `${ruleNames.length} rules were`} triggered out of {TOTAL_RULES}. Evidence level:{' '}
                  <b>{f.forensic_evidence_level}</b>.
                </>
              )}
            </p>
            {ruleNames.length > 0 && (
              <ul className="rule-list">
                {ruleNames.map((r, i) => (
                  <li key={r}>
                    <div className="rule-name">
                      {ruleLabel(r)} <span className="rule-tag">Rule triggered</span>
                    </div>
                    {sentences && <div className="rule-text">{sentences[i]}</div>}
                  </li>
                ))}
              </ul>
            )}
            {ruleNames.length > 0 && !sentences && <p className="rule-text">{f.forensic_findings}</p>}
            <p className="caveat">
              Rules compare each behaviour with this synthetic population (above the 90th percentile). They are behavioural indicators that require
              investigator review, not findings of wrongdoing.
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Behavioural profile" note="Wallet feature values against the analysed population. The bar marks the wallet’s position from lowest to highest.">
        <div className="profile">
          {FEATURE_GROUPS.map((g) => (
            <div key={g.title} className="profile-group">
              <h4>{g.title}</h4>
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th>Feature</th>
                    <th className="num">Value</th>
                    <th className="num">Median</th>
                    <th className="pos-col">Position</th>
                  </tr>
                </thead>
                <tbody>
                  {g.features.map((ft) => {
                    const v = wallet.features[ft.key];
                    const pct = percentileRank(population[ft.key], v);
                    return (
                      <tr key={ft.key}>
                        <td>
                          {ft.label}
                          {ruleFeatures.has(ft.key) && <span className="rule-tag rule-tag-inline">rule</span>}
                        </td>
                        <td className="num mono">
                          {fmtNum(v, ft.digits ?? 2)}
                          {ft.unit ? <span className="unit"> {ft.unit}</span> : null}
                        </td>
                        <td className="num mono muted">{fmtNum(median(population[ft.key]), ft.digits ?? 2)}</td>
                        <td className="pos-col">
                          <span className="pos-track" title={`Position ${Math.round(pct)} of 100 in the population`}>
                            <span className={`pos-mark${pct > HIGH_PCT ? ' hi' : ''}`} style={{ left: `${Math.min(pct, 100)}%` }} />
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Counterparty network"
        actions={
          <span className="panel-links">
            <Link to={`/network?view=network&wallet=${wallet.id}`} className="link">
              Explore network →
            </Link>
            <Link to={`/network?view=transactions&q=${wallet.id}`} className="link">
              All transactions →
            </Link>
          </span>
        }
        note={
          <>
            Direct counterparties ranked by number of transfers. Wallets that mostly send to {wallet.id} are on the left, wallets that mostly receive
            from it on the right. Arrows show the direction of the larger flow; a double arrow means value moved both ways. Select a counterparty to
            open its investigation.
          </>
        }
      >
        {graph ? (
          <>
            <WalletGraph model={graph} />
            <p className="graph-foot">
              {graph.hiddenCounterparties > 0
                ? `Showing the ${graph.nodes.length - graph.focusIds.length} most active of ${fmtInt(graph.totalCounterparties)} counterparties.`
                : `Showing all ${fmtInt(graph.totalCounterparties)} counterparties.`}{' '}
              Outlined wallets are ML-flagged.
            </p>
          </>
        ) : (
          <div className="viz-loading">Loading transactions…</div>
        )}
      </Panel>

      <Panel title="Case">
        {existingCase ? (
          <div className="case-summary">
            <div>
              <b className="mono">{existingCase.id}</b> · {existingCase.title}
            </div>
            <div className="muted">
              <Link to={`/cases/${existingCase.id}`}>Open case</Link> · Opened {fmtDateTime(existingCase.createdAt)} · {existingCase.status} · {fmtInt(existingCase.relatedTransactions.length)} related
              transactions attached
            </div>
          </div>
        ) : (
          <p className="muted">
            No case has been opened for this wallet. Flagged wallets are leads: whether to open a case is an investigator decision.
          </p>
        )}
      </Panel>

      {creating && (
        <CreateCaseDialog
          wallet={wallet}
          transfers={transfers}
          onCancel={() => setCreating(false)}
          onCreate={(input) => {
            const c = createCase({ wallet, ...input });
            setCreating(false);
            navigate('/cases', { state: { created: c.id } });
            return c;
          }}
        />
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/anomalies" className="back">
      <ArrowLeft size={14} aria-hidden="true" /> Anomalies
    </Link>
  );
}

function headlineFor(w: WalletRecord): string {
  const n = w.fusion.forensic_rule_count;
  if (w.flagged && n > 0)
    return `Flagged for investigation: the ML model detected anomalous behavior and ${n} forensic rule${n === 1 ? ' was' : 's were'} triggered. Requires investigator review.`;
  if (w.flagged)
    return 'Flagged for investigation by the ML model. No forensic rule was triggered, so there is no rule-based corroboration. Requires investigator review.';
  if (n > 0)
    return `Not flagged by the ML model, but ${n} forensic rule${n === 1 ? ' was' : 's were'} triggered. Requires investigator review if the indicators are of interest.`;
  return 'Neither the ML model nor the forensic rules flagged this wallet. It is shown for reference.';
}

/** Where the wallet's ML score sits between the lowest and highest score in the population. */
function ScoreScale({ wallets, score, boundary }: { wallets: WalletRecord[]; score: number; boundary: number }) {
  const { min, max } = useMemo(() => {
    const s = wallets.map((w) => w.fusion.ml_anomaly_score);
    return { min: Math.min(...s), max: Math.max(...s) };
  }, [wallets]);
  const at = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="scale" role="img" aria-label={`Score ${fmtScore(score)} on a scale from ${fmtScore(min)} to ${fmtScore(max)}; model boundary ${fmtScore(boundary)}`}>
      <div className="scale-track">
        <span className="scale-boundary" style={{ left: at(boundary) }} />
        <span className="scale-fill" style={{ width: at(score) }} />
        <span className="scale-marker" style={{ left: at(score) }} />
      </div>
      <div className="scale-labels">
        <span className="mono">{fmtScore(min)}</span>
        <span className="scale-boundary-label mono" style={{ left: at(boundary) }}>
          boundary {fmtScore(boundary)}
        </span>
        <span className="mono">{fmtScore(max)}</span>
      </div>
    </div>
  );
}
