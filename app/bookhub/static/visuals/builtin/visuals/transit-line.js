// Inner System Transit Line: Tracks the relative linear displacement of Mercury and Venus 
// away from the Sun as seen from Earth. Plots past and future elongations as a shifting matrix.

const DAYS_SPAN = 60; // Tracks a timeline window of 60 days total
const TIME_STEP_MILLIS = 86400000 * 0.5; // Moves forward half a day per frame step inside loop
const START_TRANSIT = new Date();

function transitLine(cv) {
  const c = cv.getContext('2d'), W = cv.width, H = cv.height;
  const cx = W / 2, cy = H / 2;
  const scaleX = W * 0.45; // Max elongation (~48° for Venus) mapped near the canvas edge
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;

  let t0 = 0;
  function frame(t) {
    if (!cv.isConnected) return;
    if (!t0) t0 = t;

    // Shift window base over time or remain static
    const elapsedDays = still ? 0 : ((t - t0) / 1000);
    const centerDate = new Date(START_TRANSIT.getTime() + elapsedDays * 86400000);

    const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
    const INK = v('--ink') || '#000', D = v('--line2') || '#ccc', BG = v('--bg') || '#fff', ACCENT = v('--accent') || '#ff0055';
    const LABEL = v('--font-label') || 'sans-serif';

    c.fillStyle = BG; c.fillRect(0, 0, W, H);

    // Draw central alignment axis (The Sun Line)
    c.beginPath(); c.strokeStyle = D; c.lineWidth = 1;
    c.setLineDash([4, 4]);
    c.moveTo(cx, 40); c.lineTo(cx, H - 40);
    c.stroke(); c.setLineDash([]);

    // Sample discrete dates across the timeline window vertical resolution
    const steps = 15;
    for (let i = 0; i <= steps; i++) {
      const fraction = (i / steps) - 0.5; // range -0.5 to +0.5
      const targetDate = new Date(centerDate.getTime() + fraction * DAYS_SPAN * 86400000);
      const yPos = 60 + (i / steps) * (H - 120);

      // Draw horizontal cross-line for time grids
      c.beginPath(); c.strokeStyle = v('--grid') || '#eee';
      c.moveTo(40, yPos); c.lineTo(W - 40, yPos);
      c.stroke();

      // Plot elongation tracks for Mercury and Venus
      ['Mercury', 'Venus'].forEach(name => {
        // Elongation returns an angle object. The visibility depends on the eastern/western geometric elongation sign
        const elong = Astronomy.Elongation(name, targetDate);
        
        // Use the heliocentric coordinates to determine whether the planet is East (Left/Evening) or West (Right/Morning) of the sun
        const pE = Astronomy.HelioVector('Earth', targetDate);
        const pP = Astronomy.HelioVector(name, targetDate);
        const crossProduct = (pE.x * pP.y) - (pE.y * pP.x);
        
        const angleRad = (elong.elongation * Math.PI / 180) * (crossProduct > 0 ? 1 : -1);
        const xPos = cx + Math.sin(angleRad) * scaleX;

        c.beginPath();
        c.fillStyle = name === 'Venus' ? ACCENT : INK;
        c.arc(xPos, yPos, name === 'Venus' ? 4 : 2.5, 0, Math.PI * 2);
        c.fill();
        
        // Label the active current point across the centre threshold row
        if (i === Math.floor(steps / 2)) {
          c.font = 'condensed 10px ' + LABEL;
          c.fillText(name.toUpperCase(), xPos - 15, yPos - 8);
        }
      });
    }

    // Text labels
    c.fillStyle = INK; c.font = '600 14px ' + LABEL;
    c.fillText('INNER PLANET ELONGATION MATRIX', 28, 28);
    c.font = 'condensed 11px ' + LABEL;
    c.fillStyle = D;
    c.fillText('← WEST (MORNING)', 40, H - 16);
    c.fillText('EAST (EVENING) →', W - 140, H - 16);

    if (!still) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

(window.VISUALS = window.VISUALS || []).push({ id: 'transit-line', name: 'Transit line', draw: transitLine });
