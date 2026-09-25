import Papa from 'papaparse';
import fusionCsv from '../../../data/fusion_results.csv?raw';
import featuresCsv from '../../../data/wallet_behavior_features.csv?raw';
import type { FeatureKey, FeatureRow, FusionRow, LeadInfo, Transfer, WalletRecord } from './types';

function parse<T>(csv: string): T[] {
  const res = Papa.parse<T>(csv, { header: true, dynamicTyping: true, skipEmptyLines: true });
  return res.data;
}

export interface Dataset {
  wallets: WalletRecord[];
  byId: Map<string, WalletRecord>;
  /** Sorted ascending feature values across all wallets, for percentile positions. */
  population: Record<FeatureKey, number[]>;
  /** Score separating the model's 'Normal' and 'Anomalous' predictions (derived from the CSV). */
  mlBoundary: number;
  issues: string[];
  /** Where the records came from: the local backend, or the CSV files bundled with the app. */
  origin: 'api' | 'csv';
}

export function loadDataset(): Dataset {
  return assembleDataset(parse<FusionRow>(fusionCsv), parse<FeatureRow>(featuresCsv), 'csv');
}

/**
 * Joins fusion and feature rows into the per-wallet view every screen uses. `extra` carries what only the backend
 * knows (its own priority rank and the lead / review state); without it the rank is derived from combined_score.
 */
export function assembleDataset(
  fusion: FusionRow[],
  features: FeatureRow[],
  origin: 'api' | 'csv',
  extra?: { ranks: Map<string, number>; leads: Map<string, LeadInfo> },
): Dataset {
  const featById = new Map(features.map((f) => [f.wallet_address, f]));

  const issues: string[] = [];
  const ranked = [...fusion].sort((a, b) => b.combined_score - a.combined_score);
  const rank = new Map(ranked.map((r, i) => [r.wallet_address, i + 1]));

  const wallets: WalletRecord[] = [];
  for (const f of fusion) {
    const feat = featById.get(f.wallet_address);
    if (!feat) {
      issues.push(`${f.wallet_address}: no row in wallet_behavior_features.csv`);
      continue;
    }
    wallets.push({
      id: f.wallet_address,
      fusion: f,
      features: feat,
      priorityRank: extra?.ranks.get(f.wallet_address) ?? rank.get(f.wallet_address)!,
      flagged: f.ml_anomaly_prediction === 'Anomalous',
      lead: extra?.leads.get(f.wallet_address),
    });
  }
  const invalid = fusion.filter((f) => !f.valid).length;
  if (invalid) issues.push(`${invalid} wallet(s) have incomplete fusion evidence (see fusion_results.csv errors).`);

  const keys = Object.keys(features[0]).filter((k) => k !== 'wallet_address') as FeatureKey[];
  const population = {} as Record<FeatureKey, number[]>;
  for (const k of keys) population[k] = features.map((f) => f[k] as number).sort((a, b) => a - b);

  const maxNormal = Math.max(...wallets.filter((w) => !w.flagged).map((w) => w.fusion.ml_anomaly_score));
  const minAnom = Math.min(...wallets.filter((w) => w.flagged).map((w) => w.fusion.ml_anomaly_score));
  const mlBoundary = (maxNormal + minAnom) / 2;

  return { wallets, byId: new Map(wallets.map((w) => [w.id, w])), population, mlBoundary, issues, origin };
}

/** Percent of wallets whose value is at or below `value` (descriptive position, not a score). */
export function percentileRank(sorted: number[], value: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= value) lo = mid + 1;
    else hi = mid;
  }
  return (lo / sorted.length) * 100;
}

export function median(sorted: number[]): number {
  const n = sorted.length;
  return n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
}

// --- raw transactions (largest file; loaded lazily so the first screen does not wait on it) ---
let transfersPromise: Promise<Transfer[]> | null = null;

export function loadTransfers(): Promise<Transfer[]> {
  transfersPromise ??= import('../../../dataset/synthetic_bitcoin_transactions.csv?raw').then((m) => {
    const rows = parse<{
      timestamp: string;
      wallet_address: string;
      amount_btc: number;
      direction: string;
      input_count: number;
      output_count: number;
      counterparty_wallet: string;
    }>(m.default);
    // Every transfer appears twice (Outgoing on the sender, Incoming on the receiver): keep one.
    const out: Transfer[] = [];
    rows.forEach((r, i) => {
      if (r.direction !== 'Outgoing') return;
      out.push({
        ts: Date.parse(r.timestamp.replace(' ', 'T') + 'Z'),
        from: r.wallet_address,
        to: r.counterparty_wallet,
        amountBtc: r.amount_btc,
        inputCount: r.input_count,
        outputCount: r.output_count,
        row: i + 2, // +1 for the header line, +1 for 1-based numbering
      });
    });
    return out;
  });
  return transfersPromise;
}
