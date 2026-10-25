// Runs inside the sandboxed frame that shows a visual (see visuals.py). It waits for the page to send the
// colours and the household's home town, picks the visual (one pinned by an admin, or today's from
// daily.js), draws it, and tells the page its name and modes so the page can show them in its header.
// The frame cannot reach Family Hub itself: the page only ever hears a name, an id and a list of modes.
(function () {
  'use strict';
  const pin = new URLSearchParams(location.search).get('pin') || '';
  const canvas = document.getElementById('visual');
  let started = false, control = null, shown = null;

  const all = () => (Array.isArray(window.VISUALS) ? window.VISUALS : []).filter((v) => v && typeof v.draw === 'function' && v.id);
  const ready = (v) => !(v.needs || []).includes('place') || window.HUB_PLACE;

  function pick() {
    if (pin) { const v = all().find((x) => x.id === pin); if (v) return v; }
    const list = all().filter(ready);
    if (!list.length) return null;
    // daily.js comes from the visuals repository; without it, one a day in turn.
    if (typeof visualForDay === 'function') return visualForDay(list);
    const d = new Date();
    return list[Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000) % list.length];
  }

  function tell() {
    parent.postMessage({
      type: 'hub-visual', id: shown ? shown.id : '', name: shown ? String(shown.name || shown.id) : '',
      modes: shown && control && Array.isArray(shown.modes) ? shown.modes.map((m) => [String(m[0]), String(m[1])]) : [],
      mode: control && typeof control.mode === 'function' ? String(control.mode()) : '',
      list: all().map((v) => ({ id: String(v.id), name: String(v.name || v.id), needs: (v.needs || []).map(String) })),
    }, '*');
  }

  function start() {
    started = true;
    shown = pick();
    if (!shown) {
      const c = canvas.getContext('2d');
      c.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--dim') || '#888';
      c.font = '600 14px sans-serif';
      c.fillText('NO VISUALS YET', 28, 40);
      tell();
      return;
    }
    canvas.setAttribute('aria-label', String(shown.name || shown.id) + ", today's visual");
    try { control = shown.draw(canvas) || null; } catch (e) { control = null; }
    tell();
  }

  addEventListener('message', (e) => {
    if (e.source !== parent || !e.data || typeof e.data !== 'object') return;
    const d = e.data;
    if (d.type === 'hub-look') {
      const root = document.documentElement;
      for (const [k, v] of Object.entries(d.tokens || {})) if (/^--[a-z0-9-]+$/.test(k)) root.style.setProperty(k, String(v));
      if (d.place && isFinite(d.place.lat) && isFinite(d.place.lon)) window.HUB_PLACE = { name: String(d.place.name || ''), lat: +d.place.lat, lon: +d.place.lon };
      if (!started) start();
    } else if (d.type === 'hub-mode' && control && typeof control.setMode === 'function') {
      control.setMode(String(d.mode));
      tell();
    }
  });
  // Opened on its own rather than inside Family Hub: start after a moment with the default colours.
  setTimeout(() => { if (!started) start(); }, 2000);
  parent.postMessage({ type: 'hub-visual-ready' }, '*');
})();
