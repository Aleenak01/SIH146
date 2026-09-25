import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Download } from 'lucide-react';
import { api } from '../api/client';
import type { CaseDetail } from '../api/types';
import { CaseStatusPill, PageHeader, PriorityPill } from '../components/bits';
import { downloadJson, stamp } from '../data/export';
import { fmtDateTime } from '../format';
import { useCases } from '../state/cases';
import { useConfirm } from '../state/confirm';

/** Cases an investigator has opened, stored in the backend database. Select a case to open its detail page. */
export function Cases() {
  const { cases, available, loading } = useCases();
  const navigate = useNavigate();
  const { guard } = useConfirm();
  const created = (useLocation().state as { created?: string } | null)?.created;
  // Most recently changed first; the backend returns them in that order already.
  const sorted = cases;

  const exportAll = async () => {
    const ok = await guard('confirmExport', {
      title: 'Export cases',
      body: `Save ${cases.length} case(s), with items, evidence saved at the time, notes and history, as a JSON file on this computer.`,
      confirmLabel: 'Export JSON',
    });
    if (!ok) return;
    const details = await Promise.all(cases.map((c) => api.get<CaseDetail>(`/api/cases/${c.case_id}`)));
    downloadJson(`sih146-cases-${stamp()}.json`, { exportedAt: new Date().toISOString(), source: 'SIH146 local backend, synthetic data', cases: details });
  };

  if (!available) {
    return (
      <div className="page">
        <PageHeader title="Cases" subtitle="Cases opened by an investigator." />
        <div className="empty-block">
          <p>Case management needs the local backend, which is not connected.</p>
          <p>Start it with “python -m backend” in the project folder, then use Retry connection above.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Cases"
        subtitle="Cases opened by an investigator from a lead, wallet, transaction or cluster. A flagged wallet is never a case until an investigator creates one. Cases are saved in the backend database on this computer."
        actions={
          cases.length > 0 && (
            <button type="button" className="btn" onClick={() => void exportAll()}>
              <Download size={14} aria-hidden="true" /> Export cases (JSON)
            </button>
          )
        }
      />
      {loading ? (
        <div className="empty-block">Loading cases…</div>
      ) : sorted.length === 0 ? (
        <div className="empty-block">
          <p>No cases yet.</p>
          <p>
            Open a lead from <Link to="/anomalies">Anomalies</Link> and choose <b>Create case</b> if it warrants formal review.
          </p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Title</th>
                <th>Priority</th>
                <th>Contains</th>
                <th>Assigned</th>
                <th>Updated</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => (
                <tr key={c.case_id} className={`row-link${c.case_id === created ? ' row-new' : ''}`} onClick={() => navigate(`/cases/${c.case_id}`)}>
                  <td>
                    <Link to={`/cases/${c.case_id}`} className="mono wallet-link" onClick={(e) => e.stopPropagation()}>
                      {c.case_id}
                    </Link>
                  </td>
                  <td className="wrap">{c.title}</td>
                  <td>{c.priority ? <PriorityPill level={c.priority} /> : <span className="muted">—</span>}</td>
                  <td className="wrap muted">
                    {Object.entries(c.item_counts)
                      .filter(([, n]) => n > 0)
                      .map(([k, n]) => `${n} ${k}${n === 1 ? '' : 's'}`)
                      .join(' · ') || '—'}
                  </td>
                  <td>{c.assigned_to ?? <span className="muted">—</span>}</td>
                  <td>{fmtDateTime(c.updated_at)}</td>
                  <td>
                    <CaseStatusPill status={c.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
