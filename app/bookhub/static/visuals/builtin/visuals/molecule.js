// Molecule of the day: a real molecule drawn from its chemical notation (SmilesDrawer), in the Scope's
// style. The molecule sits on a bracketed plate with a slow scanning line; its name, formula and where
// you find it sit beside it. A different molecule each day. Needs smiles-drawer loaded first.
(function () {
  // name, SMILES (the molecule written as text), formula, where you'd find it
  const MOLECULES = [
    ['Caffeine', 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C', 'C8H10N4O2', 'Coffee, tea and cola'],
    ['Theobromine', 'CN1C=NC2=C1C(=O)NC(=O)N2C', 'C7H8N4O2', 'Chocolate and cacao'],
    ['Vanillin', 'COC1=C(C=CC(=C1)C=O)O', 'C8H8O3', 'Vanilla pods'],
    ['Capsaicin', 'CC(C)/C=C/CCCCC(=O)NCC1=CC(=C(C=C1)O)OC', 'C18H27NO3', 'Chilli peppers, the heat'],
    ['Menthol', 'CC1CCC(C(C1)O)C(C)C', 'C10H20O', 'Mint, the cool feeling'],
    ['Limonene', 'CC1=CCC(CC1)C(=C)C', 'C10H16', 'Orange and lemon peel'],
    ['Cinnamaldehyde', 'C1=CC=C(C=C1)/C=C/C=O', 'C9H8O', 'Cinnamon'],
    ['Glucose', 'C(C1C(C(C(C(O1)O)O)O)O)O', 'C6H12O6', 'Fruit and honey; fuel for every cell'],
    ['Serotonin', 'C1=CC2=C(C=C1O)C(=CN2)CCN', 'C10H12N2O', 'The brain, carrying mood signals'],
    ['Dopamine', 'C1=CC(=C(C=C1CCN)O)O', 'C8H11NO2', 'The brain, carrying reward signals'],
    ['Melatonin', 'CC(=O)NCCC1=CNC2=C1C=C(C=C2)OC', 'C13H16N2O2', 'Made at night; it tells the body to sleep'],
    ['Adenine', 'C1=NC2=NC=NC(=C2N1)N', 'C5H5N5', 'DNA, one of its four letters'],
    ['Aspirin', 'CC(=O)OC1=CC=CC=C1C(=O)O', 'C9H8O4', 'Pain relief, first made from willow bark'],
    ['Paracetamol', 'CC(=O)NC1=CC=C(C=C1)O', 'C8H9NO2', 'The medicine cabinet'],
    ['Penicillin G', 'CC1(C(N2C(S1)C(C2=O)NC(=O)CC3=CC=CC=C3)C(=O)O)C', 'C16H18N2O4S', 'Mould; the first antibiotic'],
  ];
  // a different molecule each day, moving through the list
  const todayIndex = () => { const d = new Date(); return Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000) % MOLECULES.length; };

  function molecule(cv) {
    const c = cv.getContext('2d'), W = cv.width, H = cv.height;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const [name, smiles, formula, found] = MOLECULES[todayIndex()];
    const PX = 36, PY = 44, PW = W * 0.54, PH = H - 80;     // the plate the molecule sits on
    const plate = document.createElement('canvas');
    let drawnFor = '', secs = 0, lastT = 0;

    // draw the molecule onto its own canvas in the page's colours; done again only when the theme changes
    function render(INK, A, DIM, BG, LABEL) {
      drawnFor = INK + A + BG;
      const drawer = new SmilesDrawer.Drawer({
        width: PW, height: PH, padding: 24, bondThickness: 1.3, bondLength: 34, fontFamily: LABEL, fontSizeLarge: 11, fontSizeSmall: 5,
        themes: { hub: { C: INK, O: A, N: A, F: A, CL: A, BR: A, I: A, P: A, S: A, B: A, SI: A, H: DIM, BACKGROUND: BG } },
      });
      SmilesDrawer.parse(smiles, tree => drawer.draw(tree, plate, 'hub', false), () => {});
    }

    function frame(t) {
      if (!cv.isConnected) return;
      const dt = lastT ? Math.min(0.1, (t - lastT) / 1000) : 0; lastT = t;
      if (!still) secs += dt;
      const cs = getComputedStyle(document.documentElement), v = k => cs.getPropertyValue(k).trim();
      const INK = v('--ink'), D = v('--line2'), L = v('--line'), G = v('--grid') || L, A = v('--accent'), BG = v('--bg'), DIM = v('--dim') || D;
      const LABEL = v('--font-label') || 'sans-serif';
      if (drawnFor !== INK + A + BG) render(INK, A, DIM, BG, LABEL);
      const text = (s, x, y, col, size, weight = 600, stretch = 'condensed') => { c.fillStyle = col; c.font = weight + ' ' + size + 'px ' + LABEL; c.fontStretch = stretch; c.fillText(s, x, y); };

      c.fillStyle = BG; c.fillRect(0, 0, W, H);
      c.fillStyle = G; for (let x = 0; x <= W; x += 20) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 20) c.fillRect(0, y, W, 1);
      c.fillStyle = L; for (let x = 0; x <= W; x += 100) c.fillRect(x, 0, 1, H); for (let y = 0; y <= H; y += 100) c.fillRect(0, y, W, 1);

      // the plate: the molecule, four corner brackets, and a scanning line that sweeps across slowly
      if (plate.width) c.drawImage(plate, PX, PY, PW, PH);
      c.strokeStyle = INK; c.lineWidth = 1.5; const k = 16;
      c.beginPath();
      c.moveTo(PX, PY + k); c.lineTo(PX, PY); c.lineTo(PX + k, PY); c.moveTo(PX + PW - k, PY); c.lineTo(PX + PW, PY); c.lineTo(PX + PW, PY + k);
      c.moveTo(PX + PW, PY + PH - k); c.lineTo(PX + PW, PY + PH); c.lineTo(PX + PW - k, PY + PH); c.moveTo(PX + k, PY + PH); c.lineTo(PX, PY + PH); c.lineTo(PX, PY + PH - k);
      c.stroke();
      if (!still) { const sx = PX + ((secs / 9) % 1) * PW; c.globalAlpha = 0.6; c.fillStyle = A; c.fillRect(sx, PY + 6, 1.5, PH - 12); c.globalAlpha = 1; }
      for (let x = PX; x <= PX + PW; x += 10) { c.fillStyle = (x - PX) % 50 ? L : D; c.fillRect(x, PY + PH + 6, 1, (x - PX) % 50 ? 4 : 8); }

      // name, formula with small numbers, and where it's found
      const tx = W * 0.64, tw = W - tx - 24;
      text('MOLECULE OF THE DAY', tx, 60, DIM, 12);
      let size = 30; c.font = '800 ' + size + 'px ' + LABEL; c.fontStretch = 'expanded';
      while (c.measureText(name.toUpperCase()).width > tw && size > 14) { size -= 1; c.font = '800 ' + size + 'px ' + LABEL; c.fontStretch = 'expanded'; }   // setting font resets the width, so set both
      text(name.toUpperCase(), tx, 100, INK, size, 800, 'expanded');
      let fx = tx;
      for (const ch of formula) {
        const sub = /\d/.test(ch); c.font = '600 ' + (sub ? 13 : 20) + 'px ' + LABEL; c.fontStretch = 'normal';
        c.fillStyle = INK; c.fillText(ch, fx, sub ? 138 : 132); fx += c.measureText(ch).width + 1;
      }
      c.fillStyle = INK; c.fillRect(tx, 154, tw, 1.5);
      // wrap the "found in" line to the column
      c.font = '400 14px ' + LABEL; c.fontStretch = 'normal'; c.fillStyle = DIM;
      let line = '', y = 180;
      for (const word of found.split(' ')) { const tryLine = line ? line + ' ' + word : word; if (c.measureText(tryLine).width > tw && line) { c.fillText(line, tx, y); line = word; y += 20; } else line = tryLine; }
      c.fillText(line, tx, y);

      text('MOLECULE', 28, 28, INK, 14);
      if (!still || !plate.width) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  (window.VISUALS = window.VISUALS || []).push({ id: 'molecule', name: 'Molecule', draw: molecule });
})();
