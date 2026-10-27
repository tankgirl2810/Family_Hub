/* The screen-change scan: a sheet of grid paper with an accent edge passes down the page when anyone moves
   between screens. The stylesheet draws it and keeps it still on devices set to reduce motion.
   (The showpiece at the top of Home is now a visual in a sandboxed frame: see visualFrame in app.js.) */
'use strict';

const SetPieces = (() => {
  function transition() {
    const app = document.getElementById('app'), crt = document.getElementById('crt');
    if (!app || !crt) return;
    app.classList.remove('glitch'); crt.classList.remove('run');
    // reading offsetWidth forces the browser to re-layout right now, so removing and re-adding the class
    // actually restarts the animation instead of being squashed into "nothing changed". classic CSS trick
    void app.offsetWidth;
    app.classList.add('glitch'); crt.classList.add('run');
    // Tidy up afterwards, so nothing is left parked over or under the page.
    // Only their own animations count: ones inside the page bubble up too.
    const done = (node, cls) => { const off = (event) => { if (event.target !== node) return; node.classList.remove(cls); node.removeEventListener('animationend', off); }; node.addEventListener('animationend', off); };
    done(crt, 'run'); done(app, 'glitch');
  }

  return { transition };
})();
