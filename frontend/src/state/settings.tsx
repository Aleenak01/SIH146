import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

// Investigator preferences (browser-local). Theme is handled separately in state/theme.tsx.
export interface Settings {
  confirmCreateCase: boolean; // when off, "Create case" opens the case immediately with a default title
  confirmStatusChange: boolean;
  confirmExport: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  confirmCreateCase: true,
  confirmStatusChange: true,
  confirmExport: false,
};

const KEY = 'sih146.settings.v1';

function load(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) };
  } catch {
    /* fall through to defaults */
  }
  return DEFAULT_SETTINGS;
}

const Ctx = createContext<{ settings: Settings; update: (patch: Partial<Settings>) => void; reset: () => void } | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(load);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {
      /* not persisted */
    }
  }, [settings]);

  const update = useCallback((patch: Partial<Settings>) => setSettings((s) => ({ ...s, ...patch })), []);
  const reset = useCallback(() => setSettings(DEFAULT_SETTINGS), []);
  const value = useMemo(() => ({ settings, update, reset }), [settings, update, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSettings() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useSettings must be used inside SettingsProvider');
  return v;
}
