/* Service-worker registration and the "new build available" prompt.

   Static build only: on the dev server web.py stamps Cache-Control: no-cache on
   every page precisely so edits show up immediately, and a stray service worker
   would undo that. */

import { MODE } from './config.js';

if (MODE === 'static' && 'serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const reg = await navigator.serviceWorker.register('./sw.js');
      reg.update();

      let reloading = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        // Only reload for a *replacement* worker; the first one to take control
        // has nothing new to show.
        if (reloading || !reg.active) return;
        reloading = true;
        location.reload();
      });

      reg.addEventListener('updatefound', () => {
        const next = reg.installing;
        if (!next || !navigator.serviceWorker.controller) return;
        next.addEventListener('statechange', () => {
          if (next.state === 'installed') showUpdateToast(next);
        });
      });
    } catch {
      // No service worker: the app still works, it just needs the network.
    }
  });
}

function showUpdateToast(worker) {
  if (document.getElementById('sw-toast')) return;
  const toast = document.createElement('div');
  toast.id = 'sw-toast';
  toast.className = 'sw-toast';
  toast.innerHTML = '<span>New puzzles are ready.</span>'
    + '<button class="btn gold" type="button">Reload</button>';
  toast.querySelector('button').addEventListener('click', () => {
    worker.postMessage('skipWaiting');
  });
  document.body.appendChild(toast);
}
