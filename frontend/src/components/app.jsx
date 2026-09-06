import { useEffect, useState } from 'preact/hooks';
import { DashboardView } from '../dashboard/dashboard-view.jsx';
import { startDashboardStream } from '../dashboard/store.js';
import { ToastProvider } from '../ui/toasts.jsx';
import { ApiView } from './api-view.jsx';
import { SettingsView } from './settings-view.jsx';
import { Sidebar } from './sidebar.jsx';
import { WalletView } from './wallet-view.jsx';

const TABS = new Set(['dashboard', 'wallet', 'settings', 'api']);

function tabFromHash() {
  const tab = window.location.hash.replace('#', '');
  return TABS.has(tab) ? tab : 'dashboard';
}

function Application() {
  const [activeTab, setActiveTab] = useState(tabFromHash);
  const [minLag, setMinLag] = useState(6);

  useEffect(() => {
    startDashboardStream();
    const syncHash = () => setActiveTab(tabFromHash());
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);

  function navigate(tab) {
    if (!TABS.has(tab)) return;
    setActiveTab(tab);
    try { window.history.replaceState(null, '', `#${tab}`); } catch (_) { /* embedded browser */ }
  }

  function updateSettings(settings) {
    const value = Number(settings.min_lag_trigger);
    if (Number.isFinite(value) && value > 0) setMinLag(value);
  }

  return (
    <div class="app-shell">
      <Sidebar activeTab={activeTab} onNavigate={navigate} />
      <main class="main-content-area">
        {activeTab === 'dashboard' && <DashboardView minLag={minLag} />}
        {activeTab === 'wallet' && <WalletView />}
        {activeTab === 'settings' && <SettingsView onSettingsChange={updateSettings} />}
        {activeTab === 'api' && <ApiView />}
      </main>
    </div>
  );
}

export function App() {
  return <ToastProvider><Application /></ToastProvider>;
}
