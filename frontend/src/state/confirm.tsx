import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useSettings, type Settings } from './settings';

export interface ConfirmOptions {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  /** Destructive actions get a warning-coloured button and are never skipped by settings. */
  danger?: boolean;
}

type Guard = (setting: keyof Settings, options: ConfirmOptions) => Promise<boolean>;

const Ctx = createContext<{ confirm: (o: ConfirmOptions) => Promise<boolean>; guard: Guard } | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { settings } = useSettings();
  const [pending, setPending] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback(
    (o: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        resolver.current = resolve;
        setPending(o);
      }),
    [],
  );

  const settle = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setPending(null);
  };

  /** Ask only if the investigator has left that confirmation switched on in Settings. */
  const guard = useCallback<Guard>((setting, o) => (settings[setting] ? confirm(o) : Promise.resolve(true)), [settings, confirm]);

  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && settle(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pending]);

  return (
    <Ctx.Provider value={{ confirm, guard }}>
      {children}
      {pending && (
        <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && settle(false)}>
          <div className="modal modal-sm" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
            <div className="modal-head">
              <h2 id="confirm-title">{pending.title}</h2>
            </div>
            <div className="modal-body">
              <div className="modal-lead">{pending.body}</div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn" onClick={() => settle(false)}>
                Cancel
              </button>
              <button type="button" className={`btn ${pending.danger ? 'btn-danger' : 'btn-primary'}`} onClick={() => settle(true)} autoFocus>
                {pending.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}

export function useConfirm() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useConfirm must be used inside ConfirmProvider');
  return v;
}
