export function initialiseNavigation({ onApi, onDashboard, onSettings, onWallet }) {
  let activeTab = 'dashboard';

  function switchTab(tabName, event) {
    event?.preventDefault();
    activeTab = tabName;
    ['dashboard', 'wallet', 'settings', 'api'].forEach((tab) => {
      const nav = document.getElementById(`nav-${tab}`);
      const pane = document.getElementById(`view-${tab}`);
      nav?.classList.toggle('active', tab === tabName);
      if (pane) {
        pane.style.display = tab === tabName ? 'block' : 'none';
        pane.classList.toggle('active', tab === tabName);
      }
    });
    onDashboard?.(tabName === 'dashboard');
    if (tabName === 'wallet') onWallet?.();
    if (tabName === 'settings') onSettings?.();
    if (tabName === 'api') onApi?.();
    try { history.replaceState(null, '', `#${tabName}`); } catch (_) { /* unavailable in embedded views */ }
  }

  window.switchTab = switchTab;
  const initial = window.location.hash.replace('#', '');
  if (['dashboard', 'wallet', 'settings', 'api'].includes(initial)) switchTab(initial);
  else onDashboard?.(activeTab === 'dashboard');
}
