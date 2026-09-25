import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { api, qs } from '../api/client';
import type { CasePriority, ClusterPage, ClusterSummary } from '../api/types';
import type { WalletRecord } from '../data/types';
import { fmtInt, fmtScore } from '../format';
import { useCases } from '../state/cases';

/**
 * Confirmation step for opening a case. A case is never created automatically: the investigator reviews what will
 * be attached, may add a title, priority, assignee and a first note, and confirms explicitly. The evidence is saved
 * with the case as it is now; later analysis runs do not rewrite it.
 */
export function CreateCaseDialog({
  wallet,
  onCancel,
  onCreated,
}: {
  wallet: WalletRecord;
  onCancel: () => void;
  onCreated: (caseId: string) => void;
}) {
  const { createCase } = useCases();
  const [title, setTitle] = useState(`Review of ${wallet.id}`);
  const [note, setNote] = useState('');
  const [priority, setPriority] = useState<CasePriority | ''>(wallet.lead?.priorityLevel ?? '');
  const [assignee, setAssignee] = useState('');
  const [clusters, setClusters] = useState<ClusterSummary[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleRef.current?.select();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  useEffect(() => {
    let live = true;
    api.get<ClusterPage>(`/api/clusters${qs({ q: wallet.id, limit: 20 })}`).then(
      (p) => live && setClusters(p.items),
      () => live && setClusters([]),
    );
    return () => {
      live = false;
    };
  }, [wallet.id]);

  const f = wallet.fusion;
  const isLead = wallet.lead?.isLead ?? false;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const c = await createCase({
        title: title.trim() || `Review of ${wallet.id}`,
        priority: priority || null,
        assignedTo: assignee,
        note,
        items: [{ type: isLead ? 'lead' : 'wallet', id: wallet.id }, ...[...picked].map((id) => ({ type: 'cluster' as const, id }))],
      });
      onCreated(c.case_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="case-dialog-title"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="modal-head">
          <h2 id="case-dialog-title">Create case</h2>
          <button type="button" className="icon-btn" onClick={onCancel} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="modal-body">
          <p className="modal-lead">
            Opening a case records that an investigator has decided this lead warrants a formal review. It does not establish that any wrongdoing occurred.
          </p>

          <label className="field">
            <span>Case title</span>
            <input ref={titleRef} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
          </label>

          <div className="field-row">
            <label className="field">
              <span>Priority</span>
              <select value={priority} onChange={(e) => setPriority(e.target.value as CasePriority | '')}>
                <option value="">Not set</option>
                <option value="High">High</option>
                <option value="Medium">Medium</option>
                <option value="Low">Low</option>
              </select>
            </label>
            <label className="field">
              <span>
                Assigned to <em>(optional)</em>
              </span>
              <input value={assignee} onChange={(e) => setAssignee(e.target.value)} maxLength={80} />
            </label>
          </div>

          <label className="field">
            <span>
              Initial note <em>(optional)</em>
            </span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Why this wallet is being taken forward, and what to check first." />
          </label>

          <div className="attach">
            <div className="attach-title">Attached to the case</div>
            <dl>
              <dt>{isLead ? 'Lead' : 'Wallet'}</dt>
              <dd className="mono">{wallet.id}</dd>
              <dt>Evidence saved now</dt>
              <dd>
                ML score {fmtScore(f.ml_anomaly_score)} ({f.ml_anomaly_prediction}) · {f.forensic_rule_count} rule{f.forensic_rule_count === 1 ? '' : 's'} · combined{' '}
                {fmtScore(f.combined_score)} (#{wallet.priorityRank})
              </dd>
              <dt>Related transactions</dt>
              <dd>{fmtInt(wallet.features.transaction_count)} transfers involve this wallet (listed live from the database)</dd>
            </dl>
            {clusters && clusters.length > 0 && (
              <fieldset className="attach-clusters">
                <legend>Also attach clusters this wallet belongs to</legend>
                {clusters.map((c) => (
                  <label key={c.cluster_id} className="check">
                    <input
                      type="checkbox"
                      checked={picked.has(c.cluster_id)}
                      onChange={(e) =>
                        setPicked((p) => {
                          const n = new Set(p);
                          if (e.target.checked) n.add(c.cluster_id);
                          else n.delete(c.cluster_id);
                          return n;
                        })
                      }
                    />
                    <span>
                      <span className="mono">{c.cluster_id}</span> · {c.wallet_count} wallets · {c.method_label}
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
          </div>
          {error && <p className="field-error" role="alert">{error}</p>}
        </div>

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Creating…' : 'Create case'}
          </button>
        </div>
      </form>
    </div>
  );
}
