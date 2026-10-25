// Weather: today's wind and temperature at home (Open-Meteo, free, no key), in the Scope's style.
// Wind streaks drift across the grid in the real direction and at a pace set by the real speed;
// below them, today's temperature by the hour with a marker at now. Kept for 30 minutes in the browser.
// Home is window.HUB_PLACE ({ name, lat, lon }): Family Hub passes it in from HOME_PLACE, HOME_LAT and
// HOME_LON in its .env; on the test page it comes from place.local.js. It is never written in here, so
// this file can be shared without saying where anyone lives.
(function () {
  const REFRESH = 30 * 60 * 1000;
  const feed = p => 'https://api.open-meteo.com/v1/forecast?latitude=' + p.lat + '&longitude=' + p.lon +
    '&current=temperature_2m,wind_speed_10m,wind_direction_10m&hourly=temperature_2m&timezone=auto&forecast_days=1';
  let PLACE = null;

  async function cachedJson(key, url, maxAge) {
    let kept = null;
    try { kept = JSON.parse(localStorage.getItem(key)); } catch (e) {}
    if (kept && Date.now() - kept.at < maxAge) return kept.data;
    try {
      const data = await (await fetch(url)).json();
      try { localStorage.setItem(key, JSON.stringify({ at: Date.now(), data })); } catch (e) {}
      return data;
    } catch (e) { if (kept) return kept.data; throw e; }
  }

  let wx = null, failed = false, loadedAt = 0;
  function load() { loadedAt = Date.now(); cachedJson('visual.weather.' + PLACE.lat + ',' + PLACE.lon, feed(PLACE), REFRESH).then(d => { wx = d; failed = false; }).catch(() => { failed = true; }); }
  const POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

  function weather(cv) {
    const c = cv.getContext('2d'), W = cv.width, H = cv.height;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const SKY = H * 0.6;                                    // streaks above this line, the temperature chart below
    let seed = 5; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const streaks = Array.from({ length: 150 }, () => ({ x: rnd() * W, y: 40 + rnd() * (SKY - 50), age: rnd() }));
    PLACE = window.HUB_PLACE || null;
    if (PLACE && (!loadedAt || Date.now() - loadedAt > REFRESH)) load();
    let secs = 0, lastT = 0;

    function frame(t) {
      if (!cv.isConnected) return;
      const dt = lastT ? Math.min(0.1, (t - lastT) / 1000) : 0; lastT = t;
      if (!still) secs += dt;
      if (PLACE && Date.now() - loadedAt > REFRESH) load();

      const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
      const INK = v('--ink'), D = v('--line2'), L = v('--line'), G = v('--grid') || L, A = v('--accent'), BG = v('--bg'), DIM = v('--dim') || D;
      const LABEL = v('--font-label') || 'sans-serif';
      const text = (s, x, y, col, size, weight = 600, stretch = 'condensed') => { c.fillStyle = col; c.font = weight + ' ' + size + 'px ' + LABEL; c.fontStretch = stretch; c.fillText(s, x, y); };

      c.fillStyle = BG; c.fillRect(0, 0, W, H);
      c.fillStyle = G; for (let x = 0; x <= W; x += 20) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 20) c.fillRect(0, y, W, 1);
      c.fillStyle = L; for (let x = 0; x <= W; x += 100) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 100) c.fillRect(0, y, W, 1);
      text('WEATHER', 28, 28, INK, 14);

      if (!PLACE) { text('NO HOME TOWN SET (HOME_PLACE, HOME_LAT, HOME_LON IN .ENV)', 28, 60, DIM, 13); return; }
      if (!wx) { text(failed ? 'NO CONNECTION TO THE FORECAST' : 'LOADING THE FORECAST', 28, 60, DIM, 13); requestAnimationFrame(frame); return; }
      const now = wx.current, kmh = now.wind_speed_10m, from = now.wind_direction_10m;

      // wind: the feed gives where it blows FROM, so the streaks travel the opposite way (north is up)
      const to = (from + 180) * Math.PI / 180, ux = Math.sin(to), uy = -Math.cos(to);
      const pace = 12 + kmh * 3, len = 6 + Math.min(30, kmh * 0.9);
      c.lineWidth = 1; c.lineCap = 'round';
      for (const s of streaks) {
        s.x += ux * pace * dt; s.y += uy * pace * dt; s.age += dt * 0.4;
        // a streak that leaves the sky, or gets old, starts again somewhere upwind
        if (s.x < -40 || s.x > W + 40 || s.y < 30 || s.y > SKY || s.age > 1) {
          s.age = 0;
          if (Math.abs(ux) > Math.abs(uy)) { s.x = ux > 0 ? -20 + rnd() * W * 0.5 : W * 0.5 + rnd() * W * 0.5 + 20; s.y = 40 + rnd() * (SKY - 50); }
          else { s.x = rnd() * W; s.y = uy > 0 ? 40 + rnd() * (SKY - 50) * 0.5 : SKY - rnd() * (SKY - 50) * 0.5; }
        }
        c.globalAlpha = Math.sin(Math.min(1, s.age) * Math.PI) * 0.8;
        c.beginPath(); c.moveTo(s.x, s.y); c.lineTo(s.x - ux * len, s.y - uy * len); c.strokeStyle = D; c.stroke();
      }
      c.globalAlpha = 1; c.lineCap = 'butt';

      // now: big temperature and the wind in words, top right on a plain plate so streaks don't run through it
      const px = W * 0.66;
      c.fillStyle = BG; c.fillRect(px - 14, 30, W - px - 6, 112);
      text(Math.round(now.temperature_2m) + '°', px, 86, INK, 56, 800, 'expanded');
      text('WIND ' + Math.round(kmh) + ' KM/H FROM ' + POINTS[Math.round(from / 22.5) % 16], px, 110, INK, 13);
      const temps = wx.hourly.temperature_2m, hi = Math.max(...temps), lo = Math.min(...temps);
      text(PLACE.name.toUpperCase() + ' · HIGH ' + Math.round(hi) + '° · LOW ' + Math.round(lo) + '°', px, 130, DIM, 12);

      // today's temperature by the hour, with now marked in the accent
      const x0 = 40, x1 = W - 40, y0 = SKY + 24, y1 = H - 34;
      const X = h => x0 + (x1 - x0) * h / 23, Y = tC => y1 - (y1 - y0) * (tC - lo) / Math.max(1, hi - lo);
      c.fillStyle = INK; c.fillRect(x0, SKY + 8, x1 - x0, 1.5);
      c.beginPath(); temps.forEach((tC, h) => h ? c.lineTo(X(h), Y(tC)) : c.moveTo(X(h), Y(tC))); c.strokeStyle = INK; c.lineWidth = 1.2; c.stroke();
      for (let h = 0; h < 24; h += 3) { c.fillStyle = L; c.fillRect(X(h), y1 + 4, 1, 5); text((h % 12 || 12) + (h < 12 ? 'AM' : 'PM'), X(h) - 8, H - 12, DIM, 11); }
      const nowH = new Date(now.time).getHours() + new Date(now.time).getMinutes() / 60;
      c.fillStyle = A; c.fillRect(X(nowH), y0 - 6, 1.5, y1 - y0 + 10);
      c.beginPath(); c.arc(X(nowH), Y(now.temperature_2m), 4, 0, 2 * Math.PI); c.fill();

      if (!still || !secs) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // needs: the daily pick leaves it out until a home town is set
  (window.VISUALS = window.VISUALS || []).push({ id: 'weather', name: 'Weather', draw: weather, needs: ['place'] });
})();
