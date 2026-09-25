import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, qs } from '../api/client';
import type { CaseDetail, CasePriority, CaseStatus, CaseSummary, ItemType } from '../api/types';
import { useBackend, useDataset } from './data';

// ---------------------------------------------------------------------------------------------
// Investigation state. Cases live in the backend database (data/sih146.db), not in the browser.
//
// A flagged wallet or a lead is only a lead. Nothing here is created automatically: a wallet's review
// status changes and a case exists only when an investigator explicitly does it.
//
// Cases stored in this browser by earlier prototype versions (localStorage key sih146.investigation.v1)
// were test data; they are not read, shown or migrated.
// ---------------------------------------------------------------------------------------------

/** Per-wallet workflow state shown in the anomaly table. */
export type ReviewStatus = 'Unreviewed' | 'Under Review' | 'Case Created';
export type { CaseStatus, CasePriority, CaseSummary, CaseDetail, ItemType };

export interface NewCaseInput {
  title: string;
  description?: string;
  priority?: CasePriority | null;
  assignedTo?: string;
  note?: string;
  items: { type: ItemType; id: string; note?: string }[];
}

interface Store {
  /** Case management needs the backend; false in the bundled-CSV fallback. */
  available: boolean;
  cases: CaseSummary[];
  activeCases: number;
  loading: boolean;
  statusOf: (walletId: string) => ReviewStatus;
  caseIdsFor: (walletId: string) => string[];
  refresh: () => Promise<void>;
  markUnderReview: (walletId: string) => Promise<void>;
  markUnreviewed: (walletId: string) => Promise<void>;
  createCase: (input: NewCaseInput) => Promise<CaseDetail>;
  updateCase: (id: string, patch: Partial<{ title: string; description: string; status: CaseStatus; priority: CasePriority; assigned_to: string }>) => Promise<CaseDetail>;
  addNote: (id: string, text: string) => Promise<void>;
  markReviewed: (id: string, itemType: ItemType, itemId: string, note?: string) => Promise<void>;
  addItem: (id: string, type: ItemType, itemId: string, note?: string) => Promise<void>;
  removeItem: (id: string, type: ItemType, itemId: string) => Promise<void>;
}

const Ctx = createContext<Store | null>(null);

export function CaseProvider({ children }: { children: ReactNode }) {
  const backend = useBackend();
  const { byId } = useDataset();
  const available = backend.mode === 'api';
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [loading, setLoading] = useState(available);

  const refresh = useCallback(async () => {
    if (!available) return;
    try {
      const page = await api.get<{ items: CaseSummary[] }>(`/api/cases${qs({ limit: 500 })}`);
      setCases(page.items);
    } finally {
      setLoading(false);
    }
  }, [available]);

  // Load on connect and again whenever the analysis data is reloaded (case and review state come with it).
  useEffect(() => {
    if (!available) {
      setCases([]);
      setLoading(false);
      return;
    }
    refresh().catch(() => undefined);
  }, [available, refresh, byId]);

  const afterChange = useCallback(async () => {
    await Promise.all([refresh(), backend.reload()]);
  }, [refresh, backend]);

  const setReview = useCallback(
    async (walletId: string, status: 'Unreviewed' | 'Under Review') => {
      await api.put(`/api/wallets/${encodeURIComponent(walletId)}/review`, { status });
      await afterChange();
    },
    [afterChange],
  );

  const value = useMemo<Store>(
    () => ({
      available,
      cases,
      activeCases: backend.overview?.active_cases ?? cases.filter((c) => c.status !== 'Closed').length,
      loading,
      statusOf: (id) => byId.get(id)?.lead?.review ?? 'Unreviewed',
      caseIdsFor: (id) => byId.get(id)?.lead?.caseIds ?? [],
      refresh,
      markUnderReview: (id) => setReview(id, 'Under Review'),
      markUnreviewed: (id) => setReview(id, 'Unreviewed'),
      createCase: async (input) => {
        const created = await api.post<CaseDetail>('/api/cases', {
          title: input.title.trim(),
          description: input.description?.trim() || undefined,
          priority: input.priority || undefined,
          assigned_to: input.assignedTo?.trim() || undefined,
          note: input.note?.trim() || undefined,
          items: input.items,
        });
        await afterChange();
        return created;
      },
      updateCase: async (id, patch) => {
        const d = await api.patch<CaseDetail>(`/api/cases/${id}`, patch);
        await afterChange();
        return d;
      },
      addNote: async (id, text) => {
        await api.post(`/api/cases/${id}/notes`, { text: text.trim() });
        await refresh();
      },
      markReviewed: async (id, itemType, itemId, note) => {
        await api.post(`/api/cases/${id}/reviews`, { item_type: itemType, item_id: itemId, note: note?.trim() || undefined });
      },
      addItem: async (id, type, itemId, note) => {
        await api.post(`/api/cases/${id}/items`, { type, id: itemId, note: note?.trim() || undefined });
        await afterChange();
      },
      removeItem: async (id, type, itemId) => {
        await api.del(`/api/cases/${id}/items/${type}/${encodeURIComponent(itemId)}`);
        await afterChange();
      },
    }),
    [available, cases, backend.overview, loading, byId, refresh, setReview, afterChange],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCases(): Store {
  const v = useContext(Ctx);
  if (!v) throw new Error('useCases must be used inside CaseProvider');
  return v;
}
