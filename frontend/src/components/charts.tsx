import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Transfer, WalletRecord } from '../data/types';
import { fmtDate, fmtInt, fmtScore } from '../format';
import { Legend, Panel } from './bits';

// All chart colours are CSS variables (--viz-*) defined per theme in tokens.css, so the same
// component renders correctly in light and dark with no JS colour logic.

const AXIS = { fontSize: 11, fill: 'var(--text-3)' } as const;
const GRID = 'var(--grid)';

function ViewToggle({ table, setTable }: { table: boolean; setTable: (v: boolean) => void }) {
  return (
    <div className="seg" role="group" aria-label="Chart or table view">
      <button type="button" className={!table ? 'on' : ''} onClick={() => setTable(false)}>
        Chart
      </button>
      <button type="button" className={table ? 'on' : ''} onClick={() => setTable(true)}>
        Table
      </button>
    </div>
  );
}

function TipBox({ title, rows }: { title: string; rows: { label: string; value: string; swatch?: string }[] }) {
  return (
    <div className="viz-tip">
      <div className="viz-tip-title">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="viz-tip-row">
          <span>
            {r.swatch && <span className={`swatch ${r.swatch}`} aria-hidden="true" />}
            {r.label}
          </span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// 1. Anomaly score distribution
//    Question: how far do the flagged wallets sit from the rest of the population?
//    Bins are aligned to the model's own decision boundary (derived from the CSV), so every bin
//    is entirely 'Normal' or entirely 'Anomalous' and the split is visible without extra marks.
// ---------------------------------------------------------------------------------------------
interface ScoreBin {
  lo: number;
  hi: number;
  label: string;
  normal: number;
  anomalous: number;
}

const BIN_WIDTH = 0.025;

function binScores(wallets: WalletRecord[], boundary: number): ScoreBin[] {
  const scores = wallets.map((w) => w.fusion.ml_anomaly_score);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const start = boundary - Math.ceil((boundary - min) / BIN_WIDTH) * BIN_WIDTH;
  const n = Math.round((boundary + Math.ceil((max - boundary) / BIN_WIDTH) * BIN_WIDTH - start) / BIN_WIDTH);
  const bins: ScoreBin[] = Array.from({ length: n }, (_, i) => {
    const lo = start + i * BIN_WIDTH;
    return { lo, hi: lo + BIN_WIDTH, label: lo.toFixed(2), normal: 0, anomalous: 0 };
  });
  for (const w of wallets) {
    const i = Math.min(n - 1, Math.floor((w.fusion.ml_anomaly_score - start) / BIN_WIDTH));
    if (w.flagged) bins[i].anomalous++;
    else bins[i].normal++;
  }
  return bins;
}

export function ScoreDistribution({ wallets, boundary }: { wallets: WalletRecord[]; boundary: number }) {
  const [table, setTable] = useState(false);
  const bins = useMemo(() => binScores(wallets, boundary), [wallets, boundary]);
  const flagged = wallets.filter((w) => w.flagged).length;

  return (
    <Panel
      title="Anomaly score distribution"
      note={
        <>
          Wallets by ML anomaly score (bins of {BIN_WIDTH}). {fmtInt(flagged)} of {fmtInt(wallets.length)} score above the model boundary
          ({fmtScore(boundary)}); the flagged share reflects the model&apos;s contamination setting, not a measured rate.
        </>
      }
      actions={<ViewToggle table={table} setTable={setTable} />}
    >
      <Legend
        items={[
          { label: 'Predicted Normal', className: 'sw-neutral' },
          { label: 'Predicted Anomalous', className: 'sw-flag' },
        ]}
      />
      {table ? (
        <div className="viz-table">
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Score range</th>
                <th className="num">Normal</th>
                <th className="num">Anomalous</th>
              </tr>
            </thead>
            <tbody>
              {bins.map((b) => (
                <tr key={b.label}>
                  <td className="mono">
                    {b.lo.toFixed(3)} – {b.hi.toFixed(3)}
                  </td>
                  <td className="num mono">{b.normal}</td>
                  <td className="num mono">{b.anomalous}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="viz-frame" role="img" aria-label="Histogram of ML anomaly scores by predicted class">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={bins} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} interval={1} tickMargin={6} height={26} />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
              <Tooltip
                cursor={{ fill: 'var(--hover)' }}
                content={({ active, payload }) => {
                  const b = payload?.[0]?.payload as ScoreBin | undefined;
                  if (!active || !b) return null;
                  return (
                    <TipBox
                      title={`Score ${b.lo.toFixed(3)} – ${b.hi.toFixed(3)}`}
                      rows={[
                        { label: 'Predicted Normal', value: fmtInt(b.normal), swatch: 'sw-neutral' },
                        { label: 'Predicted Anomalous', value: fmtInt(b.anomalous), swatch: 'sw-flag' },
                      ]}
                    />
                  );
                }}
              />
              <Bar dataKey="normal" stackId="s" fill="var(--viz-neutral)" radius={[2, 2, 0, 0]} maxBarSize={24} isAnimationActive={false} />
              <Bar dataKey="anomalous" stackId="s" fill="var(--viz-flag)" radius={[2, 2, 0, 0]} maxBarSize={24} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------
// 2. Weekly transfer activity
//    Question: is activity involving flagged wallets steady, or concentrated in particular weeks?
//    Uses the raw transactions (mirrored Outgoing/Incoming rows collapsed to one transfer each).
//    Weeks are consecutive 7-day periods from the first day in the data; a trailing partial
//    period is left out so the last bar is not artificially short.
// ---------------------------------------------------------------------------------------------
interface WeekBin {
  start: number;
  label: string;
  flagged: number;
  other: number;
}

const DAY = 86_400_000;

function binWeeks(transfers: Transfer[], flaggedIds: Set<string>) {
  let first = Infinity;
  let last = -Infinity;
  for (const t of transfers) {
    if (t.ts < first) first = t.ts;
    if (t.ts > last) last = t.ts;
  }
  const origin = Math.floor(first / DAY) * DAY;
  const n = Math.floor((last - origin + 1) / (7 * DAY));
  const bins: WeekBin[] = Array.from({ length: n }, (_, i) => {
    const start = origin + i * 7 * DAY;
    return {
      start,
      label: new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(start),
      flagged: 0,
      other: 0,
    };
  });
  let omitted = 0;
  for (const t of transfers) {
    const i = Math.floor((t.ts - origin) / (7 * DAY));
    if (i >= n) {
      omitted++;
      continue;
    }
    if (flaggedIds.has(t.from) || flaggedIds.has(t.to)) bins[i].flagged++;
    else bins[i].other++;
  }
  return { bins, omitted, origin };
}

export function ActivityTrend({ transfers, wallets }: { transfers: Transfer[] | null; wallets: WalletRecord[] }) {
  const [table, setTable] = useState(false);
  const navigate = useNavigate();
  // Selecting a week lists that week's transfers in Transactions / Network.
  const openWeek = (d: { payload?: WeekBin }) => {
    if (!d.payload) return;
    const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
    navigate(`/network?view=transactions&start=${day(d.payload.start)}&end=${day(d.payload.start + 6 * DAY)}`);
  };
  const result = useMemo(
    () => (transfers ? binWeeks(transfers, new Set(wallets.filter((w) => w.flagged).map((w) => w.id))) : null),
    [transfers, wallets],
  );

  let body: ReactNode;
  if (!result) {
    body = <div className="viz-loading">Loading transactions…</div>;
  } else if (table) {
    body = (
      <div className="viz-table">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Week starting</th>
              <th className="num">Involving flagged</th>
              <th className="num">Other</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {result.bins.map((b) => (
              <tr key={b.start}>
                <td className="mono">{fmtDate(b.start)}</td>
                <td className="num mono">{b.flagged}</td>
                <td className="num mono">{b.other}</td>
                <td className="num mono">{b.flagged + b.other}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  } else {
    body = (
      <div className="viz-frame viz-click" role="img" aria-label="Weekly transfer counts split by involvement of a flagged wallet. Select a week to list its transfers.">
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={result.bins} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} interval={7} tickMargin={6} height={26} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={36} allowDecimals={false} />
            <Tooltip
              cursor={{ fill: 'var(--hover)' }}
              content={({ active, payload }) => {
                const b = payload?.[0]?.payload as WeekBin | undefined;
                if (!active || !b) return null;
                return (
                  <TipBox
                    title={`Week from ${fmtDate(b.start)}`}
                    rows={[
                      { label: 'Involving a flagged wallet', value: fmtInt(b.flagged), swatch: 'sw-flag' },
                      { label: 'Other transfers', value: fmtInt(b.other), swatch: 'sw-neutral' },
                      { label: 'Total', value: fmtInt(b.flagged + b.other) },
                    ]}
                  />
                );
              }}
            />
            <Bar dataKey="other" stackId="w" fill="var(--viz-neutral)" maxBarSize={14} isAnimationActive={false} onClick={openWeek} />
            <Bar dataKey="flagged" stackId="w" fill="var(--viz-flag)" radius={[2, 2, 0, 0]} maxBarSize={14} isAnimationActive={false} onClick={openWeek} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <Panel
      title="Weekly transfer activity"
      note={
        result
          ? `${fmtInt(transfers!.length - result.omitted)} transfers in ${result.bins.length} full 7-day periods from ${fmtDate(result.origin)}. A transfer counts as flagged-related when either party is ML-flagged. Select a week to list its transfers.`
          : 'Transfers per week, split by whether a flagged wallet is involved.'
      }
      actions={<ViewToggle table={table} setTable={setTable} />}
    >
      <Legend
        items={[
          { label: 'Involving a flagged wallet', className: 'sw-flag' },
          { label: 'Other transfers', className: 'sw-neutral' },
        ]}
      />
      {body}
    </Panel>
  );
}
