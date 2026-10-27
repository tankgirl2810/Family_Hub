// Runs inside the sandboxed frame that shows a visual (see visuals.py). It waits for the page to send the
// colours and the household's home town, picks the visual (one pinned by an admin, or today's from
// daily.js), draws it, and tells the page its name and modes so the page can show them in its header.
// The frame cannot reach Family Hub itself: the page only ever hears a name, an id and a list of modes.
(function () {
  'use strict';
  const pin = new URLSearchParams(location.search).get('pin') || '';
  const canvas = document.getElementById('visual');
  let started = false, control = null, shown = null;

  const all = () => (Array.isArray(window.VISUALS) ? window.VISUALS : []).filter((visual) => visual && typeof visual.draw === 'function' && visual.id);
  const ready = (visual) => !(visual.needs || []).includes('place') || window.HUB_PLACE;

  function pick() {
    if (pin) { const visual = all().find((candidate) => candidate.id === pin); if (visual) return visual; }
    const list = all().filter(ready);
    if (!list.length) return null;
    // daily.js comes from the visuals repository; without it, one a day in turn.
    if (typeof visualForDay === 'function') return visualForDay(list);
    const today = new Date();
    return list[Math.floor(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) / 86400000) % list.length];
  }

  function tell() {
    parent.postMessage({
      type: 'hub-visual', id: shown ? shown.id : '', name: shown ? String(shown.name || shown.id) : '',
      modes: shown && control && Array.isArray(shown.modes) ? shown.modes.map((mode) => [String(mode[0]), String(mode[1])]) : [],
      mode: control && typeof control.mode === 'function' ? String(control.mode()) : '',
      list: all().map((visual) => ({ id: String(visual.id), name: String(visual.name || visual.id), needs: (visual.needs || []).map(String) })),
    }, '*');
  }

  function start() {
    started = true;
    shown = pick();
    if (!shown) {
      const pen = canvas.getContext('2d');
      pen.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--dim') || '#888';
      pen.font = '600 14px sans-serif';
      pen.fillText('NO VISUALS YET', 28, 40);
      tell();
      return;
    }
    canvas.setAttribute('aria-label', String(shown.name || shown.id) + ", today's visual");
    try { control = shown.draw(canvas) || null; } catch (err) { control = null; }
    tell();
  }

  addEventListener('message', (event) => {
    if (event.source !== parent || !event.data || typeof event.data !== 'object') return;
    const message = event.data;
    if (message.type === 'hub-look') {
      const root = document.documentElement;
      for (const [token, value] of Object.entries(message.tokens || {})) if (/^--[a-z0-9-]+$/.test(token)) root.style.setProperty(token, String(value));
      if (message.place && isFinite(message.place.lat) && isFinite(message.place.lon)) window.HUB_PLACE = { name: String(message.place.name || ''), lat: +message.place.lat, lon: +message.place.lon };
      if (!started) start();
    } else if (message.type === 'hub-mode' && control && typeof control.setMode === 'function') {
      control.setMode(String(message.mode));
      tell();
    }
  });
  // Opened on its own rather than inside Family Hub: start after a moment with the default colours.
  setTimeout(() => { if (!started) start(); }, 2000);
  parent.postMessage({ type: 'hub-visual-ready' }, '*');
})();
