/* Phone-capture setup page: token reveal/copy and a test-capture button. */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  function setMsg(text, kind) {
    const el = $('capture-test-msg');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('capture-test-msg--error', kind === 'error');
    el.classList.toggle('capture-test-msg--ok', kind === 'ok');
  }

  function initToken() {
    const value = $('capture-token');
    const reveal = $('capture-token-reveal');
    const copy = $('capture-token-copy');
    if (!value) return;
    const token = value.dataset.token || '';
    const masked = value.dataset.masked || '••••••••';
    let shown = false;

    if (reveal) {
      reveal.addEventListener('click', () => {
        shown = !shown;
        value.textContent = shown ? token : masked;
        reveal.textContent = shown ? 'Hide' : 'Show';
      });
    }

    if (copy) {
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(token);
          setMsg('Token copied', 'ok');
        } catch (_) {
          // Fallback for insecure origins / older browsers.
          const holder = document.createElement('textarea');
          holder.value = token;
          document.body.appendChild(holder);
          holder.select();
          try {
            document.execCommand('copy');
            setMsg('Token copied', 'ok');
          } catch (err) {
            setMsg('Copy failed — reveal it and copy manually', 'error');
          }
          document.body.removeChild(holder);
        }
      });
    }
  }

  function initTest() {
    const btn = $('capture-test');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      setMsg('Sending…');
      try {
        const res = await fetch('/api/spending/daily/capture/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        });
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error((data && data.error) || res.statusText);
        setMsg('Sent — check Daily', 'ok');
      } catch (e) {
        setMsg(e.message || 'Failed to send', 'error');
      } finally {
        btn.disabled = false;
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    initToken();
    initTest();
  });
})();
