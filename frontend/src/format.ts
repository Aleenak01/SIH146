const int = new Intl.NumberFormat('en-US');

export const fmtInt = (n: number) => int.format(n);
export const fmtScore = (n: number) => n.toFixed(3);
export const fmtPct = (n: number, digits = 1) => `${n.toFixed(digits)}%`;

/** Numbers of very different magnitude (BTC totals, hours, ratios) in a compact, stable form. */
export function fmtNum(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return '—';
  if (digits === 0) return int.format(Math.round(n));
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export const fmtDate = (ms: number) => dateFmt.format(ms);
export const fmtDateTime = (iso: string) => dateTimeFmt.format(new Date(iso));

export function fmtDuration(hours: number): string {
  if (hours < 48) return `${fmtNum(hours, 1)} h`;
  return `${fmtNum(hours / 24, 1)} d`;
}

/** Transaction timestamps are naive in the dataset and treated as UTC: YYYY-MM-DD HH:MM:SS. */
export const fmtTs = (ms: number) => new Date(ms).toISOString().slice(0, 19).replace('T', ' ');

export const fmtBtc = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 8 });
