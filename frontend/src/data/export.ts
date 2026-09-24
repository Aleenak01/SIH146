import type { ReviewStatus } from '../state/cases';
import type { Transfer, WalletRecord } from './types';

// Local exports: everything is built in the browser from data already on screen and saved as a
// download. Nothing is sent anywhere.

type Cell = string | number | boolean | null | undefined;

function csvCell(v: Cell): string {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  return [columns, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

function download(filename: string, mime: string, content: string) {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// UTF-8 BOM so Excel opens the CSV with the right encoding.
export const downloadCsv = (filename: string, columns: string[], rows: Cell[][]) =>
  download(filename, 'text/csv', '﻿' + toCsv(columns, rows));

export const downloadJson = (filename: string, data: unknown) =>
  download(filename, 'application/json', JSON.stringify(data, null, 2));

/** yyyymmdd-hhmm, for filenames. */
export function stamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

// --- shared row builders --------------------------------------------------------------------

export const WALLET_COLUMNS = [
  'wallet_address',
  'ml_anomaly_score',
  'ml_anomaly_prediction',
  'forensic_rule_count',
  'forensic_evidence_level',
  'forensic_triggered_rules',
  'combined_score',
  'priority_rank',
  'review_status',
];

/** Anomaly / investigation results exactly as in the pipeline output, plus rank and review status. */
export const walletRows = (wallets: WalletRecord[], statusOf: (id: string) => ReviewStatus): Cell[][] =>
  wallets.map((w) => [
    w.id,
    w.fusion.ml_anomaly_score,
    w.fusion.ml_anomaly_prediction,
    w.fusion.forensic_rule_count,
    w.fusion.forensic_evidence_level,
    w.fusion.forensic_triggered_rules,
    w.fusion.combined_score,
    w.priorityRank,
    statusOf(w.id),
  ]);

export const TRANSFER_COLUMNS = ['timestamp_utc', 'sender', 'receiver', 'amount_btc', 'input_count', 'output_count', 'source_row'];

export const transferRows = (rows: Transfer[]): Cell[][] =>
  rows.map((t) => [new Date(t.ts).toISOString().slice(0, 19).replace('T', ' '), t.from, t.to, t.amountBtc, t.inputCount, t.outputCount, t.row]);
