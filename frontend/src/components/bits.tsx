import type { ReactNode } from 'react';
import type { EvidenceLevel } from '../data/types';
import type { CaseStatus, ReviewStatus } from '../state/cases';

/** Small shared presentational pieces. Colour comes from CSS classes/tokens only. */

export function StatusPill({ status }: { status: ReviewStatus }) {
  const cls = status === 'Unreviewed' ? 'unreviewed' : status === 'Under Review' ? 'review' : 'case';
  return (
    <span className={`status status-${cls}`}>
      <span className="status-dot" aria-hidden="true" />
      {status}
    </span>
  );
}

/** Prototype priority band (from the combined-result rank). */
export function PriorityPill({ level }: { level: 'High' | 'Medium' | 'Low' }) {
  return <span className={`prio prio-${level.toLowerCase()}`}>{level}</span>;
}

export function CaseStatusPill({ status }: { status: CaseStatus }) {
  const cls = status === 'Open' ? 'case' : status === 'Under investigation' ? 'review' : 'unreviewed';
  return (
    <span className={`status status-${cls}`}>
      <span className={`status-dot${status === 'Closed' ? ' solid-muted' : ''}`} aria-hidden="true" />
      {status}
    </span>
  );
}

const EVIDENCE: Record<EvidenceLevel, { short: string; step: number }> = {
  'No specific rule triggered': { short: 'None', step: 0 },
  'Single behavioural indicator': { short: 'Single indicator', step: 1 },
  'Multiple behavioural indicators': { short: 'Multiple indicators', step: 2 },
  'Multiple strong behavioural indicators': { short: 'Multiple strong', step: 3 },
};

export const evidenceShort = (l: EvidenceLevel) => EVIDENCE[l]?.short ?? l;
export const evidenceStep = (l: EvidenceLevel) => EVIDENCE[l]?.step ?? 0;

/** Ordinal marker (0-3 segments) + label, so the level can be scanned down a column. */
export function EvidenceCell({ level }: { level: EvidenceLevel }) {
  const e = EVIDENCE[level] ?? { short: level, step: 0 };
  return (
    <span className="evidence" title={level}>
      <span className="evidence-steps" aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <span key={i} className={i <= e.step ? 'on' : ''} />
        ))}
      </span>
      {e.short}
    </span>
  );
}

export function PredictionText({ flagged }: { flagged: boolean }) {
  return <span className={flagged ? 'pred pred-flagged' : 'pred'}>{flagged ? 'Anomalous' : 'Normal'}</span>;
}

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {back}
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  note,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode;
  note?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <p className="panel-note">{note}</p>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Legend({ items }: { items: { label: string; className: string }[] }) {
  return (
    <ul className="legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className={`swatch ${i.className}`} aria-hidden="true" />
          {i.label}
        </li>
      ))}
    </ul>
  );
}
