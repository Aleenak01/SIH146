import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Transfer, WalletRecord } from '../data/types';
import { parseRuleNames } from '../data/rules';

// ---------------------------------------------------------------------------------------------
// Investigation state (prototype: kept in the browser's localStorage, no backend).
//
// A flagged anomaly is only a lead. Nothing here is created automatically: a wallet's review
// status changes and cases exist only when an investigator explicitly does it. The shapes below
// are what a future Cases module (and a real persistence layer) would store.
// ---------------------------------------------------------------------------------------------

/** Per-wallet workflow state shown in the anomaly table. */
export type ReviewStatus = 'Unreviewed' | 'Under Review' | 'Case Created';

/** Investigator-controlled state of a case itself. */
export type CaseStatus = 'Open' | 'Under investigation' | 'Closed';

/** Evidence as it stood when the wallet was added to the case (later pipeline re-runs do not rewrite it). */
export interface EvidenceSnapshot {
  mlScore: number;
  mlPrediction: string;
  ruleCount: number;
  evidenceLevel: string;
  triggeredRules: string[];
  /** Pipeline explanation text at the time the wallet was added (absent on cases created before this was stored). */
  findings?: string;
  combinedScore: number;
  priorityRank: number;
}

export interface CaseWallet {
  address: string;
  addedAt: string; // ISO
  snapshot: EvidenceSnapshot;
}

export interface CaseEvent {
  at: string; // ISO
  actor: string;
  action: string;
  detail?: string;
}

export interface CaseNote {
  id: string;
  at: string;
  author: string;
  text: string;
}

export interface CaseRecord {
  id: string; // CASE-0001
  title: string;
  createdAt: string; // ISO
  status: CaseStatus;
  wallets: CaseWallet[];
  relatedTransactions: Transfer[];
  notes: CaseNote[];
  history: CaseEvent[];
}

export interface Persisted {
  cases: CaseRecord[];
  reviewStatus: Record<string, 'Unreviewed' | 'Under Review'>;
}

const STORAGE_KEY = 'sih146.investigation.v1';
const ACTOR = 'Investigator'; // no authentication in the offline prototype

function load(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Persisted;
      if (Array.isArray(p.cases) && p.reviewStatus) return p;
    }
  } catch {
    /* storage unavailable or corrupt: start empty */
  }
  return { cases: [], reviewStatus: {} };
}

export interface NewCaseInput {
  wallet: WalletRecord;
  title: string;
  note: string;
  transactions: Transfer[];
}

interface Store {
  cases: CaseRecord[];
  activeCases: number;
  statusOf: (walletId: string) => ReviewStatus;
  caseFor: (walletId: string) => CaseRecord | undefined;
  markUnderReview: (walletId: string) => void;
  markUnreviewed: (walletId: string) => void;
  createCase: (input: NewCaseInput) => CaseRecord;
  getCase: (id: string) => CaseRecord | undefined;
  setCaseStatus: (id: string, status: CaseStatus) => void;
  addNote: (id: string, text: string) => void;
  /** Everything the store holds, for export. */
  snapshot: () => Persisted;
  /** Removes all cases and review statuses (Settings > Local data). */
  clearAll: () => void;
}

const Ctx = createContext<Store | null>(null);

export function CaseProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Persisted>(load);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* not persisted; the session still works */
    }
  }, [state]);

  const caseFor = useCallback(
    (walletId: string) => state.cases.find((c) => c.wallets.some((w) => w.address === walletId)),
    [state.cases],
  );

  const statusOf = useCallback(
    (walletId: string): ReviewStatus => (caseFor(walletId) ? 'Case Created' : state.reviewStatus[walletId] ?? 'Unreviewed'),
    [caseFor, state.reviewStatus],
  );

  const setReview = useCallback((walletId: string, s: 'Unreviewed' | 'Under Review') => {
    setState((p) => ({ ...p, reviewStatus: { ...p.reviewStatus, [walletId]: s } }));
  }, []);

  const createCase = useCallback(
    (input: NewCaseInput): CaseRecord => {
      const now = new Date().toISOString();
      const { wallet } = input;
      const f = wallet.fusion;
      const record: CaseRecord = {
        id: `CASE-${String(state.cases.length + 1).padStart(4, '0')}`,
        title: input.title.trim() || `Review of ${wallet.id}`,
        createdAt: now,
        status: 'Open',
        wallets: [
          {
            address: wallet.id,
            addedAt: now,
            snapshot: {
              mlScore: f.ml_anomaly_score,
              mlPrediction: f.ml_anomaly_prediction,
              ruleCount: f.forensic_rule_count,
              evidenceLevel: f.forensic_evidence_level,
              triggeredRules: parseRuleNames(f.forensic_triggered_rules),
              findings: f.forensic_findings,
              combinedScore: f.combined_score,
              priorityRank: wallet.priorityRank,
            },
          },
        ],
        relatedTransactions: input.transactions,
        notes: input.note.trim()
          ? [{ id: `n-${Date.now()}`, at: now, author: ACTOR, text: input.note.trim() }]
          : [],
        history: [
          {
            at: now,
            actor: ACTOR,
            action: 'Case created',
            detail: `Opened from wallet ${wallet.id} with ${input.transactions.length} related transactions attached.`,
          },
        ],
      };
      setState((p) => ({ ...p, cases: [...p.cases, record] }));
      return record;
    },
    [state.cases.length],
  );

  const updateCase = useCallback((id: string, fn: (c: CaseRecord, now: string) => CaseRecord) => {
    const now = new Date().toISOString();
    setState((p) => ({ ...p, cases: p.cases.map((c) => (c.id === id ? fn(c, now) : c)) }));
  }, []);

  const setCaseStatus = useCallback(
    (id: string, status: CaseStatus) =>
      updateCase(id, (c, now) =>
        c.status === status
          ? c
          : {
              ...c,
              status,
              history: [...c.history, { at: now, actor: ACTOR, action: 'Status changed', detail: `${c.status} → ${status}` }],
            },
      ),
    [updateCase],
  );

  const addNote = useCallback(
    (id: string, text: string) => {
      const t = text.trim();
      if (!t) return;
      updateCase(id, (c, now) => ({
        ...c,
        notes: [...c.notes, { id: `n-${Date.now()}`, at: now, author: ACTOR, text: t }],
        history: [...c.history, { at: now, actor: ACTOR, action: 'Note added' }],
      }));
    },
    [updateCase],
  );

  const value = useMemo<Store>(
    () => ({
      cases: state.cases,
      activeCases: state.cases.filter((c) => c.status !== 'Closed').length,
      statusOf,
      caseFor,
      markUnderReview: (id) => setReview(id, 'Under Review'),
      markUnreviewed: (id) => setReview(id, 'Unreviewed'),
      createCase,
      getCase: (id) => state.cases.find((c) => c.id === id),
      setCaseStatus,
      addNote,
      snapshot: () => state,
      clearAll: () => setState({ cases: [], reviewStatus: {} }),
    }),
    [state, statusOf, caseFor, setReview, createCase, setCaseStatus, addNote],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCases(): Store {
  const v = useContext(Ctx);
  if (!v) throw new Error('useCases must be used inside CaseProvider');
  return v;
}
