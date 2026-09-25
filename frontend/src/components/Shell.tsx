import { NavLink, Outlet } from 'react-router-dom';
import { FolderOpen, LayoutDashboard, Moon, Network, Settings, Sun, TriangleAlert } from 'lucide-react';
import { useBackend } from '../state/data';
import { useTheme } from '../state/theme';
import { ConnectionBanner } from './ConnectionBanner';
import { GlobalSearch } from './GlobalSearch';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/anomalies', label: 'Anomalies', icon: TriangleAlert },
  { to: '/cases', label: 'Cases', icon: FolderOpen },
  { to: '/network', label: 'Transactions / Network', icon: Network },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export function Shell() {
  const { theme, setTheme } = useTheme();
  const { mode, online } = useBackend();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Bitcoin Transaction Intelligence</div>
        {mode === 'api' && <GlobalSearch />}
        <nav aria-label="Primary">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="seg seg-block" role="group" aria-label="Colour theme">
            <button type="button" className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')} aria-pressed={theme === 'light'}>
              <Sun size={14} aria-hidden="true" /> Light
            </button>
            <button type="button" className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')} aria-pressed={theme === 'dark'}>
              <Moon size={14} aria-hidden="true" /> Dark
            </button>
          </div>
          <p className="conn-line">
            <span className={`conn-dot ${mode === 'api' && online ? 'ok' : 'off'}`} aria-hidden="true" />
            {mode === 'api' ? (online ? 'Local backend connected' : 'Backend not answering') : 'Offline · bundled CSV'}
          </p>
          <p className="foot-note">Local prototype. Synthetic transaction data; no external services.</p>
        </div>
      </aside>
      <main className="main">
        <ConnectionBanner />
        <Outlet />
      </main>
    </div>
  );
}
