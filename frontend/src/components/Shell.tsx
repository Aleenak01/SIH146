import { NavLink, Outlet } from 'react-router-dom';
import { FolderOpen, LayoutDashboard, Moon, Network, Settings, Sun, TriangleAlert } from 'lucide-react';
import { useTheme } from '../state/theme';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/anomalies', label: 'Anomalies', icon: TriangleAlert },
  { to: '/cases', label: 'Cases', icon: FolderOpen },
  { to: '/network', label: 'Transactions / Network', icon: Network },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export function Shell() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Bitcoin Transaction Intelligence</div>
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
          <p className="foot-note">Offline prototype. Synthetic transaction data; no external services.</p>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
