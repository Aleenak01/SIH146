import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from 'lucide-react';
import type { Transfer } from '../data/types';
import { fmtBtc, fmtInt, fmtTs } from '../format';
import { useTransfers } from '../state/data';

type SortKey = 'time' | 'from' | 'to' | 'amount';

const COLS: { key: SortKey | null; label: string; align?: 'right' }[] = [
  { key: 'time', label: 'Timestamp (UTC)' },
  { key: 'from', label: 'Sender' },
  { key: 'to', label: 'Receiver' },
  { key: 'amount', label: 'Amount (BTC)', align: 'right' },
  { key: null, label: 'In / out', align: 'right' },
  { key: null, label: 'Source row', align: 'right' },
];

/**
 * Transfers from the synthetic dataset. The raw file has no transaction ID, so rows are identified
 * by their timestamp, parties and source-file row number. Clicking a row opens its full record.
 * Used by Transactions / Network and by Case detail.
 */
export function TransactionTable({
  rows,
  flaggedIds,
  pageSize = 25,
  emptyText = 'No transactions match.',
}: {
  rows: Transfer[];
  flaggedIds: Set<string>;
  pageSize?: number;
  emptyText?: string;
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'time', dir: 'desc' });
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);

  const sorted = useMemo(() => {
    const dir = sort.dir === 'asc' ? 1 : -1;
    const val = (t: Transfer) => (sort.key === 'time' ? t.ts : sort.key === 'amount' ? t.amountBtc : sort.key === 'from' ? t.from : t.to);
    return [...rows].sort((a, b) => {
      const x = val(a);
      const y = val(b);
      const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number);
      return c * dir || a.ts - b.ts;
    });
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = sorted.slice(current * pageSize, (current + 1) * pageSize);

  const onSort = (key: SortKey) => {
    setPage(0);
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'time' || key === 'amount' ? 'desc' : 'asc' }));
  };

  // A transfer is identified by its source row where present (older saved cases may lack it).
  const rowKey = (t: Transfer) => `${t.row ?? 'x'}:${t.ts}:${t.from}:${t.to}`;

  const Wallet = ({ id }: { id: string }) => (
    <Link to={`/wallets/${id}`} className="mono wallet-link" onClick={(e) => e.stopPropagation()} title="Open wallet investigation">
      {id}
      {flaggedIds.has(id) && <span className="flag-dot" title="Flagged by the ML model" aria-label="ML-flagged" />}
    </Link>
  );

  return (
    <div className="tx-table">
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th className="chev-col" aria-label="Expand" />
              {COLS.map((c) => {
                const active = c.key && sort.key === c.key;
                return (
                  <th key={c.label} className={c.align === 'right' ? 'num' : ''} aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                    {c.key ? (
                      <button type="button" className="th-btn" onClick={() => onSort(c.key!)}>
                        {c.label}
                        {active && (sort.dir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((t) => {
              const k = rowKey(t);
              const isOpen = open === k;
              return (
                <Fragment key={k}>
                  <tr className={`row-link${isOpen ? ' row-open' : ''}`} onClick={() => setOpen(isOpen ? null : k)} aria-expanded={isOpen}>
                    <td className="chev-col">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                    <td className="mono">{fmtTs(t.ts)}</td>
                    <td>
                      <Wallet id={t.from} />
                    </td>
                    <td>
                      <Wallet id={t.to} />
                    </td>
                    <td className="num mono">{fmtBtc(t.amountBtc)}</td>
                    <td className="num mono">
                      {t.inputCount ?? '—'} / {t.outputCount ?? '—'}
                    </td>
                    <td className="num mono muted">{t.row ?? '—'}</td>
                  </tr>
                  {isOpen && (
                    <tr className="detail-row">
                      <td colSpan={COLS.length + 1}>
                        <TransactionDetail t={t} flaggedIds={flaggedIds} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={COLS.length + 1} className="empty">
                  {emptyText}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager pager-between">
        <span className="legend-inline">
          <span className="flag-dot" aria-hidden="true" /> ML-flagged wallet
        </span>
        <span className="pager-controls">
          {rows.length > 0 && (
            <span>
              {fmtInt(current * pageSize + 1)}–{fmtInt(Math.min(rows.length, (current + 1) * pageSize))} of {fmtInt(rows.length)}
            </span>
          )}
          {pages > 1 && (
            <>
              <button type="button" className="btn btn-sm" disabled={current === 0} onClick={() => setPage(current - 1)}>
                Previous
              </button>
              <button type="button" className="btn btn-sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>
                Next
              </button>
            </>
          )}
        </span>
      </div>
    </div>
  );
}

/** Every field the dataset holds for this transfer, plus what can be derived from the other transfers. */
function TransactionDetail({ t, flaggedIds }: { t: Transfer; flaggedIds: Set<string> }) {
  const { transfers } = useTransfers();
  const pair = useMemo(() => {
    if (!transfers) return null;
    let forward = 0;
    let backward = 0;
    let btc = 0;
    for (const x of transfers) {
      if (x.from === t.from && x.to === t.to) {
        forward++;
        btc += x.amountBtc;
      } else if (x.from === t.to && x.to === t.from) {
        backward++;
        btc += x.amountBtc;
      }
    }
    return { forward, backward, btc };
  }, [transfers, t]);

  return (
    <div className="tx-detail">
      <dl>
        <dt>Timestamp</dt>
        <dd className="mono">{fmtTs(t.ts)} UTC</dd>
        <dt>Sender</dt>
        <dd>
          <Link to={`/wallets/${t.from}`} className="mono">
            {t.from}
          </Link>
          {flaggedIds.has(t.from) ? <span className="muted"> · ML-flagged</span> : null}
        </dd>
        <dt>Receiver</dt>
        <dd>
          <Link to={`/wallets/${t.to}`} className="mono">
            {t.to}
          </Link>
          {flaggedIds.has(t.to) ? <span className="muted"> · ML-flagged</span> : null}
        </dd>
        <dt>Amount</dt>
        <dd className="mono">{fmtBtc(t.amountBtc)} BTC</dd>
        <dt>Input / output count</dt>
        <dd className="mono">
          {t.inputCount ?? '—'} inputs · {t.outputCount ?? '—'} outputs
        </dd>
        <dt>Source row</dt>
        <dd className="mono">{t.row ?? '—'} in synthetic_bitcoin_transactions.csv (no transaction ID exists in the dataset)</dd>
        <dt>Same pair</dt>
        <dd>
          {pair
            ? `${pair.forward} transfer${pair.forward === 1 ? '' : 's'} ${t.from} → ${t.to}${pair.backward ? `, ${pair.backward} in the opposite direction` : ''} · ${fmtBtc(pair.btc)} BTC in total${pair.forward + pair.backward > 1 ? ' (repeated relationship)' : ''}`
            : 'Loading…'}
        </dd>
      </dl>
      <div className="tx-detail-actions">
        <Link className="btn btn-sm" to={`/network?view=transactions&q=${t.from}&with=${t.to}`}>
          All transfers between these wallets
        </Link>
        <Link className="btn btn-sm" to={`/network?view=network&wallet=${t.from}`}>
          Network of sender
        </Link>
        <Link className="btn btn-sm" to={`/network?view=network&wallet=${t.to}`}>
          Network of receiver
        </Link>
      </div>
    </div>
  );
}
