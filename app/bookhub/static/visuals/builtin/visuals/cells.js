// Cells: a sheet of living cells drawn as a Voronoi diagram (d3-delaunay), in the Scope's style.
// Nuclei drift, every few seconds one cell divides while another is absorbed, and a microscope lens
// wanders across, highlighting the cell beneath it and outlining its neighbours. Needs d3 loaded first.
(function () {
  const COUNT = 64;          // how many cells
  const DIVIDE_EVERY = 5;    // seconds between divisions
  const GROW = 2.2;          // seconds a division (and an absorption) takes

  function cells(cv) {
    const c = cv.getContext('2d'), W = cv.width, H = cv.height;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let seed = 23; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const ease = k => k < 0 ? 0 : k > 1 ? 1 : k * k * (3 - 2 * k);
    // each nucleus: where it rests, how it wobbles, and (while dividing or being absorbed) where it is going
    const make = (x, y, from) => ({ x, y, ph: rnd() * 6.28, sp: 0.15 + rnd() * 0.25, wob: 6 + rnd() * 10, from, born: 0, dying: null });
    let cellsList = Array.from({ length: COUNT }, () => make(20 + rnd() * (W - 40), 20 + rnd() * (H - 40), null));
    let secs = 0, lastT = 0, nextDivide = DIVIDE_EVERY;

    const at = (p, s) => {
      const wx = p.x + Math.cos(s * p.sp + p.ph) * p.wob, wy = p.y + Math.sin(s * p.sp * 1.3 + p.ph) * p.wob;
      let x = wx, y = wy;
      if (p.from) { const k = ease((s - p.born) / GROW); x = p.from[0] + (wx - p.from[0]) * k; y = p.from[1] + (wy - p.from[1]) * k; }
      if (p.dying) { const k = ease((s - p.dying.t) / GROW), q = at(p.dying.into, s); x += (q[0] - x) * k; y += (q[1] - y) * k; }
      return [x, y];
    };

    function frame(t) {
      if (!cv.isConnected) return;
      const dt = lastT ? Math.min(0.1, (t - lastT) / 1000) : 0; lastT = t;
      if (!still) secs += dt;

      // one cell divides: a daughter grows out from its parent; elsewhere one cell is absorbed by a neighbour
      if (!still && secs > nextDivide) {
        nextDivide = secs + DIVIDE_EVERY;
        const live = cellsList.filter(p => !p.dying), parent = live[Math.floor(rnd() * live.length)], a = rnd() * 6.28;
        const kid = make(Math.min(W - 20, Math.max(20, parent.x + Math.cos(a) * 34)), Math.min(H - 20, Math.max(20, parent.y + Math.sin(a) * 34)), at(parent, secs));
        kid.born = secs; cellsList.push(kid);
        const victim = live[Math.floor(rnd() * live.length)];
        if (victim !== parent) {
          let near = null, best = 1e9;
          for (const q of live) { if (q === victim) continue; const d = (q.x - victim.x) ** 2 + (q.y - victim.y) ** 2; if (d < best) { best = d; near = q; } }
          victim.dying = { t: secs, into: near };
        }
      }
      cellsList = cellsList.filter(p => !p.dying || secs - p.dying.t < GROW);

      const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
      const INK = v('--ink'), D = v('--line2'), L = v('--line'), G = v('--grid') || L, A = v('--accent'), BG = v('--bg'), DIM = v('--dim') || D;
      const LABEL = v('--font-label') || 'sans-serif';

      c.fillStyle = BG; c.fillRect(0, 0, W, H);
      c.fillStyle = G; for (let x = 0; x <= W; x += 20) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 20) c.fillRect(0, y, W, 1);

      const pts = cellsList.map(p => at(p, secs));
      const delaunay = d3.Delaunay.from(pts), vor = delaunay.voronoi([0, 0, W, H]);

      // the lens wanders on a slow loop; the cell under it is hatched in the accent, its neighbours outlined in ink
      const lx = W * 0.5 + Math.cos(secs * 0.11) * W * 0.32, ly = H * 0.5 + Math.sin(secs * 0.17) * H * 0.28;
      const under = delaunay.find(lx, ly);
      for (const n of delaunay.neighbors(under)) { c.beginPath(); vor.renderCell(n, c); c.strokeStyle = INK; c.lineWidth = 1; c.stroke(); }
      c.save(); c.beginPath(); vor.renderCell(under, c); c.clip();
      c.strokeStyle = A; c.lineWidth = 1; c.globalAlpha = 0.7;
      for (let k = -H; k < W; k += 6) { c.beginPath(); c.moveTo(k, 0); c.lineTo(k + H, H); c.stroke(); }
      c.restore(); c.globalAlpha = 1;

      // every cell wall, then nuclei (a daughter's nucleus shows hollow while it grows)
      c.beginPath(); vor.render(c); c.strokeStyle = D; c.lineWidth = 1; c.stroke();
      c.beginPath(); vor.renderCell(under, c); c.strokeStyle = A; c.lineWidth = 1.5; c.stroke();
      pts.forEach(([x, y], n) => {
        const p = cellsList[n], young = p.from && secs - p.born < GROW;
        c.beginPath(); c.arc(x, y, n === under ? 3 : 2, 0, 6.2832);
        if (young) { c.fillStyle = BG; c.fill(); c.strokeStyle = INK; c.lineWidth = 1; c.stroke(); } else { c.fillStyle = n === under ? A : INK; c.fill(); }
      });

      // the microscope lens: a ring with crosshair ticks
      c.strokeStyle = INK; c.lineWidth = 1.2; c.beginPath(); c.arc(lx, ly, 42, 0, 6.2832); c.stroke();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { c.beginPath(); c.moveTo(lx + dx * 34, ly + dy * 34); c.lineTo(lx + dx * 50, ly + dy * 50); c.stroke(); }

      c.fillStyle = BG; c.fillRect(20, 12, 70, 24); c.fillRect(20, H - 32, 90, 24);
      c.fillStyle = INK; c.font = '600 14px ' + LABEL; c.fontStretch = 'condensed'; c.fillText('CELLS', 28, 28);
      c.fillStyle = DIM; c.fillText(cellsList.filter(p => !p.dying).length + ' CELLS', 28, H - 16);
      if (!still || !secs) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  (window.VISUALS = window.VISUALS || []).push({ id: 'cells', name: 'Cells', draw: cells });
})();
