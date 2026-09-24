import { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { Transfer, WalletRecord } from '../data/types';
import { fmtInt, fmtScore } from '../format';
import type { CaseRecord } from '../state/cases';

/**
 * Confirmation step for opening a case. A case is never created automatically: the investigator
 * reviews what will be attached, may add a title and a first note, and confirms explicitly.
 */
export function CreateCaseDialog({
  wallet,
  transfers,
  onCancel,
  onCreate,
}: {
  wallet: WalletRecord;
  transfers: Transfer[] | null;
  onCancel: () => void;
  onCreate: (input: { title: string; note: string; transactions: Transfer[] }) => CaseRecord;
}) {
  const [title, setTitle] = useState(`Review of ${wallet.id}`);
  const [note, setNote] = useState('');
  const titleRef = useRef<HTMLInputElement>(null);

  const related = useMemo(
    () => (transfers ? transfers.filter((t) => t.from === wallet.id || t.to === wallet.id) : null),
    [transfers, wallet.id],
  );

  useEffect(() => {
    titleRef.current?.select();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const f = wallet.fusion;

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="case-dialog-title"
        onSubmit={(e) => {
          e.preventDefault();
          if (related) onCreate({ title, note, transactions: related });
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
            Opening a case records that an investigator has decided this lead warrants a formal review. It does not
            establish that any wrongdoing occurred.
          </p>

          <label className="field">
            <span>Case title</span>
            <input ref={titleRef} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
          </label>

          <label className="field">
            <span>
              Initial note <em>(optional)</em>
            </span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="Why this wallet is being taken forward, and what to check first."
            />
          </label>

          <div className="attach">
            <div className="attach-title">Attached to the case</div>
            <dl>
              <dt>Flagged wallet</dt>
              <dd className="mono">{wallet.id}</dd>
              <dt>Evidence snapshot</dt>
              <dd>
                ML score {fmtScore(f.ml_anomaly_score)} ({f.ml_anomaly_prediction}) · {f.forensic_rule_count} rule
                {f.forensic_rule_count === 1 ? '' : 's'} · combined {fmtScore(f.combined_score)} (#{wallet.priorityRank})
              </dd>
              <dt>Related transactions</dt>
              <dd>{related ? `${fmtInt(related.length)} transfers involving this wallet` : 'Loading…'}</dd>
            </dl>
          </div>
        </div>

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!related}>
            Create case
          </button>
        </div>
      </form>
    </div>
  );
}
