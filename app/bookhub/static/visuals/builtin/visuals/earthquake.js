// Earthquakes: this week's earthquakes (USGS live feed) on a slowly turning line-drawn globe, in the
// Scope's style. Needs d3 and topojson-client loaded first, and the world-atlas land outline.
// The feed is kept in the browser for 30 minutes, and the last copy is used if the internet drops.
(function () {
  const FEED = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson';   // magnitude 2.5 and up, past 7 days
  const LAND = 'node_modules/world-atlas/land-110m.json';
  const REFRESH = 30 * 60 * 1000;   // fetch the feed again after 30 minutes
  const SPIN = 3;                   // globe turn, degrees per second
  // the globe starts facing home (window.HUB_PLACE, as for Weather), or the Pacific when there is none
  const START_LON = () => (window.HUB_PLACE ? +window.HUB_PLACE.lon : 160);
  const VIEW_LAT = () => (window.HUB_PLACE ? Math.max(-45, Math.min(45, +window.HUB_PLACE.lat * 0.5)) : 0);

  // fetch JSON, keeping a copy in the browser; falls back to the copy if the network fails
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

  // feed and outline are shared by every copy of this visual on the page
  let land = null, quakes = null, failed = false, loadedAt = 0;
  function load() {
    loadedAt = Date.now();
    if (!land) fetch(LAND).then(r => r.json()).then(w => { land = topojson.feature(w, w.objects.land); }).catch(() => {});
    cachedJson('visual.quakes', FEED, REFRESH).then(f => {
      quakes = f.features.map(q => ({ lon: q.geometry.coordinates[0], lat: q.geometry.coordinates[1], mag: q.properties.mag, time: q.properties.time,
        where: (q.properties.place || '').split(', ').pop() })).filter(q => q.mag != null).sort((a, b) => b.mag - a.mag);
      failed = false;
    }).catch(() => { failed = true; });
  }
  const ago = ms => { const h = (Date.now() - ms) / 3600000; return h < 1 ? 'NOW' : h < 24 ? Math.round(h) + 'H AGO' : Math.round(h / 24) + 'D AGO'; };

  function earthquakes(cv) {
    const c = cv.getContext('2d'), W = cv.width, H = cv.height;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const projection = d3.geoOrthographic().scale(H * 0.43).translate([W * 0.34, H / 2]).clipAngle(90);
    const path = d3.geoPath(projection, c), graticule = d3.geoGraticule10(), sphere = { type: 'Sphere' };
    if (!loadedAt || Date.now() - loadedAt > REFRESH) load();
    let secs = 0, lastT = 0;

    function frame(t) {
      if (!cv.isConnected) return;
      const dt = lastT ? Math.min(0.1, (t - lastT) / 1000) : 0; lastT = t;
      if (!still) secs += dt;
      if (Date.now() - loadedAt > REFRESH) load();

      const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
      const INK = v('--ink'), D = v('--line2'), L = v('--line'), G = v('--grid') || L, A = v('--accent'), BG = v('--bg'), DIM = v('--dim') || D;
      const LABEL = v('--font-label') || 'sans-serif';
      const text = (s, x, y, col, size, weight = 600, stretch = 'condensed') => { c.fillStyle = col; c.font = weight + ' ' + size + 'px ' + LABEL; c.fontStretch = stretch; c.fillText(s, x, y); };

      // grid paper
      c.fillStyle = BG; c.fillRect(0, 0, W, H);
      c.fillStyle = G; for (let x = 0; x <= W; x += 20) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 20) c.fillRect(0, y, W, 1);
      c.fillStyle = L; for (let x = 0; x <= W; x += 100) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 100) c.fillRect(0, y, W, 1);

      // the globe: a clean disc, lines of latitude and longitude, land outlines
      projection.rotate([-START_LON() - secs * SPIN, -VIEW_LAT()]);
      c.beginPath(); path(sphere); c.fillStyle = BG; c.fill();
      c.beginPath(); path(graticule); c.strokeStyle = L; c.lineWidth = 1; c.stroke();
      if (land) { c.beginPath(); path(land); c.globalAlpha = 0.18; c.fillStyle = D; c.fill(); c.globalAlpha = 1; c.strokeStyle = D; c.lineWidth = 0.8; c.stroke(); }
      c.beginPath(); path(sphere); c.strokeStyle = INK; c.lineWidth = 1.2; c.stroke();

      // quakes on the side facing us: a dot sized by magnitude; the last day's pulse; the largest in the accent
      if (quakes) {
        const centre = [START_LON() + secs * SPIN, VIEW_LAT()];
        for (let n = quakes.length - 1; n >= 0; n--) {
          const q = quakes[n];
          if (d3.geoDistance([q.lon, q.lat], centre) > Math.PI / 2 - 0.02) continue;
          const [x, y] = projection([q.lon, q.lat]), r = 1.2 + Math.max(0, q.mag - 2.5) * 1.3, top = n === 0;
          if (Date.now() - q.time < 86400000 && !still) {
            const p = (secs * 0.5 + (q.time % 1000) / 1000) % 1;
            c.beginPath(); c.arc(x, y, r + p * 14, 0, 2 * Math.PI); c.globalAlpha = 1 - p; c.strokeStyle = top ? A : INK; c.lineWidth = 1; c.stroke(); c.globalAlpha = 1;
          }
          c.beginPath(); c.arc(x, y, r, 0, 2 * Math.PI); c.fillStyle = top ? A : INK; c.fill();
          if (top) { c.beginPath(); c.arc(x, y, r + 5, 0, 2 * Math.PI); c.strokeStyle = A; c.lineWidth = 1; c.stroke(); text('M' + q.mag.toFixed(1), x + r + 8, y - r - 4, A, 12); }
        }
      }

      // the week in numbers, beside the globe
      const px = W * 0.66;
      text('EARTHQUAKES', 28, 28, INK, 14);
      if (quakes) {
        text(String(quakes.length), px, 82, INK, 46, 800, 'expanded');
        text('THIS WEEK, MAGNITUDE 2.5 AND UP', px, 102, DIM, 12);
        c.fillStyle = INK; c.fillRect(px, 118, W * 0.3, 1.5);
        quakes.slice(0, 6).forEach((q, n) => {
          const y = 140 + n * 24, where = q.where.length > 22 ? q.where.slice(0, 21) + '…' : q.where;
          text('M' + q.mag.toFixed(1), px, y, n === 0 ? A : INK, 13);
          text(where.toUpperCase(), px + 44, y, INK, 13);
          c.textAlign = 'right'; text(ago(q.time), px + W * 0.3, y, DIM, 12); c.textAlign = 'left';
          c.fillStyle = L; c.fillRect(px, y + 8, W * 0.3, 1);
        });
      } else text(failed ? 'NO CONNECTION TO THE FEED' : 'LOADING THE FEED', px, 82, DIM, 13);

      if (!still || !quakes || !land) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  (window.VISUALS = window.VISUALS || []).push({ id: 'earthquakes', name: 'Earthquakes', draw: earthquakes });
})();
