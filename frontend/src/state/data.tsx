import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { loadDataset, loadTransfers, type Dataset } from '../data/loader';
import type { Transfer } from '../data/types';

const Ctx = createContext<Dataset | null>(null);

/** Parses the pipeline CSVs once. They are bundled at build time, so this is synchronous and offline. */
export function DataProvider({ children }: { children: ReactNode }) {
  const result = useMemo(() => {
    try {
      return { data: loadDataset(), error: null as string | null };
    } catch (e) {
      return { data: null, error: e instanceof Error ? e.message : String(e) };
    }
  }, []);

  if (!result.data) {
    return (
      <div className="fatal">
        <h1>Could not load pipeline output</h1>
        <p>{result.error}</p>
        <p>Expected data/fusion_results.csv and data/wallet_behavior_features.csv in the project root.</p>
      </div>
    );
  }
  return <Ctx.Provider value={result.data}>{children}</Ctx.Provider>;
}

export function useDataset(): Dataset {
  const v = useContext(Ctx);
  if (!v) throw new Error('useDataset must be used inside DataProvider');
  return v;
}

/** Raw transfers (10,000 mirrored rows -> 5,000 transfers), loaded on demand. */
export function useTransfers(): { transfers: Transfer[] | null; error: string | null } {
  const [transfers, setTransfers] = useState<Transfer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadTransfers().then(
      (t) => live && setTransfers(t),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, []);
  return { transfers, error };
}

/** Ids of the wallets the Isolation Forest flagged. */
export function useFlaggedIds(): Set<string> {
  const { wallets } = useDataset();
  return useMemo(() => new Set(wallets.filter((w) => w.flagged).map((w) => w.id)), [wallets]);
}
