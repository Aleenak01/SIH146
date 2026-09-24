import { useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp } from 'lucide-react';
import type { WalletRecord } from '../data/types';
import { fmtScore } from '../format';
import { useCases } from '../state/cases';
import { EvidenceCell, PredictionText, StatusPill } from './bits';

export type SortKey = 'wallet' | 'ml' | 'evidence' | 'rules' | 'combined' | 'status';
export interface SortState {
  key: SortKey;
  dir: 'asc' | 'desc';
}

const COLUMNS: { key: SortKey; label: string; align?: 'right'; hint?: string }[] = [
  { key: 'wallet', label: 'Wallet' },
  { key: 'ml', label: 'ML anomaly score', align: 'right', hint: 'Isolation Forest score; higher = more unusual' },
  { key: 'evidence', label: 'Forensic evidence' },
  { key: 'rules', label: 'Rules', align: 'right', hint: 'Number of forensic rules triggered' },
  { key: 'combined', label: 'Combined result', align: 'right', hint: 'Prototype fusion of ML and forensic evidence (rank among all wallets)' },
  { key: 'status', label: 'Status' },
];

export function WalletTable({
  rows,
  sort,
  onSort,
}: {
  rows: WalletRecord[];
  sort?: SortState;
  onSort?: (key: SortKey) => void;
}) {
  const navigate = useNavigate();
  const { statusOf } = useCases();

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {COLUMNS.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  className={c.align === 'right' ? 'num' : ''}
                  aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  title={c.hint}
                >
                  {onSort ? (
                    <button type="button" className="th-btn" onClick={() => onSort(c.key)}>
                      {c.label}
                      {active && (sort!.dir === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />)}
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
          {rows.map((w) => (
            <tr key={w.id} className="row-link" onClick={() => navigate(`/wallets/${w.id}`)}>
              <td>
                <a
                  className="mono wallet-link"
                  href={`/wallets/${w.id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    navigate(`/wallets/${w.id}`);
                  }}
                >
                  {w.id}
                </a>
              </td>
              <td className="num">
                <PredictionText flagged={w.flagged} />
                <span className="mono">{fmtScore(w.fusion.ml_anomaly_score)}</span>
              </td>
              <td>
                <EvidenceCell level={w.fusion.forensic_evidence_level} />
              </td>
              <td className="num mono">{w.fusion.forensic_rule_count}</td>
              <td className="num">
                <span className="rank">#{w.priorityRank}</span>
                <span className="mono">{fmtScore(w.fusion.combined_score)}</span>
              </td>
              <td>
                <StatusPill status={statusOf(w.id)} />
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={COLUMNS.length} className="empty">
                No wallets match the current filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
