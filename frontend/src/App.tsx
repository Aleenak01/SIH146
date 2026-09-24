import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Shell } from './components/Shell';
import { Anomalies } from './pages/Anomalies';
import { CaseDetail } from './pages/CaseDetail';
import { Cases } from './pages/Cases';
import { Dashboard } from './pages/Dashboard';
import { Network } from './pages/Network';
import { Planned } from './pages/Planned';
import { Settings } from './pages/Settings';
import { WalletDetail } from './pages/WalletDetail';
import { CaseProvider } from './state/cases';
import { ConfirmProvider } from './state/confirm';
import { DataProvider } from './state/data';
import { SettingsProvider } from './state/settings';
import { ThemeProvider } from './state/theme';

export default function App() {
  return (
    <ThemeProvider>
      <SettingsProvider>
        <ConfirmProvider>
          <DataProvider>
            <CaseProvider>
              <BrowserRouter>
                <Routes>
                  <Route element={<Shell />}>
                    <Route index element={<Dashboard />} />
                    <Route path="anomalies" element={<Anomalies />} />
                    <Route path="wallets/:walletId" element={<WalletDetail />} />
                    <Route path="cases" element={<Cases />} />
                    <Route path="cases/:caseId" element={<CaseDetail />} />
                    <Route path="network" element={<Network />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="*" element={<Planned title="Page not found" lead="This address does not match a screen." items={[]} />} />
                  </Route>
                </Routes>
              </BrowserRouter>
            </CaseProvider>
          </DataProvider>
        </ConfirmProvider>
      </SettingsProvider>
    </ThemeProvider>
  );
}
