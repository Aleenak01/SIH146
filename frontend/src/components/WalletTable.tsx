import { Fragment, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from 'lucide-react';
import type { WalletRecord } from '../data/types';
import { fmtScore } from '../format';
import { useCases } from '../state/cases';
import { EvidenceCell, PredictionText, PriorityPill, StatusPill } from './bits';

export type SortKey = 'wallet' | 'ml' | 'evidence' | 'rules' | 'combined' | 'status' | 'priority';
export interface SortState {
  key: SortKey;
  dir: 'asc' | 'desc';
}

const COLUMNS: { key: SortKey; label: string; align?: 'right'; hint?: string; leads?: boolean }[] = [
  { key: 'wallet', label: 'Wallet' },
  { key: 'priority', label: 'Priority', leads: true, hint: 'Prototype priority band from the combined-result rank (top 5% High, next 15% Medium) — not statistically validated' },
  { key: 'ml', label: 'ML anomaly score', align: 'right', hint: 'Isolation Forest score; higher = more unusual' },
  { key: 'evidence', label: 'Forensic evidence' },
  { key: 'rules', label: 'Rules', align: 'right', hint: 'Number of forensic rules triggered' },
  { key: 'combined', label: 'Combined result', align: 'right', hint: 'Prototype fusion of ML and forensic evidence (rank among all wallets)' },
  { key: 'status', label: 'Status' },
];

/**
 * Wallet list. With `leads` (backend mode) it adds the priority band and lets a row expand to show why the wallet is
 * a lead, with the investigator's "Create case" action. A lead is never a case until the investigator creates one.
 */
export function WalletTable({
  rows,
  sort,
  onSort,
  leads = false,
  onCreateCase,
}: {
  rows: WalletRecord[];
  sort?: SortState;
  onSort?: (key: SortKey) => void;
  leads?: boolean;
  onCreateCase?: (w: WalletRecord) => void;
}) {
  const navigate = useNavigate();
  const { statusOf, caseIdsFor } = useCases();
  const [open, setOpen] = useState<string | null>(null);
  const columns = COLUMNS.filter((c) => leads || !c.leads);
  const span = columns.length + (leads ? 1 : 0);

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {leads && <th className="chev-col" aria-label="Expand" />}
            {columns.map((c) => {
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
          {rows.map((w) => {
            const isOpen = open === w.id;
            const cases = caseIdsFor(w.id);
            return (
              <Fragment key={w.id}>
                <tr className={`row-link${isOpen ? ' row-open' : ''}`} onClick={() => (leads ? setOpen(isOpen ? null : w.id) : navigate(`/wallets/${w.id}`))} aria-expanded={leads ? isOpen : undefined}>
                  {leads && <td className="chev-col">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>}
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
                  {leads && <td>{w.lead ? <PriorityPill level={w.lead.priorityLevel} /> : null}</td>}
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
                {leads && isOpen && (
                  <tr className="detail-row">
                    <td colSpan={span}>
                      <div className="lead-detail">
                        <div>
                          <h4>Why this wallet is a lead</h4>
                          {w.lead && w.lead.reasons.length > 0 ? (
                            <ul>
                              {w.lead.reasons.map((r, i) => (
                                <li key={i}>{r}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="muted">Not a lead: the model did not flag it and its priority band is not High. Shown for reference.</p>
                          )}
                          <p className="caveat">Prototype fusion weighting — not statistically validated. A lead is a starting point for review, not a case and not evidence of wrongdoing.</p>
                        </div>
                        <div className="lead-actions">
                          <Link to={`/wallets/${w.id}`} className="btn btn-sm">
                            Open investigation
                          </Link>
                          {cases.length > 0 ? (
                            cases.map((id) => (
                              <Link key={id} to={`/cases/${id}`} className="btn btn-sm">
                                Open {id}
                              </Link>
                            ))
                          ) : (
                            onCreateCase && (
                              <button type="button" className="btn btn-sm btn-primary" onClick={() => onCreateCase(w)}>
                                Create case…
                              </button>
                            )
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={span} className="empty">
                No wallets match the current filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
