import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search } from 'lucide-react';
import { api, qs } from '../api/client';
import type { SearchHit, SearchOut } from '../api/types';

const LABELS: Record<string, string> = {
  wallets: 'Wallets',
  transactions: 'Transactions',
  clusters: 'Clusters',
  ip_observations: 'IP addresses (synthetic)',
  devices: 'Devices (synthetic)',
  sessions: 'Sessions (synthetic)',
  cases: 'Cases',
};

/** Where a search hit leads. Every destination is an existing screen. */
export function hitPath(h: SearchHit): string {
  switch (h.type) {
    case 'wallet':
      return `/wallets/${h.id}`;
    case 'transaction':
      return `/network?view=transactions&tx=${encodeURIComponent(h.id)}`;
    case 'cluster':
      return `/network?view=clusters&cluster=${encodeURIComponent(h.id)}`;
    case 'ip':
    case 'device':
    case 'session':
      return `/network?view=clusters&entity=${h.type}:${encodeURIComponent(h.id)}`;
    case 'ip_observation':
      return `/network?view=clusters&entity=ip:${encodeURIComponent(h.data.ip_address)}`;
    case 'case':
      return `/cases/${h.id}`;
    default:
      return '/';
  }
}

/** Searches wallets, transactions, clusters, synthetic IP / device / session identifiers and cases in the backend. */
export function GlobalSearch() {
  const [q, setQ] = useState('');
  const [result, setResult] = useState<SearchOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const term = q.trim();

  useEffect(() => {
    if (term.length < 2) {
      setResult(null);
      setError(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      api.get<SearchOut>(`/api/search${qs({ q: term, limit: 5 })}`).then(
        (r) => {
          if (!live) return;
          setResult(r);
          setError(null);
        },
        (e) => live && setError(e instanceof Error ? e.message : String(e)),
      );
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [term]);

  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const go = (h: SearchHit) => {
    setOpen(false);
    setQ('');
    navigate(hitPath(h));
  };

  const groups = result ? Object.entries(result.categories).filter(([, c]) => c.total > 0) : [];

  return (
    <div className="gsearch" ref={box}>
      <label className="gsearch-box">
        <Search size={14} aria-hidden="true" />
        <input
          type="search"
          value={q}
          placeholder="Search data…"
          aria-label="Search wallets, transactions, clusters, IP addresses, devices and cases"
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setOpen(false);
            if (e.key === 'Enter') {
              const first = groups[0]?.[1].items[0];
              if (first) go(first);
            }
          }}
        />
      </label>
      {open && term.length >= 2 && (
        <div className="gsearch-pop" role="listbox">
          {error && <p className="gsearch-empty">Search failed: {error}</p>}
          {!error && result && groups.length === 0 && <p className="gsearch-empty">No match for “{term}”.</p>}
          {groups.map(([name, cat]) => (
            <div key={name} className="gsearch-group">
              <div className="gsearch-head">
                {LABELS[name] ?? name} <span>{cat.total}</span>
              </div>
              {cat.items.map((h) => (
                <button key={`${h.type}:${h.id}`} type="button" role="option" className="gsearch-hit" onClick={() => go(h)}>
                  <span className="mono">{h.label}</span>
                  {h.subtitle && <em>{h.subtitle}</em>}
                </button>
              ))}
              {cat.total > cat.items.length && <div className="gsearch-more">{cat.total - cat.items.length} more — refine the search</div>}
            </div>
          ))}
          {result && result.notes.length > 0 && groups.length > 0 && <p className="gsearch-note">{result.notes[0]}</p>}
        </div>
      )}
    </div>
  );
}
