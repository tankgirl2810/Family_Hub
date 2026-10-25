// Live sky: the real planets around the Sun, drawn like the Scope's Orbit mode in the Minimalist Family Hub.
// Each planet sits at its real direction from the Sun (Astronomy Engine), on an idealised ring at its
// average distance; every ring gets its own tilt, which drifts slowly so the outer system moves too.
// Pluto, the asteroid belt, Jupiter's Trojans, the Kuiper Belt and a faint star field fill the space.
// Two modes: a journey that runs forward from 3000 years ago (and remembers where it got to), and today.
// Colours and the label face are read from the page's tokens each frame, so it follows the hub's theme
// and accent. Needs astronomy.browser.min.js first.

// fixed settings
const DAYS_PER_SECOND = 35;     // journey speed: 35 days of planet movement per second
const YEARS_BACK = 3000;        // the journey starts this many years before today, and starts again on reaching today
const DRIFT = 0.07;             // how far each ring's tilt sways either way (radians)
const TRAIL = 0.35;             // trail behind each planet: how many seconds of its path to show (capped below)
const MOON_UP_TO = 5;           // the Moon is drawn only at this speed or slower; faster, it just flickers
const SAVE_KEY = 'liveSky';     // where this browser remembers the mode and how far the journey has got

const DAY = 86400000;
const journeyStart = () => Date.now() - YEARS_BACK * 365.25 * DAY;
const yearText = ms => { const y = new Date(ms).getUTCFullYear(); return y <= 0 ? (1 - y) + ' BC' : 'AD ' + y; };

// name, average distance from the Sun (AU), ring tilt (radians), dot radius, style, sway time (seconds)
const PLANETS = [
  ['Mercury', 0.387, -0.30, 3, 'ink',     23],
  ['Venus',   0.723,  0.22, 4, 'ink',     29],
  ['Earth',   1.000, -0.12, 5, 'accent',  37],
  ['Mars',    1.524,  0.10, 4, 'ink',     31],
  ['Jupiter', 5.203, -0.20, 7, 'ink',     43],
  ['Saturn',  9.537,  0.16, 6, 'hollow',  53],
  ['Uranus', 19.19,  -0.06, 4, 'ink',     47],
  ['Neptune', 30.07,  0.08, 4, 'ink',     59],
  ['Pluto',   39.48, -0.34, 3, 'hollow',  67],
];

// the browser's memory for this visual; private windows may refuse it, so every use is guarded
const memory = {
  get() { try { return JSON.parse(localStorage.getItem(SAVE_KEY)) || {}; } catch (e) { return {}; } },
  set(v) { try { localStorage.setItem(SAVE_KEY, JSON.stringify(Object.assign(memory.get(), v))); } catch (e) {} },
};

function liveSky(cv) {
  const c = cv.getContext('2d'), W = cv.width, H = cv.height;
  const cx = W * 0.42, cy = H / 2, FLAT = 0.38;            // centre a little left, rings squashed to read as tilted
  const R0 = 24, R1 = W * 0.38;                             // innermost and outermost ring widths in pixels
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // spread the real distances so the inner planets get room (power 0.3)
  const P = 0.3, lo = Math.pow(PLANETS[0][1], P), hi = Math.pow(PLANETS[PLANETS.length - 1][1], P);
  const ringR = au => Math.max(8, R0 + (R1 - R0) * (Math.pow(au, P) - lo) / (hi - lo));
  // a point on a tilted ring: angle a (real direction), ring width rx, tilt rot
  const onRing = (a, rx, rot) => { const x = Math.cos(a) * rx, y = -Math.sin(a) * rx * FLAT; return [cx + x * Math.cos(rot) - y * Math.sin(rot), cy + x * Math.sin(rot) + y * Math.cos(rot)]; };
  // how far round its ring each planet travels per second on the journey, for the trails
  const spin = Object.fromEntries(PLANETS.map(([name]) => [name, 2 * Math.PI * DAYS_PER_SECOND / Astronomy.PlanetOrbitalPeriod(name)]));

  // small bodies, scattered once from a fixed seed so they look the same on every visit
  let seed = 11; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const speedAt = a => 2 * Math.PI / (Math.pow(a, 1.5) * 365.25);   // radians per day at a distance (Kepler)
  // the main asteroid belt, between Mars and Jupiter
  const BELT = Array.from({ length: 170 }, () => { const a = 2.1 + rnd() * 1.2; return [a, rnd() * 2 * Math.PI, speedAt(a)]; });
  // Jupiter's Trojans: two swarms sharing its orbit, 60° ahead and 60° behind it
  const TROJANS = Array.from({ length: 70 }, (_, n) => [(n % 2 ? 1 : -1) * Math.PI / 3 + (rnd() - 0.5) * 0.6, 5.2 + (rnd() - 0.5) * 0.5]);
  // the Kuiper Belt, out around Pluto and past it; each dot gets a little tilt of its own so it reads as a band
  const KUIPER = Array.from({ length: 220 }, () => { const a = 36 + rnd() * 12; return [a, rnd() * 2 * Math.PI, speedAt(a), (rnd() - 0.5) * 0.12]; });
  // a sparse field of tiny star crosses, kept clear of the rings in the middle and the furniture on the right
  const STARS = [];
  while (STARS.length < 34) {
    const x = 12 + rnd() * W * 0.82, y = 12 + rnd() * (H - 24), dx = (x - cx) / (R1 + 26), dy = (y - cy) / ((R1 + 26) * FLAT * 1.6);
    if (dx * dx + dy * dy > 1 && !(x < 130 && (y < 44 || y > H - 40))) STARS.push([x, y, rnd() * 6.28, 1.5 + rnd() * 2]);
  }

  // where the journey has got to, and which mode is showing; both come back from the browser's memory
  const saved = memory.get();
  let mode = saved.mode === 'today' ? 'today' : 'journey';
  let sim = typeof saved.journey === 'number' && saved.journey < Date.now() ? saved.journey : journeyStart();
  const save = () => memory.set({ mode, journey: sim });
  addEventListener('pagehide', save);

  let secs = 0, lastT = 0, lastSave = 0;
  function frame(t) {
    if (!cv.isConnected) { save(); removeEventListener('pagehide', save); return; }   // stops when the canvas leaves the page
    const dt = lastT ? Math.min(0.1, (t - lastT) / 1000) : 0; lastT = t;        // a hidden tab doesn't jump ahead
    if (!still) secs += dt;
    if (mode === 'journey' && !still) { sim += dt * DAYS_PER_SECOND * DAY; if (sim > Date.now()) sim = journeyStart(); }
    if (t - lastSave > 1000) { save(); lastSave = t; }
    const date = new Date(mode === 'today' ? Date.now() : sim), days = (date.getTime() - Date.UTC(2000, 0, 1)) / DAY;
    const moving = mode === 'journey' && !still;

    const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
    const INK = v('--ink'), D = v('--line2'), L = v('--line'), G = v('--grid') || L, A = v('--accent'), BG = v('--bg'), DIM = v('--dim') || D;
    const LABEL = v('--font-label') || 'sans-serif';
    const ln = (x0, y0, x1, y1, col) => { c.beginPath(); c.strokeStyle = col; c.lineWidth = 1; c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); };
    const dot = (x, y, r, col, hollow) => { c.beginPath(); c.arc(x, y, r, 0, 6.2832); if (hollow) { c.fillStyle = BG; c.fill(); c.strokeStyle = col; c.lineWidth = 1; c.stroke(); } else { c.fillStyle = col; c.fill(); } };
    const label = (s, x, y, col, size) => { c.fillStyle = col; c.font = '600 ' + size + 'px ' + LABEL; c.fontStretch = 'condensed'; c.fillText(s, x, y); };
    const speck = (x, y, col) => { c.fillStyle = col; c.fillRect(x - 0.75, y - 0.75, 1.5, 1.5); };
    // each ring's tilt right now: its base tilt plus a slow sway on its own timing
    const tilt = (rot, sway) => rot + DRIFT * Math.sin(secs * 2 * Math.PI / sway + rot * 10);

    // grid paper, as in every Scope mode
    c.fillStyle = BG; c.fillRect(0, 0, W, H);
    c.fillStyle = G; for (let x = 0; x <= W; x += 20) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 20) c.fillRect(0, y, W, 1);
    c.fillStyle = L; for (let x = 0; x <= W; x += 100) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 100) c.fillRect(0, y, W, 1);

    // faint star crosses in the empty corners, twinkling slowly
    c.strokeStyle = D; c.lineWidth = 1;
    for (const [x, y, ph, s] of STARS) {
      c.globalAlpha = 0.35 + 0.35 * Math.sin(secs * 0.8 + ph);
      c.beginPath(); c.moveTo(x - s, y); c.lineTo(x + s, y); c.moveTo(x, y - s); c.lineTo(x, y + s); c.stroke();
    }
    c.globalAlpha = 1;

    // the Sun, with a hairline halo
    dot(cx, cy, 10, INK);
    c.beginPath(); c.strokeStyle = D; c.lineWidth = 1; c.arc(cx, cy, 17, 0, 6.2832); c.stroke();

    // rings, each swaying on its own; Earth's in ink, Pluto's dashed
    for (const [name, au, rot, , , sway] of PLANETS) {
      const rx = ringR(au);
      c.setLineDash(name === 'Pluto' ? [2, 5] : []);
      c.beginPath(); c.strokeStyle = name === 'Earth' ? INK : D; c.lineWidth = 1; c.ellipse(cx, cy, rx, rx * FLAT, tilt(rot, sway), 0, 6.2832); c.stroke();
    }
    c.setLineDash([]);

    // the Kuiper Belt, faint, out past Neptune
    c.globalAlpha = 0.6;
    for (const [a, start, speed, own] of KUIPER) { const [x, y] = onRing(start + speed * days, ringR(a), tilt(0.08 + own, 71)); speck(x, y, D); }
    c.globalAlpha = 1;

    // the main asteroid belt, tilted between Mars's ring and Jupiter's
    for (const [a, start, speed] of BELT) { const [x, y] = onRing(start + speed * days, ringR(a), tilt(0.10 + (a - 1.524) / (5.203 - 1.524) * -0.30, 41)); speck(x, y, D); }

    // planets at their real direction on this date, each with a short fading trail along its ring on the journey
    for (const [name, au, rot, r, style, sway] of PLANETS) {
      const p = Astronomy.HelioVector(name, date), a = Math.atan2(p.y, p.x), rx = ringR(au), rt = tilt(rot, sway);
      const col = style === 'accent' ? A : INK;
      if (moving) {
        const back = Math.min(spin[name] * TRAIL, 1.4);      // the fastest planets' trails stop at about a quarter turn
        c.strokeStyle = col; c.lineWidth = Math.max(1, r * 0.7); c.lineCap = 'round';
        for (let i = 0; i < 12; i++) {
          const [x0, y0] = onRing(a - back * i / 12, rx, rt), [x1, y1] = onRing(a - back * (i + 1) / 12, rx, rt);
          c.globalAlpha = 0.45 * (1 - i / 12); c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke();
        }
        c.globalAlpha = 1; c.lineCap = 'butt';
      }
      // Jupiter's Trojans ride its ring, 60° either side of it
      if (name === 'Jupiter') for (const [off, ta] of TROJANS) { const [x, y] = onRing(a + off, ringR(ta), rt); speck(x, y, D); }
      const [x, y] = onRing(a, rx, rt);
      dot(x, y, r, col, style === 'hollow');
      if (name === 'Saturn') { c.beginPath(); c.strokeStyle = INK; c.lineWidth = 1; c.ellipse(x, y, r * 2.1, r * 0.7, -0.3, 0, 6.2832); c.stroke(); }
      if (name === 'Earth') {
        // the Moon on a tiny ring round Earth, when time runs slowly enough to follow it (always, for today)
        if (!moving || DAYS_PER_SECOND <= MOON_UP_TO) {
          const m = Astronomy.GeoMoon(date), ma = Math.atan2(m.y, m.x);
          c.beginPath(); c.strokeStyle = D; c.lineWidth = 1; c.ellipse(x, y, 11, 11 * FLAT, rt, 0, 6.2832); c.stroke();
          dot(x + Math.cos(ma) * 11 * Math.cos(rt) + Math.sin(ma) * 11 * FLAT * Math.sin(rt), y + Math.cos(ma) * 11 * Math.sin(rt) - Math.sin(ma) * 11 * FLAT * Math.cos(rt), 1.8, INK);
        }
        label('EARTH', x + r + 6, y - r - 3, INK, 12);
      }
    }

    // the side furniture from the Orbit mode: a bracket, a column of squares, a dotted arc
    ln(W * 0.86, 30, W * 0.94, 30, INK); ln(W * 0.94, 30, W * 0.94, H - 30, INK);
    for (let i = 0; i < 5; i++) { c.strokeStyle = INK; c.lineWidth = 1; c.strokeRect(W * 0.88 + 0.5, 60 + i * 26 + 0.5, 16, 16); if (i < 2) { c.fillStyle = INK; c.fillRect(W * 0.88 + 4, 64 + i * 26, 9, 9); } }
    c.fillStyle = D; for (let i = 0; i < 30; i++) { const a = -1.2 + i * 0.05; c.fillRect(W * 0.82 + Math.cos(a) * 120 - 1, H * 0.5 + Math.sin(a) * 120 - 1, 2, 2); }

    label('LIVE SKY', 28, 28, INK, 14);
    label(yearText(date.getTime()), 28, H - 16, DIM, 14);
    if (!still || !lastSave) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // the page uses these to show and switch the mode
  return {
    mode: () => mode,
    setMode(m) { mode = m === 'today' ? 'today' : 'journey'; save(); if (still) requestAnimationFrame(frame); },
  };
}

// add it to the list of visuals, with its two modes; the page decides which one to start
(window.VISUALS = window.VISUALS || []).push({
  id: 'live-sky', name: 'Live sky', draw: liveSky,
  modes: [['journey', 'From ' + yearText(journeyStart())], ['today', 'Today']],
});
