/* Theme handling: follows the device by default, manual override cycles
   system -> light -> dark. Loaded in <head> so the first paint is correct. */

(function () {
  const KEY = 'trainer-theme';
  const mq = window.matchMedia('(prefers-color-scheme: light)');
  const pref = () => localStorage.getItem(KEY) || 'system';

  function apply() {
    const p = pref();
    const resolved = p === 'system' ? (mq.matches ? 'light' : 'dark') : p;
    document.documentElement.dataset.theme = resolved;
  }

  const LABELS = { system: '◐ auto', light: '☀ light', dark: '☾ dark' };

  function syncButton() {
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = LABELS[pref()];
  }

  mq.addEventListener('change', apply);
  document.addEventListener('DOMContentLoaded', () => {
    syncButton();
    const btn = document.getElementById('theme-btn');
    if (btn) btn.addEventListener('click', () => {
      const order = ['system', 'light', 'dark'];
      localStorage.setItem(KEY, order[(order.indexOf(pref()) + 1) % 3]);
      apply();
      syncButton();
    });
  });
  apply();
})();
