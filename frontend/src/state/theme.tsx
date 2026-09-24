import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

// The theme is a `data-theme` attribute on <html>. All colour lives in CSS custom properties
// (src/styles/tokens.css), defined separately for each theme; components and charts only ever
// reference the tokens, so nothing is inverted or recoloured in JS. index.html sets the attribute
// before first paint.
//
// `preference` is what the investigator chose (light / dark / follow the system); `theme` is the
// resolved value actually applied. The sidebar toggle sets an explicit light/dark preference.

export type Theme = 'light' | 'dark';
export type ThemePreference = Theme | 'system';
const KEY = 'sih146.theme';

const systemTheme = (): Theme =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

function storedPreference(): ThemePreference {
  try {
    const s = localStorage.getItem(KEY);
    if (s === 'light' || s === 'dark') return s;
  } catch {
    /* ignore */
  }
  return 'system';
}

const Ctx = createContext<{
  theme: Theme;
  preference: ThemePreference;
  setTheme: (t: Theme) => void;
  setPreference: (p: ThemePreference) => void;
} | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPref] = useState<ThemePreference>(storedPreference);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme: Theme = preference === 'system' ? system : preference;

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setSystem(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPref(p);
    try {
      if (p === 'system') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, p);
    } catch {
      /* preference just won't persist */
    }
  }, []);

  const value = useMemo(() => ({ theme, preference, setTheme: setPreference, setPreference }), [theme, preference, setPreference]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useTheme must be used inside ThemeProvider');
  return v;
}
