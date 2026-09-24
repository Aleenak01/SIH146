import { useMemo } from 'react';
import pipeline from 'virtual:pipeline-info';
import { Download } from 'lucide-react';
import { PageHeader, Panel } from '../components/bits';
import { WALLET_COLUMNS, downloadCsv, downloadJson, stamp, walletRows, TRANSFER_COLUMNS, transferRows } from '../data/export';
import { TOTAL_RULES } from '../data/rules';
import { fmtDateTime, fmtDate, fmtInt, fmtPct, fmtScore } from '../format';
import { useCases } from '../state/cases';
import { useConfirm } from '../state/confirm';
import { useDataset, useTransfers } from '../state/data';
import { useSettings } from '../state/settings';
import { useTheme, type ThemePreference } from '../state/theme';

const fmtBytes = (n: number) => (n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

export function Settings() {
  return (
    <div className="page">
      <PageHeader
        title="Settings"
        subtitle="Preferences for this browser, and a description of the local prototype configuration. Nothing here changes the analysis pipeline."
      />
      <Appearance />
      <Confirmations />
      <Configuration />
      <PipelineOutputs />
      <ExportData />
      <LocalData />
    </div>
  );
}

// -------------------------------------------------------------------------------------------
function Appearance() {
  const { preference, theme, setPreference } = useTheme();
  const options: { value: ThemePreference; label: string; hint: string }[] = [
    { value: 'light', label: 'Light', hint: 'Always use the light theme' },
    { value: 'dark', label: 'Dark', hint: 'Always use the dark theme' },
    { value: 'system', label: 'System', hint: 'Follow this device’s appearance setting' },
  ];
  return (
    <Panel title="Appearance" note={`Currently showing the ${theme} theme. The sidebar toggle sets the same preference.`}>
      <div className="option-list" role="radiogroup" aria-label="Theme preference">
        {options.map((o) => (
          <label key={o.value} className="option">
            <input type="radio" name="theme" checked={preference === o.value} onChange={() => setPreference(o.value)} />
            <span>
              <b>{o.label}</b>
              <em>{o.hint}</em>
            </span>
          </label>
        ))}
      </div>
    </Panel>
  );
}

function Confirmations() {
  const { settings, update, reset } = useSettings();
  const rows: { key: keyof typeof settings; label: string; hint: string }[] = [
    { key: 'confirmCreateCase', label: 'Confirm before creating a case', hint: 'Shows the case details for review. If off, Create case opens the case immediately with a default title.' },
    { key: 'confirmStatusChange', label: 'Confirm before changing a case status', hint: 'Status changes are recorded in the case history.' },
    { key: 'confirmExport', label: 'Confirm before exporting data', hint: 'Exports are saved to this computer only. Off by default.' },
  ];
  return (
    <Panel title="Confirmations" note="Choose which investigator actions ask for confirmation. Clearing local data always asks.">
      <div className="option-list">
        {rows.map((r) => (
          <label key={r.key} className="option">
            <input type="checkbox" checked={settings[r.key]} onChange={(e) => update({ [r.key]: e.target.checked })} />
            <span>
              <b>{r.label}</b>
              <em>{r.hint}</em>
            </span>
          </label>
        ))}
      </div>
      <p className="settings-actions">
        <button type="button" className="btn btn-sm" onClick={reset}>
          Restore defaults
        </button>
      </p>
    </Panel>
  );
}

// -------------------------------------------------------------------------------------------
function Configuration() {
  const { wallets, mlBoundary, population } = useDataset();
  const { transfers } = useTransfers();
  const m = pipeline.model;

  const s = useMemo(() => {
    const flagged = wallets.filter((w) => w.flagged).length;
    const scores = wallets.map((w) => w.fusion.ml_anomaly_score);
    const f0 = wallets[0].fusion;
    return {
      flagged,
      records: wallets.reduce((n, w) => n + w.features.transaction_count, 0),
      transfers: wallets.reduce((n, w) => n + w.features.outgoing_count, 0),
      withRules: wallets.filter((w) => w.fusion.forensic_rule_count >= 1).length,
      fused: wallets.filter((w) => w.fusion.valid).length,
      min: Math.min(...scores),
      max: Math.max(...scores),
      mlWeight: f0.ml_weight,
      forensicWeight: f0.forensic_weight,
      features: Object.keys(population).length,
    };
  }, [wallets, population]);

  const period = transfers ? `${fmtDate(Math.min(...transfers.map((t) => t.ts)))} – ${fmtDate(Math.max(...transfers.map((t) => t.ts)))}` : 'Loading…';
  const modelParams = [
    m.nEstimators !== null ? `${m.nEstimators} trees` : null,
    m.contamination !== null ? `contamination ${m.contamination}` : null,
    m.randomState !== null ? `random state ${m.randomState}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Panel
      title="Model, data and pipeline"
      note="The local prototype configuration. Counts are derived from the pipeline output files; model settings are read from ml/anomaly_detection.py."
    >
      <div className="kv-grid">
        <KV group="Model">
          <Row label="Model" value="Isolation Forest (scikit-learn)" />
          <Row label="Type" value="Unsupervised anomaly detection; no labels are used" />
          <Row label="Settings" value={modelParams || 'Not found in ml/anomaly_detection.py'} />
          <Row label="Flag threshold" value={`Scores above ${fmtScore(mlBoundary)} are predicted Anomalous (set by the contamination assumption, not measured prevalence)`} />
          <Row label="Forensic rules" value={`${TOTAL_RULES} behavioural rules, thresholds at the population 90th percentile`} />
          <Row label="Combined result" value={`${Math.round(s.mlWeight * 100)}% ML score + ${Math.round(s.forensicWeight * 100)}% forensic rule share (prototype weights, not validated)`} />
          <Row label="Detection method" value="Statistical model and rules only. No language model is involved in detection." />
        </KV>
        <KV group="Data">
          <Row label="Dataset type" value="Synthetic Bitcoin transaction and network metadata. Not real blockchain data." />
          <Row label="Wallets analysed" value={fmtInt(wallets.length)} />
          <Row label="Transaction records" value={`${fmtInt(s.records)} (${fmtInt(s.transfers)} distinct transfers; each appears once per party)`} />
          <Row label="Transaction period" value={period} />
          <Row label="Engineered features" value={`${s.features} per wallet`} />
          <Row label="Flagged wallets" value={`${fmtInt(s.flagged)} (${fmtPct((s.flagged / wallets.length) * 100)}) predicted Anomalous`} />
          <Row label="Wallets with rules" value={`${fmtInt(s.withRules)} trigger at least one rule`} />
          <Row label="ML score range" value={`${fmtScore(s.min)} – ${fmtScore(s.max)}`} />
          <Row label="Fusion status" value={`${fmtInt(s.fused)} of ${fmtInt(wallets.length)} wallets fully fused`} />
        </KV>
      </div>
    </Panel>
  );
}

function KV({ group, children }: { group: string; children: React.ReactNode }) {
  return (
    <div className="kv">
      <h3>{group}</h3>
      <dl>{children}</dl>
    </div>
  );
}
function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

// -------------------------------------------------------------------------------------------
function PipelineOutputs() {
  const files = pipeline.files;
  const derived = files.filter((f) => f.stage !== 'Raw data' && f.exists);
  const walletRowCounts = new Set(derived.map((f) => f.rows));
  const consistent = derived.length === files.length - 1 && walletRowCounts.size === 1;
  const latest = files.filter((f) => f.exists && f.stage !== 'Raw data' && f.modified).map((f) => f.modified!).sort().at(-1);

  return (
    <Panel
      title="Pipeline outputs"
      note={
        <>
          Read from disk when the frontend started (or was built), on {fmtDateTime(pipeline.generatedAt)}. The pipeline runs outside this app: run the scripts in <span className="mono">ml/</span> and reload
          to refresh. {latest ? `Most recent output written ${fmtDateTime(latest)}.` : ''}
        </>
      }
    >
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Output file</th>
              <th>Produced by</th>
              <th className="num">Rows</th>
              <th className="num">Columns</th>
              <th className="num">Size</th>
              <th>Last modified</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.file}>
                <td>{f.stage}</td>
                <td className="mono">{f.file}</td>
                <td className="mono muted">{f.producer}</td>
                <td className="num mono">{f.exists ? fmtInt(f.rows) : '—'}</td>
                <td className="num mono">{f.exists ? f.columns : '—'}</td>
                <td className="num mono">{f.exists ? fmtBytes(f.bytes) : '—'}</td>
                <td>{f.modified ? fmtDateTime(f.modified) : '—'}</td>
                <td>
                  <span className={`status ${f.exists ? 'status-case' : 'status-review'}`}>
                    <span className="status-dot" aria-hidden="true" />
                    {f.exists ? 'Present' : 'Missing'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="graph-foot">
        {consistent
          ? `Feature, anomaly, forensic and fusion outputs each have ${fmtInt([...walletRowCounts][0])} rows (one per wallet).`
          : 'The wallet-level outputs do not all have the same number of rows, or one is missing. Re-run the pipeline in order.'}
      </p>
    </Panel>
  );
}

// -------------------------------------------------------------------------------------------
function ExportData() {
  const { wallets } = useDataset();
  const { transfers } = useTransfers();
  const { statusOf, cases } = useCases();
  const { guard } = useConfirm();

  const run = async (title: string, body: string, label: string, action: () => void) => {
    if (await guard('confirmExport', { title, body, confirmLabel: label })) action();
  };

  const items: { name: string; detail: string; format: string; disabled?: boolean; onClick: () => void }[] = [
    {
      name: 'Anomaly results',
      detail: `All ${fmtInt(wallets.length)} wallets: ML score and prediction, forensic evidence and rules, combined result and rank, review status.`,
      format: 'CSV',
      onClick: () =>
        run('Export anomaly results', `Save all ${fmtInt(wallets.length)} wallets as a CSV file on this computer.`, 'Export CSV', () =>
          downloadCsv(`sih146-anomaly-results-${stamp()}.csv`, WALLET_COLUMNS, walletRows([...wallets].sort((a, b) => a.priorityRank - b.priorityRank), statusOf)),
        ),
    },
    {
      name: 'Flagged wallets',
      detail: `The ${fmtInt(wallets.filter((w) => w.flagged).length)} wallets the ML model predicts as Anomalous, in priority order.`,
      format: 'CSV',
      onClick: () =>
        run('Export flagged wallets', 'Save the ML-flagged wallets as a CSV file on this computer.', 'Export CSV', () =>
          downloadCsv(
            `sih146-flagged-wallets-${stamp()}.csv`,
            WALLET_COLUMNS,
            walletRows(wallets.filter((w) => w.flagged).sort((a, b) => a.priorityRank - b.priorityRank), statusOf),
          ),
        ),
    },
    {
      name: 'Transactions',
      detail: transfers ? `All ${fmtInt(transfers.length)} transfers in the dataset. Use Transactions / Network to export a filtered selection.` : 'Loading…',
      format: 'CSV',
      disabled: !transfers,
      onClick: () =>
        transfers &&
        run('Export transactions', `Save all ${fmtInt(transfers.length)} transfers as a CSV file on this computer.`, 'Export CSV', () =>
          downloadCsv(`sih146-transactions-${stamp()}.csv`, TRANSFER_COLUMNS, transferRows(transfers)),
        ),
    },
    {
      name: 'Cases',
      detail: `${fmtInt(cases.length)} case${cases.length === 1 ? '' : 's'} with wallets, evidence snapshots, related transactions, notes and history.`,
      format: 'JSON',
      disabled: cases.length === 0,
      onClick: () =>
        run('Export cases', `Save ${fmtInt(cases.length)} case(s) as a JSON file on this computer.`, 'Export JSON', () =>
          downloadJson(`sih146-cases-${stamp()}.json`, { exportedAt: new Date().toISOString(), source: 'SIH146 frontend prototype, synthetic data', cases }),
        ),
    },
  ];

  return (
    <Panel title="Export data" note="Files are created in this browser from the data shown in the app and saved to your computer. Nothing is uploaded.">
      <div className="table-wrap">
        <table className="data-table">
          <tbody>
            {items.map((i) => (
              <tr key={i.name}>
                <th scope="row" className="export-name">{i.name}</th>
                <td className="wrap muted">{i.detail}</td>
                <td className="mono">{i.format}</td>
                <td className="num">
                  <button type="button" className="btn btn-sm" onClick={i.onClick} disabled={i.disabled}>
                    <Download size={13} aria-hidden="true" /> Export
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="graph-foot">
        Full investigation backup (cases and review statuses) is under Local investigation data below.
      </p>
    </Panel>
  );
}

// -------------------------------------------------------------------------------------------
function LocalData() {
  const { cases, snapshot, clearAll } = useCases();
  const { confirm, guard } = useConfirm();
  const reviewCount = Object.values(snapshot().reviewStatus).filter((s) => s !== 'Unreviewed').length;

  const backup = async () => {
    if (
      await guard('confirmExport', {
        title: 'Export investigation data',
        body: 'Save all cases and wallet review statuses as a JSON file on this computer.',
        confirmLabel: 'Export JSON',
      })
    )
      downloadJson(`sih146-investigation-data-${stamp()}.json`, {
        exportedAt: new Date().toISOString(),
        source: 'SIH146 frontend prototype, synthetic data',
        ...snapshot(),
      });
  };

  const clear = async () => {
    const ok = await confirm({
      title: 'Clear local investigation data',
      body: (
        <>
          This permanently removes <b>{fmtInt(cases.length)}</b> case{cases.length === 1 ? '' : 's'} and <b>{fmtInt(reviewCount)}</b> wallet review status
          {reviewCount === 1 ? '' : 'es'} from this browser. The pipeline output and transaction data are not affected. Export first if you need a copy.
        </>
      ),
      confirmLabel: 'Clear data',
      danger: true,
    });
    if (ok) clearAll();
  };

  return (
    <Panel
      title="Local investigation data"
      note="Cases, notes, history and wallet review statuses are kept in this browser's local storage, not in the project files. They are not shared with other browsers or devices."
    >
      <div className="kv">
        <dl>
          <Row label="Cases" value={fmtInt(cases.length)} />
          <Row label="Wallets marked under review or with a case" value={fmtInt(reviewCount + cases.reduce((n, c) => n + c.wallets.length, 0))} />
        </dl>
      </div>
      <p className="settings-actions">
        <button type="button" className="btn btn-sm" onClick={backup} disabled={cases.length === 0 && reviewCount === 0}>
          <Download size={13} aria-hidden="true" /> Export investigation data (JSON)
        </button>
        <button type="button" className="btn btn-sm btn-danger-outline" onClick={clear} disabled={cases.length === 0 && reviewCount === 0}>
          Clear local data…
        </button>
      </p>
    </Panel>
  );
}
