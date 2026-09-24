import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Download } from 'lucide-react';
import { CaseStatusPill, PageHeader } from '../components/bits';
import { downloadJson, stamp } from '../data/export';
import { fmtDateTime, fmtInt } from '../format';
import { useCases } from '../state/cases';
import { useConfirm } from '../state/confirm';

/**
 * Cases an investigator has opened. Select a case to open its detail page.
 */
export function Cases() {
  const { cases } = useCases();
  const navigate = useNavigate();
  const { guard } = useConfirm();
  const created = (useLocation().state as { created?: string } | null)?.created;
  const sorted = [...cases].sort((a, b) => b.createdAt.localeCompare(a.createdAt));

  return (
    <div className="page">
      <PageHeader
        title="Cases"
        subtitle="Cases opened by an investigator from a wallet investigation. Select a case to open it. Stored locally in this browser for the prototype."
        actions={
          cases.length > 0 && (
            <button
              type="button"
              className="btn"
              onClick={async () => {
                const ok = await guard('confirmExport', {
                  title: 'Export cases',
                  body: `Save ${cases.length} case(s) as a JSON file on this computer.`,
                  confirmLabel: 'Export JSON',
                });
                if (ok) downloadJson(`sih146-cases-${stamp()}.json`, { exportedAt: new Date().toISOString(), source: 'SIH146 frontend prototype, synthetic data', cases });
              }}
            >
              <Download size={14} aria-hidden="true" /> Export cases (JSON)
            </button>
          )
        }
      />
      {sorted.length === 0 ? (
        <div className="empty-block">
          <p>No cases yet.</p>
          <p>
            Open a wallet from <Link to="/anomalies">Anomalies</Link> and choose <b>Create case</b> if it warrants formal review.
          </p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Title</th>
                <th>Flagged wallets</th>
                <th className="num">Transactions</th>
                <th className="num">Notes</th>
                <th>Created</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => (
                <tr key={c.id} className={`row-link${c.id === created ? ' row-new' : ''}`} onClick={() => navigate(`/cases/${c.id}`)}>
                  <td>
                    <Link to={`/cases/${c.id}`} className="mono wallet-link" onClick={(e) => e.stopPropagation()}>
                      {c.id}
                    </Link>
                  </td>
                  <td className="wrap">{c.title}</td>
                  <td>
                    {c.wallets.map((w) => (
                      <Link key={w.address} to={`/wallets/${w.address}`} className="mono wallet-link" onClick={(e) => e.stopPropagation()}>
                        {w.address}
                      </Link>
                    ))}
                  </td>
                  <td className="num mono">{fmtInt(c.relatedTransactions.length)}</td>
                  <td className="num mono">{c.notes.length}</td>
                  <td>{fmtDateTime(c.createdAt)}</td>
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
